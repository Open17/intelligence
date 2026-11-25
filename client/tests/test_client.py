from __future__ import annotations

import numpy as np
import pytest

from intelligence_client.config import ClientSettings
from intelligence_client.dsp import Resampler, earcons, float_to_int16, int16_to_float
from intelligence_client.vad import FRAME_BYTES, VadGate


# ---- Resampler -------------------------------------------------------------------------------
def dominant_freq(x: np.ndarray, rate: int) -> float:
    spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return float(np.fft.rfftfreq(len(x), 1 / rate)[np.argmax(spectrum)])


@pytest.mark.parametrize(("src", "dst"), [(48000, 16000), (44100, 16000), (22050, 48000), (22050, 44100), (16000, 16000)])
def test_resampler_keeps_pitch_and_length_in_blocks(src: int, dst: int) -> None:
    t = np.arange(src) / src
    x = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    r = Resampler(src, dst)
    out = np.concatenate([r.process(x[i : i + 1234]) for i in range(0, len(x), 1234)])
    assert abs(len(out) - dst) <= 2
    assert abs(dominant_freq(out, dst) - 440) < 3
    # No clicks at block boundaries: a smooth sine never jumps by much per sample.
    assert np.max(np.abs(np.diff(out[200:]))) < 2 * np.pi * 440 / dst * 0.5 * 1.2


def test_downsampling_removes_aliasing_tones() -> None:
    src, dst = 48000, 16000
    t = np.arange(src) / src
    x = np.sin(2 * np.pi * 12000 * t).astype(np.float32)  # above 8 kHz: must be filtered out
    out = Resampler(src, dst).process(x)
    assert np.sqrt(np.mean(out[100:] ** 2)) < 0.05


def test_int16_roundtrip() -> None:
    x = np.array([0.0, 0.5, -0.5, 1.5, -1.5], dtype=np.float32)
    y = int16_to_float(float_to_int16(x))
    assert np.allclose(y, [0, 0.5, -0.5, 1.0, -1.0], atol=1e-3)


def test_earcons_are_short_and_quiet() -> None:
    sounds = earcons(22050)
    assert set(sounds) == {"wake", "ok", "error"}
    for s in sounds.values():
        assert 0 < len(s) < 22050 and np.max(np.abs(s)) <= 0.26


# ---- VAD gate ---------------------------------------------------------------------------------
class ScriptedVad:
    """Frames whose first byte is 1 are speech."""

    def is_speech(self, frame: bytes, rate: int) -> bool:
        return frame[0] == 1


def frames(pattern: str) -> list[bytes]:
    return [bytes([1 if c == "S" else 0]) + bytes(FRAME_BYTES - 1) for c in pattern]


def test_gate_sends_pre_roll_speech_and_hangover() -> None:
    gate = VadGate(ScriptedVad(), pre_roll_ms=150, start_frames=3, start_window=5, hangover_ms=90)
    sent: list[bytes] = []
    events: list[str] = []
    for f in frames("....SSS" + "SS" + "..." + "...."):
        out = gate.process(f)
        if out.started:
            events.append("start")
        sent += out.frames
        if out.ended:
            events.append("end")
    assert events == ["start", "end"]
    # 5 frames pre-roll (150 ms incl. the triggering frame), 2 more speech, 3 hangover frames
    assert len(sent) == 5 + 2 + 3
    assert not gate.open


def test_gate_ignores_short_noises() -> None:
    gate = VadGate(ScriptedVad(), start_frames=3, start_window=5)
    outs = [gate.process(f) for f in frames("S..S..S..S...")]
    assert not any(o.started for o in outs) and not any(o.frames for o in outs)


def test_gate_with_real_webrtc_vad() -> None:
    webrtcvad = pytest.importorskip("webrtcvad")
    gate = VadGate(webrtcvad.Vad(2))
    silence = bytes(FRAME_BYTES)
    assert all(not gate.process(silence).started for _ in range(50))


# ---- Config -----------------------------------------------------------------------------------
def test_config_file_and_env(tmp_path, monkeypatch) -> None:
    env = tmp_path / "client.env"
    env.write_text("# comment\nINTEL_SERVER_URL=wss://10.77.0.1:8765\nINTEL_OUTPUT_DEVICE='USB Audio'\nINTEL_VOLUME=0.8\n")
    monkeypatch.setenv("INTEL_DEVICE_NAME", "kitchen")
    s = ClientSettings.load(env)
    assert s.server_url == "wss://10.77.0.1:8765"
    assert s.output_device == "USB Audio" and s.volume == 0.8 and s.device_name == "kitchen"
    assert s.earcons is True
