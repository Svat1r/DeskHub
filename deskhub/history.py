"""История скриншотов: каждый снимок сохраняется, текст на нём распознаётся в фоне, есть поиск."""
import os
import sqlite3
import threading
import time
from pathlib import Path

from PySide6.QtCore import QMimeData, QObject, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget, QListWidgetItem,
                               QPushButton, QVBoxLayout)

from . import ocr
from .screenshot import GlassPanel
from .toast import toast

HISTORY_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DeskHub" / "history"
MAX_ITEMS = 500
THUMB = 220


class History(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        self.db_path = HISTORY_DIR / "history.db"
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS shots (id INTEGER PRIMARY KEY, ts REAL, file TEXT, w INT, h INT,"
                       " text TEXT DEFAULT '', search TEXT DEFAULT '', ocr_done INT DEFAULT 0)")
        # снимки, у которых OCR не успел пройти (программу закрыли) — доделываем
        QTimer.singleShot(5000, self._resume_ocr)

    def _db(self):
        return sqlite3.connect(self.db_path, timeout=5)

    def add(self, pm: QPixmap):
        ts = time.time()
        name = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime(ts)) + f"_{int(ts * 1000) % 1000:03d}.png"
        path = HISTORY_DIR / name
        img = pm.toImage()
        img.save(str(path), "PNG")
        img.scaled(THUMB, THUMB, Qt.KeepAspectRatio, Qt.SmoothTransformation).save(
            str(path.with_suffix(".thumb.jpg")), "JPG", 85)
        with self._db() as db:
            cur = db.execute("INSERT INTO shots (ts, file, w, h) VALUES (?,?,?,?)", (ts, name, img.width(), img.height()))
            row_id = cur.lastrowid
        self._cleanup()
        threading.Thread(target=self._ocr, args=(row_id, img), daemon=True).start()
        self.changed.emit()

    def _ocr(self, row_id: int, img: QImage):
        try:
            text = ocr.recognize(img)
        except Exception:
            text = ""
        with self._db() as db:
            db.execute("UPDATE shots SET text=?, search=?, ocr_done=1 WHERE id=?", (text, text.lower(), row_id))
        self.changed.emit()

    def _resume_ocr(self):
        with self._db() as db:
            rows = db.execute("SELECT id, file FROM shots WHERE ocr_done=0").fetchall()

        def work():
            for row_id, file in rows:
                img = QImage(str(HISTORY_DIR / file))
                if not img.isNull():
                    self._ocr(row_id, img)

        if rows:
            threading.Thread(target=work, daemon=True).start()

    def _cleanup(self):
        with self._db() as db:
            old = db.execute("SELECT id, file FROM shots ORDER BY ts DESC LIMIT -1 OFFSET ?", (MAX_ITEMS,)).fetchall()
            for row_id, file in old:
                self._remove_files(file)
                db.execute("DELETE FROM shots WHERE id=?", (row_id,))

    @staticmethod
    def _remove_files(file: str):
        for p in (HISTORY_DIR / file, (HISTORY_DIR / file).with_suffix(".thumb.jpg")):
            try:
                p.unlink()
            except OSError:
                pass

    def search(self, query: str = ""):
        q = query.strip().lower()
        with self._db() as db:
            if q:
                rows = db.execute("SELECT id, ts, file, w, h, text FROM shots WHERE search LIKE ? ORDER BY ts DESC",
                                  (f"%{q}%",)).fetchall()
            else:
                rows = db.execute("SELECT id, ts, file, w, h, text FROM shots ORDER BY ts DESC").fetchall()
        return rows

    def delete(self, row_id: int):
        with self._db() as db:
            row = db.execute("SELECT file FROM shots WHERE id=?", (row_id,)).fetchone()
            if row:
                self._remove_files(row[0])
                db.execute("DELETE FROM shots WHERE id=?", (row_id,))
        self.changed.emit()

    def clear(self):
        for row in self.search():
            self._remove_files(row[2])
        with self._db() as db:
            db.execute("DELETE FROM shots")
        self.changed.emit()


def _when(ts: float) -> str:
    lt, now = time.localtime(ts), time.localtime()
    if lt.tm_yday == now.tm_yday and lt.tm_year == now.tm_year:
        return "сегодня " + time.strftime("%H:%M", lt)
    return time.strftime("%d.%m %H:%M", lt)


