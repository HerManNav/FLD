"""Windows desktop UI for scraping and durable Freenet downloads."""

import os
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from .freenet import Cancelled, scrape
from .gui_widgets import DownloadList, SpeedGraph
from .paths import DOWNLOAD_DIR, ICON_PATH
from .queue_manager import DownloadManager
from .windows_tray import NativeTray


def action_worker(manager, actions, events):
    """Keep state locks, disk commits, and node shutdown off Tk's thread."""
    while True:
        name, args, generation, cancel = actions.get()
        try:
            if name == "stop":
                return
            if name == "exit":
                manager.close()
                events.put((None, "closed", ()))
                return
            if name == "remove_unstarted":
                events.put((generation, "remove_done", (manager.remove_unstarted(),)))
                continue
            if name == "remove_completed":
                events.put((None, "remove_completed_done", (manager.remove_completed(),)))
                continue
            if name == "delete_tasks":
                events.put((None, "delete_done", (manager.delete_tasks(*args),)))
                continue
            if name == "scan_add":
                if cancel.is_set():
                    events.put((generation, "scan_cancelled", ()))
                else:
                    manager.add(args[0])
                    events.put((generation, "scan_done", (len(args[0]),)))
            else:
                getattr(manager, name)(*args)
        except Exception as exc:
            if name == "scan_add":
                kind, event_generation = "scan_error", generation
            elif name == "delete_tasks":
                kind, event_generation = "delete_error", None
            elif name == "remove_completed":
                kind, event_generation = "remove_completed_error", None
            else:
                kind, event_generation = "action_error", generation
            events.put((event_generation, kind, (str(exc),)))
        finally:
            actions.task_done()


