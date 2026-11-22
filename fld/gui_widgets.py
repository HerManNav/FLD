"""Small, bounded-cost widgets for the downloader's large task list."""

from collections import deque
import math
import re
import time
import tkinter as tk
from tkinter import font, ttk

from .metrics import clock_text, elapsed_seconds, remaining_seconds


class CanvasValue:
    def __init__(self, value, changed):
        self.value, self.changed = value, changed

    def get(self):
        return self.value

    def set(self, value):
        self.value = value
        self.changed(value)


class DownloadList(ttk.Frame):
    """Virtualized rows, one fixed header, and four shared native controls."""

    HEADINGS = ("ID", "Select", "File", "Done / Size (MB)", "MB/s", "Status", "Start", "Pause", "Cancel", "Progress", "Elapsed (min)", "ETA (min)")
    SORTABLE = {0, 2, 3, 4, 5, 9, 10, 11}
    TEXT_SORT = {2, 5}
    DEFAULT_WIDTHS = (50, 42, 180, 142, 76, 150, 58, 58, 58, 130, 72, 72)
    ROW_HEIGHT, HEADER_HEIGHT = 32, 28

    def __init__(self, parent, action, details, widths_changed=lambda widths: None,
                 selection_changed=lambda: None, delete_selected=lambda: None):
        super().__init__(parent)
        self.text_font = font.nametofont("TkDefaultFont", root=self.winfo_toplevel())
        self.fit_cache = {}
        self.ROW_HEIGHT = max(32, self.text_font.metrics("linespace") + 16)
        self.HEADER_HEIGHT = max(28, self.text_font.metrics("linespace") + 12)
        self.action, self.details, self.widths_changed = action, details, widths_changed
        self.selection_changed, self.delete_selected = selection_changed, delete_selected
        self.selected_ids, self.anchor_id, self.focus_id = set(), None, None
        self.user_widths = None
        self.resizing = self.resize_id = self.render_id = self.scroll_id = None
        self.tasks, self.order, self.order_index, self.pool, self.visible = {}, [], {}, [], {}
        self.filename_widths, self.match_cache = {}, {}
        self.filter_text, self.filter_regex = "", None
        self.sort_column, self.descending, self.running_first = 0, False, False
        self.projection_generation = 0
        self.native_pressed = None
        self.native_press_invalidated = False
        self.suspended = False
        self.wheel_remainder = 0.0
        self.view_x = self.view_y = 0
        self.viewport_key = None
        self.header_key = None
        self.control_row = self.hover_row = None
        self.overlay_active = False
        self.scrolling_until = 0
        self.pressed_cell = None

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.header_frame = ttk.Frame(self, height=self.HEADER_HEIGHT, relief="solid", borderwidth=1)
        self.header_frame.grid(row=0, column=0, sticky="ew")
        self.header_frame.grid_propagate(False)
        self.canvas = tk.Canvas(self, highlightthickness=1, highlightbackground="#aaaaaa", background="white",
                                yscrollincrement=self.ROW_HEIGHT, takefocus=True)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.vertical = ttk.Scrollbar(self, orient="vertical", command=self.yview)
        self.vertical.grid(row=1, column=1, sticky="ns")
        self.horizontal = ttk.Scrollbar(self, orient="horizontal", command=self.xview)
        self.horizontal.grid(row=2, column=0, sticky="ew")
        self.corner = ttk.Frame(self)
        self.corner.grid(row=0, column=1, sticky="nsew")
        self.canvas.configure(yscrollcommand=self.scrolled, xscrollcommand=self.xscrolled)
        self.content = self.canvas
        self.tag = "FLDList" + str(id(self))
        self.bind_class(self.tag, "<MouseWheel>", self.scroll)
        self.bind_class(self.tag, "<Button-4>", lambda event: self.scroll_units(-3))
        self.bind_class(self.tag, "<Button-5>", lambda event: self.scroll_units(3))
        self.headers = [ttk.Label(self.header_frame, text=heading, padding=(5, 4), relief="raised")
                        for heading in self.HEADINGS]
        for widget in (self, self.header_frame, self.canvas, *self.headers):
            self.tag_widget(widget)
        self.checkbox_value = tk.BooleanVar(master=self)
        self.controls = {"checkbox": ttk.Checkbutton(self.canvas, variable=self.checkbox_value, command=self.select_control)}
        self.context_menu = tk.Menu(self, tearoff=False)
        self.context_menu.add_command(label="Delete selected", command=self.delete_selected)
        for name in ("start", "pause", "cancel"):
            self.controls[name + "_button"] = ttk.Button(self.canvas, text=name.title(), width=5,
                                                           command=lambda name=name: self.invoke_control(name))
        for widget in self.controls.values():
            self.tag_widget(widget)
            widget.bind("<Down>", self.keyboard_down)
            widget.bind("<Up>", self.keyboard_up)
            widget.bind("<Delete>", self.keyboard_delete)
            widget.bind("<Button-3>", self.context_press)
            if widget is self.controls["checkbox"]:
                widget.bind("<space>", lambda event: self.toggle_focus())
            widget.bind("<Motion>", self.pointer_motion)
            widget.bind("<Enter>", self.pointer_motion)
            if widget is not self.controls["checkbox"]:
                widget.bind("<ButtonPress-1>", lambda event, widget=widget: self.mark_native_press(widget))
                widget.bind("<ButtonRelease-1>", self.schedule_native_release)
        self.canvas.bind("<Motion>", self.pointer_motion)
        self.canvas.bind("<Leave>", self.defer_hover_clear)
        self.canvas.bind("<Button-1>", self.pointer_press)
        self.canvas.bind("<ButtonRelease-1>", self.pointer_release)
        self.canvas.bind("<Down>", self.keyboard_down)
        self.canvas.bind("<Up>", self.keyboard_up)
        self.canvas.bind("<Delete>", self.keyboard_delete)
        self.canvas.bind("<Button-3>", self.context_press)
        self.canvas.bind("<space>", lambda event: self.toggle_focus())
        self.canvas.bind("<FocusIn>", lambda event: self.activate_row(
            self.control_row if self.control_row in self.visible else next(iter(self.visible), None)))
        for index, header in enumerate(self.headers):
            header.bind("<Motion>", lambda event, index=index: self.header_motion(event, index))
            header.bind("<Button-1>", lambda event, index=index: self.start_resize(event, index))
            header.bind("<B1-Motion>", self.drag_resize)
            header.bind("<ButtonRelease-1>", self.end_resize)
            header.bind("<Double-Button-1>", lambda event, index=index: self.autosize(event, index))
        self.canvas.bind("<Configure>", lambda event: self.schedule_render())
        self.header_frame.bind("<Configure>", lambda event: self.position_viewport(force=True))
        self.root_binding = self.winfo_toplevel().bind("<MouseWheel>", self.route_wheel, add="+")
        self.widths = ()
        self.resize_columns()

    def tag_widget(self, widget):
        if self.tag not in widget.bindtags():
            widget.bindtags((self.tag, *widget.bindtags()))

    def route_wheel(self, event):
        widget = self.winfo_containing(event.x_root, event.y_root)
        while widget is not None:
            if widget == self:
                return self.scroll(event)
            widget = getattr(widget, "master", None)

    def scroll(self, event):
        self.wheel_remainder -= event.delta * 3 / 120
        units = math.trunc(self.wheel_remainder)
        self.wheel_remainder -= units
        if units:
            if event.state & 1:
                self.xview("scroll", units, "units")
            else:
                self.scroll_units(units)
        return "break"

    def begin_scroll(self):
        self.scrolling_until = time.monotonic() + .15
        self.hide_controls()
        self.clear_pressed()
        self.invalidate_native_press()
        if self.scroll_id is not None:
            self.after_cancel(self.scroll_id)
        self.scroll_id = self.after(150, self.finish_scroll)

    def finish_scroll(self):
        self.scroll_id = None
        self.render(refresh=True)
        self.update_hover_from_pointer()
        if self.control_row in self.visible:
            self.activate_row(self.control_row)

    def scroll_units(self, units):
        self.begin_scroll()
        self.canvas.yview_scroll(units, "units")
        self.position_viewport()
        self.schedule_render()
        return "break"

    def yview(self, *args):
        self.begin_scroll()
        self.canvas.yview(*args)
        self.position_viewport()
        self.schedule_render()

    def xview(self, *args):
        self.begin_scroll()
        self.canvas.xview(*args)
        self.position_viewport()
        self.schedule_render()

    def xscrolled(self, first, last):
        self.horizontal.set(first, last)
        self.position_viewport()
        self.schedule_render()

    def scrolled(self, first, last):
        self.vertical.set(first, last)
        self.position_viewport()
        self.schedule_render()

    def schedule_render(self):
        if self.render_id is None and not self.suspended:
            self.render_id = self.after_idle(self.render)

    def heading_text(self, index):
        if index == self.sort_column:
            return self.HEADINGS[index] + (" ▼" if self.descending else " ▲")
        return self.HEADINGS[index]

    def resize_columns(self, measure=True):
        if measure:
            self.filename_widths = {task["filename"]: self.text_font.measure(task["filename"]) + 12
                                    for task in self.tasks.values()}
            widths = list(self.DEFAULT_WIDTHS)
            widths[2] = min(280, max([60, *self.filename_widths.values()]))
            widths[0] = max(50, self.text_font.measure(self.HEADINGS[0]) + 12)
            self.natural_widths = tuple(max(width, self.text_font.measure(self.heading_text(index)) +
                                            (24 if index in (6, 7, 8) else 12))
                                        for index, width in enumerate(widths))
        self.widths = tuple(self.user_widths or self.natural_widths)
        self.update_scrollregion()
        self.position_viewport(force=True)

    def update_scrollregion(self):
        height = max(1, self.canvas.winfo_height(), len(self.order) * self.ROW_HEIGHT)
        self.canvas.configure(scrollregion=(0, 0, sum(self.widths), height))

    def move_to_y(self, y):
        region_height = max(self.canvas.winfo_height(), len(self.order) * self.ROW_HEIGHT, 1)
        maximum = max(0, region_height - self.canvas.winfo_height())
        y = min(maximum, max(0, y))
        self.canvas.yview_moveto(y / max(1, maximum))

    def position_viewport(self, force=False):
        if not self.widths:
            return
        x, y = self.canvas.canvasx(0), self.canvas.canvasy(0)
        width, height = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        key = x, y, width, height, self.widths
        if key == self.viewport_key and not force:
            return
        self.viewport_key = key
        self.view_x, self.view_y = x, y
        header_key = x, self.widths, tuple(self.heading_text(i) for i in range(len(self.HEADINGS)))
        if header_key != self.header_key:
            left = -x
            for index, (header, column_width) in enumerate(zip(self.headers, self.widths)):
                header.configure(text=header_key[2][index])
                header.place(x=left, y=0, width=column_width, height=self.HEADER_HEIGHT)
                left += column_width
            self.header_key = header_key

    def set_widths(self, widths):
        if widths is not None and len(widths) == len(self.HEADINGS):
            self.user_widths = [max(40, min(2000, int(width))) for width in widths]
            self.resize_columns(measure=False)
            self.render()

    def divider(self, event, index):
        if event.x <= 5 and index:
            return index - 1
        if event.x >= self.headers[index].winfo_width() - 6:
            return index
        return None

    def header_motion(self, event, index):
        self.headers[index].configure(cursor="sb_h_double_arrow" if self.divider(event, index) is not None else "arrow")

    def start_resize(self, event, index):
        column = self.divider(event, index)
        if column is not None:
            self.resizing = column, event.x_root, self.widths[column]
            self.user_widths = list(self.widths)
            self.header_press = ("resize", column)
        else:
            self.header_press = ("sort", index, event.x_root, event.y_root)
            self.header_dragged = False
        return "break"

    def drag_resize(self, event):
        if self.resizing is not None:
            column, x, width = self.resizing
            self.user_widths[column] = max(40, min(2000, width + event.x_root - x))
            if self.resize_id is None:
                self.resize_id = self.after(33, self.apply_resize)
            return "break"
        press = getattr(self, "header_press", None)
        if press and press[0] == "sort" and (abs(event.x_root - press[2]) > 4 or abs(event.y_root - press[3]) > 4):
            self.header_dragged = True

    def end_resize(self, event=None):
        if self.resizing is not None:
            self.apply_resize()
            self.resizing = None
            self.widths_changed(list(self.widths))
            self.header_press = None
            return "break"
        press = getattr(self, "header_press", None)
        self.header_press = None
        header = self.headers[press[1]] if press and press[0] == "sort" else None
        inside_header = (header is not None and event is not None and
                         header.winfo_rootx() <= event.x_root < header.winfo_rootx() + header.winfo_width() and
                         header.winfo_rooty() <= event.y_root < header.winfo_rooty() + header.winfo_height())
        if (press and press[0] == "sort" and inside_header and not getattr(self, "header_dragged", False)
                and self.divider(event, press[1]) is None):
            self.sort_by(press[1])
        return "break"

    def apply_resize(self):
        if self.resize_id is not None:
            self.after_cancel(self.resize_id)
            self.resize_id = None
        old_y = self.canvas.canvasy(0)
        self.resize_columns(measure=False)
        self.move_to_y(old_y)
        self.render()

    def autosize(self, event, index):
        column = self.divider(event, index)
        if column is not None:
            self.user_widths = list(self.widths)
            self.user_widths[column] = self.natural_widths[column]
            self.resizing = None
            self.resize_columns(measure=False)
            self.render()
            self.widths_changed(list(self.widths))
            return "break"

    def sort_by(self, column):
        if column not in self.SORTABLE:
            return
        if column == self.sort_column:
            self.descending = not self.descending
        else:
            self.sort_column = column
            self.descending = column not in self.TEXT_SORT and column not in (0, 11)
        self.rebuild_projection(force_top=True)

    def configure_filter(self, pattern):
        try:
            compiled = re.compile(pattern, re.IGNORECASE) if pattern else None
        except re.error as exc:
            return str(exc)
        self.filter_text, self.filter_regex = pattern, compiled
        self.rebuild_projection(force_top=True)
        return None

    def set_running_first(self, enabled):
        enabled = bool(enabled)
        if enabled == self.running_first:
            return
        self.running_first = enabled
        if enabled:
            self.sort_column, self.descending = 9, True
        self.rebuild_projection(force_top=True)

    def sort_value(self, task, now):
        column = self.sort_column
        if column == 0:
            return task.get("display_id", 0)
        if column == 2:
            return task["filename"].casefold()
        if column == 3:
            return (task.get("size"), task.get("fetched", 0))
        if column == 4:
            return task.get("speed", 0) if task.get("desired") == "running" else 0
        if column == 5:
            return re.sub(r"\s+\([0-9.]+%\)$", "", task.get("status", "")).casefold()
        if column == 9:
            return min(1.0, max(0.0, task.get("fraction", 0)))
        if column == 10:
            return elapsed_seconds(task, now) if task.get("timer_known", True) else None
        if column == 11:
            return remaining_seconds(task)
        return task.get("display_id", 0)

    def rebuild_projection(self, force_top=False, preserve=True):
        old_order = self.order
        old_y = self.canvas.canvasy(0)
        old_offset = old_y % self.ROW_HEIGHT
        old_first = old_order[min(len(old_order) - 1, max(0, int(old_y // self.ROW_HEIGHT)))] if old_order else None
        old_control = self.control_row
        now = time.time()
        visible = []
        for task in self.tasks.values():
            filename = task["filename"]
            if self.filter_regex is not None:
                key = self.filter_text, filename
                matched = self.match_cache.get(key)
                if matched is None:
                    matched = bool(self.filter_regex.search(filename))
                    self.match_cache[key] = matched
                if not matched:
                    continue
            visible.append(task)
        known, unknown = [], []
        for task in visible:
            value = self.sort_value(task, now)
            is_unknown = value is None or (self.sort_column == 3 and value[0] is None)
            (unknown if is_unknown else known).append((task, value))
        known.sort(key=lambda pair: pair[0].get("display_id", 0))
        known.sort(key=lambda pair: pair[1], reverse=self.descending)
        unknown.sort(key=lambda pair: pair[0].get("display_id", 0))
        ordered = known + unknown
        if self.running_first:
            ordered.sort(key=lambda pair: pair[0].get("desired") != "running")
        candidate = [pair[0]["id"] for pair in ordered]
        if candidate == old_order and not force_top:
            self.render(refresh=True)
            self.selection_changed()
            return
        self.hide_controls()
        self.clear_pressed()
        self.invalidate_native_press()
        self.projection_generation += 1
        self.order = candidate
        self.order_index = {identifier: index for index, identifier in enumerate(self.order)}
        self.selection_changed()
        self.update_scrollregion()
        if force_top:
            self.canvas.yview_moveto(0)
        elif preserve and old_first in self.order_index:
            y = self.order_index[old_first] * self.ROW_HEIGHT + old_offset
            self.move_to_y(y)
        else:
            self.move_to_y(old_y)
        if not self.order:
            self.canvas.yview_moveto(0)
            self.hover_row = None
        if self.control_row not in self.order_index:
            self.control_row = None
        if self.hover_row not in self.order_index:
            self.set_hover(None)
        self.viewport_key = None
        self.position_viewport(force=True)
        self.render(refresh=True)
        if time.monotonic() >= self.scrolling_until:
            pointer_row = self.update_hover_from_pointer()
            if pointer_row is None and self.focus_is_control() and old_control in self.visible:
                self.activate_row(old_control)

    def set_tasks(self, tasks):
        previous = self.tasks
        updated = {task["id"]: dict(task) for task in tasks}
        membership_changed = set(previous) != set(updated)
        names_changed = any(previous.get(identifier, {}).get("filename") != task["filename"]
                            for identifier, task in updated.items())
        state_changed = any(previous.get(identifier, {}).get("desired") != task.get("desired")
                            for identifier, task in updated.items())
        selected_changed = any(previous.get(identifier, {}).get("selected") != task.get("selected")
                               for identifier, task in updated.items())
        self.tasks = updated
        old_selection = set(self.selected_ids)
        self.selected_ids.intersection_update(updated)
        if self.anchor_id not in updated:
            self.anchor_id = None
        if self.focus_id not in updated:
            self.focus_id = None
        if old_selection != self.selected_ids:
            self.selection_changed()
        if membership_changed or names_changed:
            live_names = {task["filename"] for task in updated.values()}
            self.match_cache = {key: value for key, value in self.match_cache.items() if key[1] in live_names}
            self.resize_columns(measure=True)
            self.rebuild_projection(preserve=True)
        elif state_changed:
            self.rebuild_projection(preserve=True)
        elif selected_changed:
            self.render(refresh=True)
        else:
            self.render()

    def refresh_metric_order(self):
        if self.sort_column in {3, 4, 9, 10, 11} or self.running_first:
            self.rebuild_projection(preserve=True)
        else:
            self.render(refresh=True)

    def make_slot(self):
        slot = {"id": None, "cache": {}, "shown": False, "widgets": []}
        tag = "row" + str(len(self.pool))
        slot["background"] = self.canvas.create_rectangle(0, 0, 0, 0, fill="white", outline="#eeeeee", tags=tag)
        slot["check_box"] = self.canvas.create_rectangle(0, 0, 0, 0, fill="white", outline="#888888", tags=tag)
        slot["check_tick"] = self.canvas.create_line(0, 0, 0, 0, 0, 0, fill="white", width=2, tags=tag)
        slot["checked"] = CanvasValue(False, lambda value: self.paint_check(slot, value))
        for name in ("display_id", "filename", "size", "speed", "status", "elapsed", "eta"):
            slot[name + "_item"] = self.canvas.create_text(0, 0, anchor="w", font=self.text_font,
                                                            fill="#202020", tags=tag)
            slot[name] = CanvasValue("", lambda value, name=name: self.paint_text(slot, name, value))
        for name in ("start", "pause", "cancel"):
            slot[name + "_box"] = self.canvas.create_rectangle(0, 0, 0, 0, fill="#f0f0f0", outline="#b0b0b0", tags=tag)
            slot[name + "_text"] = self.canvas.create_text(0, 0, text=name.title(), font=self.text_font, tags=tag)
        slot.update(self.controls)
        slot["track"] = self.canvas.create_rectangle(0, 0, 0, 0, fill="#eeeeee", outline="#999999", tags=tag)
        slot["bar"] = self.canvas.create_rectangle(0, 0, 0, 0, fill="#21ae45", outline="", tags=tag)
        slot["progress"] = CanvasValue(0, lambda value: self.paint_progress(slot, value))
        slot["graphics"] = [slot["background"], slot["track"], slot["bar"], slot["check_box"], slot["check_tick"],
                            *[slot[name + suffix] for name in ("start", "pause", "cancel") for suffix in ("_box", "_text")],
                            *[slot[name + "_item"] for name in ("display_id", "filename", "size", "speed", "status", "elapsed", "eta")]]
        self.pool.append(slot)
        return slot

    def fit_text(self, text, width):
        text = str(text)
        key = text, width
        if key in self.fit_cache:
            return self.fit_cache[key]
        result = text
        if self.text_font.measure(text) > width:
            left, right = 0, len(text)
            while left < right:
                middle = (left + right + 1) // 2
                if self.text_font.measure(text[:middle] + "…") <= width:
                    left = middle
                else:
                    right = middle - 1
            result = text[:left] + "…"
        if len(self.fit_cache) > 2048:
            self.fit_cache.clear()
        self.fit_cache[key] = result
        return result

    def paint_text(self, slot, name, value):
        width = slot.get("text_widths", {}).get(name, 100)
        self.canvas.itemconfigure(slot[name + "_item"], text=self.fit_text(str(value), width))

    def paint_progress(self, slot, value):
        if "progress_area" in slot:
            left, top, right, bottom = slot["progress_area"]
            filled = (right - left - 2) * min(1, max(0, value / 100))
            self.canvas.coords(slot["bar"], left + 1, top + 1, left + 1 + filled, bottom - 1)

    def paint_check(self, slot, checked):
        self.canvas.itemconfigure(slot["check_box"], fill="#0067c0" if checked else "white")
        self.canvas.itemconfigure(slot["check_tick"], state="normal" if checked and slot["shown"] else "hidden")

    def position_slot(self, slot, index):
        y = index * self.ROW_HEIGHT
        xs, total = [], 0
        for width in self.widths:
            xs.append(total)
            total += width
        self.canvas.coords(slot["background"], 0, y, total, y + self.ROW_HEIGHT)
        self.canvas.itemconfigure(slot["background"], fill=self.row_background(slot["id"], index))
        cx, cy = xs[1] + self.widths[1] / 2, y + self.ROW_HEIGHT / 2
        self.canvas.coords(slot["check_box"], cx - 7, cy - 7, cx + 7, cy + 7)
        self.canvas.coords(slot["check_tick"], cx - 4, cy, cx - 1, cy + 3, cx + 4, cy - 3)
        for name, column in (("start", 6), ("pause", 7), ("cancel", 8)):
            self.canvas.coords(slot[name + "_box"], xs[column] + 4, y + 3, xs[column] + self.widths[column] - 4, y + self.ROW_HEIGHT - 3)
            self.canvas.coords(slot[name + "_text"], xs[column] + self.widths[column] / 2, cy)
        slot["text_widths"] = {}
        for name, column in (("display_id", 0), ("filename", 2), ("size", 3), ("speed", 4),
                             ("status", 5), ("elapsed", 10), ("eta", 11)):
            slot["text_widths"][name] = self.widths[column] - 10
            self.canvas.coords(slot[name + "_item"], xs[column] + 5, cy)
            self.paint_text(slot, name, slot[name].get())
        slot["progress_area"] = xs[9] + 5, y + 8, xs[9] + self.widths[9] - 5, y + self.ROW_HEIGHT - 8
        self.canvas.coords(slot["track"], *slot["progress_area"])
        self.paint_progress(slot, slot["progress"].get())
        if not slot["shown"]:
            for item in slot["graphics"]:
                self.canvas.itemconfigure(item, state="normal")
            slot["shown"] = True
            self.paint_check(slot, slot["checked"].get())

    def select_control(self):
        slot = self.visible.get(self.control_row)
        if slot is None or slot["id"] != self.control_row or self.control_row not in self.order_index:
            return
        checked = self.checkbox_value.get()
        slot["checked"].set(checked)
        slot["cache"]["checked"] = checked
        self.action("select", slot["id"], checked)

    def invoke_control(self, name):
        row = self.control_row
        slot = self.visible.get(row)
        if self.native_pressed is not None:
            pressed_name, pressed_row, generation = self.native_pressed
            invalid = (self.native_press_invalidated or generation != self.projection_generation or
                       pressed_name != name or pressed_row != row)
            self.native_pressed = None
            self.native_press_invalidated = False
            if invalid:
                return
        if not self.overlay_active or slot is None or slot["id"] != row or row not in self.order_index:
            return
        self.action(name, row)

    def mark_native_press(self, widget):
        name = next((key.removesuffix("_button") for key, value in self.controls.items() if value is widget), None)
        if name is not None:
            self.native_pressed = name, self.control_row, self.projection_generation
            self.native_press_invalidated = False

    def schedule_native_release(self, event=None):
        self.after_idle(self.clear_native_press)

    def invalidate_native_press(self):
        if self.native_pressed is not None:
            self.native_press_invalidated = True
        for widget in self.controls.values():
            try:
                widget.state(["!pressed"])
            except tk.TclError:
                pass

    def clear_native_press(self):
        if self.native_pressed is not None:
            self.native_pressed = None
            self.native_press_invalidated = False

    def focus_is_control(self):
        focus = self.focus_get()
        return focus is self.canvas or focus in self.controls.values()

    def hide_controls(self):
        if self.overlay_active:
            for widget in self.controls.values():
                widget.place_forget()
            self.overlay_active = False

    def activate_row(self, row):
        if row not in self.visible or row not in self.order_index:
            self.hide_controls()
            return
        self.control_row, self.overlay_active = row, True
        task = self.tasks[row]
        index = self.order_index[row]
        y, x = index * self.ROW_HEIGHT - self.view_y, -self.view_x
        xs = []
        for width in self.widths:
            xs.append(x)
            x += width
        for name, column in (("checkbox", 1), ("start_button", 6), ("pause_button", 7), ("cancel_button", 8)):
            position = ((xs[column] + (self.widths[column] - 22) / 2, y + 3, 22, self.ROW_HEIGHT - 6)
                        if name == "checkbox" else (xs[column] + 4, y + 3, self.widths[column] - 8, self.ROW_HEIGHT - 6))
            if getattr(self.controls[name], "_fld_position", None) != position or not self.controls[name].place_info():
                self.controls[name].place(x=position[0], y=position[1], width=position[2], height=position[3])
                self.controls[name]._fld_position = position
        if self.checkbox_value.get() != task["selected"]:
            self.checkbox_value.set(task["selected"])
        for name, enabled in (("start", task["desired"] not in {"running", "done"}),
                              ("pause", task["desired"] == "running"),
                              ("cancel", task["desired"] in {"running", "paused", "failed"})):
            button = self.controls[name + "_button"]
            state = "normal" if enabled else "disabled"
            if str(button["state"]) != state:
                button.configure(state=state)

    def hit(self, event):
        index = int((event.y + self.view_y) // self.ROW_HEIGHT)
        if not 0 <= index < len(self.order):
            return None, None
        x = event.x + self.view_x
        for column, width in enumerate(self.widths):
            if 0 <= x < width:
                return self.order[index], column
            x -= width
        return self.order[index], None

    def pointer_motion(self, event):
        self.update_hover_from_pointer(event.x_root, event.y_root)

    def update_hover_from_pointer(self, x_root=None, y_root=None):
        if x_root is None:
            x_root, y_root = self.winfo_pointerxy()
        widget = self.winfo_containing(x_root, y_root)
        inside = False
        while widget is not None and widget != self:
            if widget in self.headers or widget in (self.header_frame, self.vertical, self.horizontal):
                self.set_hover(None)
                return None
            widget = getattr(widget, "master", None)
        if widget == self:
            inside = True
        if not inside:
            self.set_hover(None)
            return None
        x = x_root - self.canvas.winfo_rootx()
        y = y_root - self.canvas.winfo_rooty()
        if not (0 <= x < self.canvas.winfo_width() and 0 <= y < self.canvas.winfo_height()):
            self.set_hover(None)
            return None
        row, _ = self.hit(type("Pointer", (), {"x": x, "y": y})())
        self.set_hover(row)
        if time.monotonic() >= self.scrolling_until and row in self.visible:
            self.activate_row(row)
        return row

    def set_hover(self, row):
        if row == self.hover_row:
            return
        previous_id = self.hover_row
        previous = self.visible.get(previous_id)
        self.hover_row = row if row in self.order_index else None
        if previous is not None:
            index = self.order_index.get(previous_id, 0)
            self.canvas.itemconfigure(previous["background"], fill=self.row_background(previous_id, index))
        current = self.visible.get(self.hover_row)
        if current is not None:
            index = self.order_index.get(self.hover_row, 0)
            self.canvas.itemconfigure(current["background"], fill=self.row_background(self.hover_row, index))

    def row_background(self, identifier, index):
        selected, hovered = identifier in self.selected_ids, identifier == self.hover_row
        if selected and hovered:
            return "#b8d8f5"
        if selected:
            return "#c8e1f7"
        if hovered:
            return "#dceeff"
        return "#f7f8f9" if index % 2 else "white"

    def selected_visible_ids(self):
        return tuple(identifier for identifier in self.order if identifier in self.selected_ids)

    def clear_selection_ids(self, identifiers=()):
        removed = set(identifiers)
        before = set(self.selected_ids)
        self.selected_ids.difference_update(removed)
        if self.anchor_id in removed:
            self.anchor_id = None
        if self.focus_id in removed:
            self.focus_id = None
        self.paint_selection_change(before | removed)

    def paint_selection_change(self, affected):
        for identifier in affected:
            slot = self.visible.get(identifier)
            if slot is not None:
                self.canvas.itemconfigure(slot["background"],
                    fill=self.row_background(identifier, self.order_index[identifier]))
        self.selection_changed()

    def set_row_selection(self, selected, anchor=None, focus=None):
        before = set(self.selected_ids)
        self.selected_ids = set(selected)
        self.anchor_id, self.focus_id = anchor, focus
        self.paint_selection_change(before | self.selected_ids)

    def select_gesture(self, identifier, control=False, shift=False):
        if identifier not in self.order_index:
            return
        if shift:
            anchor = self.anchor_id if self.anchor_id in self.order_index else None
            if anchor is None:
                anchor = self.focus_id if self.focus_id in self.order_index else identifier
                self.anchor_id = anchor
            left, right = sorted((self.order_index[anchor], self.order_index[identifier]))
            selected = set(self.order[left:right + 1])
            if control:
                selected.update(self.selected_ids)
            self.set_row_selection(selected, self.anchor_id, identifier)
        elif control:
            selected = set(self.selected_ids)
            if identifier in selected:
                selected.remove(identifier)
            else:
                selected.add(identifier)
            self.set_row_selection(selected, identifier, identifier)
        else:
            self.set_row_selection({identifier}, identifier, identifier)

    def keyboard_down(self, event):
        return self.move_focus(1, event)

    def keyboard_up(self, event):
        return self.move_focus(-1, event)

    def keyboard_delete(self, event):
        self.delete_selected()
        return "break"

    def defer_hover_clear(self, event=None):
        if getattr(self, "hover_clear_id", None) is not None:
            self.after_cancel(self.hover_clear_id)
        self.hover_clear_id = self.after_idle(self.finish_hover_clear)

    def finish_hover_clear(self):
        self.hover_clear_id = None
        self.update_hover_from_pointer()

    def clear_pressed(self):
        self.pressed_cell = None

    def pointer_press(self, event):
        row, column = self.hit(event)
        state = getattr(event, "state", 0)
        self.pressed_cell = (row, column, self.projection_generation, bool(state & 4), bool(state & 1))
        if row is not None:
            self.activate_row(row)
            self.details(row)
            self.canvas.focus_set()

    def pointer_release(self, event):
        hit = self.hit(event)
        pressed = self.pressed_cell
        if (pressed and pressed[:2] == hit and pressed[2] == self.projection_generation):
            row, column, _, control, shift = pressed
            if row is None:
                if not control and not shift:
                    self.set_row_selection(set(), None, None)
            else:
                self.activate_row(row)
                name = {1: "checkbox", 6: "start_button", 7: "pause_button", 8: "cancel_button"}.get(column)
                if name:
                    self.controls[name].invoke()
                else:
                    self.select_gesture(row, control, shift)
        self.pressed_cell = None

    def context_press(self, event):
        x = event.x_root - self.canvas.winfo_rootx()
        y = event.y_root - self.canvas.winfo_rooty()
        row, _ = self.hit(type("Pointer", (), {"x": x, "y": y})())
        if row is None:
            return "break"
        if row not in self.selected_ids:
            self.set_row_selection({row}, row, row)
        else:
            self.focus_id = row
            self.selection_changed()
        self.activate_row(row)
        self.details(row)
        self.canvas.focus_set()
        try:
            self.context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                self.context_menu.grab_release()
            except tk.TclError:
                pass
        return "break"

    def move_focus(self, delta, event=None):
        if not self.order:
            return "break"
        if self.focus_id in self.order_index:
            index = max(0, min(len(self.order) - 1, self.order_index[self.focus_id] + delta))
        elif event is None and self.control_row in self.order_index:
            index = max(0, min(len(self.order) - 1, self.order_index[self.control_row] + delta))
        else:
            index = 0 if delta > 0 else len(self.order) - 1
        y = index * self.ROW_HEIGHT
        if y < self.view_y or y + self.ROW_HEIGHT > self.view_y + self.canvas.winfo_height():
            self.yview("moveto", y / max(1, len(self.order) * self.ROW_HEIGHT))
        self.render()
        identifier = self.order[index]
        self.activate_row(identifier)
        self.details(identifier)
        control = bool(event and event.state & 4)
        shift = bool(event and event.state & 1)
        if shift:
            anchor = self.anchor_id if self.anchor_id in self.order_index else None
            if anchor is None:
                anchor = self.focus_id if self.focus_id in self.order_index else identifier
                self.anchor_id = anchor
            left, right = sorted((self.order_index[anchor], index))
            selected = set(self.order[left:right + 1])
            if control:
                selected.update(self.selected_ids)
            self.set_row_selection(selected, self.anchor_id, identifier)
        elif not control:
            self.set_row_selection({identifier}, identifier, identifier)
        else:
            self.focus_id = identifier
            self.selection_changed()
        return "break"

    def toggle_focus(self):
        row = self.focus_id if self.focus_id in self.visible else self.control_row
        if row in self.visible:
            self.activate_row(row)
            self.controls["checkbox"].invoke()
        return "break"

    def update_slot(self, slot, task, now):
        active, done = task["desired"] == "running", task["desired"] == "done"
        total = task["size"]
        size = "Unknown" if total is None else ("" if done else "~") + f"{task['fetched']/1_000_000:.2f} / " + ("" if task["size_final"] else "~") + f"{total/1_000_000:.2f}"
        values = dict(checked=task["selected"], display_id=task.get("display_id", ""), filename=task["filename"], size=size,
                      speed=f"~{task['speed']/1_000_000:.2f}" if active else "0.00", progress=100*task["fraction"],
                      status=task["status"] + (f" ({100*task['fraction']:.1f}%)" if active else ""),
                      elapsed=clock_text(elapsed_seconds(task, now)) if task.get("timer_known", True) else "--:--",
                      eta=clock_text(remaining_seconds(task)))
        for name, value in values.items():
            if slot["cache"].get(name) != value:
                slot[name].set(value)
                slot["cache"][name] = value
        for name, enabled in (("start", not active and not done), ("pause", active),
                              ("cancel", task["desired"] in {"running", "paused", "failed"})):
            state = "normal" if enabled else "disabled"
            if slot["cache"].get(name) != state:
                self.canvas.itemconfigure(slot[name + "_text"], fill="#202020" if enabled else "#999999")
                slot["cache"][name] = state

    def render(self, refresh=False):
        if self.render_id is not None:
            self.after_cancel(self.render_id)
            self.render_id = None
        if self.suspended:
            return
        self.position_viewport()
        first = max(0, int(self.canvas.canvasy(0) // self.ROW_HEIGHT) - 1)
        offset = self.canvas.canvasy(0) % self.ROW_HEIGHT
        count = math.ceil((max(1, self.canvas.winfo_height()) + offset) / self.ROW_HEIGHT) + 2
        indices = range(first, min(len(self.order), first + count))
        previous_visible = {slot["id"]: slot for slot in self.pool if slot["id"] is not None}
        self.visible = {}
        now = time.time()
        wanted = {self.order[index] for index in indices}
        available = [slot for slot in self.pool if slot["id"] not in wanted]
        for index in indices:
            identifier = self.order[index]
            slot = previous_visible.get(identifier)
            if slot is None:
                slot = available.pop() if available else self.make_slot()
                slot["id"] = identifier
                slot["cache"].clear()
                slot["position"] = None
            if slot.get("position") != (index, self.widths):
                self.position_slot(slot, index)
                slot["position"] = (index, self.widths)
            if refresh or not slot["cache"]:
                self.update_slot(slot, self.tasks[identifier], now)
            self.visible[identifier] = slot
        for slot in self.pool:
            if slot["id"] in self.visible:
                continue
            if slot["shown"]:
                for item in slot["graphics"]:
                    self.canvas.itemconfigure(item, state="hidden")
                slot["shown"] = False
            slot["id"] = None
            slot["position"] = None
        if self.overlay_active:
            if self.control_row in self.visible:
                self.activate_row(self.control_row)
            else:
                self.hide_controls()

    def destroy(self):
        for identifier in (self.resize_id, self.render_id, self.scroll_id, getattr(self, "hover_clear_id", None)):
            if identifier is not None:
                self.after_cancel(identifier)
        self.winfo_toplevel().unbind("<MouseWheel>", self.root_binding)
        try:
            self.context_menu.destroy()
        except tk.TclError:
            pass
        self.unbind_class(self.tag, "<MouseWheel>")
        self.unbind_class(self.tag, "<Button-4>")
        self.unbind_class(self.tag, "<Button-5>")
        super().destroy()
        self.visible.clear()
        self.pool.clear()


class SpeedGraph(tk.Canvas):
    def __init__(self, parent):
        super().__init__(parent, width=280, height=58, background="#f8fafb", highlightthickness=1, highlightbackground="#bbc6ce")
        self.samples = deque(maxlen=121)
        self.axis_max = .01
        self.bind("<Configure>", lambda event: self.draw())

    def sample(self, speed, now=None, draw=True):
        now = time.monotonic() if now is None else now
        self.samples.append((now, max(0, speed)))
        while self.samples and self.samples[0][0] < now - 120:
            self.samples.popleft()
        if draw:
            self.draw()

    def draw(self):
        self.delete("all")
        width, height = max(100, self.winfo_width()), max(50, self.winfo_height())
        speed = self.samples[-1][1] if self.samples else 0
        peak = max((value for _, value in self.samples), default=0)
        magnitude = 10 ** math.floor(math.log10(max(.01, peak)))
        self.axis_max = next(step for step in (1, 2, 5, 10) if step * magnitude >= max(.01, peak)) * magnitude
        self.create_text(4, 2, anchor="nw", text="MB/s", fill="#617381", font=("Segoe UI", 7))
        self.create_text(44, 2, anchor="nw", text=f"Total ~{speed:.2f} MB/s  |  2 min", fill="#234760", font=("Segoe UI", 8))
        left, top, right, bottom = 44, 20, width - 7, height - 13
        decimals = max(1, min(3, 1 - math.floor(math.log10(self.axis_max))))
        for fraction in (0, .5, 1):
            y = bottom - fraction * (bottom - top)
            self.create_line(left, y, right, y, fill="#dce3e8")
            self.create_text(left - 5, y, anchor="e", text=f"{self.axis_max*fraction:.{decimals}f}",
                             font=("Segoe UI", 7), fill="#617381", tags="y_tick")
        self.create_line(left, top, left, bottom, fill="#a8b7c3")
        self.create_text(left, height - 1, anchor="sw", text="-2:00", font=("Segoe UI", 7), fill="#617381")
        self.create_text(right, height - 1, anchor="se", text="now", font=("Segoe UI", 7), fill="#617381")
        if len(self.samples) > 1:
            now, maximum = self.samples[-1][0], self.axis_max
            points = [(left + (stamp - now + 120) / 120 * (right - left), bottom - value / maximum * (bottom - top))
                      for stamp, value in self.samples]
            self.create_line(*[coordinate for point in points for coordinate in point], fill="#188747", width=2)
