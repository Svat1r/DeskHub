"""ИИ-ассистент по горячей клавише: выделил текст -> перевести / исправить / пересказать (Claude API)."""
import ctypes
import re
import threading

import anthropic
from PySide6.QtCore import QMimeData, QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QCursor, QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QPushButton, QTextBrowser, QVBoxLayout)

from . import secure
from .screenshot import GlassPanel
from .toast import toast

MODELS = {
    "claude-opus-5-5": "Claude Opus 5.5 — лучшее качество",
    "claude-sonnet-5-5": "Claude Sonnet 5.5 — быстрее и дешевле",
    "claude-haiku-5-5": "Claude Haiku 5.5 — самый быстрый и дешёвый",
}
# серверный fallback при отказе модели доступен для Opus 5.5 и Sonnet 5.5
FALLBACK_MODELS = {"claude-opus-5-5", "claude-sonnet-5-5"}

SYSTEM = ("Ты — помощник, встроенный в Windows. Пользователь выделил текст в какой-то программе и выбрал действие. "
          "Выводи только результат — без вступлений, кавычек и пояснений, если о них не просят. "
          "Сохраняй переносы строк и форматирование исходного текста.")

ACTIONS = [
    ("translate", "Перевести"),
    ("fix", "Исправить"),
    ("summarize", "Пересказать"),
    ("explain", "Объяснить"),
    ("reply", "Ответить"),
]

_CYR = re.compile(r"[А-Яа-яЁё]")
_LAT = re.compile(r"[A-Za-z]")


def instruction_for(action: str, text: str) -> str:
    if action == "translate":
        lang = "английский" if len(_CYR.findall(text)) >= len(_LAT.findall(text)) else "русский"
        return f"Переведи текст на {lang} язык. Выведи только перевод."
    return {
        "fix": "Исправь орфографию, пунктуацию и грамматику. Сохрани язык, стиль и смысл. Выведи только исправленный текст.",
        "summarize": "Кратко перескажи главное на русском языке, 3–6 пунктов.",
        "explain": "Объясни простыми словами на русском языке, что здесь написано и что это значит.",
        "reply": "Напиши короткий уместный ответ на это сообщение на языке сообщения. Выведи только текст ответа.",
    }[action]


# --- работа с клавиатурой и буфером обмена -------------------------------------------
_user32 = ctypes.windll.user32
KEYUP = 0x2


def _key(vk, up=False):
    _user32.keybd_event(vk, 0, KEYUP if up else 0, 0)


def release_modifiers():
    # «пустая» клавиша перед отпусканием Alt — чтобы не открылось меню окна
    _key(0xFF)
    _key(0xFF, True)
    for vk in (0x12, 0xA4, 0xA5, 0x10, 0xA0, 0xA1, 0x11, 0xA2, 0xA3, 0x5B, 0x5C):
        if _user32.GetAsyncKeyState(vk) & 0x8000:
            _key(vk, True)


def send_ctrl(letter: str):
    vk = ord(letter.upper())
    _key(0x11)
    _key(vk)
    _key(vk, True)
    _key(0x11, True)


def clone_mime(md: QMimeData) -> QMimeData:
    copy = QMimeData()
    for fmt in md.formats():
        copy.setData(fmt, md.data(fmt))
    return copy


class _Bridge(QObject):
    chunk = Signal(str)
    done = Signal(str)
    error = Signal(str)


