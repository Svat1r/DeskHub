"""Музыкальный виджет на рабочем столе + всплывающий оверлей поверх окон."""
import ctypes
import math
import time
from ctypes import wintypes

from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QPropertyAnimation, QRectF, Qt, QTimer
from PySide6.QtGui import (QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QImage, QLinearGradient, QPainter,
                           QPainterPath, QPixmap)
from PySide6.QtWidgets import QWidget

from .. import volume
from ..media_service import MediaService, MediaState
from .base import BaseWidget, font, paint_glass

APP_NAMES = {
    "yandex": "Яндекс Музыка", "spotify": "Spotify", "chrome": "Chrome", "msedge": "Edge",
    "firefox": "Firefox", "opera": "Opera", "telegram": "Telegram", "vlc": "VLC", "comet": "Comet",
    "zunemusic": "Медиаплеер", "applemusic": "Apple Music", "aimp": "AIMP", "foobar": "foobar2000",
    "soundcloud": "SoundCloud", "vk": "VK Музыка", "browser": "Яндекс Браузер",
}


def app_label(app_id: str) -> str:
    low = app_id.lower()
    for key, label in APP_NAMES.items():
        if key in low:
            return label
    return app_id.split("!")[-1].split("\\")[-1].removesuffix(".exe")[:24]


def fmt_time(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}"


def _skip_icon(p: QPainter, c: QPointF, direction: int, size: float = 1.0):
    """Иконка «следующий/предыдущий»: два скруглённых треугольника и черта."""
    s = 6.5 * size
    path = QPainterPath()
    for off in (-s * 0.95, s * 0.15):
        x0 = c.x() + off * direction
        tri = QPainterPath()
        tri.moveTo(x0, c.y() - s)
        tri.lineTo(x0 + s * 1.15 * direction, c.y())
        tri.lineTo(x0, c.y() + s)
        tri.closeSubpath()
        path.addPath(tri)
    bar_x = c.x() + s * 1.35 * direction
    path.addRoundedRect(QRectF(bar_x - (0 if direction > 0 else 2.2 * size), c.y() - s, 2.2 * size, 2 * s), 1, 1)
    p.drawPath(path)


