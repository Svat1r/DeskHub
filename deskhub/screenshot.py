"""Скриншотер: выделение области -> копировать / сохранить / текст (OCR) / ссылка."""
import math
import os
import threading
from pathlib import Path

from PySide6.QtCore import (QBuffer, QByteArray, QDateTime, QIODevice, QObject, QPoint, QPointF, QRect, QRectF, Qt,
                            QUrl, Signal)
from PySide6.QtGui import (QColor, QCursor, QDesktopServices, QFont, QGuiApplication, QKeySequence, QPainter,
                           QPainterPath, QPen, QPixmap, QPolygonF)
from PySide6.QtNetwork import QHttpMultiPart, QHttpPart, QNetworkAccessManager, QNetworkRequest
from PySide6.QtWidgets import (QApplication, QCheckBox, QFrame, QHBoxLayout, QLineEdit, QMessageBox, QPlainTextEdit,
                               QPushButton, QVBoxLayout, QWidget)

from . import ocr
from .toast import toast
from .widgets.base import font, paint_glass

SAVE_DIR = Path(os.environ.get("USERPROFILE", Path.home())) / "Pictures" / "DeskHub"

BUTTON_CSS = """
QPushButton { background: rgba(255,255,255,22); color: white; border: 1px solid rgba(255,255,255,30);
              border-radius: 9px; padding: 6px 12px; font: 600 13px 'Segoe UI Variable Display'; }
QPushButton:hover { background: rgba(255,255,255,45); }
QPushButton:pressed { background: rgba(255,255,255,70); }
QPushButton#primary { background: rgba(120,170,255,200); border: none; }
QPushButton#primary:hover { background: rgba(140,185,255,230); }
"""


class GlassPanel(QWidget):
    """Окно с «стеклянным» фоном, на которое кладутся обычные Qt-виджеты."""

    def __init__(self, flags=Qt.Tool | Qt.WindowStaysOnTopHint):
        super().__init__(None, Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint | flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(BUTTON_CSS)
        self._drag = None

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        paint_glass(p, QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 18, alpha=238)
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _):
        self._drag = None

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(e)


class TextResult(GlassPanel):
    """Окно с распознанным текстом."""

    ask_ai = Signal(str)

    def __init__(self, text: str):
        super().__init__()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.resize(520, 340)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 14)
        self.edit = QPlainTextEdit(text or "Текст не найден")
        self.edit.setStyleSheet("QPlainTextEdit { background: rgba(0,0,0,60); color: white; border: none;"
                                "border-radius: 10px; padding: 8px; font: 14px 'Segoe UI Variable Display'; }")
        lay.addWidget(self.edit)
        row = QHBoxLayout()
        copy = QPushButton("Копировать", objectName="primary")
        copy.clicked.connect(self.copy)
        ai = QPushButton("Спросить ИИ…")
        ai.clicked.connect(lambda: (self.ask_ai.emit(self.edit.toPlainText()), self.close()))
        close = QPushButton("Закрыть")
        close.clicked.connect(self.close)
        row.addWidget(copy)
        row.addWidget(ai)
        row.addStretch()
        row.addWidget(close)
        lay.addLayout(row)
        pos = QCursor.pos()
        scr = QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()
        g = scr.availableGeometry()
        self.move(min(max(pos.x() - 260, g.left() + 10), g.right() - 530), min(pos.y() + 20, g.bottom() - 350))

    def copy(self):
        QApplication.clipboard().setText(self.edit.toPlainText())
        toast("Текст скопирован")
        self.close()


TOOLS = [("pen", "✏", "Карандаш"), ("arrow", "↗", "Стрелка"), ("rect", "▢", "Рамка"),
         ("text", "T", "Текст"), ("blur", "▦", "Размытие — скрыть личное")]
COLORS = ["#ff4d4f", "#ffc53d", "#52c41a", "#40a9ff", "#ffffff", "#111111"]


