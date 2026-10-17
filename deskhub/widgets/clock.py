"""Анимированные часы: аналоговый циферблат с плавной секундной стрелкой + цифры и дата."""
import math

from PySide6.QtCore import QDateTime, QLocale, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QConicalGradient, QFont, QPen

from .base import BaseWidget, font, paint_glass

ACCENT = QColor(120, 190, 255)


class ClockWidget(BaseWidget):
    name = "clock"
    title = "Часы"
    default_size = (360, 160)

    def __init__(self, config):
        super().__init__(config)
        self.locale = QLocale(QLocale.Russian, QLocale.Russia)
        self.timer = QTimer(self, interval=33, timerType=Qt.PreciseTimer)
        self.timer.timeout.connect(self.update)
        self.timer.start()

    def paintEvent(self, _):
        now = QDateTime.currentDateTime()
        t = now.time()
        p = self.begin_paint()
        paint_glass(p, self.glass_rect())

        # --- циферблат ---
        c = QPointF(80, self.height() / 2)
        r = 58.0
        secs = t.second() + t.msec() / 1000.0

        # кольцо прогресса секунд
        p.setPen(QPen(QColor(255, 255, 255, 25), 4))
        p.drawEllipse(c, r, r)
        grad = QConicalGradient(c, 90)
        grad.setColorAt(0.0, ACCENT)
        grad.setColorAt(1.0, QColor(190, 120, 255))
        pen = QPen(grad, 4)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawArc(QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), 90 * 16, int(-secs / 60 * 360 * 16))

        # риски часов
        for i in range(12):
            a = math.radians(i * 30)
            inner = r - (11 if i % 3 == 0 else 7)
            p.setPen(QPen(QColor(255, 255, 255, 200 if i % 3 == 0 else 90), 2.2 if i % 3 == 0 else 1.4,
                          Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(c.x() + math.sin(a) * inner, c.y() - math.cos(a) * inner),
                       QPointF(c.x() + math.sin(a) * (r - 4), c.y() - math.cos(a) * (r - 4)))

        def hand(angle_deg, length, width, color, tail=0.0):
            a = math.radians(angle_deg)
            p.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(c.x() - math.sin(a) * tail, c.y() + math.cos(a) * tail),
                       QPointF(c.x() + math.sin(a) * length, c.y() - math.cos(a) * length))

        minutes = t.minute() + secs / 60
        hours = (t.hour() % 12) + minutes / 60
        hand(hours * 30, r * 0.5, 4.5, QColor(255, 255, 255))
        hand(minutes * 6, r * 0.75, 3, QColor(235, 235, 245))
        hand(secs * 6, r * 0.85, 1.4, QColor(255, 120, 120), tail=10)
        p.setBrush(QColor(255, 120, 120))
        p.setPen(Qt.NoPen)
        p.drawEllipse(c, 3.2, 3.2)

        # --- цифры ---
        x = 160
        p.setPen(QColor(255, 255, 255))
        p.setFont(font(54, QFont.DemiBold))
        hm = t.toString("HH:mm")
        p.drawText(QRectF(x, 22, 200, 70), Qt.AlignLeft | Qt.AlignVCenter, hm)
        hm_w = p.fontMetrics().horizontalAdvance(hm)

        # мигающие секунды
        p.setFont(font(20, QFont.Medium))
        p.setPen(QColor(255, 255, 255, 140))
        p.drawText(QRectF(x + hm_w + 6, 50, 60, 30), Qt.AlignLeft | Qt.AlignVCenter, t.toString("ss"))

        p.setFont(font(15))
        p.setPen(QColor(255, 255, 255, 190))
        day = self.locale.toString(now.date(), "dddd")
        date = self.locale.toString(now.date(), "d MMMM")
        p.drawText(QRectF(x + 2, 96, 200, 22), Qt.AlignLeft | Qt.AlignVCenter, day.capitalize())
        p.setPen(QColor(255, 255, 255, 120))
        p.drawText(QRectF(x + 2, 116, 200, 22), Qt.AlignLeft | Qt.AlignVCenter, date)
        p.end()
