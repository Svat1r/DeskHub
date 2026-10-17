"""Анимированные погодные иконки, нарисованные QPainter'ом. t — время в секундах."""
import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QRadialGradient

# WMO weather code -> (тип иконки, описание)
WMO = {
    0: ("clear", "Ясно"), 1: ("partly", "Преимущественно ясно"), 2: ("partly", "Переменная облачность"),
    3: ("cloudy", "Пасмурно"), 45: ("fog", "Туман"), 48: ("fog", "Изморозь"),
    51: ("drizzle", "Лёгкая морось"), 53: ("drizzle", "Морось"), 55: ("drizzle", "Сильная морось"),
    56: ("drizzle", "Ледяная морось"), 57: ("drizzle", "Ледяная морось"),
    61: ("rain", "Небольшой дождь"), 63: ("rain", "Дождь"), 65: ("rain", "Ливень"),
    66: ("rain", "Ледяной дождь"), 67: ("rain", "Ледяной дождь"),
    71: ("snow", "Небольшой снег"), 73: ("snow", "Снег"), 75: ("snow", "Сильный снег"), 77: ("snow", "Снежные зёрна"),
    80: ("rain", "Ливневый дождь"), 81: ("rain", "Ливень"), 82: ("rain", "Сильный ливень"),
    85: ("snow", "Снегопад"), 86: ("snow", "Сильный снегопад"),
    95: ("thunder", "Гроза"), 96: ("thunder", "Гроза с градом"), 99: ("thunder", "Гроза с градом"),
}


def describe(code):
    return WMO.get(int(code), ("cloudy", "—"))


def _sun(p: QPainter, c: QPointF, r: float, t: float):
    glow = QRadialGradient(c, r * 1.9)
    glow.setColorAt(0, QColor(255, 200, 80, 110))
    glow.setColorAt(1, QColor(255, 200, 80, 0))
    p.setPen(Qt.NoPen)
    p.setBrush(glow)
    p.drawEllipse(c, r * 1.9, r * 1.9)
    p.save()
    p.translate(c)
    p.rotate(t * 18)
    p.setPen(QPen(QColor(255, 196, 70), max(1.5, r * 0.14), Qt.SolidLine, Qt.RoundCap))
    pulse = 1 + 0.08 * math.sin(t * 2.5)
    for i in range(8):
        a = math.radians(i * 45)
        p.drawLine(QPointF(math.cos(a) * r * 1.3, math.sin(a) * r * 1.3),
                   QPointF(math.cos(a) * r * 1.65 * pulse, math.sin(a) * r * 1.65 * pulse))
    p.restore()
    g = QRadialGradient(c - QPointF(r * 0.3, r * 0.3), r * 1.2)
    g.setColorAt(0, QColor(255, 235, 140))
    g.setColorAt(1, QColor(255, 170, 40))
    p.setPen(Qt.NoPen)
    p.setBrush(g)
    p.drawEllipse(c, r, r)


def _moon(p: QPainter, c: QPointF, r: float, t: float):
    glow = QRadialGradient(c, r * 1.8)
    glow.setColorAt(0, QColor(200, 210, 255, 70))
    glow.setColorAt(1, QColor(200, 210, 255, 0))
    p.setPen(Qt.NoPen)
    p.setBrush(glow)
    p.drawEllipse(c, r * 1.8, r * 1.8)
    path = QPainterPath()
    path.addEllipse(c, r, r)
    cut = QPainterPath()
    cut.addEllipse(c + QPointF(r * 0.55, -r * 0.35), r * 0.85, r * 0.85)
    p.setBrush(QColor(230, 232, 250))
    p.drawPath(path.subtracted(cut))
    # мерцающие звёзды
    for i, (dx, dy) in enumerate(((-1.4, -0.9), (1.2, 0.9), (-0.9, 1.3))):
        a = 0.4 + 0.6 * (0.5 + 0.5 * math.sin(t * 2 + i * 2))
        p.setBrush(QColor(255, 255, 255, int(255 * a)))
        p.drawEllipse(c + QPointF(dx * r, dy * r), r * 0.07, r * 0.07)


