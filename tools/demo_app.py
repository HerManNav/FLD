"""Run FLD with deterministic in-memory demo data; never uses the real queue or node."""

from __future__ import annotations

import argparse
import copy
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import struct
import threading
import time
import tkinter as tk
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fld.app as fld_app


FILES = (
    ("Orchid.jpg", 18, "CHK@demo/orchid"),
    ("Harbor.mp4", 120, "CHK@demo/harbor"),
    ("Notes.txt", 0.05, "CHK@demo/notes"),
    ("Sunset.jpg", 12, "CHK@demo/sunset"),
    ("Trail.mp4", 80, "CHK@demo/trail"),
    ("Archive.zip", 24, "CHK@demo/archive"),
    ("Portrait.JPG", 9, "CHK@demo/portrait"),
    ("Audio.ogg", 8, "CHK@demo/audio"),
)
NAMESPACE = uuid.UUID("2a0db761-b911-5cd5-9bf0-ea8f0c24c89b")


def make_task(index):
    filename, size_mb, key = FILES[index]
    identifier = str(uuid.uuid5(NAMESPACE, filename))
    size = round(size_mb * 1_000_000)
    return dict(
        id=identifier, display_id=index + 1, key=key, filename=filename,
        desired="new", status="Ready", size=size, size_final=True, fetched=0,
        fraction=0.0, speed=0, selected=True, path=None, error="", retry_at=0,
        retries=0, elapsed=0.0, active_since=None, timer_known=True,
        eta_speed=0, ever_started=False,
    )


class DemoManager:
    """Small thread-safe manager matching the methods used by App."""

    def __init__(self, notify=lambda tasks: None, frozen=False):
        self.lock = threading.RLock()
        self.notify = notify
        self.tasks = {}
        self.column_widths = None
        self.sort_preferences = {"column": "id", "descending": False, "running_first": False}
        self.window_preferences = {"width": 1280, "height": 720, "maximized": False}
        self.frozen = frozen
        self.closed = threading.Event()
        self.timer = threading.Thread(target=self._run, name="fld-demo-progress", daemon=True)
        self.timer.start()

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(list(self.tasks.values()))

    def _changed(self):
        self.notify(self.snapshot())

    def add(self, keys):
        with self.lock:
            by_key = {task["key"] for task in self.tasks.values()}
            for index, (_, _, key) in enumerate(FILES):
                if key in keys and key not in by_key:
                    task = make_task(index)
                    self.tasks[task["id"]] = task
                    by_key.add(key)
        self._changed()

    def set_column_widths(self, widths):
        self.column_widths = list(widths)

    def set_sort_preferences(self, preferences):
        with self.lock:
            self.sort_preferences = copy.deepcopy(preferences)

    def set_window_preferences(self, preferences):
        with self.lock:
            self.window_preferences = copy.deepcopy(preferences)

    def action(self, identifiers, desired):
        now = time.time()
        with self.lock:
            for identifier in identifiers:
                task = self.tasks.get(identifier)
                if task is None or task["desired"] == "done":
                    continue
                if desired == "running":
                    task["ever_started"] = True
                    task["desired"], task["status"] = "running", "Downloading"
                    if task["active_since"] is None:
                        task["active_since"] = now
                    task["eta_speed"] = task["size"] * 0.05
                    task["error"] = ""
                elif desired == "paused":
                    if task["active_since"] is not None:
                        task["elapsed"] += max(0.0, now - task["active_since"])
                        task["active_since"] = None
                    task["desired"], task["status"] = "paused", "Paused"
                elif desired == "cancelled":
                    if task["active_since"] is not None:
                        task["elapsed"] += max(0.0, now - task["active_since"])
                        task["active_since"] = None
                    task["desired"], task["status"] = "cancelled", "Cancelled"
                task["speed"] = 0
        self._changed()

    def start(self, identifiers):
        self.action(identifiers, "running")

    def pause(self, identifiers):
        self.action(identifiers, "paused")

    def cancel(self, identifiers):
        self.action(identifiers, "cancelled")

    def remove_unstarted(self):
        with self.lock:
            removed = [identifier for identifier, task in self.tasks.items() if not task["ever_started"]]
            for identifier in removed:
                del self.tasks[identifier]
        self._changed()
        return removed

    def remove_completed(self):
        with self.lock:
            removed = [identifier for identifier, task in self.tasks.items()
                       if task["desired"] == "done"]
            for identifier in removed:
                del self.tasks[identifier]
        self._changed()
        return removed

    def delete_tasks(self, identifiers):
        with self.lock:
            removed = [identifier for identifier in dict.fromkeys(identifiers) if identifier in self.tasks]
            for identifier in removed:
                del self.tasks[identifier]
        self._changed()
        return removed

    def set_progress(self, identifier, fraction):
        fraction = max(0.0, min(1.0, float(fraction)))
        now = time.time()
        with self.lock:
            task = self._task(identifier)
            task["fraction"] = fraction
            task["fetched"] = round(task["size"] * fraction)
            task["speed"] = round(task["size"] * 0.05) if task["desired"] == "running" and fraction < 1 else 0
            task["eta_speed"] = task["size"] * 0.05 if task["desired"] == "running" and fraction < 1 else 0
            if fraction >= 1 and task["desired"] == "running":
                if task["active_since"] is not None:
                    task["elapsed"] += max(0.0, now - task["active_since"])
                task.update(desired="done", status="Complete", fraction=1.0, speed=0,
                            eta_speed=0, active_since=None)
        self._changed()

    def set_frozen(self, frozen):
        with self.lock:
            self.frozen = bool(frozen)

    def reset(self):
        with self.lock:
            self.tasks.clear()
            self.frozen = False
        self._changed()

    def _task(self, selector):
        if isinstance(selector, int):
            matches = sorted(self.tasks.values(), key=lambda task: task["display_id"])
            if selector < 0 or selector >= len(matches):
                raise ValueError("Task index is outside the demo queue.")
            return matches[selector]
        task = self.tasks.get(str(selector))
        if task is None:
            task = next((row for row in self.tasks.values() if row["filename"] == selector), None)
        if task is None:
            raise ValueError("Demo task not found.")
        return task

    def _run(self):
        while not self.closed.wait(0.5):
            with self.lock:
                if self.frozen:
                    continue
                for task in self.tasks.values():
                    if task["desired"] != "running":
                        continue
                    before = task["fraction"]
                    task["fraction"] = min(1.0, before + 0.025)
                    task["fetched"] = round(task["size"] * task["fraction"])
                    task["speed"] = round(task["size"] * 0.05)
                    task["eta_speed"] = task["size"] * 0.05
                    if task["fraction"] >= 1.0:
                        if task["active_since"] is not None:
                            task["elapsed"] += max(0.0, time.time() - task["active_since"])
                        task["desired"], task["status"], task["speed"] = "done", "Complete", 0
                        task["active_since"], task["eta_speed"] = None, 0
            self._changed()

    def close(self):
        self.closed.set()
        if threading.current_thread() is not self.timer:
            self.timer.join(timeout=1)


