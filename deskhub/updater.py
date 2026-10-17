"""Автообновление: проверяет последний релиз на GitHub, скачивает установщик и ставит его тихо."""
import json
import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QMessageBox

from . import GITHUB_REPO, __version__
from .toast import toast

ASSET_NAME = "DeskHub-Setup.exe"
CHECK_EVERY_MS = 6 * 60 * 60 * 1000


def parse_version(tag: str) -> tuple:
    parts = []
    for p in tag.strip().lstrip("vV").split("."):
        num = "".join(ch for ch in p if ch.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts + [0] * (3 - len(parts)))


class Updater(QObject):
    def __init__(self, config, on_quit):
        super().__init__()
        self.config = config
        self.on_quit = on_quit
        self.net = QNetworkAccessManager(self)
        self.busy = False
        QTimer.singleShot(30_000, self.check)
        self.timer = QTimer(self, interval=CHECK_EVERY_MS)
        self.timer.timeout.connect(self.check)
        self.timer.start()

    @property
    def cfg(self):
        return self.config.data.setdefault("updates", {"auto_check": True, "skipped": ""})

    def _request(self, url: str) -> QNetworkRequest:
        req = QNetworkRequest(QUrl(url))
        req.setHeader(QNetworkRequest.UserAgentHeader, f"DeskHub/{__version__}")
        req.setRawHeader(b"Accept", b"application/vnd.github+json")
        req.setAttribute(QNetworkRequest.RedirectPolicyAttribute, QNetworkRequest.NoLessSafeRedirectPolicy)
        return req

    def check(self, manual: bool = False):
        if self.busy or (not manual and not self.cfg.get("auto_check", True)):
            return
        reply = self.net.get(self._request(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"))

        def done():
            reply.deleteLater()
            if reply.error() != QNetworkReply.NoError:
                if manual:
                    toast("Не удалось проверить обновления", 3)
                return
            try:
                rel = json.loads(bytes(reply.readAll()).decode("utf-8"))
            except ValueError:
                return
            self._on_release(rel, manual)

        reply.finished.connect(done)

    def _on_release(self, rel: dict, manual: bool):
        tag = rel.get("tag_name", "")
        if parse_version(tag) <= parse_version(__version__):
            if manual:
                toast(f"У тебя последняя версия ({__version__})", 3)
            return
        if not manual and self.cfg.get("skipped") == tag:
            return
        asset = next((a for a in rel.get("assets", []) if a.get("name") == ASSET_NAME), None)
        if asset is None:
            return
        notes = (rel.get("body") or "").strip()
        if len(notes) > 700:
            notes = notes[:700].rstrip() + "…"
        box = QMessageBox(QMessageBox.Information, "Обновление DeskHub",
                          f"Вышла новая версия {tag.lstrip('v')} (у тебя {__version__}).\n\n{notes}")
        upd = box.addButton("Обновить", QMessageBox.AcceptRole)
        box.addButton("Позже", QMessageBox.RejectRole)
        skip = box.addButton("Пропустить версию", QMessageBox.DestructiveRole)
        box.setDefaultButton(upd)
        box.exec()
        if box.clickedButton() is upd:
            self.download(asset, tag)
        elif box.clickedButton() is skip:
            self.cfg["skipped"] = tag
            self.config.save()

    def download(self, asset: dict, tag: str):
        if not getattr(sys, "frozen", False):
            toast("Обновление работает только в установленной версии", 3)
            return
        self.busy = True
        target = Path(os.environ.get("TEMP", ".")) / f"DeskHub-Setup-{tag}.exe"
        reply = self.net.get(self._request(asset["browser_download_url"]))
        reply.setParent(self)
        t = toast("Скачиваю обновление… 0%", 600)
        last = {"pct": -1}

        def progress(received, total):
            pct = int(received * 100 / total) if total > 0 else 0
            if pct != last["pct"] and pct % 5 == 0:
                last["pct"] = pct
                try:
                    t.text = f"Скачиваю обновление… {pct}%"
                    t.update()
                except RuntimeError:
                    pass

        def done():
            self.busy = False
            reply.deleteLater()
            data = bytes(reply.readAll())
            try:
                t.close()
            except RuntimeError:
                pass
            if reply.error() != QNetworkReply.NoError or len(data) != asset.get("size", len(data)):
                toast("Не удалось скачать обновление, попробуй позже", 4)
                return
            target.write_bytes(data)
            toast("Устанавливаю обновление — DeskHub перезапустится", 3)
            subprocess.Popen([str(target), "--silent"],
                             creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
            QTimer.singleShot(800, self.on_quit)

        reply.downloadProgress.connect(progress)
        reply.finished.connect(done)