def _cloud(p: QPainter, c: QPointF, s: float, color: QColor):
    path = QPainterPath()
    path.setFillRule(Qt.WindingFill)  # иначе на пересечениях кругов появляются «дыры»
    path.addEllipse(QPointF(c.x() - s * 0.55, c.y() + s * 0.1), s * 0.45, s * 0.38)
    path.addEllipse(QPointF(c.x(), c.y() - s * 0.15), s * 0.55, s * 0.55)
    path.addEllipse(QPointF(c.x() + s * 0.55, c.y() + s * 0.1), s * 0.42, s * 0.36)
    path.addRoundedRect(QRectF(c.x() - s * 0.95, c.y() + s * 0.05, s * 1.9, s * 0.42), s * 0.2, s * 0.2)
    path = path.simplified()
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawPath(path)


def draw_weather(p: QPainter, rect: QRectF, kind: str, t: float, is_day: bool = True):
    p.save()
    p.setClipRect(rect.adjusted(-rect.width() * 0.2, -rect.height() * 0.2, rect.width() * 0.2, rect.height() * 0.2))
    s = min(rect.width(), rect.height())
    c = rect.center()
    bob = math.sin(t * 1.2) * s * 0.025  # лёгкое покачивание облаков
    celestial = _sun if is_day else _moon

    if kind == "clear":
        celestial(p, c, s * 0.26, t)
    elif kind == "partly":
        celestial(p, c + QPointF(s * 0.14, -s * 0.14), s * 0.2, t)
        _cloud(p, c + QPointF(-s * 0.06 + math.sin(t * 0.6) * s * 0.03, s * 0.12 + bob), s * 0.34,
               QColor(235, 240, 250))
    elif kind == "cloudy":
        _cloud(p, c + QPointF(s * 0.12 + math.sin(t * 0.5) * s * 0.03, -s * 0.12), s * 0.26, QColor(150, 160, 180))
        _cloud(p, c + QPointF(-s * 0.05, s * 0.08 + bob), s * 0.34, QColor(220, 226, 238))
    elif kind == "fog":
        _cloud(p, c + QPointF(0, -s * 0.1 + bob), s * 0.3, QColor(200, 205, 215))
        p.setPen(QPen(QColor(220, 225, 235, 200), s * 0.05, Qt.SolidLine, Qt.RoundCap))
        for i in range(3):
            y = c.y() + s * (0.2 + i * 0.1)
            shift = math.sin(t * 1.5 + i) * s * 0.08
            p.drawLine(QPointF(c.x() - s * 0.3 + shift, y), QPointF(c.x() + s * 0.3 + shift, y))
    elif kind in ("rain", "drizzle", "thunder"):
        heavy = kind != "drizzle"
        cloud_col = QColor(120, 130, 150) if kind == "thunder" else QColor(190, 198, 214)
        drops = 6 if heavy else 4
        p.setPen(QPen(QColor(110, 180, 255), s * (0.035 if heavy else 0.028), Qt.SolidLine, Qt.RoundCap))
        for i in range(drops):
            phase = (t * (1.6 if heavy else 1.0) + i / drops) % 1.0
            x = c.x() - s * 0.28 + i * s * 0.56 / (drops - 1)
            y = c.y() + s * 0.05 + phase * s * 0.42
            p.setOpacity(1 - phase)
            p.drawLine(QPointF(x, y), QPointF(x - s * 0.03, y + s * 0.09))
        p.setOpacity(1)
        if kind == "thunder":
            flash = (t % 3.0) < 0.15 or 0.3 < (t % 3.0) < 0.38
            if flash:
                bolt = QPainterPath()
                bx, by = c.x() + s * 0.02, c.y() + s * 0.05
                for dx, dy in ((0, 0), (-0.1, 0.2), (0.0, 0.2), (-0.08, 0.42), (0.12, 0.14), (0.02, 0.14), (0.1, 0)):
                    pt = QPointF(bx + dx * s, by + dy * s)
                    bolt.lineTo(pt) if bolt.elementCount() else bolt.moveTo(pt)
                bolt.closeSubpath()
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(255, 220, 80))
                p.drawPath(bolt)
        _cloud(p, c + QPointF(0, -s * 0.12 + bob), s * 0.36, cloud_col)
    elif kind == "snow":
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255))
        for i in range(6):
            phase = (t * 0.45 + i / 6) % 1.0
            x = c.x() - s * 0.28 + i * s * 0.112 + math.sin(t * 2 + i) * s * 0.04
            y = c.y() + s * 0.05 + phase * s * 0.42
            p.setOpacity(1 - phase * 0.8)
            p.drawEllipse(QPointF(x, y), s * 0.03, s * 0.03)
        p.setOpacity(1)
        _cloud(p, c + QPointF(0, -s * 0.12 + bob), s * 0.36, QColor(225, 232, 245))
    p.restore()
