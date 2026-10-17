"""Сторож «поверх всех окон».

В Windows 11 есть баг: после «Свернуть все» (Win+D) и обратного разворачивания обычные окна
иногда получают флаг «поверх всех». Из-за этого, например, просмотрщик фото Telegram
открывается под главным окном. Сторож запоминает, какие окна были обычными, и если сразу
после показа рабочего стола они вдруг стали «поверх всех» — снимает флаг.
"""
import ctypes
import os
import time
from ctypes import wintypes

from PySide6.QtCore import QObject, QTimer

user32 = ctypes.windll.user32
user32.GetTopWindow.restype = wintypes.HWND
user32.GetWindow.restype = wintypes.HWND
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_uint]

WS_EX_TOPMOST = 0x8
WS_EX_TOOLWINDOW = 0x80
WS_EX_TRANSPARENT = 0x20  # «прозрачные для мыши» оверлеи (Discord, NVIDIA) не трогаем
HWND_NOTOPMOST = wintypes.HWND(-2)
SWP_FLAGS = 0x0001 | 0x0002 | 0x0010  # NOSIZE | NOMOVE | NOACTIVATE
DESKTOP_CLASSES = {"Progman", "WorkerW"}
# окна, которым «поверх всех» положено всегда
SYSTEM_CLASSES = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Windows.UI.Core.CoreWindow", "CEF-OSC-WIDGET",
                  "NotifyIconOverflowWindow", "TopLevelWindowForOverflowXamlIsland", "XamlExplorerHostIslandWindow"}
WINDOW_AFTER_DESKTOP = 15.0  # сколько секунд после показа рабочего стола ловим баг


def _class(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, buf, 64)
    return buf.value


class _WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT), ("showCmd", wintypes.UINT),
                ("ptMinPosition", wintypes.POINT), ("ptMaxPosition", wintypes.POINT),
                ("rcNormalPosition", wintypes.RECT)]


def _size(hwnd):
    """Размер окна; у свёрнутого — размер, к которому оно развернётся."""
    if user32.IsIconic(hwnd):
        wp = _WINDOWPLACEMENT(length=ctypes.sizeof(_WINDOWPLACEMENT))
        user32.GetWindowPlacement(hwnd, ctypes.byref(wp))
        r = wp.rcNormalPosition
    else:
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.right - r.left, r.bottom - r.top


def _app_windows():
    """Окна программ (в т.ч. свёрнутые; без tool-окон, оверлеев и системных): hwnd -> topmost."""
    own_pid = os.getpid()
    out = {}
    h = user32.GetTopWindow(None)
    while h:
        if user32.IsWindowVisible(h):
            ex = user32.GetWindowLongW(h, -20)
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            w, hh = _size(h)
            big = w >= 400 and hh >= 300  # уведомления и мелкие окна не трогаем
            if (big and not ex & (WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT) and pid.value != own_pid
                    and user32.GetWindowTextLengthW(h) > 0 and _class(h) not in SYSTEM_CLASSES | DESKTOP_CLASSES):
                out[int(h)] = bool(ex & WS_EX_TOPMOST)
        h = user32.GetWindow(h, 2)  # GW_HWNDNEXT
    return out


def clear_topmost(hwnd: int):
    user32.SetWindowPos(wintypes.HWND(hwnd), HWND_NOTOPMOST, 0, 0, 0, 0, SWP_FLAGS)


class TopmostGuard(QObject):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.normal: set[int] = set()  # окна, которые точно были обычными
        self.desktop_seen = 0.0
        self.timer = QTimer(self, interval=700)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    @property
    def enabled(self) -> bool:
        return self.config.data.get("topmost_guard", True)

    def tick(self):
        if not self.enabled:
            return
        fg = user32.GetForegroundWindow()
        if fg and _class(fg) in DESKTOP_CLASSES:
            self.desktop_seen = time.monotonic()
        wins = _app_windows()
        recent = time.monotonic() - self.desktop_seen < WINDOW_AFTER_DESKTOP
        for hwnd, topmost in wins.items():
            if not topmost:
                self.normal.add(hwnd)
            elif recent and hwnd in self.normal:
                clear_topmost(hwnd)  # баг Win+D: окно было обычным, а стало «поверх всех»
        self.normal &= set(wins) | {h for h in self.normal if user32.IsWindow(h)}

    def fix_all(self) -> int:
        """Снять «поверх всех» со всех окон программ (кроме системных). Возвращает количество."""
        n = 0
        for hwnd, topmost in _app_windows().items():
            if topmost:
                clear_topmost(hwnd)
                self.normal.add(hwnd)
                n += 1
        return n
