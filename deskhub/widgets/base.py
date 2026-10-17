"""Базовый класс виджета: прозрачное окно без рамки, перетаскивание, слой (стол / поверх окон)."""
import ctypes
from ctypes import wintypes

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QMenu, QWidget

FONT_FAMILY = "Segoe UI Variable Display"

_user32 = ctypes.windll.user32
_user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
_user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
_user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_uint]
GWLP_HWNDPARENT = -8
HWND_BOTTOM = wintypes.HWND(1)
SWP_NOSIZE_NOMOVE_NOACTIVATE = 0x0001 | 0x0002 | 0x0010


def pin_to_desktop(hwnd: int):
    """Делает окно «владельцем» которого является рабочий стол (Progman).

    Такие окна Windows держит прямо над обоями: они не перекрывают программы,
    но и не пропадают по Win+D — рабочий стол поднимается вместе с ними."""
    progman = _user32.FindWindowW("Progman", None)
    if progman and _user32.GetWindowLongPtrW(hwnd, GWLP_HWNDPARENT) != progman:
        _user32.SetWindowLongPtrW(hwnd, GWLP_HWNDPARENT, progman)
        _user32.SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0, SWP_NOSIZE_NOMOVE_NOACTIVATE)


def unpin(hwnd: int):
    _user32.SetWindowLongPtrW(hwnd, GWLP_HWNDPARENT, 0)


def font(size, weight=QFont.Normal):
    f = QFont(FONT_FAMILY)
    f.setPixelSize(int(size))
    f.setWeight(weight)
    f.setHintingPreference(QFont.PreferNoHinting)
    return f


def paint_glass(p: QPainter, rect: QRectF, radius=22, tint: QColor | None = None, alpha=195):
    """Полупрозрачная «стеклянная» подложка с тонкой рамкой."""
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    base = QColor(18, 20, 28, alpha)
    if tint is not None:
        # подмешиваем цвет (например, от обложки альбома)
        base = QColor(
            (base.red() * 2 + tint.red()) // 3,
            (base.green() * 2 + tint.green()) // 3,
            (base.blue() * 2 + tint.blue()) // 3,
            min(255, alpha + 10),
        )
    p.fillPath(path, base)
    p.setPen(QPen(QColor(255, 255, 255, 38), 1))
    p.drawPath(path)


class BaseWidget(QWidget):
    hide_requested = Signal(str)
    settings_changed = Signal()

    name = "base"
    title = "Виджет"
    default_size = (300, 160)

    def __init__(self, config):
        super().__init__(None)
        self.config = config
        self.cfg = config.widget(self.name)
        self._drag_offset: QPoint | None = None
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.resize(*self.default_size)
        self.apply_layer()
        self.setWindowOpacity(float(self.cfg.get("opacity", 1.0)))
        # Explorer мог перезапуститься (новый Progman) — периодически перепривязываем
        self._pin_timer = QTimer(self, interval=3000)
        self._pin_timer.timeout.connect(self._pin)
        self._pin_timer.start()

    # --- слой окна -------------------------------------------------------
    def apply_layer(self):
        visible = self.isVisible()
        # не забираем фокус у программ: клик по виджету не активирует его окно
        flags = Qt.FramelessWindowHint | Qt.Tool | Qt.NoDropShadowWindowHint | Qt.WindowDoesNotAcceptFocus
        if self.cfg.get("on_top"):
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        if visible:
            self.show()

    def _pin(self):
        if not self.isVisible():
            return
        hwnd = int(self.winId())
        if self.cfg.get("on_top"):
            unpin(hwnd)
        elif self._drag_offset is None:
            pin_to_desktop(hwnd)

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(0, self._pin)

    def set_on_top(self, value: bool):
        self.cfg["on_top"] = value
        self.config.save()
        self.apply_layer()

    def set_opacity(self, value: float):
        self.cfg["opacity"] = value
        self.config.save()
        self.setWindowOpacity(value)

    # --- позиция ---------------------------------------------------------
    def place(self, default_pos: QPoint):
        pos = self.cfg.get("pos")
        if pos:
            self.move(QPoint(*pos))
            # если монитор отключили — вернуть на видимый экран
            if self.screen() is None or not self.screen().availableGeometry().intersects(self.geometry()):
                self.move(default_pos)
        else:
            self.move(default_pos)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and not self.config.data["locked"] and not self.hit_control(e.position()):
            self._drag_offset = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_offset is not None:
            self.move(e.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._drag_offset is not None:
            self._drag_offset = None
            self.cfg["pos"] = [self.x(), self.y()]
            self.config.save()
            self._pin()
        super().mouseReleaseEvent(e)

    def hit_control(self, pos) -> bool:
        """Переопределяется, если на виджете есть кнопки, которые нельзя «тащить»."""
        return False

    # --- контекстное меню ------------------------------------------------
    def contextMenuEvent(self, e):
        menu = QMenu(self)
        top = QAction("Поверх всех окон", menu, checkable=True, checked=bool(self.cfg.get("on_top")))
        top.toggled.connect(self.set_on_top)
        menu.addAction(top)

        op_menu = menu.addMenu("Прозрачность")
        for pct in (100, 85, 70, 55, 40):
            a = QAction(f"{pct}%", op_menu, checkable=True, checked=round(self.windowOpacity() * 100) == pct)
            a.triggered.connect(lambda _=False, v=pct / 100: self.set_opacity(v))
            op_menu.addAction(a)

        self.extend_menu(menu)
        menu.addSeparator()
        hide = menu.addAction("Скрыть виджет")
        hide.triggered.connect(lambda: self.hide_requested.emit(self.name))
        menu.exec(e.globalPos())

    def extend_menu(self, menu: QMenu):
        pass

    # --- отрисовка -------------------------------------------------------
    def glass_rect(self) -> QRectF:
        return QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

    def begin_paint(self) -> QPainter:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        return p