class HistoryWindow(GlassPanel):
    def __init__(self, history: History):
        super().__init__(Qt.Window)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowTitle("История скриншотов")
        self.history = history
        self.resize(820, 600)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel("🖼  История скриншотов")
        title.setStyleSheet("color: white; font: 700 17px 'Segoe UI Variable Display';")
        close = QPushButton("✕")
        close.setFixedSize(30, 28)
        close.clicked.connect(self.close)
        head.addWidget(title)
        head.addStretch()
        head.addWidget(close)
        lay.addLayout(head)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по тексту на скриншотах…")
        self.search.setClearButtonEnabled(True)
        self.search.setStyleSheet("QLineEdit { background: rgba(0,0,0,70); color: white; border: none; "
                                  "border-radius: 10px; padding: 9px 12px; font: 14px 'Segoe UI Variable Display'; }")
        self.search.textChanged.connect(lambda: self.debounce.start())
        lay.addWidget(self.search)
        self.debounce = QTimer(self, singleShot=True, interval=200)
        self.debounce.timeout.connect(self.fill)

        self.grid = QListWidget()
        self.grid.setViewMode(QListView.IconMode)
        self.grid.setIconSize(QSize(THUMB, int(THUMB * 0.62)))
        self.grid.setGridSize(QSize(THUMB + 22, int(THUMB * 0.62) + 46))
        self.grid.setResizeMode(QListView.Adjust)
        self.grid.setMovement(QListView.Static)
        self.grid.setSelectionMode(QListWidget.ExtendedSelection)
        self.grid.setWordWrap(True)
        self.grid.setStyleSheet("""
            QListWidget { background: transparent; border: none; color: rgba(255,255,255,190);
                          font: 12px 'Segoe UI Variable Display'; }
            QListWidget::item { border-radius: 10px; padding: 6px; }
            QListWidget::item:hover { background: rgba(255,255,255,18); }
            QListWidget::item:selected { background: rgba(120,170,255,70); color: white; }
        """)
        self.grid.itemDoubleClicked.connect(lambda it: self.open_item(it))
        lay.addWidget(self.grid)

        bottom = QHBoxLayout()
        self.summary = QLabel()
        self.summary.setStyleSheet("color: rgba(255,255,255,150); font: 13px 'Segoe UI Variable Display';")
        bottom.addWidget(self.summary)
        bottom.addStretch()
        for label, slot, primary in (("Копировать", self.copy_selected, True), ("Текст", self.copy_text, False),
                                     ("Открыть", self.open_selected, False), ("Папка", self.open_folder, False),
                                     ("Удалить", self.delete_selected, False)):
            b = QPushButton(label)
            if primary:
                b.setObjectName("primary")
            b.clicked.connect(slot)
            bottom.addWidget(b)
        lay.addLayout(bottom)

        history.changed.connect(self._on_changed)
        self.fill()

    def _on_changed(self):
        # сигнал может прийти из фонового потока OCR — обновляем в UI-потоке
        QTimer.singleShot(0, self.fill)

    def fill(self):
        selected = {it.data(Qt.UserRole)[0] for it in self.grid.selectedItems()}
        query = self.search.text()
        rows = self.history.search(query)
        self.grid.clear()
        for row_id, ts, file, w, h, text in rows:
            thumb = (HISTORY_DIR / file).with_suffix(".thumb.jpg")
            it = QListWidgetItem(QIcon(QPixmap(str(thumb))), f"{_when(ts)} · {w}×{h}")
            it.setData(Qt.UserRole, (row_id, file, text))
            snippet = (text or "текст не найден").strip().replace("\n", " ")
            it.setToolTip(snippet[:300])
            self.grid.addItem(it)
            if row_id in selected:
                it.setSelected(True)
        total = len(rows)
        self.summary.setText(f"Найдено: {total}" if query.strip() else f"Всего снимков: {total}")

    def _selected(self):
        return [it.data(Qt.UserRole) for it in self.grid.selectedItems()]

    def copy_selected(self):
        sel = self._selected()
        if not sel:
            return
        if len(sel) == 1:
            QApplication.clipboard().setImage(QImage(str(HISTORY_DIR / sel[0][1])))
        else:  # несколько — копируем как файлы (можно вставить в Telegram/проводник)
            md = QMimeData()
            md.setUrls([QUrl.fromLocalFile(str(HISTORY_DIR / s[1])) for s in sel])
            QApplication.clipboard().setMimeData(md)
        toast("Скопировано")

    def copy_text(self):
        texts = [s[2] for s in self._selected() if s[2]]
        if texts:
            QApplication.clipboard().setText("\n\n".join(texts))
            toast("Текст скопирован")
        else:
            toast("На выбранных снимках текста нет")

    def open_item(self, it):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(HISTORY_DIR / it.data(Qt.UserRole)[1])))

    def open_selected(self):
        for it in self.grid.selectedItems()[:5]:
            self.open_item(it)

    def open_folder(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(HISTORY_DIR)))

    def delete_selected(self):
        for s in self._selected():
            self.history.delete(s[0])

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Delete:
            self.delete_selected()
        elif e.key() == Qt.Key_C and e.modifiers() & Qt.ControlModifier:
            self.copy_selected()
        else:
            super().keyPressEvent(e)
