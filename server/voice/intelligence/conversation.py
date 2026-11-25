"""One device's conversation: when to listen, when to answer, what to say.

    idle ──wake phrase──► listening ──request──► thinking ──first audio──► speaking
     ▲                       │  ▲                                             │
     └──follow-up window─────┘  └─────────── device finished playing ◄────────┘
        (10 s) runs out

* In ``idle`` only sentences that start with the wake phrase count.
* After a reply has finished *playing* on the device, the user has
  ``followup_seconds`` to start talking again without the wake phrase. A
  sentence counts if it *started* inside the window, even if it ends later.
* The reply streams: text -> SentenceChunker -> Piper -> device, with text
  generation and speech synthesis overlapping.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Protocol

from .config import Settings
from .memory import Task
from .speech_text import SentenceChunker
from .stt import Heard
from .tts import slices
from .wake import match_wake

log = logging.getLogger(__name__)

# A sentence that *started* before the window closed may still be in
# transcription when it closes, so the window stays open this much longer.
WINDOW_GRACE_SECONDS = 3.0

# Whisper sometimes "hears" these in noise; never treat them as a request.
_NOISE = {"", "you", "thank you.", "thanks for watching!", "[blank_audio]", "(music)", "[music]", "."}


class Speaker(Protocol):
    sample_rate: int

    async def synthesize(self, text: str) -> bytes: ...


class Thinker(Protocol):
    stop_listening_requested: bool

    def respond(self, text: str, *, event: bool = False) -> AsyncIterator[str]: ...


SendJson = Callable[..., Awaitable[None]]
SendBytes = Callable[[bytes], Awaitable[None]]


class Conversation:
    _stream_ids = itertools.count(1)

    def __init__(
        self,
        settings: Settings,
        brain: Thinker,
        tts: Speaker,
        send_json: SendJson,
        send_bytes: SendBytes,
        name: str = "device",
    ) -> None:
        self._s = settings
        self._brain = brain
        self._tts = tts
        self._send_json = send_json
        self._send_bytes = send_bytes
        self.name = name
        self.state = "idle"
        self.window_until: float | None = None
        self._playing: int | None = None
        self._playback_done = asyncio.Event()
        self._turn_lock = asyncio.Lock()
        self._window_task: asyncio.Task[None] | None = None
        self._turn_task: asyncio.Task[None] | None = None
        self.last_active = time.monotonic()

    # ---- Inputs ------------------------------------------------------------------------
    async def on_heard(self, heard: Heard) -> None:
        text = heard.text.strip()
        if text.lower() in _NOISE:
            return
        if self.state in ("thinking", "speaking"):
            return  # half duplex: we're answering already
        in_window = self.window_until is not None and heard.started_at <= self.window_until
        wake = match_wake(text)
        if wake is not None:
            request = wake.request
        elif in_window:
            request = text
        else:
            if self._s.log_transcripts:
                log.info("[%s] ignored (no wake phrase): %s", self.name, text)
            else:
                log.debug("[%s] ignored speech (no wake phrase)", self.name)
            return

        self.last_active = time.monotonic()
        if self._s.log_transcripts:
            log.info("[%s] heard: %s", self.name, text)
        if not request:
            await self._send_json("earcon", name="wake")
            await self._open_window()
            return
        await self._start_turn(request, woke=wake is not None)

    async def on_playback_done(self, stream_id: int) -> None:
        if stream_id == self._playing:
            self._playing = None
            self._playback_done.set()

    async def announce(self, task: Task) -> None:
        """Tells the user a background task finished (queued behind any current turn)."""
        status = {"done": "finished", "failed": "failed"}.get(task.status, task.status)
        event = (
            f"<task_update>\nBackground task #{task.id} ({task.kind}) {status}.\n"
            f"Request: {task.request[:1000]}\nResult: {task.result[:4000]}\n</task_update>"
        )
        async with self._turn_lock:
            await self._run_turn(event, event_turn=True)

    # ---- Turns ---------------------------------------------------------------------------
    async def _start_turn(self, request: str, *, woke: bool = False) -> None:
        self._close_window()
        self.state = "thinking"
        # The wake chime whenever the wake phrase was said; a soft tick for follow-ups.
        await self._send_json("earcon", name="wake" if woke else "ok")
        await self._send_json("state", state="thinking")

        async def run() -> None:
            async with self._turn_lock:
                await self._run_turn(request)

        # Run the turn in its own task so the device's audio keeps flowing.
        self._turn_task = asyncio.create_task(run(), name=f"turn-{self.name}")

    async def _run_turn(self, text: str, event_turn: bool = False) -> None:
        self.state = "thinking"
        if event_turn:
            await self._send_json("state", state="thinking")
        stream_id = next(self._stream_ids)
        spoke = False
        try:
            spoke = await self._speak(self._brain.respond(text, event=event_turn), stream_id)
        except Exception:
            log.exception("[%s] turn failed", self.name)
            await self._send_json("earcon", name="error")
        if spoke:
            # Wait until the device has actually played everything.
            try:
                await asyncio.wait_for(self._playback_done.wait(), 120)
            except asyncio.TimeoutError:
                log.warning("[%s] device never confirmed playback", self.name)
        self._playing = None
        self.last_active = time.monotonic()
        if self._brain.stop_listening_requested:
            await self._go_idle()
        else:
            await self._open_window()

    async def _speak(self, reply: AsyncIterator[str], stream_id: int) -> bool:
        """Streams ``reply`` to the device as speech. Returns True if any audio was sent."""
        chunks: asyncio.Queue[str | None] = asyncio.Queue()
        chunker = SentenceChunker()

        async def produce() -> None:
            try:
                async with contextlib.aclosing(reply) as r:
                    async for delta in r:
                        for c in chunker.feed(delta):
                            await chunks.put(c)
                for c in chunker.flush():
                    await chunks.put(c)
            finally:
                await chunks.put(None)

        producer = asyncio.create_task(produce())
        started = False
        said: list[str] = []
        try:
            while (chunk := await chunks.get()) is not None:
                pcm = await self._tts.synthesize(chunk)
                if not pcm:
                    continue
                if not started:
                    started = True
                    self._playing = stream_id
                    self._playback_done.clear()
                    self.state = "speaking"
                    await self._send_json("state", state="speaking")
                    await self._send_json("audio_start", id=stream_id, sample_rate=self._tts.sample_rate)
                said.append(chunk)
                for part in slices(pcm):
                    await self._send_bytes(part)
        finally:
            if not producer.done():
                producer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await producer
            if started:
                await self._send_json("audio_end", id=stream_id)
        if self._s.log_transcripts and said:
            log.info("[%s] said: %s", self.name, " ".join(said))
        return started

    # ---- Follow-up window ---------------------------------------------------------------------
    async def _open_window(self) -> None:
        self._close_window()
        self.window_until = time.monotonic() + self._s.followup_seconds
        self.state = "listening"
        await self._send_json("state", state="listening")
        self._window_task = asyncio.create_task(self._expire_window(self.window_until))

    def _close_window(self) -> None:
        if self._window_task is not None:
            self._window_task.cancel()
            self._window_task = None
        self.window_until = None

    async def _expire_window(self, until: float) -> None:
        await asyncio.sleep(max(0.0, until - time.monotonic()) + WINDOW_GRACE_SECONDS)
        if self.state == "listening" and self.window_until == until:
            await self._go_idle()

    async def _go_idle(self) -> None:
        self._close_window()
        self.state = "idle"
        await self._send_json("state", state="idle")

    async def close(self) -> None:
        self._close_window()
        if self._turn_task is not None and not self._turn_task.done():
            self._turn_task.cancel()
            with contextlib.suppress(BaseException):
                await self._turn_task
