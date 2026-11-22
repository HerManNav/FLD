"""Native Windows shell smoke tests for this app's own icon and callback."""

import gc
import os
from pathlib import Path
import tkinter as tk
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from fld.windows_tray import NativeTray


class TrayEventTest(unittest.TestCase):
    def make_tray(self):
        tray = NativeTray.__new__(NativeTray)
        tray.message, tray.hwnd = 0x8053, 1
        tray.visible, tray.version4 = True, True
        tray.menu_pending = tray.menu_open = False
        events = []
        tray.notify = events.append
        return tray, events

    def test_version4_right_click_queues_only_one_menu(self):
        tray, events = self.make_tray()
        for event in (0x205, 0x7b, 0x7b, 0x205):
            tray.window_proc(1, tray.message, 0, event)
        self.assertEqual(events, ["menu"])
        tray.visible = False
        tray.window_proc(1, tray.message, 0, 0x7b)
        self.assertEqual(events, ["menu"])

    def test_restore_from_native_menu_ignores_nested_notifications(self):
        tray, events = self.make_tray()
        tray.window_proc(1, tray.message, 0, 0x7b)
        def track(*args):
            tray.window_proc(1, tray.message, 0, 0x7b)
            tray.window_proc(1, tray.message, 0, 0x205)
            return 1
        tray.user = SimpleNamespace(CreatePopupMenu=lambda: 1, AppendMenuW=lambda *args: None,
            GetCursorPos=lambda *args: None, SetForegroundWindow=lambda *args: None,
            TrackPopupMenu=track, PostMessageW=lambda *args: None, DestroyMenu=lambda *args: None)
        tray.show_menu()
        self.assertEqual(events, ["menu", "restore"])
        self.assertFalse(tray.menu_open)
        self.assertFalse(tray.menu_pending)


@unittest.skipUnless(os.name == "nt" and os.environ.get("FLD_TEST_TRAY") == "1",
                     "Set FLD_TEST_TRAY=1 outside the sandbox for native tray integration")
class TrayTest(unittest.TestCase):
    def test_native_icon_and_restore_exit_callbacks(self):
        root = tk.Tk()
        root.withdraw()
        events = []
        tray = NativeTray(root, Path(__file__).resolve().parents[1] / "assets" / "fld.ico", events.append)
        try:
            tray.show()
            self.assertTrue(tray.visible)
            tray.window_proc(tray.hwnd, tray.message, 0, 0x400)
            self.assertEqual(events, ["restore"])
            tray.window_proc(tray.hwnd, tray.message, 0, 0x7b)
            self.assertEqual(events[-1], "menu")
            # Exercise menu creation and dispatch without opening a menu on the desktop.
            with patch.object(tray.user, "TrackPopupMenu", return_value=2), patch.object(tray.user, "SetForegroundWindow"):
                tray.show_menu()
            self.assertEqual(events[-1], "exit")
            tray.hide()
            self.assertFalse(tray.visible)
        finally:
            tray.close()
            root.destroy()
            root = tray = None
            gc.collect()
