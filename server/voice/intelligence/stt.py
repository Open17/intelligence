"""Speech-to-text: one echo-stream process per connected device.

The device only sends audio while its local VAD hears speech (privacy and
bandwidth). echo decides a sentence is finished after a stretch of silence,
so whenever real audio stops arriving this class feeds echo *real-time*
silence for a little longer than echo's force timeout. Network jitter is
covered the same way: a gap of more than ~64 ms is filled with silence.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .config import Settings

log = logging.getLogger(__name__)

CHUNK_SAMPLES = 512  # 32 ms at 16 kHz
CHUNK_BYTES = CHUNK_SAMPLES * 2
_SILENCE = bytes(CHUNK_BYTES)
_MAX_BACKLOG_BYTES = 1_000_000  # ~30 s; beyond this echo is stuck and audio is dropped


@dataclass(frozen=True)
class Heard:
    """One finished sentence from echo."""

    text: str
    raw: str
    forced: bool
    audio_ms: float
    total_ms: float
    received_at: float  # time.monotonic() when it arrived

    @property
    def started_at(self) -> float:
        """Estimated monotonic time the person *started* saying it."""
        return self.received_at - (self.total_ms + self.audio_ms) / 1000.0


SentenceHandler = Callable[[Heard], Awaitable[None]]


class SpeechToText:
    def __init__(self, settings: Settings, on_sentence: SentenceHandler, name: str = "device") -> None:
        self._s = settings
        self._on_sentence = on_sentence
        self._name = name
        self._proc: asyncio.subprocess.Process | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._last_real = 0.0
        self._fill_until = 0.0
        self._closed = False
        # Silence must outlast echo's force timeout so held sentences are emitted.
        self._fill_seconds = 2.0 + 0.6
        self.ready = asyncio.Event()

    def _command(self) -> list[str]:
        s = self._s
        return [
            s.echo_stream_bin,
            "--model", s.whisper_model,
            "--vad-model", s.vad_model,
            "--threads", str(s.stt_threads),
            "--audio-ctx", str(s.stt_audio_ctx),
            "--pause-ms", str(s.stt_pause_ms),
        ]

    async def start(self, timeout: float = 60.0) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            *self._command(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._tasks = [
            asyncio.create_task(self._read_stdout(), name=f"stt-out-{self._name}"),
            asyncio.create_task(self._read_stderr(), name=f"stt-err-{self._name}"),
            asyncio.create_task(self._fill_silence(), name=f"stt-fill-{self._name}"),
        ]
        await asyncio.wait_for(self.ready.wait(), timeout)

    def push(self, pcm: bytes) -> None:
        """Feeds microphone audio (s16le 16 kHz mono). Never blocks."""
        now = time.monotonic()
        self._last_real = now
        self._fill_until = now + self._fill_seconds
        self._write(pcm)

    def end_of_speech(self) -> None:
        """The device's VAD closed its gate: make sure silence is fed from now on."""
        self._fill_until = time.monotonic() + self._fill_seconds
        self._last_real = 0.0

    def _write(self, data: bytes) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.stdin.is_closing():
            return
        transport = proc.stdin.transport  # type: ignore[attr-defined]
        if transport.get_write_buffer_size() > _MAX_BACKLOG_BYTES:
            log.warning("[%s] speech-to-text is falling behind; dropping audio", self._name)
            return
        proc.stdin.write(data)

    async def _fill_silence(self) -> None:
        tick = CHUNK_SAMPLES / 16000.0
        next_t = time.monotonic()
        while not self._closed:
            next_t += tick
            await asyncio.sleep(max(0.0, next_t - time.monotonic()))
            now = time.monotonic()
            if now - next_t > 1.0:  # we were suspended; don't try to catch up
                next_t = now
            if now < self._fill_until and now - self._last_real > 2 * tick:
                self._write(_SILENCE)

    async def _read_stdout(self) -> None:
        assert self._proc and self._proc.stdout
        async for line in self._proc.stdout:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = msg.get("type")
            if kind == "ready":
                log.info("[%s] speech-to-text ready: %s", self._name, msg.get("transcription"))
                self.ready.set()
            elif kind == "sentence":
                heard = Heard(
                    text=msg.get("text", ""),
                    raw=msg.get("raw", ""),
                    forced=bool(msg.get("forced")),
                    audio_ms=float(msg.get("audio_ms", 0)),
                    total_ms=float(msg.get("total_ms", 0)),
                    received_at=time.monotonic(),
                )
                try:
                    await self._on_sentence(heard)
                except Exception:
                    log.exception("[%s] sentence handler failed", self._name)
            elif kind == "error":
                log.error("[%s] echo-stream: %s", self._name, msg.get("message"))
        if not self._closed:
            log.error("[%s] echo-stream exited unexpectedly (code %s)", self._name, self._proc.returncode)

    async def _read_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        async for line in self._proc.stderr:
            log.debug("[%s] echo-stream: %s", self._name, line.decode(errors="replace").rstrip())

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def close(self) -> None:
        self._closed = True
        proc = self._proc
        if proc and proc.returncode is None:
            if proc.stdin:
                proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), 1.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
