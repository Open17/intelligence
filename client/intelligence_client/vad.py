"""Local voice-activity gate.

The Pi only sends audio while someone is talking (plus a short pre-roll and
hang-over), never a constant stream of the room. That keeps bandwidth tiny
and means silence and background noise never leave the house.

WebRTC VAD is tiny (C, ~100 KB) and runs in microseconds per frame.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Protocol

FRAME_MS = 30
FRAME_SAMPLES = 16000 * FRAME_MS // 1000  # 480
FRAME_BYTES = FRAME_SAMPLES * 2


class SpeechDetector(Protocol):
    def is_speech(self, frame: bytes, sample_rate: int) -> bool: ...


def webrtc_detector(aggressiveness: int) -> SpeechDetector:
    import webrtcvad

    return webrtcvad.Vad(max(0, min(3, aggressiveness)))


@dataclass
class GateOutput:
    started: bool = False  # speech just started: send speech_start first
    frames: list[bytes] = field(default_factory=list)  # audio to send now
    ended: bool = False  # speech ended: send speech_end after the frames


class VadGate:
    """Feed 30 ms frames of 16 kHz s16le audio; get back what to send."""

    def __init__(
        self,
        detector: SpeechDetector,
        pre_roll_ms: int = 450,
        start_frames: int = 3,
        start_window: int = 5,
        hangover_ms: int = 900,
    ) -> None:
        self._vad = detector
        self._pre_roll: deque[bytes] = deque(maxlen=max(1, pre_roll_ms // FRAME_MS))
        self._recent: deque[bool] = deque(maxlen=start_window)
        self._start_frames = start_frames
        self._hangover_frames = max(1, hangover_ms // FRAME_MS)
        self._silent_run = 0
        self.open = False

    def reset(self) -> None:
        self._pre_roll.clear()
        self._recent.clear()
        self._silent_run = 0
        self.open = False

    def process(self, frame: bytes) -> GateOutput:
        out = GateOutput()
        speech = self._vad.is_speech(frame, 16000)
        if not self.open:
            self._pre_roll.append(frame)
            self._recent.append(speech)
            if sum(self._recent) >= self._start_frames:
                self.open = True
                self._silent_run = 0
                out.started = True
                out.frames = list(self._pre_roll)  # includes this frame
                self._pre_roll.clear()
            return out

        out.frames = [frame]
        self._silent_run = 0 if speech else self._silent_run + 1
        if self._silent_run >= self._hangover_frames:
            self.open = False
            self._recent.clear()
            out.ended = True
        return out
