"""Шифрование секретов (API-ключа) через Windows DPAPI — расшифровать может только этот пользователь Windows."""
import base64
import ctypes
from ctypes import wintypes


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _to_blob(data: bytes) -> _Blob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _from_blob(blob: _Blob) -> bytes:
    data = ctypes.string_at(blob.pbData, blob.cbData)
    ctypes.windll.kernel32.LocalFree(blob.pbData)
    return data


def protect(text: str) -> str:
    out = _Blob()
    inp = _to_blob(text.encode("utf-8"))
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(inp), "DeskHub", None, None, None, 0,
                                                  ctypes.byref(out)):
        raise OSError("CryptProtectData failed")
    return base64.b64encode(_from_blob(out)).decode()


def unprotect(token: str) -> str:
    if not token:
        return ""
    out = _Blob()
    inp = _to_blob(base64.b64decode(token))
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(inp), None, None, None, None, 0,
                                                    ctypes.byref(out)):
        return ""
    return _from_blob(out).decode("utf-8")
