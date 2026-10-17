"""Запись выделенной области экрана в MP4 или GIF (через ffmpeg из imageio-ffmpeg)."""
import os
import queue
import subprocess
import threading
import time
from pathlib import Path

from PySide6.QtCore import QMimeData, QObject, QPointF, QRect, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QCursor, QDesktopServices, QGuiApplication, QImage, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QWidget

from .screenshot import BUTTON_CSS
from .toast import toast

VIDEO_DIR = Path(os.environ.get("USERPROFILE", Path.home())) / "Videos" / "DeskHub"
FPS = 24
MAX_SECONDS = 10 * 60


def ffmpeg_path() -> str | None:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


class _Frame(QWidget):
    """Красная рамка вокруг записываемой области (сама в запись не попадает — она снаружи)."""

    PAD = 3

    def __init__(self, rect: QRect):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setGeometry(rect.adjusted(-self.PAD, -self.PAD, self.PAD, self.PAD))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setPen(QPen(QColor(255, 70, 70, 230), 2, Qt.DashLine))
        p.drawRect(QRectF(self.rect()).adjusted(1, 1, -1, -1))
        p.end()


class _Panel(QWidget):
    """Плашка управления: ● 00:12 · MP4/GIF · Стоп."""

    stop = Signal()
    cancel = Signal()

    def __init__(self, rect: QRect, fmt: str):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setObjectName("panel")
        self.setStyleSheet(BUTTON_CSS + """
            QWidget#inner { background: rgba(20,22,30,240); border-radius: 12px; }
            QLabel { color: white; font: 600 14px 'Segoe UI Variable Display'; }
            QPushButton#fmt:checked { background: rgba(120,170,255,200); border: none; }
            QPushButton#stop { background: rgba(255,80,80,220); border: none; }
            QPushButton#stop:hover { background: rgba(255,110,110,240); }
        """)
        inner = QWidget(self, objectName="inner")
        inner.setAttribute(Qt.WA_StyledBackground)
        lay = QHBoxLayout(inner)
        lay.setContentsMargins(12, 6, 8, 6)
        lay.setSpacing(6)
        self.dot = QLabel("●")
        self.dot.setStyleSheet("color: #ff5050; font-size: 14px;")
        self.time = QLabel("0:00")
        lay.addWidget(self.dot)
        lay.addWidget(self.time)
        lay.addSpacing(6)
        self.fmt_buttons = {}
        for f in ("MP4", "GIF"):
            b = QPushButton(f, objectName="fmt", checkable=True, checked=f.lower() == fmt)
            b.clicked.connect(lambda _=False, ff=f.lower(): self.set_fmt(ff))
            self.fmt_buttons[f.lower()] = b
            lay.addWidget(b)
        self.fmt = fmt
        stop = QPushButton("■  Стоп", objectName="stop")
        stop.setCursor(Qt.PointingHandCursor)
        stop.clicked.connect(self.stop)
        cancel = QPushButton("✕")
        cancel.setToolTip("Отменить запись")
        cancel.clicked.connect(self.cancel)
        lay.addWidget(stop)
        lay.addWidget(cancel)
        inner.adjustSize()
        self.resize(inner.size())
        scr = QGuiApplication.screenAt(rect.center()) or QGuiApplication.primaryScreen()
        g = scr.geometry()
        x = min(max(rect.right() - self.width(), g.left() + 6), g.right() - self.width() - 6)
        y = rect.bottom() + 10 if rect.bottom() + self.height() + 16 < g.bottom() else rect.top() - self.height() - 10
        if y < g.top():  # область на весь экран — плашку в угол поверх
            y = g.top() + 10
        self.move(x, y)

    def set_fmt(self, fmt):
        self.fmt = fmt
        for k, b in self.fmt_buttons.items():
            b.setChecked(k == fmt)

    def tick(self, seconds: float):
        s = int(seconds)
        self.time.setText(f"{s // 60}:{s % 60:02d}")
        self.dot.setVisible(s % 2 == 0)


def _draw_cursor(p: QPainter, pos: QPointF):
    arrow = QPolygonF([QPointF(0, 0), QPointF(0, 17), QPointF(4.5, 13), QPointF(7.5, 20), QPointF(10, 19),
                       QPointF(7, 12), QPointF(12.5, 12)])
    p.save()
    p.translate(pos)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(0, 0, 0), 1.2))
    p.setBrush(QColor(255, 255, 255))
    p.drawPolygon(arrow)
    p.restore()


