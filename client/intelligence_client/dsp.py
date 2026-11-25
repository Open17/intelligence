"""Tiny audio helpers: streaming resampler and earcons (feedback sounds).

numpy only. scipy would be ~100 MB on a 1 GB Pi for one filter.
"""

from __future__ import annotations

import math

import numpy as np


def _lowpass_taps(cutoff: float, num_taps: int = 63) -> np.ndarray:
    """Windowed-sinc low-pass FIR; ``cutoff`` as a fraction of the sample rate (0-0.5)."""
    n = np.arange(num_taps) - (num_taps - 1) / 2
    taps = 2 * cutoff * np.sinc(2 * cutoff * n) * np.blackman(num_taps)
    return (taps / taps.sum()).astype(np.float32)


class Resampler:
    """Streaming sample-rate converter for mono float32 audio.

    Low-pass filters (when downsampling) and linearly interpolates, keeping
    state between blocks so there are no clicks at block boundaries. Good
    enough for speech, cheap enough for a Pi.
    """

    def __init__(self, src_rate: int, dst_rate: int) -> None:
        self.src_rate, self.dst_rate = src_rate, dst_rate
        self._ratio = src_rate / dst_rate
        self._taps = _lowpass_taps(0.45 * min(1.0, dst_rate / src_rate)) if src_rate > dst_rate else None
        self._hist = np.zeros(0 if self._taps is None else len(self._taps) - 1, dtype=np.float32)
        self._pos = 0.0  # position of the next output sample, in input samples
        self._prev = np.zeros(1, dtype=np.float32)  # last input sample (for interpolation)

    def process(self, x: np.ndarray) -> np.ndarray:
        if self.src_rate == self.dst_rate or len(x) == 0:
            return x.astype(np.float32, copy=False)
        x = x.astype(np.float32, copy=False)
        if self._taps is not None:
            buf = np.concatenate([self._hist, x])
            x = np.convolve(buf, self._taps, mode="valid").astype(np.float32)
            self._hist = buf[-(len(self._taps) - 1) :]
        # Interpolate on [prev, x...], where index 0 is the previous block's last sample.
        src = np.concatenate([self._prev, x])
        positions = np.arange(self._pos, len(src) - 1, self._ratio)
        out = np.interp(positions, np.arange(len(src)), src).astype(np.float32)
        self._pos = (positions[-1] + self._ratio - (len(src) - 1)) if len(positions) else self._pos - len(x)
        self._prev = src[-1:]
        return out


def int16_to_float(pcm: bytes) -> np.ndarray:
    return np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0


def float_to_int16(x: np.ndarray) -> bytes:
    return (np.clip(x, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()


def tone(rate: int, notes: list[tuple[float, float]], volume: float = 0.25) -> np.ndarray:
    """A short sequence of (frequency Hz, duration s) sine notes with soft edges."""
    parts = []
    for freq, dur in notes:
        t = np.arange(int(rate * dur)) / rate
        env = np.minimum(1.0, np.minimum(t, dur - t) / 0.012)  # 12 ms fade in/out
        parts.append(np.sin(2 * math.pi * freq * t) * env * volume)
    return np.concatenate(parts).astype(np.float32)


def earcons(rate: int, volume: float = 0.25) -> dict[str, np.ndarray]:
    """'wake': rising two-note chime; 'ok': short soft tick; 'error': low falling pair."""
    return {
        "wake": tone(rate, [(660, 0.07), (990, 0.11)], volume),
        "ok": tone(rate, [(880, 0.05)], volume * 0.6),
        "error": tone(rate, [(330, 0.12), (220, 0.18)], volume),
    }
