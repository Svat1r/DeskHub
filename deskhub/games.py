"""Игры: счётчик времени, FPS-оверлей и статистика."""
import ctypes
import json
import secrets
import sys
import time
from ctypes import wintypes
from datetime import date, timedelta
from pathlib import Path

import psutil
from PySide6.QtCore import QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter
from PySide6.QtNetwork import QHostAddress, QTcpServer
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from .config import CONFIG_DIR
from .screenshot import GlassPanel
from .widgets.base import font, paint_glass

STATS_PATH = CONFIG_DIR / "games.json"
user32 = ctypes.windll.user32

# программы, которые бывают на весь экран, но играми не являются
NOT_GAMES = {
    "explorer.exe", "chrome.exe", "msedge.exe", "firefox.exe", "opera.exe", "browser.exe", "yandex.exe",
    "brave.exe", "vivaldi.exe", "comet.exe", "telegram.exe", "discord.exe", "obs64.exe", "vlc.exe",
    "mpc-hc64.exe", "mpc-be64.exe", "potplayermini64.exe", "applicationframehost.exe", "steamwebhelper.exe",
    "steam.exe", "epicgameslauncher.exe", "code.exe", "devenv.exe", "powerpnt.exe", "winword.exe",
    "excel.exe", "acrobat.exe", "lockapp.exe", "searchhost.exe", "startmenuexperiencehost.exe",
    "shellexperiencehost.exe", "textinputhost.exe", "python.exe", "pythonw.exe", "deskhub.exe",
    "claude.exe", "wallpaper64.exe", "wallpaper32.exe", "yandexmusic.exe", "spotify.exe", "nvidia app.exe",
}


def fmt_dur(sec: float, short=False) -> str:
    sec = int(sec)
    h, m = sec // 3600, sec % 3600 // 60
    if short:
        return f"{h}:{m:02d}:{sec % 60:02d}" if h else f"{m}:{sec % 60:02d}"
    if h:
        return f"{h} ч {m} мин"
    return f"{m} мин" if m else f"{sec} с"


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", _RECT), ("rcWork", _RECT), ("dwFlags", wintypes.DWORD)]


