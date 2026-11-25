"""Speech-to-text / text-to-speech tests against the real engines.

They run when the engines are available, and are skipped otherwise:

    INTEL_TEST_ECHO_BIN=build/echo-stream/echo-stream
    INTEL_TEST_MODELS=../echo/models          (ggml-base.en.bin, silero_vad.onnx)
    INTEL_TEST_PIPER_VOICE=path/to/en_US-lessac-medium.onnx

Inside the Docker image all three are set, so `make test-docker` runs them.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from pathlib import Path

import pytest

from intelligence.config import Settings
from intelligence.stt import CHUNK_BYTES, Heard, SpeechToText
from intelligence.tts import TextToSpeech, slices

ECHO_BIN = os.environ.get("INTEL_TEST_ECHO_BIN", "")
MODELS = Path(os.environ.get("INTEL_TEST_MODELS", "/opt/models"))
PIPER_VOICE = os.environ.get("INTEL_TEST_PIPER_VOICE", "")

needs_echo = pytest.mark.skipif(
    not (ECHO_BIN and Path(ECHO_BIN).exists() and (MODELS / "silero_vad.onnx").exists()),
    reason="echo-stream binary/models not available",
)
needs_piper = pytest.mark.skipif(
    not (PIPER_VOICE and Path(PIPER_VOICE).exists()), reason="Piper voice not available"
)


def test_heard_start_estimate() -> None:
    h = Heard("hi", "hi", False, audio_ms=1000, total_ms=700, received_at=100.0)
    assert h.started_at == pytest.approx(98.3)


def test_slices() -> None:
    assert [len(s) for s in slices(b"x" * 10, 4)] == [4, 4, 2]


def _settings() -> Settings:
    return replace(
        Settings(),
        echo_stream_bin=ECHO_BIN,
        whisper_model=str(MODELS / "ggml-base.en.bin"),
        vad_model=str(MODELS / "silero_vad.onnx"),
    )


async def _transcribe(pcm16: bytes) -> list[Heard]:
    """Streams PCM like a device does (32 ms chunks, real time, then the gate
    closes) and returns what echo heard."""
    heard: list[Heard] = []
    got = asyncio.Event()

    async def on_sentence(h: Heard) -> None:
        heard.append(h)
        got.set()

    stt = SpeechToText(_settings(), on_sentence)
    await stt.start()
    try:
        for i in range(0, len(pcm16), CHUNK_BYTES):
            stt.push(pcm16[i : i + CHUNK_BYTES])
            await asyncio.sleep(0.032)
        stt.end_of_speech()  # from here on, only the silence filler feeds echo
        await asyncio.wait_for(got.wait(), 10)
        # Collect any further sentences (a recording can hold several).
        while True:
            got.clear()
            try:
                await asyncio.wait_for(got.wait(), 3.5)
            except asyncio.TimeoutError:
                break
    finally:
        await stt.close()
    return heard


SPEECH_WAV = os.environ.get("INTEL_TEST_WAV", "")


@needs_echo
@pytest.mark.skipif(not (SPEECH_WAV and Path(SPEECH_WAV).exists()), reason="INTEL_TEST_WAV not set")
async def test_stt_hears_a_recording() -> None:
    import wave

    with wave.open(SPEECH_WAV) as w:
        assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
        pcm16 = w.readframes(w.getnframes())
    heard = await _transcribe(pcm16)
    text = " ".join(h.text for h in heard).lower()
    assert "my fellow americans" in text and "what your country can do" in text
    assert all(h.total_ms < 3000 for h in heard)


@needs_piper
@needs_echo
async def test_round_trip_tts_into_stt() -> None:
    """Piper says a wake phrase; echo must hear it back. Exercises both engines
    and the real-time silence filling that ends the sentence."""
    tts = TextToSpeech(PIPER_VOICE)
    await tts.load()
    pcm = await tts.synthesize("Hey Claude, what is the weather like today?")
    tts.close()
    assert len(pcm) > tts.sample_rate  # more than half a second of audio

    # Resample 22.05 kHz -> 16 kHz the simple way (fine for a test).
    import numpy as np

    src = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    n = int(len(src) * 16000 / tts.sample_rate)
    pcm16 = np.interp(np.linspace(0, len(src) - 1, n), np.arange(len(src)), src).astype(np.int16).tobytes()

    heard = await _transcribe(pcm16)
    text = heard[0].text.lower()
    assert "claude" in text or "cloud" in text
    assert "weather" in text
