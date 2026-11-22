"""Native Windows notification icon, hosted by the existing Tk window."""

import ctypes
from ctypes import wintypes
import os


class NativeTray:
    def __init__(self, root, icon_path, notify):
        if os.name != "nt":
            raise OSError("The system tray integration requires Windows.")
        self.root, self.notify = root, notify
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.shell = ctypes.WinDLL("shell32", use_last_error=True)
        result_type = ctypes.c_ssize_t
        self.callback_type = ctypes.WINFUNCTYPE(result_type, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", wintypes.BYTE * 8)]

        class NotifyIconData(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
                ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT), ("hIcon", wintypes.HICON),
                ("szTip", wintypes.WCHAR * 128), ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
                ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT), ("szInfoTitle", wintypes.WCHAR * 64),
                ("dwInfoFlags", wintypes.DWORD), ("guidItem", GUID), ("hBalloonIcon", wintypes.HICON)]

        self.user.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        self.user.GetAncestor.restype = wintypes.HWND
        self.user.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
        self.user.RegisterWindowMessageW.restype = wintypes.UINT
        self.user.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        self.user.LoadImageW.restype = wintypes.HANDLE
        self.user.DestroyIcon.argtypes = [wintypes.HICON]
        self.user.DestroyIcon.restype = wintypes.BOOL
        self.user.CreatePopupMenu.restype = wintypes.HMENU
        self.user.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
        self.user.AppendMenuW.restype = wintypes.BOOL
        self.user.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, ctypes.c_void_p]
        self.user.TrackPopupMenu.restype = wintypes.UINT
        self.user.DestroyMenu.argtypes = [wintypes.HMENU]
        self.user.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        self.user.SetForegroundWindow.argtypes = [wintypes.HWND]
        self.user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.set_proc = self.user.SetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else self.user.SetWindowLongW
        self.set_proc.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        self.set_proc.restype = ctypes.c_void_p
        self.user.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.user.CallWindowProcW.restype = result_type
        self.shell.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NotifyIconData)]
        self.shell.Shell_NotifyIconW.restype = wintypes.BOOL
        root.update_idletasks()
        self.hwnd = self.user.GetAncestor(root.winfo_id(), 2)
        self.hicon = self.user.LoadImageW(None, str(icon_path), 1, 32, 32, 0x10)
        if not self.hicon:
            raise ctypes.WinError(ctypes.get_last_error())
        self.message = 0x8000 + 83
        self.taskbar_created = self.user.RegisterWindowMessageW("TaskbarCreated")
        self.data = NotifyIconData()
        self.data.cbSize = ctypes.sizeof(NotifyIconData)
        self.data.hWnd, self.data.uID = self.hwnd, 1
        self.data.uFlags = 1 | 2 | 4 | 0x80
        self.data.uCallbackMessage, self.data.hIcon = self.message, self.hicon
        self.data.szTip = "Freenet Link Downloader"
        self.visible = False
        self.version4 = True
        self.menu_pending = self.menu_open = False
        self.callback = self.callback_type(self.window_proc)
        ctypes.set_last_error(0)
        self.previous_proc = self.set_proc(self.hwnd, -4, ctypes.cast(self.callback, ctypes.c_void_p))
        if not self.previous_proc:
            self.user.DestroyIcon(self.hicon)
            raise ctypes.WinError(ctypes.get_last_error())

    def window_proc(self, hwnd, message, wparam, lparam):
        if message == self.message:
            event = lparam & 0xffff
            # Version 4 sends WM_CONTEXTMENU; mixing it with raw button messages
            # queues a second menu while TrackPopupMenu runs its nested message loop.
            if self.visible and not self.menu_open:
                if event in ((0x400, 0x401) if self.version4 else (0x202, 0x203)):
                    self.menu_pending = False
                    self.notify("restore")
                elif event == (0x7b if self.version4 else 0x205) and not self.menu_pending:
                    self.menu_pending = True
                    self.notify("menu")
            return 0
        if message == self.taskbar_created and self.visible:
            self.visible = False
            try:
                self.show()
            except OSError:
                self.notify("restore")
        return self.user.CallWindowProcW(self.previous_proc, hwnd, message, wparam, lparam)

    def show(self):
        if not self.visible:
            if not self.shell.Shell_NotifyIconW(0, ctypes.byref(self.data)):
                raise OSError("Windows could not create the tray icon.")
            self.data.uVersion = 4
            self.version4 = bool(self.shell.Shell_NotifyIconW(4, ctypes.byref(self.data)))
            self.visible = True

    def hide(self):
        if self.visible:
            self.shell.Shell_NotifyIconW(2, ctypes.byref(self.data))
            self.visible = False
        self.menu_pending = False

    def show_menu(self):
        if not self.visible or self.menu_open:
            return
        self.menu_pending = False
        menu = self.user.CreatePopupMenu()
        if not menu:
            return
        self.menu_open = True
        command = 0
        try:
            self.user.AppendMenuW(menu, 0, 1, "Restore")
            self.user.AppendMenuW(menu, 0x800, 0, None)
            self.user.AppendMenuW(menu, 0, 2, "Exit")
            point = wintypes.POINT()
            self.user.GetCursorPos(ctypes.byref(point))
            self.user.SetForegroundWindow(self.hwnd)
            command = self.user.TrackPopupMenu(menu, 0x100 | 0x80 | 2, point.x, point.y, 0, self.hwnd, None)
            self.user.PostMessageW(self.hwnd, 0, 0, 0)
        finally:
            self.user.DestroyMenu(menu)
            self.menu_open = False
        if command in (1, 2):
            self.notify("restore" if command == 1 else "exit")

    def close(self):
        self.hide()
        if self.previous_proc:
            self.set_proc(self.hwnd, -4, self.previous_proc)
            self.previous_proc = None
        if self.hicon:
            self.user.DestroyIcon(self.hicon)
            self.hicon = None
        self.callback = None
        self.notify = lambda event: None
        self.root = None