def foreground_info():
    """(hwnd, pid, exe, title, fullscreen) активного окна."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        exe = psutil.Process(pid.value).name()
    except (psutil.Error, ValueError):
        return None
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    cls = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, cls, 64)
    if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd"):
        return None
    r = _RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    mon = user32.MonitorFromWindow(hwnd, 2)
    mi = _MONITORINFO(cbSize=ctypes.sizeof(_MONITORINFO))
    user32.GetMonitorInfoW(mon, ctypes.byref(mi))
    m = mi.rcMonitor
    fullscreen = r.left <= m.left and r.top <= m.top and r.right >= m.right and r.bottom >= m.bottom
    return hwnd, pid.value, exe, buf.value, fullscreen


class GameTracker(QObject):
    """Раз в 2 секунды смотрит на активное окно и копит время в играх."""

    game_changed = Signal(object)  # dict | None

    def __init__(self):
        super().__init__()
        self.stats = self._load()
        self.current = None  # {"exe", "name", "pid", "start"}
        self._last_tick = time.monotonic()
        self._dirty = 0
        self.timer = QTimer(self, interval=2000)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    def _load(self):
        try:
            return json.loads(STATS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        STATS_PATH.write_text(json.dumps(self.stats, ensure_ascii=False, indent=1), encoding="utf-8")

    def is_game(self, exe: str, fullscreen: bool) -> bool:
        key = exe.lower()
        entry = self.stats.get(key)
        if entry is not None:
            return not entry.get("ignored")
        return fullscreen and key not in NOT_GAMES

    def tick(self):
        now = time.monotonic()
        dt = min(now - self._last_tick, 5)
        self._last_tick = now
        info = foreground_info()
        game = None
        if info:
            _, pid, exe, title, fullscreen = info
            if self.is_game(exe, fullscreen):
                key = exe.lower()
                entry = self.stats.setdefault(key, {"name": title or exe, "total": 0, "days": {}})
                if title and entry["name"] == exe:
                    entry["name"] = title[:40]
                entry["total"] += dt
                today = date.today().isoformat()
                entry["days"][today] = entry["days"].get(today, 0) + dt
                entry["last"] = today
                game = key
                if not self.current or self.current["exe"] != key:
                    self.current = {"exe": key, "name": entry["name"], "pid": pid, "start": time.time()}
                    self.game_changed.emit(self.current)
                self.current["pid"] = pid
                self._dirty += dt
        if game is None and self.current:
            self.current = None
            self.game_changed.emit(None)
            self.save()
        if self._dirty > 60:
            self._dirty = 0
            self.save()

    def ignore(self, exe: str):
        self.stats.setdefault(exe, {"name": exe, "total": 0, "days": {}})["ignored"] = True
        self.save()


class FpsService(QObject):
    """Запускает помощника с правами администратора и принимает от него FPS."""

    updated = Signal()
    state_changed = Signal(str)  # "off" / "starting" / "on" / "denied" / "missing"

    def __init__(self):
        super().__init__()
        self.data: dict[int, dict] = {}
        self.state = "off"
        self.server = QTcpServer(self)
        self.server.newConnection.connect(self._on_conn)
        self.conn = None
        self.token = ""
        self._buf = b""

    @staticmethod
    def presentmon_path() -> Path | None:
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
        for p in (base / "tools" / "PresentMon.exe", Path(sys.executable).parent / "tools" / "PresentMon.exe"):
            if p.exists():
                return p
        return None

    def _set(self, state):
        self.state = state
        self.state_changed.emit(state)

    def start(self):
        if self.state in ("starting", "on"):
            return
        pm = self.presentmon_path()
        if pm is None:
            self._set("missing")
            return
        if not self.server.isListening():
            self.server.listen(QHostAddress.LocalHost, 0)
        self.token = secrets.token_hex(16)
        port = self.server.serverPort()
        if getattr(sys, "frozen", False):
            exe, params, cwd = sys.executable, f'--fps-helper {port} {self.token} "{pm}"', None
        else:
            root = Path(__file__).resolve().parent.parent
            exe, params, cwd = sys.executable, f'-m deskhub --fps-helper {port} {self.token} "{pm}"', str(root)
        # runas -> Windows спросит разрешение администратора; окно помощника скрыто
        r = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, cwd, 0)
        if r <= 32:
            self._set("denied")
            return
        self._set("starting")

    def stop(self):
        if self.conn is not None:
            self.conn.disconnectFromHost()  # помощник увидит разрыв и закроет PresentMon
            self.conn = None
        self.data = {}
        self._set("off")

    def _on_conn(self):
        sock = self.server.nextPendingConnection()
        if sock is None:
            return
        self._buf = b""
        authed = {"ok": False}

        def ready():
            self._buf += bytes(sock.readAll())
            while b"\n" in self._buf:
                line, self._buf = self._buf.split(b"\n", 1)
                if not authed["ok"]:
                    if line.decode(errors="ignore").strip() == self.token:
                        authed["ok"] = True
                        self.conn = sock
                        self._set("on")
                    else:
                        sock.abort()
                        return
                    continue
                try:
                    self.data = {int(k): v for k, v in json.loads(line).items()}
                    self.updated.emit()
                except (ValueError, AttributeError):
                    pass

        def gone():
            if self.conn is sock:
                self.conn = None
                self.data = {}
                if self.state == "on":
                    self._set("off")

        sock.readyRead.connect(ready)
        sock.disconnected.connect(gone)

    def for_pid(self, pid: int):
        return self.data.get(pid)


class FpsOverlay(QWidget):
    """Компактная плашка поверх игры. Клики проходят сквозь неё."""

    def __init__(self, config, tracker: GameTracker, fps: FpsService):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.config, self.tracker, self.fps = config, tracker, fps
        self.resize(210, 112)
        psutil.cpu_percent(None)
        self.cpu = 0.0
        self.ram = 0.0
        self.timer = QTimer(self, interval=500)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

    @property
    def cfg(self):
        return self.config.data.setdefault("fps", {"enabled": True, "mode": "auto", "corner": "top-left"})

    def place(self):
        scr = QGuiApplication.primaryScreen().geometry()
        corner = self.cfg.get("corner", "top-left")
        x = scr.left() + 12 if "left" in corner else scr.right() - self.width() - 12
        y = scr.top() + 12 if corner.startswith("top") else scr.bottom() - self.height() - 60
        self.move(x, y)

    def refresh(self):
        enabled = self.cfg.get("enabled")
        game = self.tracker.current
        should_show = enabled and (self.cfg.get("mode") == "always" or game is not None)
        if should_show:
            self.cpu = psutil.cpu_percent(None)
            self.ram = psutil.virtual_memory().percent
            if not self.isVisible():
                self.place()
                self.show()
            self.update()
        elif self.isVisible():
            self.hide()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        paint_glass(p, QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 14)
        info = foreground_info()
        data = self.fps.for_pid(info[1]) if info else None
        x, y = 14, 10

        # FPS крупно
        if data:
            f = data["fps"]
            color = QColor(120, 230, 140) if f >= 60 else QColor(255, 210, 90) if f >= 30 else QColor(255, 110, 110)
            p.setPen(color)
            p.setFont(font(30, QFont.Bold))
            p.drawText(QRectF(x, y - 2, 110, 36), Qt.AlignLeft | Qt.AlignVCenter, f"{f:.0f}")
            p.setPen(QColor(255, 255, 255, 150))
            p.setFont(font(11, QFont.DemiBold))
            p.drawText(QRectF(x + 70, y + 2, 120, 14), Qt.AlignLeft, "FPS")
            p.setFont(font(11))
            p.drawText(QRectF(x + 70, y + 17, 130, 14), Qt.AlignLeft, f"1% {data['low1']:.0f} · {data['ft']:.1f} мс")
        else:
            p.setPen(QColor(255, 255, 255, 150))
            p.setFont(font(12, QFont.Medium))
            msg = {"off": "FPS: трей → «Запустить\nсчётчик FPS»", "starting": "FPS запускается…",
                   "denied": "FPS: нужны права\nадминистратора", "missing": "FPS: нет PresentMon"
                   }.get(self.fps.state, "FPS: ждём кадры…")
            p.drawText(QRectF(x, y, 190, 34), Qt.AlignLeft | Qt.AlignVCenter, msg)

        p.setPen(QColor(255, 255, 255, 210))
        p.setFont(font(12, QFont.Medium))
        p.drawText(QRectF(x, y + 40, 190, 18), Qt.AlignLeft, f"CPU {self.cpu:.0f}%   RAM {self.ram:.0f}%")
        game = self.tracker.current
        p.setPen(QColor(255, 255, 255, 150))
        if game:
            p.drawText(QRectF(x, y + 62, 190, 18), Qt.AlignLeft,
                       f"🎮 {fmt_dur(time.time() - game['start'], short=True)} в игре")
        else:
            p.drawText(QRectF(x, y + 62, 190, 18), Qt.AlignLeft, time.strftime("%H:%M"))
        p.end()


class GamesStats(GlassPanel):
    """Окно статистики: сколько времени в каких играх."""

    def __init__(self, tracker: GameTracker):
        super().__init__(Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("Статистика игр")
        self.tracker = tracker
        self.resize(560, 420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        head = QHBoxLayout()
        title = QLabel("🎮  Время в играх")
        title.setStyleSheet("color: white; font: 700 17px 'Segoe UI Variable Display';")
        close = QPushButton("✕")
        close.setFixedSize(30, 28)
        close.clicked.connect(self.close)
        head.addWidget(title)
        head.addStretch()
        head.addWidget(close)
        lay.addLayout(head)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Игра", "Сегодня", "7 дней", "Всего"])
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for i in (1, 2, 3):
            self.table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeToContents)
        self.table.setStyleSheet("""
            QTableWidget { background: transparent; color: white; border: none;
                           font: 14px 'Segoe UI Variable Display'; }
            QTableWidget::item { padding: 6px; border-bottom: 1px solid rgba(255,255,255,18); }
            QTableWidget::item:selected { background: rgba(120,170,255,70); }
            QHeaderView::section { background: transparent; color: rgba(255,255,255,140); border: none;
                                   font: 600 12px 'Segoe UI Variable Display'; padding: 4px; }
        """)
        lay.addWidget(self.table)

        bottom = QHBoxLayout()
        self.summary = QLabel()
        self.summary.setStyleSheet("color: rgba(255,255,255,160); font: 13px 'Segoe UI Variable Display';")
        rm = QPushButton("Это не игра")
        rm.setToolTip("Больше не считать выбранную программу игрой")
        rm.clicked.connect(self.remove_selected)
        bottom.addWidget(self.summary)
        bottom.addStretch()
        bottom.addWidget(rm)
        lay.addLayout(bottom)
        self.fill()

    def fill(self):
        today = date.today()
        week = {(today - timedelta(days=i)).isoformat() for i in range(7)}
        rows = []
        for exe, e in self.tracker.stats.items():
            if e.get("ignored"):
                continue
            days = e.get("days", {})
            rows.append((exe, e["name"], days.get(today.isoformat(), 0),
                         sum(v for d, v in days.items() if d in week), e.get("total", 0)))
        rows.sort(key=lambda r: r[4], reverse=True)
        self.table.setRowCount(len(rows))
        for i, (exe, name, t, w, total) in enumerate(rows):
            item = QTableWidgetItem(name)
            item.setData(Qt.UserRole, exe)
            item.setToolTip(exe)
            self.table.setItem(i, 0, item)
            for j, v in enumerate((t, w, total), 1):
                it = QTableWidgetItem(fmt_dur(v) if v else "—")
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, j, it)
        today_total = sum(r[2] for r in rows)
        self.summary.setText(f"Сегодня: {fmt_dur(today_total)}" if rows else
                             "Пока пусто — запусти игру на весь экран")

    def remove_selected(self):
        row = self.table.currentRow()
        if row < 0:
            return
        self.tracker.ignore(self.table.item(row, 0).data(Qt.UserRole))
        self.fill()