class AssistantPanel(GlassPanel):
    def __init__(self, assistant, text: str, hwnd: int):
        super().__init__()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.assistant = assistant
        self.hwnd = hwnd
        self.result_text = ""
        self.last_action = None
        self.busy = False
        self.bridge = _Bridge()
        self.bridge.chunk.connect(self.on_chunk)
        self.bridge.done.connect(self.on_done)
        self.bridge.error.connect(self.on_error)

        self.setFixedWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 14)
        lay.setSpacing(10)

        head = QHBoxLayout()
        title = QLabel("✦  ИИ-ассистент")
        title.setStyleSheet("color: white; font: 700 15px 'Segoe UI Variable Display';")
        self.model_label = QLabel(MODELS.get(assistant.model, assistant.model).split(" —")[0])
        self.model_label.setStyleSheet("color: rgba(255,255,255,120); font: 12px 'Segoe UI Variable Display';")
        close = QPushButton("✕")
        close.setFixedSize(30, 28)
        close.clicked.connect(self.close)
        head.addWidget(title)
        head.addSpacing(6)
        head.addWidget(self.model_label)
        head.addStretch()
        head.addWidget(close)
        lay.addLayout(head)

        edit_css = ("background: rgba(0,0,0,60); color: white; border: none; border-radius: 10px; padding: 8px;"
                    "font: 14px 'Segoe UI Variable Display'; selection-background-color: rgba(120,170,255,160);")
        self.source = QPlainTextEdit(text)
        self.source.setPlaceholderText("Выдели текст перед нажатием горячей клавиши или вставь его сюда")
        self.source.setStyleSheet(f"QPlainTextEdit {{ {edit_css} }}")
        self.source.setFixedHeight(84 if text else 64)
        lay.addWidget(self.source)

        chips = QHBoxLayout()
        chips.setSpacing(6)
        for key, label in ACTIONS:
            b = QPushButton(label)
            b.setStyleSheet("padding: 6px 8px;")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.run(k))
            chips.addWidget(b)
        lay.addLayout(chips)

        self.custom = QLineEdit()
        self.custom.setPlaceholderText("Своя команда, например «сделай вежливее» — Enter")
        self.custom.setStyleSheet(f"QLineEdit {{ {edit_css} }}")
        self.custom.returnPressed.connect(lambda: self.run("custom"))
        lay.addWidget(self.custom)

        self.result = QTextBrowser()
        self.result.setOpenExternalLinks(True)
        self.result.setStyleSheet(f"QTextBrowser {{ {edit_css} }}")
        self.result.setMinimumHeight(150)
        self.result.hide()
        lay.addWidget(self.result)

        bottom = QHBoxLayout()
        self.status = QLabel("")
        self.status.setStyleSheet("color: rgba(255,255,255,140); font: 12px 'Segoe UI Variable Display';")
        self.copy_btn = QPushButton("Копировать", objectName="primary")
        self.copy_btn.clicked.connect(self.copy)
        self.replace_btn = QPushButton("Заменить выделенное")
        self.replace_btn.setToolTip("Вставить результат в программу вместо выделенного текста")
        self.replace_btn.clicked.connect(self.replace)
        self.replace_btn.setVisible(bool(text) and bool(hwnd))
        bottom.addWidget(self.status)
        bottom.addStretch()
        bottom.addWidget(self.replace_btn)
        bottom.addWidget(self.copy_btn)
        self.copy_btn.hide()
        self.replace_btn.hide()
        lay.addLayout(bottom)

        self.adjustSize()
        self.place_near_cursor()

    def place_near_cursor(self):
        pos = QCursor.pos()
        g = (QGuiApplication.screenAt(pos) or QGuiApplication.primaryScreen()).availableGeometry()
        x = min(max(pos.x() - self.width() // 2, g.left() + 10), g.right() - self.width() - 10)
        y = pos.y() + 24
        if y + 420 > g.bottom():
            y = max(g.top() + 10, pos.y() - 440)
        self.move(x, y)

    # --- запуск запроса ------------------------------------------------------------
    def run(self, action: str):
        if self.busy:
            return
        text = self.source.toPlainText().strip()
        if action == "custom":
            instr = self.custom.text().strip()
            if not instr:
                return
        else:
            if not text:
                self.status.setText("Сначала вставь текст")
                return
            instr = instruction_for(action, text)
        prompt = f"{instr}\n\n<text>\n{text}\n</text>" if text else instr

        key = self.assistant.api_key()
        if not key:
            self.status.setText("Нужен API-ключ")
            self.assistant.ask_key(self)
            key = self.assistant.api_key()
            if not key:
                return

        self.busy = True
        self.last_action = action
        self.result_text = ""
        self.result.clear()
        self.result.show()
        self.copy_btn.hide()
        self.replace_btn.hide()
        self.status.setText("Думаю…")
        self.adjustSize()
        threading.Thread(target=self._worker, args=(key, prompt), daemon=True).start()

    def _worker(self, key: str, prompt: str):
        model = self.assistant.model
        kwargs = dict(model=model, max_tokens=16000, system=SYSTEM, output_config={"effort": "low"},
                      messages=[{"role": "user", "content": prompt}])
        try:
            client = anthropic.Anthropic(api_key=key)
            if model in FALLBACK_MODELS:
                ctx = client.beta.messages.stream(betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                                                  **kwargs)
            else:
                ctx = client.messages.stream(**kwargs)
            with ctx as stream:
                for text in stream.text_stream:
                    self.bridge.chunk.emit(text)
                final = stream.get_final_message()
            if final.stop_reason == "refusal":
                self.bridge.error.emit("Модель отказалась отвечать на этот запрос.")
            else:
                self.bridge.done.emit(final.stop_reason or "")
        except anthropic.AuthenticationError:
            self.bridge.error.emit("Неверный API-ключ. Проверь его в меню трея → «ИИ: API-ключ…».")
        except anthropic.PermissionDeniedError:
            self.bridge.error.emit("У ключа нет доступа к этой модели.")
        except anthropic.RateLimitError:
            self.bridge.error.emit("Слишком много запросов, попробуй через минуту.")
        except anthropic.BadRequestError as e:
            msg = e.message or ""
            if "credit" in msg.lower():
                self.bridge.error.emit("На балансе API закончились деньги — пополни на console.anthropic.com.")
            else:
                self.bridge.error.emit(f"Ошибка запроса: {msg}")
        except anthropic.APIStatusError as e:
            self.bridge.error.emit(f"Ошибка сервера ({e.status_code}), попробуй ещё раз.")
        except anthropic.APIConnectionError:
            self.bridge.error.emit("Нет соединения с интернетом.")

    def on_chunk(self, text: str):
        self.result_text += text
        self.result.setPlainText(self.result_text)
        self.result.verticalScrollBar().setValue(self.result.verticalScrollBar().maximum())

    def on_done(self, stop_reason: str):
        self.busy = False
        self.result.setMarkdown(self.result_text) if self.last_action in ("summarize", "explain", "custom") \
            else self.result.setPlainText(self.result_text)
        self.status.setText("Обрезано по длине" if stop_reason == "max_tokens" else "")
        self.copy_btn.show()
        self.replace_btn.setVisible(bool(self.hwnd) and self.last_action in ("translate", "fix", "reply", "custom"))

    def on_error(self, msg: str):
        self.busy = False
        self.result.show()
        self.result.setPlainText(msg)
        self.status.setText("")

    # --- результат -------------------------------------------------------------------
    def copy(self):
        QApplication.clipboard().setText(self.result_text)
        toast("Скопировано")
        self.close()

    def replace(self):
        text, hwnd = self.result_text, self.hwnd
        self.close()
        QApplication.clipboard().setText(text)
        _user32.SetForegroundWindow(hwnd)
        QTimer.singleShot(150, lambda: send_ctrl("v"))


class Assistant(QObject):
    def __init__(self, config):
        super().__init__()
        self.config = config
        self.panels = []

    @property
    def ai_cfg(self):
        return self.config.data.setdefault("ai", {"model": "claude-opus-5-5", "key": ""})

    @property
    def model(self):
        return self.ai_cfg.get("model", "claude-opus-5-5")

    def set_model(self, model: str):
        self.ai_cfg["model"] = model
        self.config.save()

    def api_key(self) -> str:
        return secure.unprotect(self.ai_cfg.get("key", ""))

    def ask_key(self, parent=None):
        box = QMessageBox(QMessageBox.Information, "API-ключ Claude",
                          "Для ИИ-ассистента нужен API-ключ Anthropic.\n\n"
                          "1. Открой console.anthropic.com → API Keys → Create Key\n"
                          "2. Пополни баланс (запросы стоят копейки)\n"
                          "3. Вставь ключ в следующем окне.\n\n"
                          "Ключ хранится зашифрованным только на этом компьютере.",
                          QMessageBox.Ok | QMessageBox.Open | QMessageBox.Cancel, parent)
        box.button(QMessageBox.Open).setText("Открыть сайт")
        r = box.exec()
        if r == QMessageBox.Open:
            QDesktopServices.openUrl(QUrl("https://console.anthropic.com/settings/keys"))
        elif r == QMessageBox.Cancel:
            return
        key, ok = QInputDialog.getText(parent, "API-ключ Claude", "Вставь ключ (sk-ant-…):", QLineEdit.Password)
        if ok and key.strip():
            self.ai_cfg["key"] = secure.protect(key.strip())
            self.config.save()
            toast("Ключ сохранён")

    # --- вызов по горячей клавише ----------------------------------------------------
    def trigger(self):
        hwnd = _user32.GetForegroundWindow()
        cb = QApplication.clipboard()
        saved = clone_mime(cb.mimeData())
        seq = _user32.GetClipboardSequenceNumber()
        release_modifiers()
        send_ctrl("c")

        def grab():
            text = ""
            if _user32.GetClipboardSequenceNumber() != seq:
                text = cb.text()
                cb.setMimeData(saved)  # возвращаем пользователю его буфер обмена
            self.open(text, hwnd)

        QTimer.singleShot(180, grab)

    def open(self, text: str = "", hwnd: int = 0):
        p = AssistantPanel(self, text.strip(), hwnd)
        p.destroyed.connect(lambda: self.panels.remove(p) if p in self.panels else None)
        self.panels.append(p)
        p.show()
        p.activateWindow()
        p.raise_()
        (p.source if not text else p.custom).setFocus()
