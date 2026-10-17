"""Громкость: отдельной программы (той, что играет музыку) или общая громкость Windows."""
import re

try:
    from pycaw.pycaw import AudioUtilities
except Exception:  # нет звуковой подсистемы / библиотеки
    AudioUtilities = None

# подсказки для сопоставления id плеера из SMTC с именем процесса
ALIASES = {"yandex.desktop.music": ["яндекс музыка", "yandexmusic"], "spotify": ["spotify"],
           "zunemusic": ["microsoft.media.player", "zunemusic"]}


def _tokens(app_id: str):
    low = app_id.lower()
    for key, names in ALIASES.items():
        if key in low:
            yield from names
    for t in re.split(r"[.!\\/_ ]+", low.removesuffix(".exe")):
        if len(t) >= 4 and t not in ("desktop", "music", "microsoft", "windows", "player", "exe"):
            yield t


def _find_session(app_id: str):
    if not AudioUtilities or not app_id:
        return None
    sessions = [s for s in AudioUtilities.GetAllSessions() if s.Process]
    for token in _tokens(app_id):
        for s in sessions:
            if token in s.Process.name().lower():
                return s
    return None


def change(app_id: str, delta: float):
    """Меняет громкость на delta (0..1). Возвращает (новая громкость 0..1, название) или None."""
    if not AudioUtilities:
        return None
    try:
        s = _find_session(app_id)
        if s is not None:
            vol = s.SimpleAudioVolume
            new = min(1.0, max(0.0, vol.GetMasterVolume() + delta))
            vol.SetMasterVolume(new, None)
            return new, "плеер"
        ep = AudioUtilities.GetSpeakers().EndpointVolume
        new = min(1.0, max(0.0, ep.GetMasterVolumeLevelScalar() + delta))
        ep.SetMasterVolumeLevelScalar(new, None)
        return new, "система"
    except Exception:
        return None
