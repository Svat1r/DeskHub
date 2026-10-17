"""Чтение «что играет» и управление плеером через Windows SMTC (работает с Яндекс Музыкой, Spotify, браузером и т.д.)."""
import asyncio
import threading
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal

PLAYING = 4


@dataclass
class MediaState:
    has_session: bool = False
    app: str = ""
    title: str = ""
    artist: str = ""
    playing: bool = False
    position: float = 0.0  # секунды на момент updated_at
    duration: float = 0.0
    updated_at: float = field(default_factory=time.time)
    thumbnail: bytes = b""

    def current_position(self) -> float:
        if not self.duration:
            return 0.0
        pos = self.position + (time.time() - self.updated_at if self.playing else 0)
        return max(0.0, min(pos, self.duration))


class MediaService(QObject):
    changed = Signal(object)  # MediaState
    track_changed = Signal(object)
    play_state_changed = Signal(object)

    def __init__(self):
        super().__init__()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._session = None
        self._state = MediaState()
        self._thumb_key = None
        self._stop = False
        threading.Thread(target=self._run, daemon=True).start()

    # --- поток с asyncio ---------------------------------------------------
    def _run(self):
        self._loop = asyncio.new_event_loop()
        self._loop.run_until_complete(self._main())

    async def _main(self):
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager

        manager = await Manager.request_async()
        while not self._stop:
            try:
                await self._poll(manager)
            except Exception:  # плеер мог закрыться посреди запроса
                self._session = None
            await asyncio.sleep(0.5)

    async def _poll(self, manager):
        s = manager.get_current_session()
        self._session = s
        if s is None:
            if self._state.has_session:
                self._state = MediaState()
                self.changed.emit(self._state)
            return

        props = await s.try_get_media_properties_async()
        pb = s.get_playback_info()
        tl = s.get_timeline_properties()

        new = MediaState(
            has_session=True,
            app=s.source_app_user_model_id or "",
            title=props.title or "",
            artist=props.artist or "",
            playing=int(pb.playback_status) == PLAYING,
            duration=tl.end_time.total_seconds(),
            thumbnail=self._state.thumbnail,
        )
        # позиция в SMTC обновляется редко — пересчитываем относительно last_updated_time
        new.position = tl.position.total_seconds()
        try:
            new.updated_at = tl.last_updated_time.timestamp()
        except (OSError, ValueError, OverflowError):
            new.updated_at = time.time()

        key = (new.title, new.artist)
        track_changed = key != (self._state.title, self._state.artist)
        if key != self._thumb_key:
            new.thumbnail = await self._read_thumb(props) if props.thumbnail else b""
            if new.thumbnail or not props.thumbnail:
                self._thumb_key = key

        old = self._state
        self._state = new
        if (track_changed or old.playing != new.playing or old.thumbnail != new.thumbnail
                or abs(old.current_position() - new.current_position()) > 1.5 or old.duration != new.duration):
            self.changed.emit(new)
        if track_changed and new.title:
            self.track_changed.emit(new)
        elif old.playing != new.playing and old.has_session:
            self.play_state_changed.emit(new)

    async def _read_thumb(self, props) -> bytes:
        from winrt.windows.storage.streams import Buffer, InputStreamOptions
        try:
            stream = await props.thumbnail.open_read_async()
            buf = Buffer(stream.size)
            await stream.read_async(buf, stream.size, InputStreamOptions.READ_AHEAD)
            return bytes(buf)
        except Exception:
            return b""

    # --- команды из UI ------------------------------------------------------
    def _call(self, method: str):
        s, loop = self._session, self._loop
        if s is None or loop is None:
            return

        async def go():
            try:
                await getattr(s, method)()
            except Exception:
                pass

        asyncio.run_coroutine_threadsafe(go(), loop)

    def seek(self, seconds: float):
        s, loop = self._session, self._loop
        if s is None or loop is None:
            return
        # мгновенно сдвигаем локальную позицию, чтобы полоса не «прыгала» назад до ответа плеера
        self._state.position = seconds
        self._state.updated_at = time.time()

        async def go():
            try:
                await s.try_change_playback_position_async(int(seconds * 10_000_000))
            except Exception:
                pass

        asyncio.run_coroutine_threadsafe(go(), loop)

    def toggle(self):
        self._call("try_toggle_play_pause_async")

    def next(self):
        self._call("try_skip_next_async")

    def previous(self):
        self._call("try_skip_previous_async")

    @property
    def state(self) -> MediaState:
        return self._state

    def stop(self):
        self._stop = True
