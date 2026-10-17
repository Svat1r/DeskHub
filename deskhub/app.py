"""Точка входа: значок в трее, виджеты, скриншотер, ИИ-ассистент, игры, горячие клавиши."""
import ctypes
import sys
import winreg
from ctypes import wintypes

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QAction, QActionGroup, QColor, QGuiApplication, QIcon, QLinearGradient, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon, QWidget

from .assistant import MODELS, Assistant
from .config import Config
from .games import FpsOverlay, FpsService, GamesStats, GameTracker
from .guard import TopmostGuard
from .history import History, HistoryWindow
from . import __version__
from .recorder import Recorder
from .updater import Updater
from .media_service import MediaService
from .screenshot import ScreenshotTool
from .toast import toast
from .widgets.clock import ClockWidget
from .widgets.music import MusicPopup, MusicWidget
from .widgets.weather import WeatherWidget

WM_HOTKEY = 0x0312
MODS = {"ctrl": 0x2, "alt": 0x1, "shift": 0x4, "win": 0x8}
MOD_NOREPEAT = 0x4000
CORNERS = [("top-left", "Сверху слева"), ("top-center", "Сверху по центру"), ("top-right", "Сверху справа"),
           ("bottom-left", "Снизу слева"), ("bottom-right", "Снизу справа")]
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