class MusicPainter:
    """Общая отрисовка карточки трека для виджета и оверлея."""

    W, H = 420, 152
    ART = 120  # сторона обложки
    PAD = 16

    def init_music(self, media: MediaService):
        self.media = media
        self.state: MediaState = media.state
        self.cover: QPixmap | None = None
        self.tint: QColor | None = None
        self.t0 = time.monotonic()
        self.eq = [0.2] * 4
        self.hover_btn = None
        self.pressed_btn = None
        self.press_anim = {}  # кнопка -> время нажатия (для «пружинки»)
        self.hover_bar = False
        self.marquee_t0 = time.monotonic()
        self.vol_osd = None  # (громкость 0..1, источник, время)
        media.changed.connect(self.on_media)
        self.on_media(media.state)
        self.setMouseTracking(True)

    def on_media(self, st: MediaState):
        if st.thumbnail != getattr(self.state, "thumbnail", None) or self.cover is None:
            self.cover, self.tint = None, None
            if st.thumbnail:
                img = QImage.fromData(st.thumbnail)
                if not img.isNull():
                    self.cover = QPixmap.fromImage(img)
                    self.tint = img.scaled(1, 1, Qt.IgnoreAspectRatio, Qt.SmoothTransformation).pixelColor(0, 0)
        if (st.title, st.artist) != (getattr(self.state, "title", None), getattr(self.state, "artist", None)):
            self.marquee_t0 = time.monotonic()
        self.state = st
        self.update()

    # --- геометрия -------------------------------------------------------------
    def text_x(self):
        return self.PAD + self.ART + 16

    def text_w(self):
        return self.W - self.text_x() - self.PAD

    def bar_rect(self) -> QRectF:
        return QRectF(self.text_x(), 88, self.text_w(), 4)

    def buttons(self):
        cy = self.H - self.PAD - 18
        cx = self.text_x() + self.text_w() / 2
        return {
            "prev": QRectF(cx - 64, cy - 15, 30, 30),
            "toggle": QRectF(cx - 18, cy - 18, 36, 36),
            "next": QRectF(cx + 34, cy - 15, 30, 30),
        }

    def button_at(self, pos):
        if not self.state.has_session:
            return None
        for k, r in self.buttons().items():
            if r.adjusted(-5, -5, 5, 5).contains(pos):
                return k
        return None

    def on_bar(self, pos) -> bool:
        return self.state.duration > 0 and self.bar_rect().adjusted(-2, -8, 2, 8).contains(pos)

    def music_click(self, pos) -> bool:
        b = self.button_at(pos)
        if b:
            self.press_anim[b] = time.monotonic()
            {"toggle": self.media.toggle, "next": self.media.next, "prev": self.media.previous}[b]()
            if b == "toggle":  # мгновенный отклик, не дожидаясь плеера
                self.state.playing = not self.state.playing
            self.update()
            return True
        if self.on_bar(pos):
            r = self.bar_rect()
            frac = min(max((pos.x() - r.x()) / r.width(), 0), 1)
            self.media.seek(frac * self.state.duration)
            return True
        return False

    def music_hover(self, pos):
        b = self.button_at(pos)
        bar = self.on_bar(pos)
        if b != self.hover_btn or bar != self.hover_bar:
            self.hover_btn, self.hover_bar = b, bar
            self.setCursor(Qt.PointingHandCursor if (b or bar) else Qt.ArrowCursor)
            self.update()

    def music_leave(self):
        if self.hover_btn or self.hover_bar:
            self.hover_btn, self.hover_bar = None, False
            self.setCursor(Qt.ArrowCursor)
            self.update()

    def music_wheel(self, angle_delta: int):
        steps = angle_delta / 120
        res = volume.change(self.state.app, 0.04 * steps)
        if res:
            self.vol_osd = (res[0], res[1], time.monotonic())
            self.update()

    def _draw_volume(self, p: QPainter, art: QRectF):
        if not self.vol_osd:
            return
        value, source, t = self.vol_osd
        age = time.monotonic() - t
        if age > 1.6:
            self.vol_osd = None
            return
        alpha = 1.0 if age < 1.2 else (1.6 - age) / 0.4
        p.save()
        p.setOpacity(alpha)
        box = QRectF(art.x() + 8, art.bottom() - 34, art.width() - 16, 26)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(10, 12, 18, 210))
        p.drawRoundedRect(box, 13, 13)
        # значок динамика
        c = QPointF(box.x() + 15, box.center().y())
        p.setBrush(QColor(255, 255, 255, 230))
        spk = QPainterPath()
        spk.moveTo(c.x() - 5, c.y() - 3)
        spk.lineTo(c.x() - 2, c.y() - 3)
        spk.lineTo(c.x() + 3, c.y() - 7)
        spk.lineTo(c.x() + 3, c.y() + 7)
        spk.lineTo(c.x() - 2, c.y() + 3)
        spk.lineTo(c.x() - 5, c.y() + 3)
        spk.closeSubpath()
        p.drawPath(spk)
        bar = QRectF(box.x() + 28, box.center().y() - 2, box.width() - 64, 4)
        p.setBrush(QColor(255, 255, 255, 50))
        p.drawRoundedRect(bar, 2, 2)
        p.setBrush(QColor(255, 255, 255, 235))
        p.drawRoundedRect(QRectF(bar.x(), bar.y(), max(4, bar.width() * value), 4), 2, 2)
        p.setPen(QColor(255, 255, 255, 220))
        p.setFont(font(11, QFont.DemiBold))
        p.drawText(QRectF(bar.right() + 4, box.y(), 32, box.height()), Qt.AlignCenter, f"{round(value * 100)}")
        p.restore()

    def _btn_scale(self, name):
        t = self.press_anim.get(name)
        if t is None:
            return 1.0
        dt = time.monotonic() - t
        if dt > 0.35:
            self.press_anim.pop(name, None)
            return 1.0
        return 1 - 0.18 * math.sin(min(dt / 0.35, 1) * math.pi)

    # --- отрисовка -------------------------------------------------------------
    def _draw_marquee(self, p: QPainter, rect: QRectF, text: str):
        fm = QFontMetrics(p.font())
        tw = fm.horizontalAdvance(text)
        if tw <= rect.width():
            p.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, text)
            return
        # бегущая строка: пауза 2 с, прокрутка, пауза в конце
        gap = 40
        speed = 30.0
        cycle = (tw + gap) / speed
        t = (time.monotonic() - self.marquee_t0) % (cycle + 2.0)
        offset = 0 if t < 2.0 else (t - 2.0) * speed
        p.save()
        p.setClipRect(rect)
        for x in (rect.x() - offset, rect.x() - offset + tw + gap):
            p.drawText(QRectF(x, rect.y(), tw + 2, rect.height()), Qt.AlignLeft | Qt.AlignVCenter, text)
        p.restore()

    def paint_music(self, p: QPainter, rect: QRectF):
        st = self.state
        t = time.monotonic() - self.t0
        paint_glass(p, rect, 24, self.tint, alpha=200)

        # --- обложка ---
        art = QRectF(self.PAD, self.PAD, self.ART, self.ART)
        clip = QPainterPath()
        clip.addRoundedRect(art, 14, 14)
        if self.cover:
            p.save()
            p.setClipPath(clip)
            pm = self.cover.scaled(art.size().toSize() * 2, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            src = QRectF((pm.width() - art.width() * 2) / 2, (pm.height() - art.height() * 2) / 2,
                         art.width() * 2, art.height() * 2)
            p.drawPixmap(art, pm, src)
            p.restore()
        else:
            g = QLinearGradient(art.topLeft(), art.bottomRight())
            g.setColorAt(0, QColor(120, 170, 255, 70))
            g.setColorAt(1, QColor(190, 120, 255, 70))
            p.fillPath(clip, g)
            p.setPen(QColor(255, 255, 255, 170))
            p.setFont(font(44))
            p.drawText(art, Qt.AlignCenter, "♪")
        self._draw_volume(p, art)

        x, w = self.text_x(), self.text_w()
        if not st.has_session:
            p.setPen(QColor(255, 255, 255, 200))
            p.setFont(font(17, QFont.DemiBold))
            p.drawText(QRectF(x, self.PAD + 30, w, 24), Qt.AlignLeft | Qt.AlignVCenter, "Ничего не играет")
            p.setPen(QColor(255, 255, 255, 120))
            p.setFont(font(13))
            p.drawText(QRectF(x, self.PAD + 56, w, 20), Qt.AlignLeft | Qt.AlignVCenter,
                       "Включи музыку в любом плеере")
            return

        # --- верхняя строка: источник + эквалайзер ---
        eq_w = 22
        for i in range(4):
            target = (0.35 + 0.65 * abs(math.sin(t * (3.1 + i * 1.7) + i))) if st.playing else 0.15
            self.eq[i] += (target - self.eq[i]) * 0.25
            h = 14 * self.eq[i]
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 200))
            p.drawRoundedRect(QRectF(x + w - eq_w + i * 6, self.PAD + 14 - h, 3.5, h), 1.5, 1.5)
        p.setPen(QColor(255, 255, 255, 130))
        p.setFont(font(11, QFont.DemiBold))
        p.drawText(QRectF(x, self.PAD, w - eq_w - 8, 16), Qt.AlignLeft | Qt.AlignVCenter, app_label(st.app).upper())

        # --- название и исполнитель ---
        p.setPen(QColor(255, 255, 255))
        p.setFont(font(18, QFont.DemiBold))
        self._draw_marquee(p, QRectF(x, self.PAD + 20, w, 26), st.title or "Без названия")
        p.setPen(QColor(255, 255, 255, 175))
        p.setFont(font(14))
        p.drawText(QRectF(x, self.PAD + 46, w, 20), Qt.AlignLeft | Qt.AlignVCenter,
                   p.fontMetrics().elidedText(st.artist, Qt.ElideRight, int(w)))

        # --- прогресс ---
        if st.duration > 0:
            pos = st.current_position()
            bar = self.bar_rect()
            frac = pos / st.duration
            hh = 6 if self.hover_bar else 4
            b = QRectF(bar.x(), bar.center().y() - hh / 2, bar.width(), hh)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, 45))
            p.drawRoundedRect(b, hh / 2, hh / 2)
            p.setBrush(QColor(255, 255, 255, 235))
            p.drawRoundedRect(QRectF(b.x(), b.y(), max(hh, b.width() * frac), hh), hh / 2, hh / 2)
            if self.hover_bar:
                p.drawEllipse(QPointF(b.x() + b.width() * frac, b.center().y()), 6, 6)
            p.setPen(QColor(255, 255, 255, 130))
            p.setFont(font(11, QFont.Medium))
            p.drawText(QRectF(x, bar.bottom() + 5, 60, 14), Qt.AlignLeft | Qt.AlignVCenter, fmt_time(pos))
            p.drawText(QRectF(x + w - 60, bar.bottom() + 5, 60, 14), Qt.AlignRight | Qt.AlignVCenter,
                       fmt_time(st.duration))

        # --- кнопки ---
        for name, r in self.buttons().items():
            hover = name == self.hover_btn
            scale = self._btn_scale(name)
            c = r.center()
            p.save()
            p.translate(c)
            p.scale(scale, scale)
            p.translate(-c)
            p.setPen(Qt.NoPen)
            if name == "toggle":
                p.setBrush(QColor(255, 255, 255, 255 if hover else 235))
                p.drawEllipse(r)
                p.setBrush(QColor(22, 24, 32))
                if st.playing:
                    p.drawRoundedRect(QRectF(c.x() - 5.5, c.y() - 7, 4, 14), 1.5, 1.5)
                    p.drawRoundedRect(QRectF(c.x() + 1.5, c.y() - 7, 4, 14), 1.5, 1.5)
                else:
                    tri = QPainterPath()
                    tri.moveTo(c.x() - 4, c.y() - 8)
                    tri.lineTo(c.x() + 8, c.y())
                    tri.lineTo(c.x() - 4, c.y() + 8)
                    tri.closeSubpath()
                    p.drawPath(tri)
            else:
                if hover:
                    p.setBrush(QColor(255, 255, 255, 34))
                    p.drawEllipse(r)
                p.setBrush(QColor(255, 255, 255, 240 if hover else 210))
                _skip_icon(p, c, 1 if name == "next" else -1, 0.9)
            p.restore()


