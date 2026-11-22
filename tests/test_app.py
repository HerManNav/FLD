"""Exercise native Tk controls with a persistent local FCP node."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import gc
import os
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from fld.gui_widgets import elapsed_seconds, remaining_seconds, clock_text

from fld.app import App
from fake_node import PAYLOAD, PersistentNode
from fld.queue_manager import DownloadManager
from test_freenet import KEY


class AppTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.folder = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.node = PersistentNode()
        threading.Thread(target=self.node.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.addCleanup(self.node.server_close)
        self.addCleanup(self.node.shutdown)
        self.addCleanup(self.node.drop_connections)
        self.open_app()
        self.addCleanup(self.close_app)

    def close_app(self):
        self.app.close()
        self.app = self.root = None
        # Multiple Tk sessions in one test process must be collected on this thread.
        gc.collect()

    def open_app(self):
        self.manager = DownloadManager(self.folder / "queue.sqlite3", self.folder / "downloads",
            port=self.node.server_address[1], reconnect_delay=0.02,
            web_base=f"http://127.0.0.1:{self.node.web.server_port}")
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = App(self.root, self.manager)

    def wait(self, condition, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.root.update()
            if condition():
                return
            time.sleep(0.01)
        self.fail("GUI condition did not become true")

    def widgets(self, row):
        self.app.table.activate_row(row)
        return self.app.table.visible[row]

    def add_keys(self, keys):
        self.manager.add(keys)
        self.app.sync_tasks(self.manager.snapshot())

    def test_paste_scrape_highlight_download_and_remove_unstarted(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                data = f"<p>{KEY}/first file with spaces.mp4</p><p>{KEY}/second file.zip</p>".encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=http.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.addCleanup(http.server_close)
        self.addCleanup(http.shutdown)
        self.app.source.set(f"http://127.0.0.1:{http.server_port}/")
        self.app.find_button.invoke()
        self.wait(lambda: not self.app.busy and len(self.app.rows) == 2)
        first, second = self.app.rows
        self.assertNotIn("Select", self.app.table.HEADINGS)
        self.app.table.select_gesture(first)
        self.app.download_button.invoke()
        self.wait(lambda: first in self.node.jobs and self.app.rows[first]["desired"] == "running")
        self.assertNotIn(second, self.node.jobs)
        self.node.complete(first)
        self.wait(lambda: self.app.rows[first]["desired"] == "done")
        self.assertEqual(self.widgets(first)["progress"].get(), 100)
        saved = self.folder / "downloads" / "first file with spaces.mp4"
        self.assertEqual(saved.read_bytes(), PAYLOAD)
        self.app.remove_unstarted()
        self.wait(lambda: list(self.app.rows) == [first])
        self.assertEqual(self.app.source.get(), f"http://127.0.0.1:{http.server_port}/")
        self.assertTrue(saved.exists())

    def test_start_pause_each_file_and_pause_all(self):
        self.add_keys([KEY + "/one.mp4", KEY + "/two.zip"])
        first, second = self.app.rows
        self.app.table.select_visible(True)
        self.app.download_button.invoke()
        self.wait(lambda: len(self.node.jobs) == 2 and all(t["desired"] == "running" for t in self.app.rows.values()))
        self.widgets(first)["pause_button"].invoke()
        self.wait(lambda: self.node.jobs[first]["PriorityClass"] == "6" and self.app.rows[first]["desired"] == "paused")
        self.assertEqual(self.node.jobs[second]["PriorityClass"], "2")
        self.widgets(first)["start_button"].invoke()
        self.wait(lambda: self.node.jobs[first]["PriorityClass"] == "2")
        self.app.pause_all_button.invoke()
        self.wait(lambda: all(job["PriorityClass"] == "6" for job in self.node.jobs.values()))
        self.assertEqual(self.node.submissions, 2)
        self.widgets(first)["start_button"].invoke()
        self.wait(lambda: self.node.jobs[first]["PriorityClass"] == "2")
        self.assertEqual(self.node.jobs[second]["PriorityClass"], "6")

    def test_mb_size_estimated_progress_and_full_error_details(self):
        self.add_keys([KEY + "/a.jpg"])
        row = next(iter(self.app.rows))
        self.assertEqual(self.widgets(row)["size"].get(), "Unknown")
        self.widgets(row)["start_button"].invoke()
        self.wait(lambda: row in self.node.jobs and self.app.rows[row]["desired"] == "running")
        self.node.progress(row, 3)
        self.wait(lambda: self.widgets(row)["progress"].get() == 30)
        self.assertEqual(self.widgets(row)["size"].get(), f"~{len(PAYLOAD)*.3/1_000_000:.2f} / {len(PAYLOAD)/1_000_000:.2f}")
        self.assertNotIn("Blocks", self.widgets(row)["status"].get())
        self.assertEqual(self.app.table.HEADINGS[8:], ("Progress", "Elapsed (min)", "ETA (min)"))
        self.node.fail(row, 20, fatal=True)
        self.wait(lambda: self.app.rows[row]["desired"] == "failed")
        self.app.show_details(row)
        self.assertIn("code 20", self.app.detail_status.get())

    def test_gui_restores_paused_queue_and_selection(self):
        self.add_keys([KEY + "/saved queue.zip"])
        row = next(iter(self.app.rows))
        self.widgets(row)["start_button"].invoke()
        self.wait(lambda: row in self.node.jobs and self.app.rows[row]["desired"] == "running")
        self.node.progress(row, 4)
        self.wait(lambda: self.widgets(row)["progress"].get() == 40)
        self.widgets(row)["pause_button"].invoke()
        self.wait(lambda: self.node.jobs[row]["PriorityClass"] == "6")
        self.app.table.select_gesture(row)
        self.close_app()
        self.open_app()
        self.wait(lambda: row in self.app.rows and self.app.rows[row]["status"] == "Paused")
        self.assertEqual(self.widgets(row)["progress"].get(), 40)
        self.assertEqual(self.app.table.selected_ids, set())
        self.assertEqual(self.node.submissions, 1)

    def test_remove_ignores_late_page_scan_results(self):
        started, release = threading.Event(), threading.Event()

        def slow_scrape(*args):
            started.set()
            release.wait(3)
            return [KEY + "/stale.zip"]

        with patch("fld.app.scrape", side_effect=slow_scrape):
            self.app.source.set("http://example.test/")
            self.app.find()
            self.assertTrue(started.wait(1))
            old_cancel = self.app.cancel
            self.app.remove_unstarted()
            self.assertTrue(old_cancel.is_set())
            release.set()
            deadline = time.monotonic() + 1
            while self.app.events.empty() and time.monotonic() < deadline:
                time.sleep(.01)
            self.app.poll()
            self.assertEqual(self.app.rows, {})
            self.assertFalse(self.app.busy)

    def test_select_all_none_and_compact_columns(self):
        self.add_keys([KEY + "/a.jpg", KEY + "/short file.zip"])
        self.app.select_all(False)
        self.app.download()
        self.assertIn("Select at least one", self.app.status.get())
        self.app.select_all(True)
        self.assertTrue(all(data["selected"] for data in self.app.rows.values()))
        self.root.update_idletasks()
        self.assertLess(self.app.table.widths[1], 200)
        self.assertTrue(all(self.app.table.content.itemcget(data["bar"], "fill") == "#21ae45"
                            for data in self.app.table.visible.values()))

    def test_148_files_use_only_visible_controls_and_reuse_them(self):
        self.add_keys([KEY + f"/File {index:03d}.mp4" for index in range(148)])
        table = self.app.table
        with patch.object(table.canvas, "winfo_height", return_value=360):
            table.render()
            self.assertLessEqual(len(table.pool), 15)
            pool_widgets = [str(widget) for slot in table.pool for widget in slot["widgets"]]
            table.canvas.yview_moveto(1)
            table.render()
            last = list(self.app.rows)[-1]
            self.assertIn(last, table.visible)
            table.activate_row(last)
            table.focus_id = last
            table.toggle_focus()
            self.assertIn(last, table.selected_ids)
            table.canvas.yview_moveto(0)
            table.render()
            self.assertEqual(pool_widgets, [str(widget) for slot in table.pool for widget in slot["widgets"]])
            self.assertTrue(all(self.app.rows[row]["selected"] for row in table.visible))
        self.assertEqual(len(table.controls), 3)
        self.assertEqual(len(table.HEADINGS), 11)
        self.assertEqual(len(table.header_frame.winfo_children()), len(table.HEADINGS))

    def test_passive_metric_reorders_and_width_changes_preserve_viewport(self):
        self.add_keys([KEY + f"/Scroll {index:03d}.mp4" for index in range(150)])
        table = self.app.table
        self.root.deiconify()
        self.root.update_idletasks()
        with self.manager.lock:
            for task in self.manager.tasks.values():
                task["fraction"] = task["display_id"] / 151
        for task in self.manager.snapshot():
            table.tasks[task["id"]]["fraction"] = task["fraction"]
        table.sort_by(8)
        table.canvas.yview_moveto(.5)
        self.root.update_idletasks()

        def viewport_anchor():
            y = table.canvas.canvasy(0)
            index = min(len(table.order) - 1, int(y // table.ROW_HEIGHT))
            return table.order[index], y - index * table.ROW_HEIGHT

        first, offset = viewport_anchor()
        for step in range(6):
            left, right = table.order[-2:]
            table.tasks[left]["fraction"], table.tasks[right]["fraction"] = (
                table.tasks[right]["fraction"], table.tasks[left]["fraction"])
            table.refresh_metric_order()
            self.root.update_idletasks()
            self.assertEqual(viewport_anchor()[0], first)
            self.assertLessEqual(abs(viewport_anchor()[1] - offset), 1, f"reorder {step}")

        table.activate_row(first)
        table.pressed_cell = (first, 5, table.projection_generation)
        table.mark_native_press(table.controls["start_button"])
        before_yview = table.canvas.yview()
        held = table.pressed_cell, table.native_pressed
        table.refresh_metric_order()
        self.assertEqual(table.canvas.yview(), before_yview)
        self.assertEqual((table.pressed_cell, table.native_pressed), held)

        original, _ = viewport_anchor()
        column = 1
        old_width = table.widths[column]
        table.start_resize(SimpleNamespace(x=old_width - 1, x_root=100, y_root=40), column)
        table.drag_resize(SimpleNamespace(x_root=old_width + 39))
        table.end_resize(SimpleNamespace(x_root=old_width + 39, y_root=40))
        self.root.update_idletasks()
        self.assertEqual(viewport_anchor()[0], original)
        self.assertLessEqual(abs(viewport_anchor()[1] - offset), 1)

    def test_saved_named_sort_restores_before_projection_and_only_explicit_writes(self):
        self.add_keys([KEY + "/alpha.mp4", KEY + "/zeta.mp4"])
        table = self.app.table
        rows = self.manager.snapshot()
        rows[0]["desired"], rows[0]["status"] = "running", "Downloading"
        rows[1]["desired"], rows[1]["status"] = "new", "Ready"
        self.app.sync_tasks(rows)

        with patch.object(self.app.actions, "put", wraps=self.app.actions.put) as enqueue:
            table.set_running_first(True)
            table.sort_by(1)
            self.wait(lambda: self.manager.sort_preferences == {
                "column": "file", "descending": False, "running_first": True})
            saved_writes = [call.args[0][0] for call in enqueue.call_args_list]
            self.assertIn("set_sort_preferences", saved_writes)
            enqueue.reset_mock()
            table.refresh_metric_order()
            table.configure_filter("alpha")
            table.configure_filter("")
            table.set_widths(list(table.widths))
            self.assertNotIn("set_sort_preferences",
                             [call.args[0][0] for call in enqueue.call_args_list])

        self.close_app()
        self.open_app()
        table = self.app.table
        self.assertEqual(table.sort_preferences(), {
            "column": "file", "descending": False, "running_first": True})
        self.assertTrue(self.app.running_first.get())
        restored_rows = self.manager.snapshot()
        restored_rows[1]["desired"], restored_rows[1]["status"] = "running", "Downloading"
        self.app.sync_tasks(restored_rows)
        self.assertEqual([self.app.rows[row]["filename"] for row in table.order],
                         ["zeta.mp4", "alpha.mp4"])

    def test_canvas_cells_work_before_hover_controls_appear(self):
        self.add_keys([KEY + "/clickable.mp4"])
        table = self.app.table
        row = next(iter(self.app.rows))
        y = table.ROW_HEIGHT / 2 - table.view_y
        def click(column):
            x = sum(table.widths[:column]) + table.widths[column] / 2 - table.view_x
            event = SimpleNamespace(x=x, y=y)
            table.pointer_press(event)
            table.pointer_release(event)
        click(1)
        self.assertEqual(table.selected_ids, {row})
        click(5)
        self.wait(lambda: row in self.node.jobs and self.app.rows[row]["desired"] == "running")
        click(6)
        self.wait(lambda: self.app.rows[row]["desired"] == "paused")
        self.assertEqual(self.app.selected_row, row)

    def test_keyboard_navigation_changes_the_real_control_target(self):
        self.add_keys([KEY + "/first.mp4", KEY + "/second.mp4"])
        first, second = self.app.rows
        table = self.app.table
        table.activate_row(first)
        table.move_focus(1)
        self.assertEqual(table.control_row, second)
        self.assertEqual(table.selected_ids, {second})
        table.toggle_focus()
        self.assertEqual(table.selected_ids, set())

    def test_keyboard_modifier_ranges_and_space_toggle_highlight(self):
        self.add_keys([KEY + f"/key-range{index}.mp4" for index in range(3)])
        table = self.app.table
        first, second, third = table.order
        table.keyboard_down(SimpleNamespace(state=0))
        self.assertEqual(table.selected_ids, {first})
        table.keyboard_down(SimpleNamespace(state=4))
        self.assertEqual(table.focus_id, second)
        self.assertEqual(table.selected_ids, {first})
        table.keyboard_down(SimpleNamespace(state=1))
        self.assertEqual(table.focus_id, third)
        self.assertEqual(table.selected_ids, {first, second, third})

        table.set_row_selection({first}, first, first)
        table.activate_row(second)
        self.root.deiconify()
        self.root.update()
        table.focus_id = first
        table.toggle_focus()
        self.assertEqual(table.selected_ids, set())
        self.assertTrue(self.app.rows[first]["selected"])
        self.assertTrue(self.app.rows[second]["selected"])

    def test_ctrl_a_selects_visible_rows_without_changing_table_state(self):
        self.add_keys([KEY + "/visible-a.mp4", KEY + "/hidden-b.zip", KEY + "/visible-c.jpg"])
        table = self.app.table
        first, hidden, third = table.order
        table.configure_filter(r"visible")
        self.root.deiconify()
        self.root.update()
        table.selected_ids = {hidden}
        table.anchor_id, table.focus_id = "absent-anchor", "absent-focus"
        table.control_row = first
        self.app.selected_row = hidden
        checkboxes = {row: self.app.rows[row]["selected"] for row in table.tasks}
        view = (table.view_x, table.view_y)
        table.canvas.focus_force()
        table.canvas.event_generate("<Control-a>")
        self.root.update()
        self.assertEqual(table.selected_ids, {first, hidden, third})
        self.assertEqual((table.anchor_id, table.focus_id), ("absent-anchor", "absent-focus"))
        self.assertEqual(table.control_row, first)
        self.assertEqual(self.app.selected_row, hidden)
        self.assertEqual({row: task["selected"] for row, task in self.app.rows.items()}, checkboxes)
        self.assertEqual((table.view_x, table.view_y), view)
        self.assertTrue(table.canvas.bind("<Control-a>"))
        self.assertTrue(all(widget.bind("<Control-A>") for widget in table.controls.values()))
        for widget in table.controls.values():
            table.selected_ids = {hidden}
            table.anchor_id, table.focus_id = "absent-anchor", "absent-focus"
            widget.focus_force()
            widget.event_generate("<Control-A>")
            self.root.update()
            self.assertEqual(table.selected_ids, {first, hidden, third})
            self.assertEqual((table.anchor_id, table.focus_id), ("absent-anchor", "absent-focus"))
        table.configure_filter(r"no-match")
        table.selected_ids = {hidden}
        table.canvas.focus_force()
        table.canvas.event_generate("<Control-a>")
        self.root.update()
        self.assertEqual(table.order, [])
        self.assertEqual(table.selected_ids, {hidden})

    def test_ctrl_a_in_source_and_filter_entries_keeps_native_text_selection(self):
        self.app.source.set("source text")
        self.app.filter_text.set("filter text")
        self.root.deiconify()
        self.root.update()
        for entry in (self.app.entry, self.app.filter_entry):
            entry.focus_force()
            entry.event_generate("<Control-a>")
            self.root.update()
            self.assertTrue(entry.selection_present())

    def test_scrolling_keeps_overlapping_rows_in_the_same_controls(self):
        self.add_keys([KEY + f"/File {index:03d}.mp4" for index in range(150)])
        table = self.app.table
        with patch.object(table.canvas, "winfo_height", return_value=360):
            table.render()
            before = dict(table.visible)
            table.scroll_units(3)
            table.render()
            overlapping = set(before) & set(table.visible)
            self.assertGreater(len(overlapping), 5)
            self.assertTrue(all(before[row] is table.visible[row] for row in overlapping))

    def test_column_drag_and_auto_fit_are_saved(self):
        self.add_keys([KEY + "/columns.mp4"])
        table = self.app.table
        self.root.update_idletasks()
        original = table.widths[2]
        table.start_resize(SimpleNamespace(x=original - 1, x_root=100, y_root=50), 2)
        table.drag_resize(SimpleNamespace(x_root=200))
        table.end_resize(SimpleNamespace(x_root=200, y_root=50))
        self.assertEqual(table.widths[2], original + 100)
        self.wait(lambda: self.manager.column_widths is not None and self.manager.column_widths[2] == original + 100)
        self.close_app()
        self.open_app()
        self.assertEqual(len(self.manager.column_widths), 11)
        self.assertEqual(self.app.table.widths[2], original + 100)
        self.assertEqual(self.app.table.HEADINGS[9:], ("Elapsed (min)", "ETA (min)"))
        table = self.app.table
        self.assertGreaterEqual(table.natural_widths[9], table.text_font.measure("Elapsed (min)") + 12)
        self.assertGreaterEqual(table.natural_widths[10], table.text_font.measure("ETA (min)") + 12)
        table.autosize(SimpleNamespace(x=table.widths[2] - 1), 2)
        self.assertEqual(table.widths[2], table.natural_widths[2])

    def test_progress_redraws_once_per_tick_and_controls_update_immediately(self):
        self.add_keys([KEY + "/steady.mp4"])
        task = self.manager.snapshot()[0]
        task.update(desired="running", size=1000000, size_final=True, fraction=.1, fetched=100000)
        self.app.sync_tasks([dict(task)])
        row = task["id"]
        self.assertEqual(self.widgets(row)["progress"].get(), 10)
        task.update(fraction=.2, fetched=200000)
        self.app.sync_tasks([dict(task)])
        self.assertEqual(self.widgets(row)["progress"].get(), 10)
        self.app.tick()
        self.assertEqual(self.widgets(row)["progress"].get(), 20)
        task["desired"] = "paused"
        self.app.sync_tasks([dict(task)])
        self.assertIn("disabled", self.widgets(row)["pause_button"].state())

    def test_graph_has_numeric_adapting_mb_per_second_axis(self):
        graph = self.app.graph
        graph.sample(.8, now=0, draw=False)
        graph.draw()
        self.assertEqual(graph.axis_max, 1)
        ticks = [graph.itemcget(item, "text") for item in graph.find_withtag("y_tick")]
        self.assertEqual(ticks, ["0.0", "0.5", "1.0"])
        graph.sample(3, now=1)
        self.assertEqual(graph.axis_max, 5)
        graph.sample(.2, now=122)
        self.assertEqual(graph.axis_max, .2)

    def test_restore_does_not_reopen_a_stale_tray_menu(self):
        class Tray:
            def __init__(self, root, icon, notify):
                self.notify, self.visible = notify, False
                self.menu_pending, self.count = False, 0
            def show(self): self.visible = True
            def hide(self): self.visible = False
            def close(self): pass
            def show_menu(self):
                self.count += 1
                self.menu_pending = False
                if self.count == 1:
                    self.notify("menu")
                self.notify("restore")
        self.app.tray_factory = Tray
        self.app.hide_to_tray()
        tray = self.app.tray
        tray.menu_pending = True
        tray.notify("menu")
        with patch.object(self.root, "deiconify"), patch.object(self.root, "lift"):
            self.app.poll()
        self.assertEqual(tray.count, 1)
        self.assertFalse(self.app.hidden)

    def test_high_resolution_wheel_and_background_gaps_scroll(self):
        self.add_keys([KEY + f"/File {index:03d}.mp4" for index in range(148)])
        self.root.update_idletasks()
        table = self.app.table
        before = table.canvas.canvasy(0)
        event = SimpleNamespace(delta=-30, state=0)
        table.scroll(event)
        table.scroll(event)
        self.assertGreater(table.canvas.canvasy(0), before)
        before = table.canvas.canvasy(0)
        table.content.event_generate("<MouseWheel>", delta=-120)
        self.assertGreater(table.canvas.canvasy(0), before)
        with patch.object(table, "winfo_containing", return_value=table.content):
            self.assertEqual(table.route_wheel(SimpleNamespace(delta=-120, state=0, x_root=5, y_root=5)), "break")

    def test_scanning_and_queue_commit_do_not_block_tk(self):
        started, release = threading.Event(), threading.Event()
        original = self.manager.add
        def slow_add(keys):
            started.set()
            release.wait(3)
            original(keys)
        with patch("fld.app.scrape", return_value=[KEY + "/scan.zip"]), patch.object(self.manager, "add", side_effect=slow_add):
            self.app.source.set("http://example.test/")
            self.app.find()
            self.assertTrue(started.wait(1))
            self.assertTrue(self.app.busy)
            self.assertTrue(self.app.scan_progress.grid_info())
            heartbeat = []
            self.root.after(5, lambda: heartbeat.append(True))
            self.wait(lambda: bool(heartbeat), timeout=1)
            self.assertTrue(self.app.busy)
            release.set()
            self.wait(lambda: not self.app.busy and len(self.app.rows) == 1)
            self.assertFalse(self.app.scan_progress.grid_info())

    def test_graph_sums_active_speeds_and_keeps_only_two_minutes(self):
        self.add_keys([KEY + "/one.mp4", KEY + "/two.mp4", KEY + "/paused.zip"])
        tasks = self.manager.snapshot()
        for task, speed, desired in zip(tasks, (500000, 250000, 900000), ("running", "running", "paused")):
            task.update(speed=speed, desired=desired)
        self.app.sync_tasks(tasks)
        self.app.tick()
        self.assertEqual(self.app.graph.samples[-1][1], .75)
        graph = self.app.graph
        graph.samples.clear()
        for second in range(241):
            graph.sample(1, now=second, draw=False)
        self.assertEqual(len(graph.samples), 121)
        self.assertEqual(graph.samples[0][0], 120)

    def test_elapsed_and_eta_follow_progress_bar(self):
        self.add_keys([KEY + "/timed.mp4"])
        task = self.manager.snapshot()[0]
        task.update(desired="running", elapsed=10, active_since=110, size=1000000, size_final=True,
                    fetched=250000, fraction=.25, speed=125000, eta_speed=125000)
        with patch("fld.metrics.time.time", return_value=172):
            self.app.sync_tasks([task])
        widgets = self.widgets(task["id"])
        self.assertEqual(widgets["elapsed"].get(), "01:12")
        self.assertEqual(widgets["eta"].get(), "00:06")
        self.assertEqual(self.app.table.HEADINGS[-3:], ("Progress", "Elapsed (min)", "ETA (min)"))
        task["speed"] = 0
        self.assertEqual(remaining_seconds(task), 6)
        task["eta_speed"] = 0
        self.assertIsNone(remaining_seconds(task))

    def test_x_hides_to_tray_and_menu_can_restore_or_exit(self):
        class Tray:
            def __init__(self, root, icon, notify):
                self.notify, self.visible, self.closed = notify, False, False
            def show(self): self.visible = True
            def hide(self): self.visible = False
            def close(self): self.closed = True
            def show_menu(self): self.notify("exit")
        self.app.tray_factory = Tray
        self.root.tk.call(self.root.protocol("WM_DELETE_WINDOW"))
        self.assertTrue(self.app.hidden)
        self.assertTrue(self.app.tray.visible)
        self.assertTrue(self.app.action_thread.is_alive())
        tray = self.app.tray
        with patch.object(self.root, "deiconify"), patch.object(self.root, "lift"):
            tray.notify("restore")
            self.app.poll()
        self.assertFalse(self.app.hidden)
        self.app.hide_to_tray()
        tray.notify("menu")
        self.app.poll()
        self.assertTrue(self.app.exiting)
        deadline = time.monotonic() + 6
        while not self.app.closed and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertTrue(self.app.closed)
        self.assertTrue(tray.closed)

    def test_filename_regex_sorting_and_visible_bulk_scope(self):
        self.add_keys([KEY + "/file10.mp4", KEY + "/File2.jpg", KEY + "/Photo.JPG", KEY + "/notes.txt"])
        table = self.app.table
        rows = self.manager.snapshot()
        self.assertEqual([self.app.rows[row]["display_id"] for row in table.order], [1, 2, 3, 4])
        table.sort_by(1)
        self.assertEqual([self.app.rows[row]["filename"] for row in table.order],
                         ["file10.mp4", "File2.jpg", "notes.txt", "Photo.JPG"])
        self.app.filter_text.set(r"\.jpg$")
        self.app.apply_filter()
        jpg_rows = list(table.order)
        self.assertEqual(len(jpg_rows), 2)
        self.assertIn("Showing 2 of 4 files", self.app.status.get())
        self.assertTrue(all(self.app.rows[row]["filename"].lower().endswith(".jpg") for row in jpg_rows))
        self.app.filter_text.set("[")
        self.app.apply_filter()
        self.assertEqual(table.order, jpg_rows)
        self.assertTrue(self.app.filter_error.get().startswith("Invalid regex:"))
        self.app.filter_text.set(r"^no-match$")
        self.app.apply_filter()
        self.assertEqual(table.order, [])
        self.assertEqual(str(self.app.download_button["state"]), "disabled")
        self.assertTrue(self.app.table.header_frame.grid_info())
        self.app.filter_text.set("")
        self.app.apply_filter()
        self.assertEqual(len(table.order), 4)
        self.assertEqual(str(self.app.download_button["state"]), "disabled")
        self.assertEqual(self.app.status.get(), "4 files: 0 running, 0 paused, 0 complete. Queue saved automatically.")
        table.select_visible(True)
        self.assertEqual(str(self.app.download_button["state"]), "normal")
        self.app.filter_text.set(r"\.jpg$")
        self.app.apply_filter()
        self.app.select_all(False)
        hidden = next(row for row in self.app.rows if row not in table.order)
        self.assertTrue(self.app.rows[hidden]["selected"])
        first = table.order[0]
        table.select_gesture(first)
        self.app.download()
        self.wait(lambda: self.app.rows[first]["desired"] == "running")
        self.assertEqual(self.app.rows[hidden]["desired"], "new")

    def test_row_highlight_gestures_preserve_uuid_across_sort_and_filter(self):
        self.add_keys([KEY + f"/highlight{index}.mp4" for index in range(3)])
        table = self.app.table
        first, second, third = table.order
        table.select_gesture(first)
        table.select_gesture(second, control=True)
        self.assertEqual(table.selected_ids, {first, second})
        self.assertEqual(table.anchor_id, second)
        table.select_gesture(third, shift=True)
        self.assertEqual(table.selected_ids, {second, third})
        self.assertEqual(table.anchor_id, second)
        table.select_gesture(first, control=True, shift=True)
        self.assertEqual(table.selected_ids, {first, second, third})
        table.set_row_selection({third}, third, third)
        table.select_gesture(first, shift=True)
        self.assertEqual(table.selected_ids, {first, second, third})
        self.assertEqual(table.selected_ids, {first, second, third})
        self.assertTrue(all(self.app.rows[row]["selected"] for row in (first, second, third)))
        table.sort_by(1)
        table.configure_filter("highlight[12]")
        self.assertEqual(table.selected_ids, {first, second, third})
        self.assertEqual(set(table.selected_visible_ids()), {second, third})
        table.configure_filter("")
        self.assertEqual(set(table.selected_visible_ids()), {first, second, third})
        self.assertTrue(self.app.rows[first]["selected"])
        self.assertTrue(self.app.rows[second]["selected"])
        self.assertTrue(self.app.rows[third]["selected"])

    def test_highlight_background_precedence_and_filter_button_state(self):
        self.add_keys([KEY + "/hover-one.mp4", KEY + "/hover-two.mp4"])
        table = self.app.table
        first, second = table.order
        table.set_row_selection({first}, first, first)
        table.set_hover(first)
        self.assertEqual(self.widgets(first)["background"] and
                         table.canvas.itemcget(self.widgets(first)["background"], "fill"), "#b8d8f5")
        table.set_hover(second)
        self.assertEqual(table.canvas.itemcget(self.widgets(first)["background"], "fill"), "#c8e1f7")
        self.assertEqual(table.canvas.itemcget(self.widgets(second)["background"], "fill"), "#dceeff")
        table.configure_filter("^no-match$")
        self.assertEqual(str(self.app.delete_button["state"]), "disabled")
        table.configure_filter("")
        self.assertEqual(str(self.app.delete_button["state"]), "normal")

    def test_context_menu_scope_and_delete_key_routing(self):
        self.add_keys([KEY + "/menu-one.mp4", KEY + "/menu-two.mp4"])
        table = self.app.table
        first, second = table.order
        table.set_row_selection({first, second}, first, second)
        table.position_viewport(force=True)
        def right_click(row):
            x = table.canvas.winfo_rootx() + table.view_x + 20
            y_root = table.canvas.winfo_rooty() + (
                table.order_index[row] * table.ROW_HEIGHT + table.ROW_HEIGHT / 2 - table.view_y)
            event = SimpleNamespace(x_root=x, y_root=y_root)
            table.context_press(event)
        with patch.object(table.context_menu, "tk_popup") as popup, patch.object(table.canvas, "focus_set") as focus_set:
            right_click(first)
            self.assertEqual(table.selected_ids, {first, second})
            self.assertEqual(table.focus_id, first)
            self.assertEqual(self.app.selected_row, first)
            focus_set.assert_called()
            right_click(first)
            self.assertEqual(popup.call_count, 2)
            table.set_row_selection({second}, second, second)
            right_click(first)
            self.assertEqual(table.selected_ids, {first})
            before_empty = popup.call_count
            empty_y = table.canvas.winfo_rooty() + table.canvas.winfo_height() - 4
            table.context_press(SimpleNamespace(x_root=table.canvas.winfo_rootx() + 20, y_root=empty_y))
            self.assertEqual(popup.call_count, before_empty)
        table.set_row_selection({first}, first, first)
        with patch.object(table, "delete_selected") as delete:
            table.keyboard_delete(SimpleNamespace())
        delete.assert_called_once_with()

    def test_delete_selected_confirms_frozen_uuid_snapshot(self):
        self.add_keys([KEY + "/delete-one.mp4", KEY + "/delete-two.zip"])
        table = self.app.table
        first, second = table.order
        table.set_row_selection({first, second}, first, second)
        self.assertEqual(str(self.app.delete_button["state"]), "normal")
        def confirm(*args, **kwargs):
            self.assertEqual(args[0], "Delete selected tasks?")
            self.assertIn("Remove 2 selected task(s) from the list?", args[1])
            self.assertEqual(kwargs["default"], "no")
            table.set_row_selection({second}, second, second)
            return True
        with patch("fld.app.messagebox.askyesno", side_effect=confirm) as ask:
            self.app.delete_button.invoke()
        ask.assert_called_once()
        self.wait(lambda: not self.app.delete_pending and not self.app.rows)
        self.assertEqual(self.manager.tasks[first]["desired"], "removed")
        self.assertEqual(self.manager.tasks[second]["desired"], "removed")
        self.assertIsNone(table.anchor_id)
        self.assertIsNone(table.focus_id)
        self.assertEqual(self.app.status.get(), "Removed 2 selected task(s). Completed files were kept on disk.")

    def test_delete_confirmation_cancel_error_and_exit_paths(self):
        self.add_keys([KEY + "/confirm-path.mp4"])
        row = self.app.table.order[0]
        self.app.table.set_row_selection({row}, row, row)
        with patch("fld.app.messagebox.askyesno", return_value=False) as ask:
            self.app.delete_selected()
        ask.assert_called_once()
        self.assertFalse(self.app.delete_pending)
        self.assertEqual(str(self.app.delete_button["state"]), "normal")
        with patch("fld.app.messagebox.askyesno", side_effect=RuntimeError("dialog failed")):
            with self.assertRaisesRegex(RuntimeError, "dialog failed"):
                self.app.delete_selected()
        self.assertFalse(self.app.delete_pending)

        def exit_during_dialog(*args, **kwargs):
            self.app.exiting = True
            return True
        before = self.app.actions.qsize()
        with patch("fld.app.messagebox.askyesno", side_effect=exit_during_dialog):
            self.app.delete_selected()
        self.assertEqual(self.app.actions.qsize(), before)
        self.assertFalse(self.app.delete_pending)
        self.app.exiting = False

    def test_delete_error_resets_guard_and_stale_ack_cannot_restore_old_uuid(self):
        self.add_keys([KEY + "/stale-delete.mp4"])
        old_id = self.app.table.order[0]
        stale = self.manager.snapshot()
        self.app.table.set_row_selection({old_id}, old_id, old_id)
        with patch("fld.app.messagebox.askyesno", return_value=True), \
             patch.object(self.manager, "delete_tasks", side_effect=OSError("disk full")):
            self.app.delete_selected()
            self.wait(lambda: not self.app.delete_pending)
        self.assertEqual(self.app.status.get(), "Error: disk full")
        self.assertEqual(str(self.app.delete_button["state"]), "normal")

        self.manager.delete_tasks([old_id])
        self.manager.add([KEY + "/stale-delete.mp4"])
        new_id = self.manager.snapshot()[0]["id"]
        self.assertNotEqual(new_id, old_id)
        while True:
            try:
                self.app.events.get_nowait()
            except Exception:
                break
        self.app.generation += 1
        self.app.events.put((None, "tasks", (stale,)))
        self.app.events.put((self.app.generation - 1, "delete_done", ([old_id],)))
        self.app.events.put((None, "tasks", (self.manager.snapshot(),)))
        self.wait(lambda: new_id in self.app.rows and old_id not in self.app.rows)
        self.assertNotIn(old_id, self.app.table.selected_ids)
        self.assertEqual(self.app.selected_row, None)

    def test_remove_completed_is_global_serialized_and_preserves_files(self):
        self.add_keys([KEY + "/done.mp4", KEY + "/running.zip", KEY + "/new.jpg"])
        done, running, new = self.app.table.order
        output = self.folder / "downloads" / "done.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(PAYLOAD)
        with self.manager.lock:
            self.manager.tasks[done].update(desired="done", status="100%", path=str(output))
            self.manager.tasks[running].update(desired="running", status="Downloaded", fraction=1)
        self.app.sync_tasks(self.manager.snapshot())
        self.app.table.configure_filter(r"new")
        self.assertEqual(str(self.app.remove_completed_button["state"]), "normal")
        generation, cancelled = self.app.generation, self.app.cancel.is_set()
        with patch("fld.app.messagebox.askyesno", side_effect=AssertionError("unexpected modal")):
            self.app.remove_completed_button.invoke()
        self.assertTrue(self.app.remove_completed_pending)
        self.assertEqual((self.app.generation, self.app.cancel.is_set()), (generation, cancelled))
        self.wait(lambda: not self.app.remove_completed_pending and done not in self.app.rows)
        self.assertEqual(set(self.app.rows), {running, new})
        self.assertEqual(self.manager.tasks[done]["desired"], "removed")
        self.assertEqual(output.read_bytes(), PAYLOAD)
        self.assertEqual(self.app.status.get(), "Removed 1 completed task(s). Completed files were kept on disk.")

    def test_remove_completed_error_resets_pending_state(self):
        self.add_keys([KEY + "/completed-error.mp4"])
        row = self.app.table.order[0]
        task = dict(self.app.rows[row], desired="done")
        self.app.sync_tasks([task])
        with patch.object(self.manager, "remove_completed", side_effect=OSError("disk full")):
            self.app.remove_completed()
            self.wait(lambda: not self.app.remove_completed_pending)
        self.assertEqual(self.app.status.get(), "Error: disk full")
        self.assertIn(row, self.app.rows)

    def test_remove_completed_ack_precedes_stale_generation_snapshot(self):
        self.add_keys([KEY + "/same-key.mp4"])
        old_id = self.app.table.order[0]
        old_task = dict(self.app.rows[old_id], desired="done")
        self.app.sync_tasks([old_task])
        self.app.table.set_row_selection({old_id}, old_id, old_id)
        new_task = dict(old_task, id="fresh-uuid")
        while True:
            try:
                self.app.events.get_nowait()
            except Exception:
                break
        stale_generation = self.app.generation - 1
        self.app.events.put((stale_generation, "tasks", ([old_task],)))
        self.app.events.put((stale_generation, "remove_completed_done", ([old_id],)))
        self.app.events.put((stale_generation, "tasks", ([old_task, new_task],)))
        self.wait(lambda: old_id not in self.app.rows and "fresh-uuid" in self.app.rows)
        self.assertEqual(self.app.table.selected_ids, set())
        self.assertEqual(self.app.status.get(), "Removed 1 completed task(s). Completed files were kept on disk.")

    def test_remove_completed_zero_ack_pending_guard_and_exit_enablement(self):
        self.add_keys([KEY + "/stale-completed.mp4"])
        row = self.app.table.order[0]
        self.app.sync_tasks([dict(self.app.rows[row], desired="done")])
        self.app.exiting = True
        self.app.update_remove_completed_button()
        self.assertEqual(str(self.app.remove_completed_button["state"]), "disabled")
        self.app.remove_completed()
        self.assertFalse(self.app.remove_completed_pending)
        self.app.exiting = False
        self.app.update_remove_completed_button()
        with patch.object(self.app.actions, "put", wraps=self.app.actions.put) as enqueue:
            self.app.remove_completed()
            self.app.remove_completed()
            self.assertEqual(enqueue.call_count, 1)
        self.wait(lambda: not self.app.remove_completed_pending)
        self.assertIn(row, self.app.rows)
        self.assertEqual(self.app.status.get(), "Removed 0 completed task(s). Completed files were kept on disk.")

    def test_sort_unknown_ties_running_first_and_metric_tick_invalidation(self):
        self.add_keys([KEY + f"/file{index}.jpg" for index in range(4)])
        tasks = self.manager.snapshot()
        tasks[0].update(desired="paused", size=1000, fetched=0, fraction=.4, status="Same")
        tasks[1].update(desired="running", size=1000, fetched=0, fraction=.8, status="Same", speed=20, eta_speed=20)
        tasks[2].update(desired="running", size=None, fetched=0, fraction=.8, status="Same", speed=10)
        tasks[3].update(desired="new", size=1000, fetched=0, fraction=.2, status="Same", timer_known=False)
        self.app.sync_tasks(tasks)
        table = self.app.table
        table.set_running_first(True)
        self.assertEqual(self.app.rows[table.order[0]]["display_id"], 2)
        self.assertEqual(self.app.rows[table.order[1]]["display_id"], 3)
        table.sort_by(0)
        self.assertEqual([self.app.rows[row]["display_id"] for row in table.order], [2, 3, 1, 4])
        table.sort_by(10)
        self.assertEqual(self.app.rows[table.order[-1]]["display_id"], 4)
        table.sort_by(8)
        focused = table.order[0]
        table.activate_row(focused)
        table.pressed_cell = (focused, 6, table.projection_generation)
        before_generation = table.projection_generation
        table.refresh_metric_order()
        self.assertEqual(table.projection_generation, before_generation)
        self.assertTrue(table.overlay_active)
        self.assertEqual(table.pressed_cell, (focused, 6, before_generation))
        self.app.rows[focused]["fraction"] = .01
        tasks = [dict(task) for task in self.app.rows.values()]
        self.app.sync_tasks(tasks)
        table.refresh_metric_order()
        self.assertNotEqual(table.projection_generation, before_generation)
        self.assertIsNone(table.pressed_cell)

    def test_fixed_header_empty_repopulation_hover_and_stable_release(self):
        self.add_keys([KEY + f"/File {index}.mp4" for index in range(30)])
        table = self.app.table
        self.root.deiconify()
        self.root.update_idletasks()
        header_y = table.headers[0].winfo_rooty()
        table.canvas.yview_moveto(.5)
        table.position_viewport(force=True)
        self.assertEqual(table.headers[0].winfo_rooty(), header_y)
        row = table.order[0]
        table.activate_row(row)
        press = SimpleNamespace(x=table.widths[1] + 2, y=table.ROW_HEIGHT / 2)
        table.pointer_press(press)
        table.sort_by(1)
        generation = table.projection_generation
        release = SimpleNamespace(x=press.x, y=press.y)
        table.pointer_release(release)
        self.assertEqual(table.projection_generation, generation)
        old = list(table.tasks.values())
        table.set_tasks([])
        self.assertEqual(table.order, [])
        self.assertIsNone(table.hover_row)
        table.set_tasks(old)
        self.assertEqual(table.order[0], old[0]["id"])

    def test_remove_ack_tombstones_ignore_stale_snapshot_after_generation_change(self):
        self.add_keys([KEY + "/started.mp4", KEY + "/new.zip"])
        started, unstarted = list(self.app.rows)
        self.manager.start([started])
        self.app.sync_tasks(self.manager.snapshot())
        stale = self.manager.snapshot()
        self.app.generation += 1
        self.app.events.put((self.app.generation - 1, "remove_done", ([unstarted],)))
        self.app.events.put((None, "tasks", (stale,)))
        self.app.poll()
        self.assertNotIn(unstarted, self.app.rows)
        self.assertIn(started, self.app.rows)
        latest = self.manager.snapshot()
        latest[0]["status"] = "Preserved update"
        self.app.sync_tasks(latest)
        self.assertEqual(self.app.rows[started]["status"], "Preserved update")

    def test_window_preferences_track_maximize_minimize_and_flush(self):
        self.root.update_idletasks()
        self.app.startup_restoring = False
        self.app.normal_size = (1000, 700)
        self.app.last_non_minimized_state = "normal"
        with patch.object(self.root, "state", return_value="zoomed"):
            self.app.window_configure(SimpleNamespace(widget=self.root))
            self.app.capture_window_preferences()
        self.assertTrue(self.app.maximized)
        self.assertEqual(self.app.normal_size, (1000, 700))
        with patch.object(self.root, "state", return_value="iconic"):
            self.app.window_unmap(SimpleNamespace(widget=self.root))
        self.assertTrue(self.app.maximized)
        self.app.flush_window_preferences()
        if self.app.geometry_after_id is not None:
            self.root.after_cancel(self.app.geometry_after_id)
            self.app.geometry_after_id = None
        self.wait(lambda: self.manager.window_preferences["maximized"])
        self.assertEqual(self.manager.window_preferences["width"], 1000)
        with patch.object(self.root, "state", return_value="normal"):
            self.app.maximized = True
            self.app.capture_window_preferences()
        self.wait(lambda: not self.manager.window_preferences["maximized"])

    @unittest.skipUnless(os.name == "nt" and os.environ.get("FLD_TEST_WINDOW") == "1",
                         "Set FLD_TEST_WINDOW=1 for native window preference integration")
    def test_native_window_geometry_maximize_tray_exit_and_restore(self):
        self.root.geometry("1020x610")
        self.root.deiconify()
        self.wait(lambda: (self.root.winfo_width(), self.root.winfo_height()) == (1020, 610))
        self.wait(lambda: self.manager.window_preferences == {"width": 1020, "height": 610, "maximized": False})
        self.assertTrue(self.app.sizegrip.winfo_ismapped())
        self.root.state("zoomed")
        self.wait(lambda: self.root.state() == "zoomed" and self.manager.window_preferences["maximized"])
        self.assertEqual((self.manager.window_preferences["width"], self.manager.window_preferences["height"]), (1020, 610))
        self.assertFalse(self.app.sizegrip.winfo_ismapped())

        class Tray:
            def __init__(self, root, icon, notify):
                self.notify, self.visible = notify, False
            def show(self): self.visible = True
            def hide(self): self.visible = False
            def close(self): pass

        self.app.tray_factory = Tray
        self.app.hide_to_tray()
        self.assertEqual(self.root.state(), "withdrawn")
        self.app.restore()
        self.wait(lambda: self.root.state() == "zoomed")
        self.app.close()
        self.app = self.root = None
        self.open_app()
        self.root.deiconify()
        self.wait(lambda: self.root.state() == "zoomed")
        self.assertEqual((self.manager.window_preferences["width"], self.manager.window_preferences["height"]), (1020, 610))
        self.root.state("normal")
        self.wait(lambda: self.root.state() == "normal" and not self.manager.window_preferences["maximized"])
        self.assertEqual((self.root.winfo_width(), self.root.winfo_height()), (1020, 610))
        self.assertTrue(self.app.sizegrip.winfo_ismapped())


if __name__ == "__main__":
    unittest.main()
