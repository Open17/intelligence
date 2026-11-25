"""Talks to a running Intelligence stack like a Pi would, with real speech.

    python stack_client.py URL PKI_DIR SPEECH.pcm [FOLLOWUP.pcm]

Streams 16 kHz s16le speech through the real client VAD gate and protocol,
then reports what came back (JSON on stdout). Used by test_stack.sh.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

from intelligence_client.app import Device
from intelligence_client.config import ClientSettings
from intelligence_client.vad import FRAME_BYTES, VadGate, webrtc_detector


class Mic:
    def __init__(self) -> None:
        self.frames: asyncio.Queue[bytes] = asyncio.Queue()

    async def play_file(self, pcm: bytes) -> float:
        """Streams the speech, then 2 s of silence; returns when the speech ended."""
        speech_frames = len(pcm) // FRAME_BYTES
        pcm += bytes(16000 * 2 * 2)
        speech_end = 0.0
        for n, i in enumerate(range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES)):
            if n == speech_frames:
                speech_end = time.monotonic()
            self.frames.put_nowait(pcm[i : i + FRAME_BYTES])
            await asyncio.sleep(0.03)  # real time
        return speech_end


class Player:
    def __init__(self) -> None:
        self.device: Device | None = None
        self.streams: list[dict] = []
        self.earcons: list[str] = []
        self._id: int | None = None
        self.first_audio_at: float | None = None

    @property
    def busy(self) -> bool:
        return self._id is not None

    def begin_speech(self, stream_id: int, rate: int) -> None:
        self._id = stream_id
        self.streams.append({"id": stream_id, "rate": rate, "bytes": 0})

    def feed_speech(self, pcm: bytes) -> None:
        if self.first_audio_at is None:
            self.first_audio_at = time.monotonic()
        self.streams[-1]["bytes"] += len(pcm)

    def end_speech(self, stream_id: int) -> None:
        self._id = None
        assert self.device is not None
        self.device.playback_finished(stream_id)

    def earcon(self, name: str) -> None:
        self.earcons.append(name)

    def stop(self) -> None:
        self._id = None


async def main() -> None:
    url, pki = sys.argv[1], Path(sys.argv[2])
    speech = [Path(p).read_bytes() for p in sys.argv[3:]]
    settings = replace(
        ClientSettings(), server_url=url, ca_cert=pki / "ca.crt",
        client_cert=pki / "device-pi.crt", client_key=pki / "device-pi.key",
    )
    mic, player = Mic(), Player()
    device = Device(settings, mic, player, VadGate(webrtc_detector(2)))  # type: ignore[arg-type]
    player.device = device
    runner = asyncio.create_task(device.run_forever())
    result: dict = {"turns": []}
    try:
        async with asyncio.timeout(30):
            await device.connected.wait()
        for pcm in speech:
            n_before = len(player.streams)
            player.first_audio_at = None
            sent_end = await mic.play_file(pcm)
            async with asyncio.timeout(60):
                while len(player.streams) == n_before or device.state != "listening":
                    await asyncio.sleep(0.05)
            result["turns"].append({
                "audio_bytes": player.streams[-1]["bytes"],
                "sample_rate": player.streams[-1]["rate"],
                "reply_audio_after_speech_end_s": round((player.first_audio_at or sent_end) - sent_end, 2),
            })
        result["earcons"] = player.earcons
        result["final_state"] = device.state
    except TimeoutError:
        result["error"] = f"timed out (state={device.state}, streams={player.streams})"
    finally:
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)
    print(json.dumps(result))


if __name__ == "__main__":
    asyncio.run(main())