class MusicWidget(MusicPainter, BaseWidget):
    name = "music"
    title = "Музыка"
    default_size = (MusicPainter.W, MusicPainter.H)

    def __init__(self, config, media: MediaService):
        BaseWidget.__init__(self, config)
        self.init_music(media)
        self.anim = QTimer(self, interval=33)
        self.anim.timeout.connect(self._tick)
        self.anim.start()

    def _tick(self):
        # 30 кадров/с, пока играет музыка или идёт анимация; иначе — раз в секунду
        busy = self.state.playing or self.press_anim or self.hover_btn or self.hover_bar or self.vol_osd or any(
            v > 0.16 for v in self.eq)
        want = 33 if busy else 1000
        if self.anim.interval() != want:
            self.anim.setInterval(want)
        self.update()

    def on_media(self, st):
        super().on_media(st)
        if hasattr(self, "anim"):
            self.anim.setInterval(33)

    def hit_control(self, pos):
        return self.button_at(pos) is not None or self.on_bar(pos)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.music_click(e.position()):
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        self.music_hover(e.position())
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        self.music_leave()
        super().leaveEvent(e)

    def wheelEvent(self, e):
        self.music_wheel(e.angleDelta().y())
        self.anim.setInterval(33)

    def paintEvent(self, _):
        p = self.begin_paint()
        self.paint_music(p, self.glass_rect())
        p.end()


