"""Хранение настроек в %APPDATA%/DeskHub/config.json."""
import json
import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "DeskHub"
CONFIG_PATH = CONFIG_DIR / "config.json"

DEFAULTS = {
    "locked": False,
    "city": "Москва",
    "widgets": {
        # on_top: поверх всех окон, иначе — на рабочем столе под окнами
        "clock": {"enabled": True, "pos": None, "on_top": False, "opacity": 1.0},
        "weather": {"enabled": True, "pos": None, "on_top": False, "opacity": 1.0},
        "music": {"enabled": True, "pos": None, "on_top": False, "opacity": 1.0},
    },
    # Всплывающий оверлей плеера (замена стандартной плашке Windows)
    "music_popup": {
        "enabled": True,
        "corner": "top-right",  # top-left / top-right / bottom-left / bottom-right / top-center
        "seconds": 4,
        "on_track_change": True,
        "on_play_pause": True,
        "hotkey": "Alt+Shift+M",
    },
}


class Config:
    def __init__(self):
        self.data = json.loads(json.dumps(DEFAULTS))
        try:
            loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            self._merge(self.data, loaded)
        except (OSError, ValueError):
            pass

    def _merge(self, base, new):
        for k, v in new.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                self._merge(base[k], v)
            else:
                base[k] = v

    def save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")

    def widget(self, name):
        return self.data["widgets"].setdefault(name, {"enabled": True, "pos": None})