def draw_shape(p: QPainter, s: dict, shot: QPixmap):
    """Рисует одну пометку. Координаты — в логических пикселях оверлея."""
    color = QColor(s["color"])
    if s["kind"] == "pen" and len(s["pts"]) > 1:
        path = QPainterPath(s["pts"][0])
        for pt in s["pts"][1:]:
            path.lineTo(pt)
        p.setPen(QPen(color, s["w"], Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
    elif s["kind"] == "arrow":
        a, b = s["pts"][0], s["pts"][-1]
        line = b - a
        length = math.hypot(line.x(), line.y())
        if length < 3:
            return
        ux, uy = line.x() / length, line.y() / length
        head = max(14, s["w"] * 4)
        base = QPointF(b.x() - ux * head, b.y() - uy * head)
        p.setPen(QPen(color, s["w"], Qt.SolidLine, Qt.RoundCap))
        p.drawLine(a, base)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawPolygon(QPolygonF([b, QPointF(base.x() - uy * head * 0.55, base.y() + ux * head * 0.55),
                                 QPointF(base.x() + uy * head * 0.55, base.y() - ux * head * 0.55)]))
    elif s["kind"] == "rect":
        p.setPen(QPen(color, s["w"], Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(s["pts"][0], s["pts"][-1]).normalized(), 4, 4)
    elif s["kind"] == "blur":
        r = QRectF(s["pts"][0], s["pts"][-1]).normalized()
        if r.width() < 2 or r.height() < 2:
            return
        dpr = shot.devicePixelRatio()
        src = QRect(int(r.x() * dpr), int(r.y() * dpr), int(r.width() * dpr), int(r.height() * dpr))
        piece = shot.copy(src).toImage()
        block = max(6, int(10 * dpr))
        small = piece.scaled(max(1, piece.width() // block), max(1, piece.height() // block),
                             Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        p.drawImage(r, small.scaled(piece.size(), Qt.IgnoreAspectRatio, Qt.FastTransformation))
    elif s["kind"] == "text" and s.get("text"):
        f = font(s["size"], QFont.Bold)
        path = QPainterPath()
        path.addText(s["pts"][0], f, s["text"])
        # обводка, чтобы текст читался на любом фоне
        outline = QColor(0, 0, 0, 200) if color.lightness() > 100 else QColor(255, 255, 255, 220)
        p.setPen(QPen(outline, 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawPath(path)


class SelectionOverlay(QWidget):
    """Полноэкранное «замороженное» изображение одного монитора: выделение области + пометки."""

    def __init__(self, tool, screen, shot: QPixmap, mode: str):
        super().__init__(None, Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint)
        self.tool, self.shot, self.mode = tool, shot, mode
        self.setGeometry(screen.geometry())
        self.setCursor(Qt.CrossCursor)
        self.setMouseTracking(True)
        self.origin: QPoint | None = None
        self.sel = QRect()
        self.toolbar = None
        self.shapes: list[dict] = []
        self.current: dict | None = None
        self.active_tool: str | None = None
        self.color = COLORS[0]
        self.text_edit: QLineEdit | None = None
        self.tool_buttons = {}
        self.color_buttons = {}

    # --- отрисовка -----------------------------------------------------------------
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.drawPixmap(self.rect(), self.shot)
        p.fillRect(self.rect(), QColor(0, 0, 0, 110))
        if not self.sel.isNull():
            r = self.sel.normalized()
            dpr = self.shot.devicePixelRatio()
            src = QRect(int(r.x() * dpr), int(r.y() * dpr), int(r.width() * dpr), int(r.height() * dpr))
            p.drawPixmap(r, self.shot, src)
            p.save()
            p.setClipRect(r)
            for s in self.shapes + ([self.current] if self.current else []):
                draw_shape(p, s, self.shot)
            p.restore()
            p.setPen(QPen(QColor(120, 180, 255), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r.adjusted(0, 0, -1, -1))
            label = f"{src.width()} × {src.height()}"
            p.setFont(font(12, QFont.DemiBold))
            w = p.fontMetrics().horizontalAdvance(label) + 14
            lr = QRectF(r.x(), r.y() - 26 if r.y() > 30 else r.y() + 4, w, 22)
            p.setBrush(QColor(20, 22, 30, 220))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(lr, 6, 6)
            p.setPen(Qt.white)
            p.drawText(lr, Qt.AlignCenter, label)
        else:
            p.setFont(font(16, QFont.Medium))
            p.setPen(QColor(255, 255, 255, 220))
            hint = {"text": "Выдели область для распознавания текста",
                    "record": "Выдели область для записи экрана"}.get(self.mode, "Выдели область")
            p.drawText(self.rect().adjusted(0, 60, 0, 0), Qt.AlignHCenter | Qt.AlignTop, hint + "  ·  Esc — отмена")
        p.end()

    # --- мышь ----------------------------------------------------------------------
    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            if self.active_tool:
                self.set_tool(None)
            else:
                self.tool.cancel()
            return
        if e.button() != Qt.LeftButton:
            return
        self.commit_text()
        pos = e.position()
        sel = self.sel.normalized()
        if self.active_tool and sel.contains(pos.toPoint()):
            if self.active_tool == "text":
                self.start_text(pos)
                return
            width = 3 if self.active_tool == "pen" else 4
            self.current = {"kind": self.active_tool, "color": self.color, "w": width, "pts": [pos, pos]}
            self.update()
            return
        if self.shapes:
            return  # уже есть пометки — не сбрасываем выделение случайным кликом
        if self.toolbar:
            self.toolbar.deleteLater()
            self.toolbar = None
        self.origin = pos.toPoint()
        self.sel = QRect(self.origin, self.origin)
        self.update()

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self.current is not None:
            r = QRectF(self.sel.normalized())
            pos = QPointF(min(max(pos.x(), r.left()), r.right()), min(max(pos.y(), r.top()), r.bottom()))
            if self.current["kind"] == "pen":
                self.current["pts"].append(pos)
            else:
                self.current["pts"][-1] = pos
            self.update()
        elif self.origin is not None:
            self.sel = QRect(self.origin, pos.toPoint())
            self.update()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        if self.current is not None:
            self.shapes.append(self.current)
            self.current = None
            self.update()
            return
        if self.origin is None:
            return
        self.origin = None
        r = self.sel.normalized()
        if r.width() < 4 or r.height() < 4:
            self.sel = QRect()
            self.update()
            return
        if self.mode in ("text", "record"):
            self.tool.finish(self.crop(), self.mode, self.sel_global())
        else:
            self.show_toolbar(r)

    def sel_global(self) -> QRect:
        return self.sel.normalized().translated(self.geometry().topLeft())

    # --- текст -----------------------------------------------------------------------
    def start_text(self, pos: QPointF):
        size = 22
        ed = QLineEdit(self)
        ed.setFont(font(size, QFont.Bold))
        ed.setStyleSheet(f"QLineEdit {{ background: rgba(0,0,0,90); color: {self.color}; border: 1px dashed "
                         f"rgba(255,255,255,140); border-radius: 4px; padding: 0 4px; }}")
        ed.setPlaceholderText("Текст…")
        ed.resize(260, size + 14)
        ed.move(int(pos.x()), int(pos.y() - (size + 14) / 2))
        ed.returnPressed.connect(self.commit_text)
        ed.setProperty("anchor", QPointF(pos.x() + 5, pos.y() + size * 0.35))
        ed.setProperty("size", size)
        ed.show()
        ed.setFocus()
        self.text_edit = ed

    def commit_text(self):
        ed = self.text_edit
        if ed is None:
            return
        self.text_edit = None
        if ed.text().strip():
            self.shapes.append({"kind": "text", "color": self.color, "w": 0, "pts": [ed.property("anchor")],
                                "text": ed.text(), "size": ed.property("size")})
        ed.deleteLater()
        self.setFocus()
        self.update()

    # --- результат -------------------------------------------------------------------
    def crop(self) -> QPixmap:
        """Выделенная область вместе с пометками, в реальных пикселях экрана."""
        self.commit_text()
        r = self.sel.normalized()
        dpr = self.shot.devicePixelRatio()
        pm = self.shot.copy(QRect(int(r.x() * dpr), int(r.y() * dpr), int(r.width() * dpr), int(r.height() * dpr)))
        pm.setDevicePixelRatio(1)
        if self.shapes:
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            p.scale(dpr, dpr)
            p.translate(-r.x(), -r.y())
            for s in self.shapes:
                draw_shape(p, s, self.shot)
            p.end()
        return pm

    # --- панель инструментов -----------------------------------------------------------
    def show_toolbar(self, r: QRect):
        bar = QWidget(self)
        bar.setObjectName("bar")
        bar.setStyleSheet(BUTTON_CSS + """
            QWidget#bar { background: rgba(20,22,30,240); border-radius: 12px; }
            QPushButton#tool { padding: 5px 9px; font: 15px 'Segoe UI Symbol'; min-width: 18px; }
            QPushButton#tool:checked { background: rgba(120,170,255,200); border: none; }
            QFrame#sep { background: rgba(255,255,255,40); }
        """)
        bar.setAttribute(Qt.WA_StyledBackground)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(5)

        for key, glyph, tip in TOOLS:
            b = QPushButton(glyph, objectName="tool", checkable=True)
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda checked, k=key: self.set_tool(k if checked else None))
            self.tool_buttons[key] = b
            lay.addWidget(b)
        for c in COLORS:
            sw = QPushButton()
            sw.setFixedSize(20, 20)
            sw.setCursor(Qt.PointingHandCursor)
            sw.clicked.connect(lambda _=False, col=c: self.set_color(col))
            self.color_buttons[c] = sw
            lay.addWidget(sw)
        undo = QPushButton("↶", objectName="tool")
        undo.setToolTip("Отменить (Ctrl+Z)")
        undo.clicked.connect(self.undo)
        lay.addWidget(undo)
        sep = QFrame(objectName="sep")
        sep.setFixedSize(1, 22)
        lay.addWidget(sep)

        for label, action, primary in (("Копировать", "copy", True), ("Сохранить", "save", False),
                                       ("Текст", "text", False), ("Ссылка", "link", False), ("✕", "cancel", False)):
            b = QPushButton(label)
            if primary:
                b.setObjectName("primary")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, a=action: self.act(a))
            lay.addWidget(b)
        self.set_color(self.color)
        bar.adjustSize()
        x = min(max(r.right() - bar.width(), 6), self.width() - bar.width() - 6)
        y = r.bottom() + 8 if r.bottom() + bar.height() + 14 < self.height() else r.top() - bar.height() - 8
        bar.move(x, max(6, y))
        bar.show()
        self.toolbar = bar

    def set_tool(self, key):
        self.commit_text()
        self.active_tool = key
        for k, b in self.tool_buttons.items():
            b.setChecked(k == key)
        self.setCursor(Qt.IBeamCursor if key == "text" else Qt.CrossCursor)

    def set_color(self, color):
        self.color = color
        for c, b in self.color_buttons.items():
            ring = "3px solid white" if c == color else "1px solid rgba(255,255,255,90)"
            b.setStyleSheet(f"QPushButton {{ background: {c}; border: {ring}; border-radius: 10px; padding: 0; }}")

    def undo(self):
        if self.shapes:
            self.shapes.pop()
            self.update()

    def act(self, action):
        if action == "cancel":
            self.tool.cancel()
        else:
            self.tool.finish(self.crop(), action, self.sel_global())

    def keyPressEvent(self, e):
        if self.text_edit is not None:
            if e.key() == Qt.Key_Escape:
                self.text_edit.deleteLater()
                self.text_edit = None
            return
        if e.key() == Qt.Key_Escape:
            self.tool.cancel()
        elif e.matches(QKeySequence.Undo):
            self.undo()
        elif self.toolbar and e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.act("copy")
        elif self.toolbar and e.matches(QKeySequence.Save):
            self.act("save")


class ScreenshotTool(QObject):
    ocr_done = Signal(str)
    ask_ai = Signal(str)
    record_requested = Signal(QRect)  # выбрана область для записи экрана

    def __init__(self, config, history=None):
        super().__init__()
        self.config = config
        self.history = history
        self.overlays: list[SelectionOverlay] = []
        self.net = QNetworkAccessManager(self)
        self.ocr_done.connect(self._show_text)
        self._results = []

    def start(self, mode: str = "menu"):
        if self.overlays:
            return
        for screen in QGuiApplication.screens():
            shot = screen.grabWindow(0)
            ov = SelectionOverlay(self, screen, shot, mode)
            self.overlays.append(ov)
            ov.show()
        # фокус на мониторе под курсором, чтобы работал Esc
        cur = QGuiApplication.screenAt(QCursor.pos())
        for ov in self.overlays:
            if ov.geometry().contains(QCursor.pos()) or cur is None:
                ov.activateWindow()
                ov.raise_()
                break

    def cancel(self):
        for ov in self.overlays:
            ov.close()
            ov.deleteLater()
        self.overlays.clear()

    def finish(self, pm: QPixmap, action: str, rect: QRect | None = None):
        self.cancel()
        if action == "record":
            self.record_requested.emit(rect)
            return
        if self.history is not None:
            self.history.add(pm)
        if action == "copy":
            QApplication.clipboard().setPixmap(pm)
            toast("Скриншот скопирован")
        elif action == "save":
            SAVE_DIR.mkdir(parents=True, exist_ok=True)
            path = SAVE_DIR / f"Скриншот {QDateTime.currentDateTime().toString('yyyy-MM-dd HH-mm-ss')}.png"
            pm.save(str(path), "PNG")
            QApplication.clipboard().setPixmap(pm)
            toast(f"Сохранено и скопировано\n{path.name}", 3,
                  on_click=lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(SAVE_DIR))))
        elif action == "text":
            toast("Распознаю текст…", 1.2)
            img = pm.toImage()
            threading.Thread(target=lambda: self.ocr_done.emit(self._safe_ocr(img)), daemon=True).start()
        elif action == "link":
            self.upload(pm)

    @staticmethod
    def _safe_ocr(img):
        try:
            return ocr.recognize(img)
        except Exception as e:  # OCR недоступен в системе и т.п.
            return f"Ошибка распознавания: {e}"

    def _show_text(self, text: str):
        if text.strip() and not text.startswith("Ошибка"):
            QApplication.clipboard().setText(text)
        w = TextResult(text)
        w.ask_ai.connect(self.ask_ai)
        w.destroyed.connect(lambda: self._results.remove(w) if w in self._results else None)
        self._results.append(w)
        w.show()
        w.activateWindow()

    # --- загрузка со ссылкой ------------------------------------------------
    def upload(self, pm: QPixmap):
        if not self.config.data.get("upload_warned"):
            box = QMessageBox(QMessageBox.Question, "Ссылка на скриншот",
                              "Картинка будет загружена на catbox.moe и станет доступна любому, у кого есть ссылка.\n"
                              "Удалить её потом будет нельзя. Продолжить?", QMessageBox.Yes | QMessageBox.No)
            cb = QCheckBox("Больше не спрашивать")
            box.setCheckBox(cb)
            if box.exec() != QMessageBox.Yes:
                return
            if cb.isChecked():
                self.config.data["upload_warned"] = True
                self.config.save()

        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QIODevice.WriteOnly)
        pm.save(buf, "PNG")
        buf.close()

        multi = QHttpMultiPart(QHttpMultiPart.FormDataType)
        part = QHttpPart()
        part.setHeader(QNetworkRequest.ContentDispositionHeader, 'form-data; name="reqtype"')
        part.setBody(b"fileupload")
        multi.append(part)
        filep = QHttpPart()
        filep.setHeader(QNetworkRequest.ContentTypeHeader, "image/png")
        filep.setHeader(QNetworkRequest.ContentDispositionHeader,
                        'form-data; name="fileToUpload"; filename="screenshot.png"')
        filep.setBody(data)
        multi.append(filep)

        req = QNetworkRequest(QUrl("https://catbox.moe/user/api.php"))
        req.setHeader(QNetworkRequest.UserAgentHeader, "DeskHub/1.0")
        reply = self.net.post(req, multi)
        multi.setParent(reply)
        toast("Загружаю…", 10)

        def done():
            text = bytes(reply.readAll()).decode("utf-8", "replace").strip()
            if reply.error() == reply.NetworkError.NoError and text.startswith("http"):
                QApplication.clipboard().setText(text)
                toast(f"Ссылка скопирована\n{text}", 4, on_click=lambda: QDesktopServices.openUrl(QUrl(text)))
            else:
                toast("Не удалось загрузить скриншот", 3)
            reply.deleteLater()

        reply.finished.connect(done)
