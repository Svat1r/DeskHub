"""Распознавание текста встроенным OCR Windows (работает офлайн).

Русский движок читает латиницу как кириллицу («Hello» -> «Непо»), английский — наоборот.
Поэтому запускаем оба и для каждого слова выбираем правдоподобный вариант.
"""
import asyncio
import re

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage

# нормальное латинское слово: hello / Hello / HELLO (а не «npoBepKa» или «3TO»)
_LATIN_WORD = re.compile(r"^[\"'(«]*([a-z]+|[A-Z][a-z]*|[A-Z]+)([-'][A-Za-z]+)*[\"'.,:;!?)»]*$")
_HAS_CYR = re.compile(r"[А-Яа-яЁё]")


async def _run(engine, bmp):
    result = await engine.recognize_async(bmp)
    lines = []
    for line in result.lines:
        words = []
        for w in line.words:
            r = w.bounding_rect
            words.append((w.text, (r.x, r.y, r.width, r.height)))
        lines.append(words)
    return lines


def _overlap(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    return ix * iy / max(1, min(aw * ah, bw * bh))


def _merge(ru_lines, en_lines):
    en_words = [w for line in en_lines for w in line]
    out = []
    for line in ru_lines:
        words = []
        for text, rect in line:
            best = max(en_words, key=lambda e: _overlap(rect, e[1]), default=None)
            if (best and _overlap(rect, best[1]) > 0.5 and _HAS_CYR.search(text)
                    and _LATIN_WORD.match(best[0]) and len(best[0]) >= 3):
                text = best[0]
            words.append(text)
        out.append(" ".join(words))
    return "\n".join(out)


async def _recognize(img: QImage) -> str:
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.security.cryptography import CryptographicBuffer

    img = img.convertToFormat(QImage.Format_ARGB32)  # = BGRA8 в памяти
    buf = CryptographicBuffer.create_from_byte_array(bytes(img.constBits())[: img.sizeInBytes()])
    bmp = SoftwareBitmap.create_copy_from_buffer(buf, BitmapPixelFormat.BGRA8, img.width(), img.height())

    tags = {l.language_tag.split("-")[0]: l for l in OcrEngine.available_recognizer_languages}
    ru = OcrEngine.try_create_from_language(tags["ru"]) if "ru" in tags else None
    en = OcrEngine.try_create_from_language(tags["en"]) if "en" in tags else None
    if ru is None and en is None:
        engine = OcrEngine.try_create_from_user_profile_languages()
        return "\n".join(" ".join(t for t, _ in l) for l in await _run(engine, bmp))
    if ru is None or en is None:
        lines = await _run(ru or en, bmp)
        return "\n".join(" ".join(t for t, _ in l) for l in lines)
    return _merge(await _run(ru, bmp), await _run(en, bmp))


def recognize(img: QImage) -> str:
    """Блокирующий вызов — запускать в фоновом потоке."""
    if img.height() < 300:  # мелкий текст OCR видит плохо — увеличиваем
        img = img.scaled(img.width() * 2, img.height() * 2, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    if max(img.width(), img.height()) > 4000:
        img = img.scaled(QSize(4000, 4000), Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return asyncio.run(_recognize(img))
