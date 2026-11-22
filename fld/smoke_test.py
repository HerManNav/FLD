"""Isolated end-to-end smoke check for the frozen Windows executable."""

import json
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys
import tempfile
import time
import tkinter as tk
import traceback

from . import paths
from .app import App
from .queue_manager import DownloadManager


def _write_report(path, report):
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def _verify_native_icon(root, icon_path):
    """Load the bundled ICO and confirm the mapped Tk window has a native icon."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.LoadImageW.argtypes = [
        wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
        ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.DestroyIcon.argtypes = [wintypes.HICON]
    user32.DestroyIcon.restype = wintypes.BOOL
    user32.SendMessageW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    ]
    user32.SendMessageW.restype = ctypes.c_ssize_t

    hwnd = user32.GetAncestor(root.winfo_id(), 2)
    if not hwnd:
        raise ctypes.WinError(ctypes.get_last_error(), "Could not resolve Tk window handle.")
    loaded_icon = user32.LoadImageW(None, str(icon_path), 1, 32, 32, 0x10)
    if not loaded_icon:
        raise ctypes.WinError(ctypes.get_last_error(), "Could not load bundled ICO natively.")
    try:
        big = user32.SendMessageW(hwnd, 0x007F, 1, 0)
        small = user32.SendMessageW(hwnd, 0x007F, 0, 0)
        class_long = (user32.GetClassLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8
                      else user32.GetClassLongW)
        class_long.argtypes = [wintypes.HWND, ctypes.c_int]
        class_long.restype = ctypes.c_size_t
        class_big = class_long(hwnd, -14)
        class_small = class_long(hwnd, -34)
        window_icon_available = bool(big or small or class_big or class_small)
        if not window_icon_available:
            raise RuntimeError("Tk window has no native big, small, or class icon handle.")
        if not user32.DestroyIcon(loaded_icon):
            raise ctypes.WinError(ctypes.get_last_error(), "Could not release smoke-test ICO handle.")
        loaded_icon = None
        return {
            "native_icon_loaded": True,
            "window_icon_available": True,
            "window_icon_handles": {
                "big": int(big),
                "small": int(small),
                "class_big": int(class_big),
                "class_small": int(class_small),
            },
        }
    finally:
        if loaded_icon:
            user32.DestroyIcon(loaded_icon)


def run(report_path):
    """Run smoke checks against temporary state; return process exit code."""
    report = {
        "frozen": paths.FROZEN,
        "python_version": sys.version,
        "tk_version": None,
        "executable_path": str(Path(sys.executable).resolve()),
        "data_root": str(paths.DATA_DIR.resolve()),
        "resource_root": str(paths.RESOURCE_DIR.resolve()),
        "icon_path": str(paths.ICON_PATH.resolve()),
        "native_icon_loaded": False,
        "window_icon_available": False,
        "window_icon_handles": {},
        "controls": {},
        "worker_started": False,
        "worker_stopped": False,
        "ctrl_a_selected_count": 0,
        "completed_removed_count": 0,
        "sentinel_preserved": False,
        "app_closed": False,
        "manager_closed": False,
        "temporary_data_removed": False,
    }
    exit_code = 1
    app = root = manager = None
    temporary = None
    manager_closed = False
    try:
        if not paths.FROZEN:
            raise RuntimeError("Smoke test requires a frozen executable.")
        if os.name != "nt":
            raise RuntimeError("Smoke test requires Windows.")
        executable_dir = Path(sys.executable).resolve().parent
        if paths.DATA_DIR.resolve() != executable_dir:
            raise RuntimeError("Writable data root does not resolve beside the executable.")
        if paths.ICON_PATH.resolve() != paths.RESOURCE_DIR.resolve() / "assets" / "fld.ico":
            raise RuntimeError("Application icon does not resolve inside the bundled resource tree.")
        if not paths.ICON_PATH.is_file():
            raise RuntimeError("Bundled application icon is missing.")

        temporary = tempfile.TemporaryDirectory(prefix="fld-smoke-")
        temp_path = Path(temporary.name)
        queue_path = temp_path / "queue.sqlite3"
        download_path = temp_path / "downloads"
        report["smoke_queue_path"] = str(queue_path)
        report["smoke_download_dir"] = str(download_path)
        manager = DownloadManager(queue_path, download_path, autostart=False)
        manager.add(["KSK@fld-smoke-completed"])
        with manager.lock:
            task = next(iter(manager.tasks.values()))
            completed_id = task["id"]
            task["desired"] = "done"
            task["status"] = "Complete"
            sentinel = download_path / task["filename"]
            sentinel.write_bytes(b"FLD smoke output sentinel\x00\x01")
            task["path"] = str(sentinel)
            manager.store.save(manager.tasks)

        root = tk.Tk()
        report["tk_version"] = str(root.tk.call("info", "patchlevel"))
        app = App(root, manager=manager, tray_factory=lambda *_args: None)
        root.deiconify()
        root.update()
        if not root.winfo_ismapped():
            raise RuntimeError("Production window did not map.")
        report.update(_verify_native_icon(root, paths.ICON_PATH))

        report["controls"] = {
            "remove_completed": hasattr(app, "remove_completed_button"),
            "delete_selected": hasattr(app, "delete_button"),
            "table": hasattr(app, "table"),
            "status": hasattr(app, "status"),
        }
        report["column_headings"] = list(app.table.HEADINGS)
        report["shared_row_controls"] = len(app.table.controls)
        report["no_select_heading"] = "Select" not in app.table.HEADINGS
        if not all(report["controls"].values()):
            raise RuntimeError("Expected production controls are missing.")
        if (len(app.table.HEADINGS) != 11 or not report["no_select_heading"] or
                report["shared_row_controls"] != 3):
            raise RuntimeError("List does not have 11 headings and three shared row controls.")
        report["worker_started"] = app.action_thread.is_alive()
        if not report["worker_started"]:
            raise RuntimeError("Action worker did not start.")

        app.canvas.focus_force()
        root.update()
        app.canvas.event_generate("<Control-a>")
        root.update()
        report["ctrl_a_selected_count"] = len(app.table.selected_ids)
        if report["ctrl_a_selected_count"] != 1:
            raise RuntimeError("Ctrl+A did not select the visible synthetic row.")

        app.remove_completed_button.invoke()
        deadline = time.monotonic() + 8
        expected_status = "Removed 1 completed task(s). Completed files were kept on disk."
        while time.monotonic() < deadline and app.status.get() != expected_status:
            root.update()
        if (app.status.get() != expected_status or manager.snapshot() or app.rows or
                app.remove_completed_pending or completed_id not in app.removed_ids):
            raise RuntimeError("Remove completed acknowledgement did not finish before timeout.")
        report["completed_removed_count"] = len({completed_id} & app.removed_ids)
        report["sentinel_preserved"] = sentinel.read_bytes() == b"FLD smoke output sentinel\x00\x01"
        if not report["sentinel_preserved"]:
            raise RuntimeError("Completed output sentinel changed or disappeared.")

        worker_thread = app.action_thread
        app.close()
        app = None
        report["app_closed"] = True
        report["worker_stopped"] = not worker_thread.is_alive()
        report["manager_closed"] = True
        manager_closed = True
        if not report["worker_stopped"]:
            raise RuntimeError("Action worker did not stop cleanly.")
        exit_code = 0
    except Exception as exc:
        report["error"] = str(exc)
        report["traceback"] = traceback.format_exc()
    finally:
        if app is not None and not app.closed:
            try:
                worker_thread = app.action_thread
                app.close()
                report["app_closed"] = True
                report["worker_stopped"] = not worker_thread.is_alive()
                report["manager_closed"] = True
            except Exception as exc:
                report["shutdown_error"] = str(exc)
                exit_code = 1
        elif manager is not None and not manager_closed:
            try:
                manager.close()
                report["manager_closed"] = True
            except Exception as exc:
                report["shutdown_error"] = str(exc)
                exit_code = 1
        if root is not None:
            try:
                root.destroy()
            except tk.TclError:
                pass
        if temporary is not None:
            temp_path = Path(temporary.name)
            try:
                temporary.cleanup()
                report["temporary_data_removed"] = not temp_path.exists()
            except Exception as exc:
                report["temporary_cleanup_error"] = str(exc)
                exit_code = 1
            if not report["temporary_data_removed"]:
                exit_code = 1
                report.setdefault("error", "Temporary smoke data was not removed.")
        try:
            _write_report(report_path, report)
        except Exception as exc:
            print(f"Could not write smoke report: {exc}", file=sys.stderr)
            exit_code = 1
    return exit_code