# тёмное оформление меню и диалогов в стиле виджетов
APP_CSS = """
QMenu { background: #1b1e27; color: #eef0f6; border: 1px solid #343846; border-radius: 10px; padding: 6px;
        font: 13px 'Segoe UI Variable Display'; }
QMenu::item { padding: 7px 26px 7px 26px; border-radius: 6px; }
QMenu::item:selected { background: #2f3a52; }
QMenu::item:disabled { color: #6b7080; }
QMenu::separator { height: 1px; background: #2c303c; margin: 5px 8px; }
QMenu::section { color: #8a90a2; padding: 8px 10px 4px 10px; font: 600 11px 'Segoe UI Variable Display'; }
QMenu::indicator { width: 14px; height: 14px; left: 6px; }
QMessageBox, QInputDialog { background: #1b1e27; }
QMessageBox QLabel, QInputDialog QLabel, QCheckBox { color: #eef0f6; font: 13px 'Segoe UI Variable Display'; }
QDialog QPushButton { background: #2a2f3d; color: white; border: 1px solid #3a4052; border-radius: 8px;
                      padding: 6px 16px; font: 600 13px 'Segoe UI Variable Display'; min-width: 70px; }
QDialog QPushButton:hover { background: #35405a; }
QDialog QPushButton:default { background: #4a78d6; border: none; }
QLineEdit { background: #10131a; color: white; border: 1px solid #343846; border-radius: 8px; padding: 6px 8px;
            font: 13px 'Segoe UI Variable Display'; selection-background-color: #4a78d6; }
QToolTip { background: #1b1e27; color: #eef0f6; border: 1px solid #343846; padding: 4px 8px; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 4px 2px; }
QScrollBar::handle:vertical { background: rgba(255,255,255,60); border-radius: 3px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""

# действие -> (название, сочетание по умолчанию, запасные варианты)
HOTKEYS = {
    "popup": ("Показать плеер", "Alt+Shift+M", ["Ctrl+Alt+P", "Ctrl+Alt+K"]),
    "shot": ("Скриншот области", "Alt+Shift+S", ["Ctrl+Alt+S", "Ctrl+Shift+F12"]),
    "ocr": ("Текст с экрана", "Alt+Shift+T", ["Ctrl+Alt+T", "Ctrl+Shift+F11"]),
    "ai": ("ИИ-ассистент", "Alt+Shift+A", ["Ctrl+Alt+A", "Ctrl+Shift+F10"]),
    "fps": ("FPS-оверлей вкл/выкл", "Alt+Shift+F", ["Ctrl+Alt+F", "Ctrl+Shift+F9"]),
    "record": ("Запись экрана", "Alt+Shift+R", ["Ctrl+Alt+R", "Ctrl+Shift+F8"]),
    "history": ("История скриншотов", "Alt+Shift+H", ["Ctrl+Alt+H", "Ctrl+Shift+F7"]),
}


def parse_hotkey(text: str):
    mods, vk = 0, 0
    for part in text.lower().replace(" ", "").split("+"):
        if part in MODS:
            mods |= MODS[part]
        elif len(part) == 1:
            vk = ord(part.upper())
        elif part.startswith("f") and part[1:].isdigit():
            vk = 0x70 + int(part[1:]) - 1
    return mods | MOD_NOREPEAT, vk


class HotkeyWindow(QWidget):
    """Невидимое окно, которое получает WM_HOTKEY от Windows."""

    def __init__(self):
        super().__init__()
        self.callbacks = {}
        self.winId()  # создать нативное окно

    def register(self, hk_id: int, text: str, callback) -> bool:
        mods, vk = parse_hotkey(text)
        ctypes.windll.user32.UnregisterHotKey(int(self.winId()), hk_id)
        if not vk or not ctypes.windll.user32.RegisterHotKey(int(self.winId()), hk_id, mods, vk):
            return False
        self.callbacks[hk_id] = callback
        return True

    def nativeEvent(self, event_type, message):
        if bytes(event_type) == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam in self.callbacks:
                self.callbacks[msg.wParam]()
                return True, 0
        return False, 0


def make_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, 64, 64)
    g.setColorAt(0, QColor(120, 190, 255))
    g.setColorAt(1, QColor(190, 120, 255))
    p.setBrush(g)
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(4, 4, 56, 56), 16, 16)
    p.setBrush(QColor(255, 255, 255, 235))
    for x, y, w, h in ((14, 14, 16, 16), (34, 14, 16, 16), (14, 34, 36, 16)):
        p.drawRoundedRect(QRectF(x, y, w, h), 5, 5)
    p.end()
    return QIcon(pm)


# --- автозапуск ---------------------------------------------------------------------
def autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, "DeskHub")
            return True
    except OSError:
        return False


def set_autostart(enable: bool):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enable:
            winreg.SetValueEx(k, "DeskHub", 0, winreg.REG_SZ, f'"{sys.executable}" --autostart')
        else:
            try:
                winreg.DeleteValue(k, "DeskHub")
            except FileNotFoundError:
                pass


class DeskHub:
    def __init__(self, app: QApplication):
        self.app = app
        self.config = Config()
        self.media = MediaService()

        self.widgets = {
            "clock": ClockWidget(self.config),
            "weather": WeatherWidget(self.config),
            "music": MusicWidget(self.config, self.media),
        }
        self.popup = MusicPopup(self.config, self.media)
        self.popup.desktop_widget = self.widgets["music"]
        self.history = History()
        self.history_window = None
        self.shots = ScreenshotTool(self.config, self.history)
        self.recorder = Recorder(self.config)
        self.shots.record_requested.connect(self.recorder.start)
        self.assistant = Assistant(self.config)
        self.shots.ask_ai.connect(lambda text: self.assistant.open(text))
        self.tracker = GameTracker()
        self.fps = FpsService()
        self.overlay = FpsOverlay(self.config, self.tracker, self.fps)
        self.fps.state_changed.connect(self.on_fps_state)
        self.stats_window = None
        self.guard = TopmostGuard(self.config)
        self.updater = Updater(self.config, self.quit)

        # раскладка по умолчанию — столбиком у правого края
        scr = QGuiApplication.primaryScreen().availableGeometry()
        y = scr.top() + 40
        for name, w in self.widgets.items():
            w.place(QPoint(scr.right() - w.width() - 40, y))
            y += w.height() + 16
            w.hide_requested.connect(lambda n: self.set_enabled(n, False))
            if self.config.widget(name)["enabled"]:
                w.show()

        self.hotkeys = HotkeyWindow()
        self.actions = {
            "popup": self.popup_now,
            "shot": lambda: self.shots.start("menu"),
            "ocr": lambda: self.shots.start("text"),
            "ai": self.assistant.trigger,
            "fps": self.toggle_fps,
            "record": self.toggle_record,
            "history": self.show_history,
        }
        self.register_hotkeys()

        self.tray = QSystemTrayIcon(make_icon(), app)
        self.tray.setToolTip(f"DeskHub {__version__}")
        self.menu = QMenu()
        self.menu.aboutToShow.connect(self.build_menu)
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self.on_tray)
        self.tray.show()

        # FPS запрашивает права администратора — включаем его только если пользователь этого хотел
        if self.overlay.cfg.get("enabled") and self.overlay.cfg.get("helper"):
            self.fps.start()

    # --- горячие клавиши ------------------------------------------------------------
    def register_hotkeys(self):
        hk = self.config.data.setdefault("hotkeys", {})
        # совместимость: раньше сочетание плеера хранилось в music_popup
        hk.setdefault("popup", self.config.data["music_popup"].get("hotkey", "Alt+Shift+M"))
        self.hotkey_state = {}
        changed = False
        for i, (action, (_, default, spare)) in enumerate(HOTKEYS.items(), start=1):
            current = hk.get(action, default)
            for combo in dict.fromkeys([current, default, *spare]):
                if self.hotkeys.register(i, combo, self.actions[action]):
                    self.hotkey_state[action] = combo
                    if combo != current:
                        hk[action] = combo
                        changed = True
                    break
            else:
                self.hotkey_state[action] = None
        if changed:
            self.config.save()

    def hk_label(self, action):
        return self.hotkey_state.get(action) or "занято"

    # --- действия ----------------------------------------------------------------------
    def popup_now(self):
        # по горячей клавише показываем, даже если автопоказ выключен
        self.popup.popup(manual=True)

    def toggle_fps(self):
        cfg = self.overlay.cfg
        cfg["enabled"] = not cfg.get("enabled")
        self.config.save()
        if cfg["enabled"]:
            self.ensure_fps_helper()
            toast("FPS-оверлей включён" + (" — появится в игре" if cfg.get("mode") == "auto" else ""))
        else:
            self.fps.stop()
            toast("FPS-оверлей выключен")
        self.overlay.refresh()

    def toggle_record(self):
        if self.recorder.active:
            self.recorder.stop()
        else:
            self.shots.start("record")

    def show_history(self):
        if self.history_window is not None:
            try:
                self.history_window.show()
                self.history_window.raise_()
                self.history_window.activateWindow()
                return
            except RuntimeError:  # окно уже закрыто и удалено
                pass
        self.history_window = HistoryWindow(self.history)
        scr = QGuiApplication.primaryScreen().availableGeometry()
        self.history_window.move(scr.center() - self.history_window.rect().center())
        self.history_window.show()
        self.history_window.activateWindow()
        self.history_window.search.setFocus()

    def ensure_fps_helper(self):
        cfg = self.overlay.cfg
        if not cfg.get("helper"):
            r = QMessageBox.question(
                None, "FPS в играх",
                "Чтобы видеть FPS других программ, Windows требует права администратора.\n"
                "Сейчас появится запрос — нажми «Да».\n\n"
                "Без этого оверлей покажет только CPU, RAM и время в игре.",
                QMessageBox.Ok | QMessageBox.Cancel)
            if r != QMessageBox.Ok:
                return
            cfg["helper"] = True
            self.config.save()
        self.fps.start()

    def on_fps_state(self, state):
        if state == "denied":
            toast("FPS: права администратора не выданы", 3)
        elif state == "missing":
            toast("FPS: не найден PresentMon.exe", 3)

    def show_stats(self):
        if self.stats_window is not None:
            try:
                self.stats_window.fill()
                self.stats_window.show()
                self.stats_window.raise_()
                self.stats_window.activateWindow()
                return
            except RuntimeError:
                pass
        self.tracker.save()
        self.stats_window = GamesStats(self.tracker)
        scr = QGuiApplication.primaryScreen().availableGeometry()
        self.stats_window.move(scr.center() - self.stats_window.rect().center())
        self.stats_window.show()
        self.stats_window.activateWindow()

    def on_tray(self, reason):
        if reason == QSystemTrayIcon.Trigger:  # клик по значку — поднять виджеты
            for name, w in self.widgets.items():
                if self.config.widget(name)["enabled"]:
                    w.show()
                    w.raise_()

    def set_enabled(self, name: str, value: bool):
        self.config.widget(name)["enabled"] = value
        self.config.save()
        self.widgets[name].setVisible(value)

    # --- меню в трее -----------------------------------------------------------------
    def _check(self, menu, text, checked, slot):
        a = QAction(text, menu, checkable=True, checked=checked)
        a.toggled.connect(slot)
        menu.addAction(a)
        return a

    def _radio(self, menu, items, current, slot):
        grp = QActionGroup(menu)
        for key, label in items:
            a = QAction(label, menu, checkable=True, checked=current == key)
            a.triggered.connect(lambda _=False, k=key: slot(k))
            grp.addAction(a)
            menu.addAction(a)

    def build_menu(self):
        m = self.menu
        m.clear()
        cfg = self.config.data

        def saver(d, key, after=None):
            def f(v):
                d[key] = v
                self.config.save()
                if after:
                    after()
            return f

        # --- инструменты ---
        m.addSection("Инструменты")
        m.addAction(f"Скриншот области\t{self.hk_label('shot')}").triggered.connect(lambda: self.shots.start("menu"))
        m.addAction(f"Текст с экрана\t{self.hk_label('ocr')}").triggered.connect(lambda: self.shots.start("text"))
        rec = "Остановить запись" if self.recorder.active else "Запись экрана"
        m.addAction(f"{rec}\t{self.hk_label('record')}").triggered.connect(self.toggle_record)
        m.addAction(f"История скриншотов\t{self.hk_label('history')}").triggered.connect(self.show_history)
        m.addAction(f"ИИ-ассистент\t{self.hk_label('ai')}").triggered.connect(lambda: self.assistant.open())

        ai = m.addMenu("Настройки ИИ")
        ai.addAction("API-ключ…").triggered.connect(lambda: self.assistant.ask_key())
        ai.addSeparator()
        self._radio(ai, list(MODELS.items()), self.assistant.model, self.assistant.set_model)

        # --- игры ---
        m.addSection("Игры")
        fc = self.overlay.cfg
        self._check(m, f"FPS-оверлей\t{self.hk_label('fps')}", bool(fc.get("enabled")),
                    lambda v: self.toggle_fps() if v != bool(fc.get("enabled")) else None)
        fm = m.addMenu("Настройки оверлея")
        self._radio(fm, [("auto", "Показывать только в играх"), ("always", "Показывать всегда")],
                    fc.get("mode", "auto"), saver(fc, "mode", self.overlay.refresh))
        fm.addSeparator()
        pos = fm.addMenu("Положение")
        self._radio(pos, [c for c in CORNERS if c[0] != "top-center"], fc.get("corner", "top-left"),
                    saver(fc, "corner", self.overlay.place))
        if self.fps.state != "on":
            fm.addSeparator()
            fm.addAction("Запустить счётчик FPS (права админа)…").triggered.connect(self.ensure_fps_helper)
        m.addAction("Статистика игр…").triggered.connect(self.show_stats)

        # --- виджеты ---
        m.addSection("Виджеты")
        for name, w in self.widgets.items():
            self._check(m, w.title, self.config.widget(name)["enabled"],
                        lambda v, n=name: self.set_enabled(n, v))
        layer = m.addMenu("Поверх всех окон")
        for name, w in self.widgets.items():
            self._check(layer, w.title, bool(self.config.widget(name).get("on_top")),
                        lambda v, ww=w: ww.set_on_top(v))
        self._check(m, "Закрепить положение", cfg["locked"], saver(cfg, "locked"))

        pc = cfg["music_popup"]
        pm = m.addMenu("Всплывающий плеер")
        self._check(pm, "Включён", pc["enabled"], saver(pc, "enabled"))
        self._check(pm, "При смене трека", pc["on_track_change"], saver(pc, "on_track_change"))
        self._check(pm, "При паузе / воспроизведении", pc["on_play_pause"], saver(pc, "on_play_pause"))
        pos_menu = pm.addMenu("Положение")
        self._radio(pos_menu, CORNERS, pc["corner"], lambda k: (saver(pc, "corner")(k), self.popup_now()))
        dur_menu = pm.addMenu("Время показа")
        self._radio(dur_menu, [(s, f"{s} сек") for s in (2, 4, 6, 10)], pc["seconds"], saver(pc, "seconds"))
        pm.addAction(f"Показать сейчас\t{self.hk_label('popup')}").triggered.connect(self.popup_now)
        m.addAction("Город для погоды…").triggered.connect(self.widgets["weather"].ask_city)

        # --- исправления Windows ---
        m.addSection("Windows")
        self._check(m, "Чинить «поверх всех» после Win+D", self.guard.enabled, saver(cfg, "topmost_guard"))
        m.addAction("Снять «поверх всех» со всех окон").triggered.connect(
            lambda: toast(f"Исправлено окон: {self.guard.fix_all()}"))

        # --- общее ---
        m.addSeparator()
        frozen = getattr(sys, "frozen", False)
        a = self._check(m, "Запускать вместе с Windows", frozen and autostart_enabled(), self.toggle_autostart)
        a.setEnabled(frozen)
        upd = m.addMenu(f"Обновления (версия {__version__})")
        upd.addAction("Проверить сейчас").triggered.connect(lambda: self.updater.check(manual=True))
        self._check(upd, "Проверять автоматически", self.updater.cfg.get("auto_check", True),
                    saver(self.updater.cfg, "auto_check"))
        m.addAction("Выход").triggered.connect(self.quit)

    def toggle_autostart(self, value: bool):
        try:
            set_autostart(value)
            toast("Автозапуск включён" if value else "Автозапуск выключен")
        except OSError as e:
            toast(f"Не удалось изменить автозапуск: {e}", 3)

    def quit(self):
        self.tracker.save()
        if self.recorder.active:
            self.recorder.stop()
        self.fps.stop()
        self.media.stop()
        self.tray.hide()
        self.app.quit()


def uninstall():
    """Удаление программы (вызывается из «Установленных приложений»)."""
    import os
    import shutil
    import subprocess
    from pathlib import Path

    from PySide6.QtWidgets import QCheckBox

    app = QApplication(sys.argv)
    app.setStyleSheet(APP_CSS)
    box = QMessageBox(QMessageBox.Question, "Удаление DeskHub", "Удалить DeskHub с компьютера?",
                      QMessageBox.Yes | QMessageBox.No)
    data_cb = QCheckBox("Удалить также настройки, историю скриншотов и статистику игр")
    box.setCheckBox(data_cb)
    if box.exec() != QMessageBox.Yes:
        return
    subprocess.run(["taskkill", "/F", "/IM", "DeskHub.exe", "/FI", f"PID ne {os.getpid()}"], capture_output=True,
                   creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        set_autostart(False)
    except OSError:
        pass
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall\DeskHub")
    except OSError:
        pass
    for link in (Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/DeskHub.lnk",
                 Path(os.environ["USERPROFILE"]) / "Desktop/DeskHub.lnk"):
        link.unlink(missing_ok=True)
    if data_cb.isChecked():
        shutil.rmtree(Path(os.environ["APPDATA"]) / "DeskHub", ignore_errors=True)
        shutil.rmtree(Path(os.environ["LOCALAPPDATA"]) / "DeskHub", ignore_errors=True)
    # саму папку программы удаляем после выхода — пока exe работает, он занят
    install_dir = Path(sys.executable).parent
    subprocess.Popen(f'cmd /c timeout /t 2 /nobreak >nul & rmdir /s /q "{install_dir}"', shell=True,
                     creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS)
    QMessageBox.information(None, "DeskHub", "DeskHub удалён.")


def selftest():
    """Проверка, что все модули работают в собранной программе: пишет отчёт в %TEMP%\\deskhub_selftest.txt."""
    import os
    import traceback
    from pathlib import Path

    lines = [f"DeskHub {__version__}"]

    def check(name, fn):
        try:
            lines.append(f"OK   {name}: {fn()}")
        except Exception:
            lines.append(f"FAIL {name}: {traceback.format_exc(limit=2).strip().splitlines()[-1]}")

    app = QApplication(sys.argv)  # noqa: F841 — нужен для Qt-объектов
    from . import ocr, volume
    from .recorder import ffmpeg_path
    from PySide6.QtGui import QImage
    check("звук (pycaw)", lambda: volume.change("", 0.0))
    check("ffmpeg", lambda: Path(ffmpeg_path()).exists())
    check("OCR", lambda: repr(ocr.recognize(QImage(40, 20, QImage.Format_ARGB32))))
    check("PresentMon", lambda: FpsService.presentmon_path())
    check("anthropic", lambda: __import__("anthropic").__version__)
    check("winrt media", lambda: bool(__import__("winrt.windows.media.control", fromlist=["x"])))
    Path(os.environ.get("TEMP", ".")).joinpath("deskhub_selftest.txt").write_text("\n".join(lines), encoding="utf-8")


def main():
    if "--selftest" in sys.argv:
        selftest()
        return
    if "--uninstall" in sys.argv:
        uninstall()
        return
    if "--fps-helper" in sys.argv:
        from .fps_helper import run
        i = sys.argv.index("--fps-helper")
        run(int(sys.argv[i + 1]), sys.argv[i + 2], sys.argv[i + 3])
        return

    # только один экземпляр программы
    ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\DeskHubSingleton")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        return

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("DeskHub")
    app.setStyleSheet(APP_CSS)
    hub = DeskHub(app)

    # при первом запуске .exe включаем автозапуск (потом можно выключить в меню)
    if getattr(sys, "frozen", False) and not hub.config.data.get("autostart_asked"):
        hub.config.data["autostart_asked"] = True
        hub.config.save()
        try:
            set_autostart(True)
            toast("DeskHub запущен и будет стартовать вместе с Windows.\nНастройки — значок в трее", 5)
        except OSError:
            pass
    elif "--autostart" not in sys.argv:
        toast("DeskHub запущен — значок в трее", 3)
    sys.exit(app.exec())