class App:
    def __init__(self, root, manager=None, tray_factory=NativeTray):
        self.root, self.tray_factory = root, tray_factory
        self.tray, self.hidden, self.exiting = None, False, False
        self.closed = False
        self.events, self.actions = queue.Queue(), queue.Queue()
        self.cancel = threading.Event()
        self.generation, self.busy = 0, False
        self.rows = {}
        self.removed_ids, self.selected_row = set(), None
        self.delete_pending = False
        self.remove_completed_pending = False
        self.filter_after_id = self.geometry_after_id = None
        self.window_state_after_ids = set()
        self.restoring_window_state = False
        self.startup_restoring = True
        self.normal_size = (1280, 650)
        self.last_non_minimized_state = "normal"
        self.maximized = False
        self.manager = manager or DownloadManager(notify=self.queue_changed)
        self.manager.notify = self.queue_changed
        saved = self.manager.window_preferences
        self.normal_size = self.clamp_window_size(saved["width"], saved["height"])
        self.maximized = saved["maximized"]
        self.source = tk.StringVar(master=root)
        self.filter_text = tk.StringVar(master=root)
        self.status = tk.StringVar(master=root, value="Paste a page URL, then click Find links.")
        self.filter_error = tk.StringVar(master=root)
        self.detail_key, self.detail_status = tk.StringVar(master=root), tk.StringVar(master=root)
        root.title("Freenet Link Downloader")
        root.geometry(f"{self.normal_size[0]}x{self.normal_size[1]}")
        root.minsize(800, 460)
        if os.name == "nt":
            root.iconbitmap(default=str(ICON_PATH))
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        # Clam elements keep the bar green even when Windows uses another accent color.
        for element in ("trough", "pbar"):
            name = f"Green.Horizontal.Progressbar.{element}"
            if name not in style.element_names():
                style.element_create(name, "from", "clam", f"Horizontal.Progressbar.{element}")
        style.layout("Green.Horizontal.TProgressbar", [
            ("Green.Horizontal.Progressbar.trough", {"sticky": "nswe", "children": [
                ("Green.Horizontal.Progressbar.pbar", {"side": "left", "sticky": "ns"}),
            ]}),
        ])
        style.configure("Green.Horizontal.TProgressbar", background="#21ae45", lightcolor="#45c55f",
                        darkcolor="#179638", troughcolor="#eeeeee", bordercolor="#999999")
        menu = tk.Menu(root)
        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(label="Hide to tray", command=self.hide_to_tray)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.begin_exit)
        menu.add_cascade(label="File", menu=file_menu)
        root.configure(menu=menu)
        body = ttk.Frame(root, padding=12)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(3, weight=1)
        ttk.Label(body, text="Page URL (web page, localhost:8888 page, or Freenet key):").grid(row=0, column=0, sticky="w")
        source_row = ttk.Frame(body)
        source_row.grid(row=1, column=0, sticky="ew", pady=(5, 10))
        source_row.columnconfigure(0, weight=1)
        self.entry = ttk.Entry(source_row, textvariable=self.source)
        self.entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.entry.bind("<Return>", lambda event: self.find())
        self.find_button = ttk.Button(source_row, text="Find links", command=self.find)
        self.find_button.grid(row=0, column=1)
        ttk.Label(source_row, text="Filename regex:").grid(row=0, column=2, padx=(12, 4))
        self.filter_entry = ttk.Entry(source_row, textvariable=self.filter_text, width=22)
        self.filter_entry.grid(row=0, column=3, sticky="ew")
        self.filter_entry.bind("<KeyRelease>", self.schedule_filter)
        self.filter_entry.bind("<Return>", self.apply_filter_now)
        self.filter_entry.bind("<<Paste>>", self.schedule_filter)
        self.filter_entry.bind("<<Cut>>", self.schedule_filter)
        self.filter_error_label = ttk.Label(body, textvariable=self.filter_error, foreground="#b00020")
        self.filter_error_label.grid(row=2, column=0, sticky="w", pady=(0, 3))
        self.filter_error_label.grid_remove()
        self.table = DownloadList(body, self.table_action, self.show_details,
                                  lambda widths: self.manager_action("set_column_widths", widths),
                                  self.update_selection_buttons, self.delete_selected,
                                  lambda preferences: self.manager_action("set_sort_preferences", preferences))
        self.table.grid(row=3, column=0, sticky="nsew")
        self.canvas = self.table.canvas
        controls = ttk.Frame(body)
        controls.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        self.graph = SpeedGraph(controls)
        self.graph.pack(side="right")
        ttk.Button(controls, text="Select all", command=lambda: self.select_all(True)).pack(side="left")
        ttk.Button(controls, text="Select none", command=lambda: self.select_all(False)).pack(side="left", padx=6)
        self.running_first = tk.BooleanVar(master=root, value=self.manager.sort_preferences["running_first"])
        ttk.Checkbutton(controls, text="Running first", variable=self.running_first,
                        command=self.toggle_running_first).pack(side="left", padx=(8, 0))
        ttk.Label(controls, text="Click a file for its full key and status.").pack(side="left", padx=8)
        details = ttk.Frame(body)
        details.grid(row=5, column=0, sticky="ew", pady=(6, 0))
        details.columnconfigure(1, weight=1)
        ttk.Label(details, text="Selected key:").grid(row=0, column=0, padx=(0, 8))
        ttk.Entry(details, textvariable=self.detail_key, state="readonly").grid(row=0, column=1, sticky="ew")
        ttk.Label(details, textvariable=self.detail_status, wraplength=990).grid(row=1, column=0, columnspan=2, sticky="w", pady=(3, 0))
        actions = ttk.Frame(body)
        actions.grid(row=6, column=0, sticky="ew", pady=(8, 6))
        actions.columnconfigure(0, weight=1)
        primary_actions = ttk.Frame(actions)
        primary_actions.grid(row=0, column=0, sticky="ew")
        secondary_actions = ttk.Frame(actions)
        secondary_actions.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.download_button = ttk.Button(primary_actions, text="Download selected", command=self.download, state="disabled")
        self.download_button.pack(side="left")
        self.pause_all_button = ttk.Button(primary_actions, text="Pause all downloads", command=self.pause_all)
        self.pause_all_button.pack(side="left", padx=8)
        self.cancel_button = ttk.Button(primary_actions, text="Cancel all", command=self.stop, state="disabled")
        self.cancel_button.pack(side="left")
        ttk.Button(secondary_actions, text="Remove unstarted", command=self.remove_unstarted).pack(side="left", padx=(0, 8))
        self.remove_completed_button = ttk.Button(secondary_actions, text="Remove completed",
            command=self.remove_completed, state="disabled")
        self.remove_completed_button.pack(side="left", padx=(0, 8))
        self.delete_button = ttk.Button(secondary_actions, text="Delete selected", command=self.delete_selected, state="disabled")
        self.delete_button.pack(side="left")
        ttk.Button(secondary_actions, text="Open download folder", command=self.open_folder).pack(side="right")
        ttk.Label(body, text=f"Save to: {DOWNLOAD_DIR}", wraplength=990).grid(row=7, column=0, sticky="w", pady=(0, 5))
        ttk.Separator(body).grid(row=8, column=0, sticky="ew", pady=(0, 4))
        footer = ttk.Frame(body, height=22)
        footer.grid(row=9, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status, width=1, anchor="w").grid(row=0, column=0, sticky="ew")
        self.scan_label = ttk.Label(footer, text="Scanning...")
        self.scan_label.grid(row=0, column=1, padx=(8, 6))
        self.scan_progress = ttk.Progressbar(footer, length=130, mode="indeterminate", style="Green.Horizontal.TProgressbar")
        self.scan_progress.grid(row=0, column=2, sticky="e")
        self.scan_label.grid_remove()
        self.scan_progress.grid_remove()
        self.sizegrip = ttk.Sizegrip(footer)
        self.sizegrip.grid(row=0, column=3, padx=(5, 0), sticky="se")
        root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self.entry.focus_set()
        self.table.restore_sort_preferences(self.manager.sort_preferences)
        self.table.set_widths(self.manager.column_widths)
        self.sync_tasks(self.manager.snapshot())
        self.action_thread = threading.Thread(target=action_worker, args=(self.manager, self.actions, self.events), daemon=True)
        self.action_thread.start()
        self.poll_id = root.after(50, self.poll)
        self.tick_id = root.after(1000, self.tick)
        root.bind("<Configure>", self.window_configure, add="+")
        root.bind("<Map>", self.window_map, add="+")
        root.bind("<Unmap>", self.window_unmap, add="+")
        root.after_idle(self.finish_startup_restore)

    def queue_changed(self, tasks):
        self.events.put((None, "tasks", (tasks,)))

    def clamp_window_size(self, width, height):
        try:
            max_width = max(800, self.root.winfo_screenwidth() - 80)
            max_height = max(460, self.root.winfo_screenheight() - 100)
        except tk.TclError:
            max_width, max_height = 1280, 650
        return min(max(800, width), max_width), min(max(460, height), max_height)

    def current_window_state(self):
        try:
            return self.root.state()
        except tk.TclError:
            return "normal"

    def finish_startup_restore(self):
        if self.closed:
            return
        self.startup_restoring = False
        if self.maximized:
            self.begin_window_state_restore("zoomed")

    def schedule_window_state_idle(self, callback):
        callback_id = None

        def run():
            self.window_state_after_ids.discard(callback_id)
            if not self.closed:
                callback()

        callback_id = self.root.after_idle(run)
        self.window_state_after_ids.add(callback_id)
        return callback_id

    def cancel_geometry_capture(self):
        if self.geometry_after_id is not None:
            self.root.after_cancel(self.geometry_after_id)
            self.geometry_after_id = None

    def begin_window_state_restore(self, target):
        if self.closed:
            return
        self.restoring_window_state = True
        self.cancel_geometry_capture()
        self.schedule_window_state_idle(lambda: self.apply_window_state_restore(target))

    def apply_window_state_restore(self, target):
        if self.closed:
            return
        self.root.state(target)
        self.schedule_window_state_idle(lambda: self.finish_window_state_restore(target))

    def finish_window_state_restore(self, target):
        if self.closed:
            return
        self.restoring_window_state = False
        self.set_sizegrip_visible(target != "zoomed")
        self.schedule_geometry_capture()

    def set_sizegrip_visible(self, visible):
        if visible:
            self.sizegrip.grid()
        else:
            self.sizegrip.grid_remove()

    def schedule_geometry_capture(self):
        self.cancel_geometry_capture()
        self.geometry_after_id = self.root.after(250, self.capture_window_preferences)

    def window_configure(self, event):
        if (event.widget is not self.root or self.startup_restoring or self.restoring_window_state
                or self.hidden or self.closed):
            return
        state = self.current_window_state()
        if state == "iconic":
            return
        if state == "zoomed":
            self.maximized = True
            self.last_non_minimized_state = "zoomed"
            self.set_sizegrip_visible(False)
        else:
            self.maximized = False
            self.last_non_minimized_state = "normal"
            self.set_sizegrip_visible(True)
        self.schedule_geometry_capture()

    def window_map(self, event):
        if event.widget is not self.root or self.startup_restoring or self.restoring_window_state:
            return
        self.schedule_window_state_idle(self.apply_tracked_window_state)

    def window_unmap(self, event):
        if event.widget is not self.root or self.startup_restoring:
            return
        if self.current_window_state() != "iconic":
            self.maximized = self.last_non_minimized_state == "zoomed"

    def apply_tracked_window_state(self):
        if self.closed or self.hidden or self.restoring_window_state:
            return
        self.begin_window_state_restore("zoomed" if self.maximized else "normal")

    def capture_window_preferences(self):
        self.geometry_after_id = None
        if self.closed or self.hidden or self.startup_restoring or self.restoring_window_state:
            return
        state = self.current_window_state()
        if state == "iconic":
            return
        if state == "zoomed":
            self.maximized = True
            self.last_non_minimized_state = "zoomed"
        else:
            self.maximized = False
            self.last_non_minimized_state = "normal"
            width, height = self.root.winfo_width(), self.root.winfo_height()
            if width >= 800 and height >= 460:
                self.normal_size = self.clamp_window_size(width, height)
        self.queue_window_preferences()

    def queue_window_preferences(self):
        self.manager_action("set_window_preferences", {
            "width": self.normal_size[0], "height": self.normal_size[1], "maximized": self.maximized})

    def flush_window_preferences(self):
        if not self.startup_restoring and not self.hidden and self.current_window_state() == "normal":
            width, height = self.root.winfo_width(), self.root.winfo_height()
            if width >= 800 and height >= 460:
                self.normal_size = self.clamp_window_size(width, height)
            self.maximized = False
            self.last_non_minimized_state = "normal"
        self.queue_window_preferences()

    def schedule_filter(self, event=None):
        if self.filter_after_id is not None:
            self.root.after_cancel(self.filter_after_id)
        self.filter_after_id = self.root.after(150, self.apply_filter)

    def apply_filter_now(self, event=None):
        if self.filter_after_id is not None:
            self.root.after_cancel(self.filter_after_id)
            self.filter_after_id = None
        self.apply_filter()
        return "break"

    def apply_filter(self):
        self.filter_after_id = None
        error = self.table.configure_filter(self.filter_text.get())
        if error is not None:
            self.filter_error.set("Invalid regex: " + error)
            self.filter_error_label.grid()
            return
        self.filter_error.set("")
        self.filter_error_label.grid_remove()
        self.update_view_status()

    def toggle_running_first(self):
        self.table.set_running_first(self.running_first.get())

    def update_view_status(self):
        total, shown = len(self.rows), len(self.table.order)
        active = sum(task["desired"] == "running" for task in self.rows.values())
        paused = sum(task["desired"] == "paused" for task in self.rows.values())
        done = sum(task["desired"] == "done" for task in self.rows.values())
        if self.table.filter_text and not self.filter_error.get():
            self.status.set(f"Showing {shown} of {total} files.")
        elif not self.busy and not self.status.get().startswith("Error:"):
            self.status.set(f"{total} files: {active} running, {paused} paused, {done} complete. Queue saved automatically.")
        self.update_download_button()

    def update_download_button(self):
        if self.closed or not hasattr(self, "download_button"):
            return
        identifiers = self.table.selected_visible_ids()
        enabled = (not self.exiting and any(identifier in self.rows and
                    self.rows[identifier].get("desired") != "done" for identifier in identifiers))
        try:
            self.download_button.configure(state="normal" if enabled else "disabled")
        except tk.TclError:
            pass

    def update_selection_buttons(self):
        self.update_download_button()
        self.update_delete_button()

    def update_delete_button(self):
        if self.closed or not hasattr(self, "delete_button"):
            return
        enabled = bool(self.table.selected_visible_ids()) and not self.exiting and not self.delete_pending
        try:
            self.delete_button.configure(state="normal" if enabled else "disabled")
        except tk.TclError:
            pass

    def update_remove_completed_button(self):
        if self.closed:
            return
        enabled = any(task.get("desired") == "done" for task in self.rows.values()) and not self.exiting and not self.remove_completed_pending
        try:
            self.remove_completed_button.configure(state="normal" if enabled else "disabled")
        except tk.TclError:
            pass

    def remove_completed(self):
        if self.closed or self.exiting or self.remove_completed_pending:
            return
        if not any(task.get("desired") == "done" for task in self.rows.values()):
            self.update_remove_completed_button()
            return
        self.remove_completed_pending = True
        self.update_remove_completed_button()
        self.actions.put(("remove_completed", (), None, None))

    def delete_selected(self):
        if self.closed or self.exiting or self.delete_pending:
            return
        identifiers = tuple(dict.fromkeys(
            identifier for identifier in self.table.selected_visible_ids() if identifier in self.rows))
        if not identifiers:
            self.update_delete_button()
            return
        self.delete_pending = True
        self.update_delete_button()
        count = len(identifiers)
        body = (f"Remove {count} selected task(s) from the list?\n\n"
                "Active or paused downloads will be cancelled. Completed files will be kept on disk.")
        try:
            confirmed = messagebox.askyesno("Delete selected tasks?", body, parent=self.root,
                                            icon="warning", default="no")
        except Exception:
            self.delete_pending = False
            self.update_delete_button()
            raise
        if not confirmed or self.exiting:
            self.delete_pending = False
            self.update_delete_button()
            return
        self.actions.put(("delete_tasks", (identifiers,), None, None))

    def manager_action(self, name, *args):
        if not self.exiting:
            self.actions.put((name, args, None, None))

    def table_action(self, name, row, *args):
        if row not in self.rows:
            return
        self.manager_action(name, [row])

    def show_details(self, row):
        if row not in self.rows:
            return
        self.selected_row = row
        task = self.rows[row]
        if self.detail_key.get() != task["key"]:
            self.detail_key.set(task["key"])
        details = task["status"]
        if task["size"] is not None:
            details += f" | {task['size']:,} bytes" + ("" if task["size_final"] else " (size estimate)")
        if task["error"]:
            details += " | " + task["error"]
        if self.detail_status.get() != details:
            self.detail_status.set(details)

    def sync_tasks(self, tasks):
        self.rows = {task["id"]: dict(task) for task in tasks if task["id"] not in self.removed_ids}
        self.table.set_tasks(list(self.rows.values()))
        self.update_selection_buttons()
        self.update_remove_completed_button()
        if self.selected_row in self.removed_ids or (self.selected_row is not None and self.selected_row not in self.rows):
            self.selected_row = None
            self.detail_key.set("")
            self.detail_status.set("")
        elif self.selected_row in self.rows:
            self.show_details(self.selected_row)
        elif self.selected_row is None:
            self.detail_key.set("")
            self.detail_status.set("")
        self.update_download_button()
        active = sum(task["desired"] == "running" for task in self.rows.values())
        paused = sum(task["desired"] == "paused" for task in self.rows.values())
        done = sum(task["desired"] == "done" for task in self.rows.values())
        self.pause_all_button.configure(state="normal" if active else "disabled")
        self.cancel_button.configure(state="normal" if active or paused or self.busy else "disabled")
        if tasks and not self.busy and not self.status.get().startswith("Error:"):
            text = f"{len(self.rows)} files: {active} running, {paused} paused, {done} complete. Queue saved automatically."
            if self.table.filter_text and not self.filter_error.get():
                text = f"Showing {len(self.table.order)} of {len(self.rows)} files. {active} running, {paused} paused, {done} complete."
            if self.status.get() != text:
                self.status.set(text)

    def set_busy(self, busy):
        self.busy = busy
        self.find_button.configure(state="disabled" if busy else "normal")
        self.entry.configure(state="disabled" if busy else "normal")
        if busy:
            self.scan_label.grid()
            self.scan_progress.grid()
            self.scan_progress.start(25)
        else:
            self.scan_progress.stop()
            self.scan_label.grid_remove()
            self.scan_progress.grid_remove()

    def find(self):
        if self.busy or self.exiting:
            return
        source = self.source.get().strip()
        if not source:
            self.status.set("Paste a page URL first.")
            return
        self.generation += 1
        generation = self.generation
        self.cancel = threading.Event()
        cancel = self.cancel
        self.set_busy(True)
        self.status.set("Reading page; existing downloads continue...")
        events, actions = self.events, self.actions
        def run():
            try:
                keys = scrape(source, cancel, lambda text: events.put((generation, "scan_status", (text,))))
                actions.put(("scan_add", (keys,), generation, cancel))
            except Cancelled:
                events.put((generation, "scan_cancelled", ()))
            except Exception as exc:
                events.put((generation, "scan_error", (str(exc),)))
        threading.Thread(target=run, daemon=True).start()

    def select_all(self, checked):
        identifiers = list(self.table.order)
        if not identifiers:
            self.status.set("No files match the current filename regex." if self.table.filter_text else "The queue has no files.")
            return
        self.table.select_visible(checked)

    def download(self):
        selected = [row for row in self.table.selected_visible_ids()
                    if row in self.rows and self.rows[row]["desired"] != "done"]
        if not selected:
            self.status.set("No files match the current filename regex." if not self.table.order else
                            "Select at least one visible file that has not already downloaded.")
            return
        self.manager_action("start", selected)

    def start_file(self, row):
        self.table_action("start", row)

    def pause_file(self, row):
        self.table_action("pause", row)

    def cancel_file(self, row):
        self.table_action("cancel", row)

    def pause_all(self):
        self.manager_action("pause", [row for row, task in self.rows.items() if task["desired"] == "running"])

    def stop(self):
        self.cancel.set()
        self.manager_action("cancel", [row for row, task in self.rows.items() if task["desired"] in {"running", "paused"}])

    def remove_unstarted(self):
        self.cancel.set()
        self.generation += 1
        self.set_busy(False)
        self.status.set("Removing unstarted files; started downloads continue...")
        self.actions.put(("remove_unstarted", (), self.generation, None))

    def open_folder(self):
        try:
            DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(DOWNLOAD_DIR)
        except OSError as exc:
            messagebox.showerror("Download folder", str(exc))

    def hide_to_tray(self):
        if self.exiting:
            return
        self.flush_window_preferences()
        try:
            if self.tray is None:
                self.tray = self.tray_factory(self.root, ICON_PATH, lambda event: self.events.put((None, "tray", (event,))))
            self.tray.show()
        except OSError as exc:
            self.status.set("Tray unavailable; minimized to taskbar: " + str(exc))
            self.root.iconify()
            return
        self.hidden = self.table.suspended = True
        self.root.withdraw()

    def restore(self):
        if self.tray:
            self.tray.hide()
        target = "zoomed" if self.maximized else "normal"
        self.restoring_window_state = True
        self.cancel_geometry_capture()
        self.hidden = self.table.suspended = False
        self.root.deiconify()
        self.root.lift()
        self.schedule_window_state_idle(lambda: self.apply_window_state_restore(target))
        self.table.render()
        self.graph.draw()

    def show_tray_menu(self):
        if self.hidden and self.tray and self.tray.visible and getattr(self.tray, "menu_pending", True):
            self.tray.show_menu()

    def begin_exit(self):
        if self.exiting:
            return
        self.flush_window_preferences()
        self.exiting = True
        self.update_selection_buttons()
        self.update_remove_completed_button()
        self.cancel.set()
        self.set_busy(False)
        self.status.set("Exiting; saving queue and pending controls...")
        self.actions.put(("exit", (), None, None))

    def close(self):
        """Synchronous shutdown for embedding and tests; menus use begin_exit."""
        if self.closed:
            return
        self.flush_window_preferences()
        self.exiting = True
        self.update_remove_completed_button()
        self.cancel.set()
        self.actions.put(("stop", (), None, None))
        self.action_thread.join(6)
        if self.action_thread.is_alive():
            raise OSError("Queue actions are still finishing.")
        self.manager.close()
        self.finish_close()

    def finish_close(self):
        self.closed = True
        self.manager.notify = lambda tasks: None
        self.root.after_cancel(self.poll_id)
        self.root.after_cancel(self.tick_id)
        if self.filter_after_id is not None:
            self.root.after_cancel(self.filter_after_id)
            self.filter_after_id = None
        if self.geometry_after_id is not None:
            self.root.after_cancel(self.geometry_after_id)
            self.geometry_after_id = None
        for callback_id in self.window_state_after_ids:
            self.root.after_cancel(callback_id)
        self.window_state_after_ids.clear()
        if self.tray:
            self.tray.close()
            self.tray = None
        self.table.destroy()
        self.root.destroy()
        self.rows.clear()
        # Release Tcl variables on the UI thread, even if worker GC runs later.
        self.source = self.filter_text = self.running_first = self.status = self.filter_error = self.detail_key = self.detail_status = None

    def tick(self):
        self.root.after_cancel(self.tick_id)
        speed = sum(task["speed"] for task in self.rows.values() if task["desired"] == "running") / 1_000_000
        self.graph.sample(speed, draw=not self.hidden)
        if not self.hidden:
            self.table.refresh_metric_order()
        self.tick_id = self.root.after(1000, self.tick)

    def poll(self):
        self.root.after_cancel(self.poll_id)
        # Coalesce progress updates so large transfers cannot starve Tk's event loop.
        latest_tasks = None
        removed_ack = set()
        remove_ack_seen = False
        delete_ack = None
        completed_ack = None
        completed_ack_seen = False
        for _ in range(1000):
            try:
                generation, kind, values = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "closed":
                self.finish_close()
                return
            if kind == "tasks":
                latest_tasks = values[0]
                continue
            if kind == "tray":
                if values[0] == "restore":
                    self.restore()
                elif values[0] == "exit":
                    self.begin_exit()
                else:
                    self.show_tray_menu()
                continue
            if kind == "action_error":
                self.status.set("Error: " + values[0])
                self.exiting = False
                self.update_remove_completed_button()
                continue
            if kind == "delete_error":
                self.delete_pending = False
                self.update_delete_button()
                self.status.set("Error: " + values[0])
                continue
            if kind == "remove_completed_error":
                self.remove_completed_pending = False
                self.update_remove_completed_button()
                self.status.set("Error: " + values[0])
                continue
            if kind == "remove_done":
                remove_ack_seen = True
                removed_ack.update(values[0])
                self.removed_ids.update(values[0])
                continue
            if kind == "delete_done":
                delete_ack = tuple(values[0])
                self.removed_ids.update(delete_ack)
                self.delete_pending = False
                continue
            if kind == "remove_completed_done":
                completed_ack_seen = True
                completed_ack = tuple(values[0])
                self.removed_ids.update(completed_ack)
                continue
            if generation != self.generation:
                continue
            if kind == "scan_status":
                self.status.set("Reading page: " + values[0])
            elif kind in {"scan_done", "scan_error", "scan_cancelled"}:
                self.set_busy(False)
                if kind == "scan_done":
                    self.status.set(f"Found {values[0]} link(s); new files added to the saved queue.")
                elif kind == "scan_error":
                    self.status.set("Error: " + values[0])
                else:
                    self.status.set("Page scan cancelled.")
        if latest_tasks is not None:
            self.sync_tasks(latest_tasks)
        if remove_ack_seen:
            if self.selected_row in removed_ack:
                self.selected_row = None
                self.detail_key.set("")
                self.detail_status.set("")
            self.sync_tasks(list(self.rows.values()))
            self.status.set(f"Removed {len(removed_ack)} unstarted file(s). Started files were kept.")
        if delete_ack is not None:
            if self.selected_row in delete_ack:
                self.selected_row = None
                self.detail_key.set("")
                self.detail_status.set("")
            self.sync_tasks(list(self.rows.values()))
            self.table.clear_selection_ids(delete_ack)
            self.status.set(f"Removed {len(delete_ack)} selected task(s). Completed files were kept on disk.")
            self.update_delete_button()
        if completed_ack_seen:
            self.remove_completed_pending = False
            if self.selected_row in completed_ack:
                self.selected_row = None
                self.detail_key.set("")
                self.detail_status.set("")
            self.table.clear_selection_ids(completed_ack)
            self.sync_tasks(list(self.rows.values()))
            self.status.set(f"Removed {len(completed_ack)} completed task(s). Completed files were kept on disk.")
            self.update_remove_completed_button()
        self.poll_id = self.root.after(50, self.poll)


def main():
    if os.name == "nt":
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("FLD.Downloader")
    root = tk.Tk()
    try:
        App(root)
    except Exception as exc:
        messagebox.showerror("Cannot start downloader", str(exc))
        root.destroy()
    else:
        root.mainloop()


if __name__ == "__main__":
    main()