class Recorder(QObject):
    busy_changed = Signal(bool)
    _finished = Signal(str, str)  # путь, ошибка

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.active = False
        self._finished.connect(self._on_finished)

    def start(self, rect: QRect):
        if self.active:
            return
        exe = ffmpeg_path()
        if not exe:
            toast("Запись недоступна: не найден ffmpeg", 3)
            return
        screen = QGuiApplication.screenAt(rect.center()) or QGuiApplication.primaryScreen()
        self.screen = screen
        self.rect = rect.intersected(screen.geometry())
        dpr = screen.devicePixelRatio()
        # libx264 требует чётные размеры
        self.w = int(self.rect.width() * dpr) // 2 * 2
        self.h = int(self.rect.height() * dpr) // 2 * 2
        if self.w < 16 or self.h < 16:
            toast("Слишком маленькая область", 2)
            return
        VIDEO_DIR.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H-%M-%S")
        self.mp4 = VIDEO_DIR / f"Запись {stamp}.mp4"
        self.proc = subprocess.Popen(
            [exe, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{self.w}x{self.h}",
             "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(self.mp4)],
            stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
        self.frames = queue.Queue(maxsize=FPS * 3)
        self.writer = threading.Thread(target=self._write, daemon=True)
        self.writer.start()

        self.frame = _Frame(self.rect)
        self.frame.show()
        self.panel = _Panel(self.rect, self.config.data.get("record_format", "mp4"))
        self.panel.stop.connect(self.stop)
        self.panel.cancel.connect(lambda: self.stop(cancel=True))
        self.panel.show()

        self.active = True
        self.t0 = time.monotonic()
        self.sent = 0
        self.last = None
        self.timer = QTimer(self, interval=int(1000 / FPS), timerType=Qt.PreciseTimer)
        self.timer.timeout.connect(self._grab)
        self.timer.start()
        self.busy_changed.emit(True)
        toast("Запись началась — повторное нажатие горячей клавиши остановит", 2)

    def _grab(self):
        elapsed = time.monotonic() - self.t0
        self.panel.tick(elapsed)
        if elapsed > MAX_SECONDS:
            self.stop()
            return
        g = self.screen.geometry()
        pm = self.screen.grabWindow(0, self.rect.x() - g.x(), self.rect.y() - g.y(),
                                    self.rect.width(), self.rect.height())
        img = pm.toImage().convertToFormat(QImage.Format_ARGB32)
        if img.width() != self.w or img.height() != self.h:
            img = img.copy(0, 0, self.w, self.h) if img.width() >= self.w and img.height() >= self.h else \
                img.scaled(self.w, self.h)
        # курсор рисуем сами — в снимок экрана он не попадает
        cur = QCursor.pos()
        if self.rect.contains(cur):
            p = QPainter(img)
            dpr = self.w / self.rect.width()
            _draw_cursor(p, QPointF((cur.x() - self.rect.x()) * dpr, (cur.y() - self.rect.y()) * dpr))
            p.end()
        data = bytes(img.constBits())[: self.w * self.h * 4]
        # держим ровную скорость: если таймер опоздал — дублируем кадр
        due = int(elapsed * FPS) + 1
        while self.sent < due:
            try:
                self.frames.put_nowait(data)
            except queue.Full:
                break
            self.sent += 1

    def _write(self):
        while True:
            data = self.frames.get()
            if data is None:
                break
            try:
                self.proc.stdin.write(data)
            except OSError:
                break

    def stop(self, cancel: bool = False):
        if not self.active:
            return
        self.active = False
        self.timer.stop()
        fmt = self.panel.fmt
        self.config.data["record_format"] = fmt
        self.config.save()
        self.frame.close()
        self.panel.close()
        self.busy_changed.emit(False)
        if not cancel:
            toast("Сохраняю запись…", 10)
        threading.Thread(target=self._finish, args=(fmt, cancel), daemon=True).start()

    def _finish(self, fmt: str, cancel: bool):
        self.frames.put(None)
        self.writer.join(10)
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        err = self.proc.stderr.read().decode("utf-8", "replace")[-300:]
        self.proc.wait(60)
        if cancel:
            self.mp4.unlink(missing_ok=True)
            self._finished.emit("", "cancel")
            return
        if self.proc.returncode != 0 or not self.mp4.exists():
            self._finished.emit("", err or "ffmpeg завершился с ошибкой")
            return
        out = self.mp4
        if fmt == "gif":
            gif = self.mp4.with_suffix(".gif")
            vf = ("fps=15,scale='min(960,iw)':-2:flags=lanczos,split[a][b];"
                  "[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4")
            r = subprocess.run([ffmpeg_path(), "-y", "-loglevel", "error", "-i", str(self.mp4), "-vf", vf, str(gif)],
                               capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            if r.returncode == 0 and gif.exists():
                self.mp4.unlink(missing_ok=True)
                out = gif
        self._finished.emit(str(out), "")

    def _on_finished(self, path: str, err: str):
        if err == "cancel":
            toast("Запись отменена")
            return
        if err:
            toast(f"Не удалось сохранить запись\n{err[:120]}", 4)
            return
        # кладём файл в буфер обмена — можно сразу вставить в Telegram/Discord
        md = QMimeData()
        md.setUrls([QUrl.fromLocalFile(path)])
        QApplication.clipboard().setMimeData(md)
        size_mb = Path(path).stat().st_size / 1024 / 1024
        toast(f"Запись сохранена и скопирована ({size_mb:.1f} МБ)\n{Path(path).name}", 4,
              on_click=lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(VIDEO_DIR))))
