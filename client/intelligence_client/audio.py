"""Microphone capture and speaker playback with sounddevice (PortAudio).

Both streams stay open for the life of the program, so starting to talk or
play costs no device-open latency. If a device can't run at the rate we
want (USB devices are often 48 kHz only), we run it at its own rate and
resample.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from collections.abc import Callable

import numpy as np

from .dsp import Resampler, earcons, float_to_int16, int16_to_float
from .vad import FRAME_BYTES

log = logging.getLogger(__name__)

MIC_RATE = 16000


def _sd():
    import sounddevice  # imported lazily so tests run without PortAudio

    return sounddevice


def resolve_device(spec: str, kind: str) -> int | None:
    """'' -> default; '3' -> index 3; 'USB' -> first device whose name contains it."""
    if not spec:
        return None
    if spec.isdigit():
        return int(spec)
    sd = _sd()
    channels = "max_input_channels" if kind == "input" else "max_output_channels"
    for i, dev in enumerate(sd.query_devices()):
        if spec.lower() in dev["name"].lower() and dev[channels] > 0:
            return i
    raise ValueError(f"no {kind} device matching {spec!r}; run with --list-devices")


def _pick_rate(device: int | None, wanted: int, kind: str) -> int:
    sd = _sd()
    try:
        if kind == "input":
            sd.check_input_settings(device=device, samplerate=wanted, channels=1, dtype="int16")
        else:
            sd.check_output_settings(device=device, samplerate=wanted, channels=1, dtype="int16")
        return wanted
    except Exception:
        rate = int(sd.query_devices(device, kind)["default_samplerate"])
        log.info("%s device does not do %d Hz; using %d Hz and resampling", kind, wanted, rate)
        return rate


class Microphone:
    """Delivers 30 ms frames of 16 kHz s16le mono audio to an asyncio queue."""

    def __init__(self, device: str, loop: asyncio.AbstractEventLoop) -> None:
        self._device = resolve_device(device, "input")
        self._loop = loop
        self.frames: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)  # ~6 s
        self._rate = _pick_rate(self._device, MIC_RATE, "input")
        self._resampler = Resampler(self._rate, MIC_RATE) if self._rate != MIC_RATE else None
        self._pending = b""
        self._stream = None

    def _callback(self, indata, frames, time_info, status) -> None:  # audio thread
        data = bytes(indata)
        if self._resampler is not None:
            data = float_to_int16(self._resampler.process(int16_to_float(data)))
        self._pending += data
        while len(self._pending) >= FRAME_BYTES:
            frame, self._pending = self._pending[:FRAME_BYTES], self._pending[FRAME_BYTES:]
            self._loop.call_soon_threadsafe(self._put, frame)

    def _put(self, frame: bytes) -> None:
        if self.frames.full():
            self.frames.get_nowait()  # we're behind: drop the oldest audio
        self.frames.put_nowait(frame)

    def start(self) -> None:
        sd = _sd()
        self._stream = sd.RawInputStream(
            samplerate=self._rate,
            blocksize=int(self._rate * 0.03),
            device=self._device,
            channels=1,
            dtype="int16",
            latency="low",
            callback=self._callback,
        )
        self._stream.start()
        log.info("microphone: %s at %d Hz", _sd().query_devices(self._device, "input")["name"], self._rate)

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None


class Player:
    """Plays server speech and local earcons through one always-open stream.

    ``on_done(stream_id)`` fires (on the event loop) when the last sample of
    a speech stream has been handed to the sound card; the server starts the
    follow-up window from that moment.
    """

    def __init__(
        self,
        device: str,
        loop: asyncio.AbstractEventLoop,
        on_done: Callable[[int], None],
        volume: float = 1.0,
        preferred_rate: int = 22050,
    ) -> None:
        self._device = resolve_device(device, "output")
        self._loop = loop
        self._on_done = on_done
        self._volume = volume
        self.rate = _pick_rate(self._device, preferred_rate, "output")
        self._earcons = earcons(self.rate)
        self._lock = threading.Lock()
        self._queue: deque[np.ndarray] = deque()  # float32 blocks at self.rate
        self._current: np.ndarray | None = None
        self._offset = 0
        self._stream_id: int | None = None
        self._stream_ended = False
        self._resampler: Resampler | None = None
        self._stream = None

    @property
    def busy(self) -> bool:
        """True while anything is queued or playing."""
        with self._lock:
            return self._current is not None or bool(self._queue) or self._stream_id is not None

    # ---- Called from the event loop -------------------------------------------------------
    def begin_speech(self, stream_id: int, sample_rate: int) -> None:
        with self._lock:
            self._stream_id = stream_id
            self._stream_ended = False
            self._resampler = Resampler(sample_rate, self.rate) if sample_rate != self.rate else None

    def feed_speech(self, pcm: bytes) -> None:
        samples = int16_to_float(pcm)
        with self._lock:
            if self._resampler is not None:
                samples = self._resampler.process(samples)
            self._queue.append(samples * self._volume)

    def end_speech(self, stream_id: int) -> None:
        with self._lock:
            if stream_id == self._stream_id:
                self._stream_ended = True

    def earcon(self, name: str) -> None:
        sound = self._earcons.get(name)
        if sound is not None:
            with self._lock:
                self._queue.append(sound * self._volume)

    def stop(self) -> None:
        """Drops everything queued (server sent stop_audio)."""
        with self._lock:
            self._queue.clear()
            self._current = None
            if self._stream_id is not None:
                self._stream_ended = True

    # ---- Audio thread -------------------------------------------------------------------------
    def _callback(self, outdata, frames, time_info, status) -> None:
        out = np.zeros(frames, dtype=np.float32)
        filled = 0
        done_id = None
        with self._lock:
            while filled < frames:
                if self._current is None:
                    if not self._queue:
                        break
                    self._current, self._offset = self._queue.popleft(), 0
                take = min(frames - filled, len(self._current) - self._offset)
                out[filled : filled + take] = self._current[self._offset : self._offset + take]
                filled += take
                self._offset += take
                if self._offset >= len(self._current):
                    self._current = None
            if self._current is None and not self._queue and self._stream_id is not None and self._stream_ended:
                done_id, self._stream_id = self._stream_id, None
        outdata[:] = float_to_int16(out)
        if done_id is not None:
            self._loop.call_soon_threadsafe(self._on_done, done_id)

    def start(self) -> None:
        sd = _sd()
        self._stream = sd.RawOutputStream(
            samplerate=self.rate,
            blocksize=int(self.rate * 0.02),
            device=self._device,
            channels=1,
            dtype="int16",
            latency="low",
            callback=self._callback,
        )
        self._stream.start()
        log.info("speaker: %s at %d Hz", _sd().query_devices(self._device, "output")["name"], self.rate)

    def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