class MusicPopup(MusicPainter, QWidget):
    """Всплывающая карточка трека поверх всех окон (вместо стандартной плашки Windows)."""

    MARGIN = 24

    def __init__(self, config, media: MediaService):
        QWidget.__init__(self, None)
        self.config = config
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
                            | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.resize(self.W, self.H)
        self.init_music(media)

        self.anim = QTimer(self, interval=33)
        self.anim.timeout.connect(self._tick)
        self._click_through = None
        self.hide_timer = QTimer(self, singleShot=True)
        self.hide_timer.timeout.connect(self.fade_out)
        self.fade = QPropertyAnimation(self, b"windowOpacity", self)
        self.fade.setEasingCurve(QEasingCurve.OutCubic)
        self.fade.finished.connect(self._fade_done)
        self.slide = QPropertyAnimation(self, b"pos", self)
        self.slide.setEasingCurve(QEasingCurve.OutCubic)
        self.slide.setDuration(260)

        self.desktop_widget: QWidget | None = None  # виджет плеера на рабочем столе
        media.track_changed.connect(lambda _: self.cfg["on_track_change"] and self.popup())
        media.play_state_changed.connect(lambda _: self.cfg["on_play_pause"] and self.popup())

    @property
    def cfg(self):
        return self.config.data["music_popup"]

    def _target_pos(self):
        scr = QGuiApplication.primaryScreen().availableGeometry()
        m, corner = self.MARGIN, self.cfg.get("corner", "top-right")
        x = {"left": scr.left() + m, "right": scr.right() - self.W - m,
             "center": scr.center().x() - self.W // 2}[corner.split("-")[1]]
        y = scr.top() + m if corner.startswith("top") else scr.bottom() - self.H - m
        return x, y

    def desktop_widget_visible(self) -> bool:
        """Виден ли сейчас виджет плеера на экране (не закрыт окнами/игрой)."""
        w = self.desktop_widget
        if w is None or not w.isVisible():
            return False
        hwnd = int(w.winId())
        dpr = w.devicePixelRatioF()
        g = w.frameGeometry()
        user32 = ctypes.windll.user32
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.GetAncestor.restype = wintypes.HWND
        # проверяем центр и точки у углов: если хоть одна — наш виджет, значит его видно
        for fx, fy in ((0.5, 0.5), (0.15, 0.2), (0.85, 0.2), (0.15, 0.8), (0.85, 0.8)):
            pt = wintypes.POINT(int((g.x() + g.width() * fx) * dpr), int((g.y() + g.height() * fy) * dpr))
            h = user32.WindowFromPoint(pt)
            if h and user32.GetAncestor(h, 2) == hwnd:  # GA_ROOT
                return True
        return False

    def popup(self, manual: bool = False):
        if not self.state.has_session:
            return
        if not manual and (not self.cfg.get("enabled") or self.desktop_widget_visible()):
            return  # плеер и так виден на рабочем столе — второй не нужен
        x, y = self._target_pos()
        appearing = not self.isVisible() or self.fade.endValue() == 0.0
        if appearing:
            self.fade.stop()
            # выезжает на 16px со стороны своего края
            dy = -16 if self.cfg.get("corner", "top-right").startswith("top") else 16
            if not self.isVisible():
                self.setWindowOpacity(0.0)
                self.move(x, y + dy)
                self.show()
                self._click_through = None
                self._set_click_through(True)
            self.slide.stop()
            self.slide.setStartValue(self.pos())
            self.slide.setEndValue(QPoint(x, y))
            self.slide.start()
            self.fade.setDuration(220)
            self.fade.setStartValue(self.windowOpacity())
            self.fade.setEndValue(1.0)
            self.fade.start()
        else:
            self.move(x, y)
        self.anim.start()
        self.hide_timer.start(int(self.cfg.get("seconds", 4) * 1000))

    def _set_click_through(self, value: bool):
        """Клики проходят сквозь карточку везде, кроме её кнопок и полосы прогресса —
        чтобы она не мешала нажимать вкладки и кнопки окон под собой."""
        if value == self._click_through or not self.isVisible():
            return
        self._click_through = value
        hwnd = int(self.winId())
        user32 = ctypes.windll.user32
        ex = user32.GetWindowLongW(hwnd, -20)
        ex = ex | 0x20 if value else ex & ~0x20  # WS_EX_TRANSPARENT
        user32.SetWindowLongW(hwnd, -20, ex)

    def _cursor_local(self):
        return QPointF(self.mapFromGlobal(QCursor.pos()))

    def _tick(self):
        pos = self._cursor_local()
        inside = QRectF(self.rect()).contains(pos)
        interactive = inside and (self.button_at(pos) is not None or self.on_bar(pos))
        self._set_click_through(not interactive)
        # события мыши сквозь «прозрачное» окно не приходят — подсветку считаем сами
        if inside:
            self.music_hover(pos)
        else:
            self.music_leave()
        self.update()

    def fade_out(self):
        if QRectF(self.rect()).contains(self._cursor_local()):  # пока курсор на карточке — не прячем
            self.hide_timer.start(1000)
            return
        self.fade.stop()
        self.fade.setDuration(400)
        self.fade.setStartValue(self.windowOpacity())
        self.fade.setEndValue(0.0)
        self.fade.start()

    def _fade_done(self):
        if self.fade.endValue() == 0.0:
            self.hide()
            self.anim.stop()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            if self.music_click(e.position()):
                self.hide_timer.start(int(self.cfg.get("seconds", 4) * 1000))

    def mouseMoveEvent(self, e):
        self.music_hover(e.position())

    def leaveEvent(self, e):
        self.music_leave()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setRenderHint(QPainter.TextAntialiasing)
        self.paint_music(p, QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5))
        p.end()
