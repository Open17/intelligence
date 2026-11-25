"""Text-to-speech with Piper, running on the server.

Piper is fast on a CPU (a sentence takes ~100-300 ms on an Ampere A1 core)
and fully local, so nothing you hear back leaves your server either. Each
chunk from SentenceChunker is synthesized as soon as it arrives, so audio
starts playing on the Pi while the model is still writing.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger(__name__)

SEND_SLICE_BYTES = 16 * 1024  # ~0.37 s at 22.05 kHz; small frames keep playback smooth


class TextToSpeech:
    def __init__(self, voice_path: str, length_scale: float = 1.0) -> None:
        self._voice_path = voice_path
        self._length_scale = length_scale
        self._voice = None
        self._config = None
        # One thread: Piper/ONNX Runtime parallelizes internally, and ordering matters.
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts")
        self.sample_rate = 22050

    async def load(self) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._pool, self._load_sync)
        log.info("text-to-speech ready: %s (%d Hz)", self._voice_path, self.sample_rate)

    def _load_sync(self) -> None:
        from piper import PiperVoice, SynthesisConfig

        self._voice = PiperVoice.load(self._voice_path)
        self._config = SynthesisConfig(length_scale=self._length_scale)
        self.sample_rate = self._voice.config.sample_rate
        # Warm up: the first synthesis is much slower (graph optimization).
        self._synth_sync("Ready.")

    def _synth_sync(self, text: str) -> bytes:
        assert self._voice is not None
        return b"".join(c.audio_int16_bytes for c in self._voice.synthesize(text, self._config))

    async def synthesize(self, text: str) -> bytes:
        """PCM s16le mono at ``sample_rate`` for ``text``."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, self._synth_sync, text)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


def slices(pcm: bytes, size: int = SEND_SLICE_BYTES) -> list[bytes]:
    return [pcm[i : i + size] for i in range(0, len(pcm), size)]
