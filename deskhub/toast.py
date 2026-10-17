"""Маленькое всплывающее уведомление внизу экрана."""
from PySide6.QtCore import QPropertyAnimation, QRectF, Qt, QTimer
from PySide6.QtGui import QFont, QGuiApplication, QPainter
from PySide6.QtWidgets import QWidget

from .widgets.base import font, paint_glass

_current = None


class Toast(QWidget):
    def __init__(self, text: str, seconds: float = 2.2, on_click=None):
        super().__init__(None)
        self.text = text
        self.on_click = on_click
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint
                            | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setFont(font(14, QFont.Medium))
        lines = text.split("\n")
        w = max(self.fontMetrics().horizontalAdvance(l) for l in lines) + 44
        self.resize(min(max(w, 160), 640), 22 * len(lines) + 24)
        scr = QGuiApplication.primaryScreen().availableGeometry()
        self.move(scr.center().x() - self.width() // 2, scr.bottom() - self.height() - 40)
        self.setWindowOpacity(0)
        self.fade = QPropertyAnimation(self, b"windowOpacity", self, duration=180, startValue=0.0, endValue=1.0)
        self.fade.start()
        QTimer.singleShot(int(seconds * 1000), self.fade_out)
        if on_click:
            self.setCursor(Qt.PointingHandCursor)

    def fade_out(self):
        self.fade = QPropertyAnimation(self, b"windowOpacity", self, duration=350,
                                       startValue=self.windowOpacity(), endValue=0.0)
        self.fade.finished.connect(self.close)
        self.fade.start()

    def mousePressEvent(self, _):
        if self.on_click:
            self.on_click()
        self.close()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        paint_glass(p, QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 16)
        p.setPen(Qt.white)
        p.setFont(self.font())
        p.drawText(self.rect(), Qt.AlignCenter, self.text)
        p.end()


def toast(text: str, seconds: float = 2.2, on_click=None):
    global _current
    if _current is not None:
        try:
            _current.close()
        except RuntimeError:
            pass
    _current = Toast(text, seconds, on_click)
    _current.show()
    return _current