class DemoTray:
    """No-op tray stand-in keeps demo window close behavior self-contained."""

    def __init__(self, root, icon_path, callback):
        self.callback, self.visible = callback, False
        self.menu_pending = False

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False

    def close(self):
        self.visible = False


class DemoApp(fld_app.App):
    def open_folder(self):
        self.status.set("Downloads are simulated; no files are written.")


class DemoRECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class DemoBITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class DemoBITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", DemoBITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class WindowExporter:
    """Capture only this process's own real Tk window through PrintWindow."""

    GA_ROOT = 2
    DIB_RGB_COLORS = 0
    BI_RGB = 0

    def __init__(self, root):
        if os.name != "nt":
            raise OSError("--record requires Windows PrintWindow support.")
        self.root = root
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self.user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        self.user32.GetAncestor.restype = wintypes.HWND
        self.user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.user32.IsWindowVisible.argtypes = [wintypes.HWND]
        self.user32.IsWindowVisible.restype = wintypes.BOOL
        self.user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(DemoRECT)]
        self.user32.GetWindowRect.restype = wintypes.BOOL
        self.user32.GetWindowDC.argtypes = [wintypes.HWND]
        self.user32.GetWindowDC.restype = wintypes.HDC
        self.user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        self.user32.ReleaseDC.restype = ctypes.c_int
        self.user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
        self.user32.PrintWindow.restype = wintypes.BOOL
        self.gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        self.gdi32.CreateCompatibleDC.restype = wintypes.HDC
        self.gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
        self.gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
        self.gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
        self.gdi32.SelectObject.restype = wintypes.HANDLE
        self.gdi32.DeleteDC.argtypes = [wintypes.HDC]
        self.gdi32.DeleteDC.restype = wintypes.BOOL
        self.gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
        self.gdi32.DeleteObject.restype = wintypes.BOOL
        self.gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                                         ctypes.c_void_p, ctypes.POINTER(DemoBITMAPINFO), wintypes.UINT]
        self.gdi32.GetDIBits.restype = ctypes.c_int

    def window_status(self):
        mapped = bool(self.root.winfo_ismapped())
        child_hwnd = wintypes.HWND(self.root.winfo_id())
        hwnd = self.user32.GetAncestor(child_hwnd, self.GA_ROOT)
        process_id = wintypes.DWORD()
        rect = DemoRECT()
        visible = bool(hwnd and self.user32.IsWindowVisible(hwnd))
        owner_thread = (self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
                        if hwnd else 0)
        rect_ok = bool(hwnd and self.user32.GetWindowRect(hwnd, ctypes.byref(rect)))
        bounds = (rect.left, rect.top, rect.right, rect.bottom) if rect_ok else None
        width = rect.right - rect.left if rect_ok else 0
        height = rect.bottom - rect.top if rect_ok else 0
        owned = bool(owner_thread and process_id.value == os.getpid())
        ready = bool(mapped and hwnd and visible and rect_ok and width > 0 and height > 0 and owned)
        return {
            "ready": ready, "mapped": mapped, "visible": visible, "rect_ok": rect_ok,
            "bounds": bounds, "width": width, "height": height,
            "owner_pid": process_id.value, "owner_thread": owner_thread,
        }, hwnd

    @staticmethod
    def describe_status(status):
        return (f"mapped={status['mapped']} visible={status['visible']} "
                f"GetWindowRect={status['rect_ok']} bounds={status['bounds']} "
                f"owner_pid={status['owner_pid']} owner_thread={status['owner_thread']}")

    def capture(self, output):
        status, hwnd = self.window_status()
        if not status["ready"]:
            raise OSError("Demo window is not ready for own-window capture: " + self.describe_status(status))
        width, height = status["width"], status["height"]
        window_dc = memory_dc = bitmap = previous = None
        print_result = None
        lines = None
        try:
            window_dc = self.user32.GetWindowDC(hwnd)
            if not window_dc:
                raise ctypes.WinError(ctypes.get_last_error())
            memory_dc = self.gdi32.CreateCompatibleDC(window_dc)
            bitmap = self.gdi32.CreateCompatibleBitmap(window_dc, width, height)
            if not memory_dc or not bitmap:
                raise ctypes.WinError(ctypes.get_last_error())
            previous = self.gdi32.SelectObject(memory_dc, bitmap)
            if not previous or previous == wintypes.HANDLE(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            print_result = bool(self.user32.PrintWindow(hwnd, memory_dc, 0))
            if not print_result:
                raise OSError("PrintWindow could not render the demo window.")
            if not self.gdi32.SelectObject(memory_dc, previous):
                previous = None
                raise ctypes.WinError(ctypes.get_last_error())
            previous = None
            info = DemoBITMAPINFO()
            info.bmiHeader = DemoBITMAPINFOHEADER(
                ctypes.sizeof(DemoBITMAPINFOHEADER), width, -height, 1, 32,
                self.BI_RGB, width * height * 4, 0, 0, 0, 0,
            )
            pixels = ctypes.create_string_buffer(width * height * 4)
            lines = self.gdi32.GetDIBits(memory_dc, bitmap, 0, height, pixels, ctypes.byref(info), self.DIB_RGB_COLORS)
            if lines != height:
                raise OSError(f"GetDIBits returned {lines} of {height} scanlines.")
            data = pixels.raw
            pixel_count = width * height
            sample_step = max(1, pixel_count // 4096)
            sampled_rgb = [tuple(data[offset + channel] for channel in (2, 1, 0))
                           for pixel in range(0, pixel_count, sample_step)
                           for offset in (pixel * 4,)]
            diversity = len(set(sampled_rgb))
            extrema = tuple((min(rgb[channel] for rgb in sampled_rgb),
                             max(rgb[channel] for rgb in sampled_rgb)) for channel in range(3))
            if diversity <= 1:
                raise OSError(
                    "Captured demo window has uniformly flat RGB; refusing to save it. "
                    f"width={width} height={height}; {self.describe_status(status)}; "
                    f"PrintWindow={print_result} GetDIBits={lines}/{height}; "
                    f"RGB extrema={extrema}; sampled RGB tuple diversity={diversity}/{len(sampled_rgb)}."
                )
            output = Path(output)
            output.parent.mkdir(parents=True, exist_ok=True)
            pixel_offset = 14 + ctypes.sizeof(DemoBITMAPINFOHEADER)
            file_header = b"BM" + struct.pack("<IHHI", pixel_offset + len(data), 0, 0, pixel_offset)
            dib_header = struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 32, 0,
                                     len(data), 0, 0, 0, 0)
            with output.open("wb") as stream:
                stream.write(file_header)
                stream.write(dib_header)
                stream.write(data)
            return width, height
        finally:
            if previous:
                self.gdi32.SelectObject(memory_dc, previous)
            if bitmap:
                self.gdi32.DeleteObject(bitmap)
            if memory_dc:
                self.gdi32.DeleteDC(memory_dc)
            if window_dc:
                self.user32.ReleaseDC(hwnd, window_dc)


def demo_scrape(source, cancel, report):
    """Replace only fld.app.scrape: retain the real App scan/action flow."""
    del source
    report("Reading demo page...")
    deadline = time.monotonic() + 0.75
    while time.monotonic() < deadline:
        if cancel.wait(min(0.05, max(0, deadline - time.monotonic()))):
            raise fld_app.Cancelled()
    if cancel.is_set():
        raise fld_app.Cancelled()
    return [key for _, _, key in FILES]


class CommandFile:
    """Poll one JSON file and acknowledge each strictly increasing sequence."""

    def __init__(self, path, ack_path, manager, close, inspect):
        self.path, self.ack_path = Path(path), Path(ack_path)
        self.manager, self.close, self.inspect = manager, close, inspect
        self.last_sequence = 0
        self.signature = None

    def poll(self):
        try:
            stat = self.path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
            if signature == self.signature:
                return
            self.signature = signature
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            sequence = payload.get("seq")
            if isinstance(sequence, bool) or not isinstance(sequence, int):
                raise ValueError("seq must be an integer.")
            if sequence <= self.last_sequence:
                return
            operation = payload.get("action")
            result = {"seq": sequence, "ok": True}
            if operation == "freeze":
                self.manager.set_frozen(True)
            elif operation == "resume":
                self.manager.set_frozen(False)
            elif operation == "progress":
                self.manager.set_progress(payload.get("task"), payload.get("fraction"))
            elif operation == "reset":
                self.manager.reset()
            elif operation == "inspect":
                result.update(self.inspect())
            elif operation == "close":
                self.close()
            else:
                raise ValueError("action must be freeze, resume, progress, reset, inspect, or close.")
            self.last_sequence = sequence
        except FileNotFoundError:
            return
        except Exception as exc:
            result = {"seq": payload.get("seq") if "payload" in locals() else None, "ok": False, "error": str(exc)}
            if isinstance(result["seq"], int) and result["seq"] > self.last_sequence:
                self.last_sequence = result["seq"]
        self.ack_path.parent.mkdir(parents=True, exist_ok=True)
        self.ack_path.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")


class DemoRecorder:
    """Drive the approved GUI story and export sequential own-window frames."""

    DURATION_MS = 22_000

    def __init__(self, root, app, manager, directory):
        import tkinter.ttk as ttk
        self.root, self.app, self.manager = root, app, manager
        self.directory = Path(directory)
        self.frames_dir = self.directory / "frames"
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        self.exporter = WindowExporter(root)
        self.manifest = []
        self.started = time.monotonic()
        self.finished = False
        self.failed = False
        self._select_none = self._button("Select none")
        self._running_first = next(widget for widget in self._widgets(root)
                                   if isinstance(widget, ttk.Checkbutton)
                                   and widget.cget("text") == "Running first")
        self.size = None

    @staticmethod
    def _widgets(parent):
        for child in parent.winfo_children():
            yield child
            yield from DemoRecorder._widgets(child)

    def _button(self, text):
        import tkinter.ttk as ttk
        return next(widget for widget in self._widgets(self.root)
                    if isinstance(widget, ttk.Button) and widget.cget("text") == text)

    def capture(self):
        self.root.update_idletasks()
        deadline = time.monotonic() + 2.0
        self._capture_when_ready(deadline)

    def _capture_when_ready(self, deadline):
        if self.finished:
            return
        status, _ = self.exporter.window_status()
        if not status["ready"]:
            if time.monotonic() >= deadline:
                self.fail(OSError("Timed out waiting for the demo's own window to be mapped and visible "
                                  f"for capture: {self.exporter.describe_status(status)}"))
            else:
                self.root.after(50, lambda: self._capture_when_ready(deadline))
            return
        try:
            self._save_frame()
        except Exception as exc:
            self.fail(exc)

    def _save_frame(self):
        number = len(self.manifest)
        relative = Path("frames") / f"frame_{number:04d}.bmp"
        width, height = self.exporter.capture(self.directory / relative)
        if width < 800 or height < 460:
            raise OSError(f"Captured demo window is unexpectedly small: {width}x{height}.")
        if self.size is not None and self.size != (width, height):
            raise OSError(f"Demo window changed size during recording: {self.size} to {(width, height)}.")
        self.size = width, height
        now_ms = 0 if not self.manifest else round((time.monotonic() - self.started) * 1000)
        self.manifest.append({"path": relative.as_posix(), "duration_ms": now_ms,
                              "width": width, "height": height})
        self._write_manifest()

    def _write_manifest(self):
        durations = []
        for index, frame in enumerate(self.manifest):
            next_ms = (self.manifest[index + 1]["duration_ms"]
                       if index + 1 < len(self.manifest) else self.DURATION_MS)
            durations.append(max(1, next_ms - frame["duration_ms"]))
        payload = [{**frame, "duration_ms": duration}
                   for frame, duration in zip(self.manifest, durations)]
        (self.directory / "manifest.json").write_text(
            json.dumps({"frames": payload, "duration_ms": self.DURATION_MS}, indent=2) + "\n",
            encoding="utf-8")

    def _at(self, seconds, action):
        delay = max(0, round(seconds * 1000 - (time.monotonic() - self.started) * 1000))
        self.root.after(delay, lambda: self._safe(action))

    def _safe(self, action):
        if self.finished:
            return
        try:
            action()
        except Exception as exc:
            self.fail(exc)

    def _capture(self):
        self.capture()

    def _wait_for(self, description, predicate, continuation, timeout=3.0):
        deadline = time.monotonic() + timeout
        def poll():
            if self.finished:
                return
            try:
                if predicate():
                    continuation()
                elif time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out waiting for {description}.")
                else:
                    self.root.after(50, poll)
            except Exception as exc:
                self.fail(exc)
        poll()

    def start(self):
        self.root.update_idletasks()
        deadline = time.monotonic() + 2.0
        self.root.after(50, lambda: self._start_when_ready(deadline))

    def _start_when_ready(self, deadline):
        if self.finished:
            return
        status, _ = self.exporter.window_status()
        if not status["ready"]:
            if time.monotonic() >= deadline:
                self.fail(OSError("Timed out waiting for the demo's own window to map and paint: "
                                  + self.exporter.describe_status(status)))
            else:
                self.root.after(50, lambda: self._start_when_ready(deadline))
            return
        try:
            self.started = time.monotonic()
            self._save_frame()
        except Exception as exc:
            self.fail(exc)
            return
        self._schedule_story()

    def _schedule_story(self):
        self._at(1.0, lambda: (self.app.entry.delete(0, "end"),
                               self.app.entry.insert(0, "https://example.invalid/toy-links"),
                               self._capture()))
        self._at(2.0, lambda: (self.app.find_button.invoke(), self._capture()))
        for second in (2.2, 2.4, 2.6):
            self._at(second, self._capture)
        self._at(3.0, lambda: self._wait_for(
            "eight Ready rows with IDs 1..8",
            lambda: len(self.app.rows) == 8 and
            [row["display_id"] for row in sorted(self.app.rows.values(), key=lambda row: row["display_id"])] == list(range(1, 9)) and
            all(row["desired"] == "new" for row in self.app.rows.values()),
            self._rows_ready))
        self._at(4.0, self._select_files)
        self._at(5.0, lambda: (self.app.download_button.invoke(),
                               self._wait_for("Orchid and Harbor running",
                                              self._chosen_running, self._started)))
        for second, values in ((6, (.08, .04)), (7, (.20, .12)),
                               (8, (.34, .24)), (9, (.48, .36)),
                               (11, (.60, .50)), (12, (.72, .66)),
                               (14, (.82, .76)), (15, (.90, .84)),
                               (16, (.96, .91)), (17, (1, .96)), (18, (1, 1))):
            self._at(second, lambda values=values: self._progress(values))
        self._at(8.5, self._running_first_on)
        self._at(10.0, lambda: self._filter(r"\.jpg$"))
        self._at(13.0, lambda: self._filter(""))
        self._at(19.2, self._settled_final)
        self._at(22.0, self.finish)

    def _rows_ready(self):
        self.capture()

    def _select_files(self):
        for filename in ("Orchid.jpg", "Harbor.mp4"):
            identifier = next(row["id"] for row in self.app.rows.values()
                              if row["filename"] == filename)
            self.app.table.select_gesture(identifier, control=True)
        self.capture()

    def _chosen_running(self):
        chosen = [row for row in self.app.rows.values()
                  if row["filename"] in {"Orchid.jpg", "Harbor.mp4"}]
        return len(chosen) == 2 and all(row["desired"] == "running" for row in chosen)

    def _started(self):
        self.capture()

    def _progress(self, values):
        self.manager.set_progress("Orchid.jpg", values[0])
        self.manager.set_progress("Harbor.mp4", values[1])
        self._wait_for("visible progress updates",
                       lambda: all(abs(next((row["fraction"] for row in self.app.rows.values()
                                             if row["filename"] == filename), -1) - fraction) < 1e-9
                                   for filename, fraction in zip(("Orchid.jpg", "Harbor.mp4"), values)),
                       self._render_progress)

    def _render_progress(self):
        self.app.table.render(refresh=True)
        self.root.update_idletasks()
        self.capture()

    def _settled_final(self):
        def final_view_ready():
            tasks = list(self.app.rows.values())
            samples = self.app.graph.samples
            return (sum(task["desired"] == "running" for task in tasks) == 0 and
                    sum(task["desired"] == "done" for task in tasks) == 2 and
                    sum(task["desired"] == "new" for task in tasks) == 6 and
                    bool(samples) and samples[-1][1] == 0)

        self._wait_for("normal zero-speed graph tick and completed final view",
                       final_view_ready, self.capture, timeout=2.5)

    def _running_first_on(self):
        if not self.app.running_first.get():
            self._running_first.invoke()
        self.capture()

    def _filter(self, value):
        self.app.filter_text.set(value)
        self.app.apply_filter_now()
        expected = 3 if value else 8
        self._wait_for(f"{expected} visible rows after filter change",
                       lambda: len(self.app.table.order) == expected, self.capture)

    def finish(self):
        if self.finished:
            return
        self.finished = True
        print(f"Recorded {len(self.manifest)} frames to {self.directory}; manifest duration 22000 ms.")
        self.app.begin_exit()

    def fail(self, error):
        if self.finished:
            return
        self.failed = True
        self.finished = True
        print(f"Recording failed: {error}", file=sys.stderr)
        try:
            self.manager.close()
        finally:
            self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commands", type=Path, help="Poll this JSON command file.")
    parser.add_argument("--ack", type=Path, help="Write acknowledgements here (default: COMMANDS.ack).")
    parser.add_argument("--record", type=Path, help="Record the approved GUI story into this directory.")
    args = parser.parse_args()

    if args.commands and args.record:
        parser.error("--commands and --record cannot be used together.")
    if args.record:
        if os.name == "nt":
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        elif os.name != "nt":
            parser.error("--record requires Windows PrintWindow support.")

    root = tk.Tk()
    root.title("FLD demo - simulated downloads")
    root.geometry("1280x720")
    root.minsize(800, 460)
    manager = DemoManager(frozen=bool(args.commands or args.record))
    fld_app.DOWNLOAD_DIR = Path("downloads (simulation only)")
    app = DemoApp(root, manager=manager, tray_factory=DemoTray)
    fld_app.scrape = demo_scrape
    root.title("FLD demo - simulated downloads")
    root.geometry("1280x720")

    if args.commands:
        ack = args.ack or args.commands.with_name(args.commands.name + ".ack")
        def inspect_window():
            with manager.lock:
                preferences = copy.deepcopy(manager.window_preferences)
            state = root.state()
            return {
                "window_preferences": preferences,
                "window": {
                    "state": state,
                    "client_size": [root.winfo_width(), root.winfo_height()],
                    "normal_size": list(app.normal_size),
                    "hidden": bool(app.hidden),
                },
            }

        controller = CommandFile(args.commands, ack, manager, app.begin_exit, inspect_window)

        def check_commands():
            if app.closed:
                return
            controller.poll()
            root.after(50, check_commands)

        root.after(50, check_commands)
    recorder = None
    if args.record:
        try:
            recorder = DemoRecorder(root, app, manager, args.record)
        except Exception as exc:
            manager.close()
            root.destroy()
            parser.error(f"Cannot initialize recorder: {exc}")
        root.after_idle(recorder.start)
    root.mainloop()
    manager.close()
    return 1 if recorder is not None and recorder.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
