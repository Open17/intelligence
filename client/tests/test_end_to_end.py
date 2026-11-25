"""End to end: the real Pi client against the real voice gateway.

Certificates come from the real setup library (deploy/lib/pki.sh), the
connection is real mutual TLS 1.3, and both sides run their real protocol
code. Only the sound card, speech-to-text, text-to-speech and the model are
fakes. Skipped if the voice server package isn't importable.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server" / "voice"))
try:
    from intelligence.config import Settings
    from intelligence.gateway import Gateway
    from intelligence.stt import Heard
except ImportError:  # pragma: no cover
    pytest.skip("voice server package not available", allow_module_level=True)

from intelligence_client.app import Device
from intelligence_client.config import ClientSettings
from intelligence_client.vad import FRAME_BYTES, VadGate

pytestmark = pytest.mark.skipif(not shutil.which("bash") or not shutil.which("openssl"), reason="needs bash+openssl")


@pytest.fixture(scope="module")
def pki(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("pki")
    script = f"""
        set -e
        source "{ROOT}/deploy/lib/common.sh"
        PKI_DIR="{d}"
        source "{ROOT}/deploy/lib/pki.sh"
        pki_init_ca
        pki_server_cert 10.77.0.1
        pki_device_cert pi
    """
    subprocess.run(["bash", "-c", script], check=True, capture_output=True)
    return d


# ---- Server-side fakes -------------------------------------------------------------------
class FakeSTT:
    """Hears 'Hey Claude, what time is it?' whenever the device's VAD closes."""

    def __init__(self, settings, on_sentence, name) -> None:
        self._on_sentence = on_sentence
        self.bytes_received = 0

    async def start(self) -> None: ...

    def push(self, data: bytes) -> None:
        self.bytes_received += len(data)

    def end_of_speech(self) -> None:
        text = "Hey Claude, what time is it?" if not FakeSTT.followup else "And in Tokyo?"
        FakeSTT.followup = True
        h = Heard(text, text, False, 900, 700, time.monotonic())
        asyncio.get_running_loop().create_task(self._on_sentence(h))

    async def close(self) -> None: ...

    followup = False


class FakeTTS:
    sample_rate = 22050

    async def synthesize(self, text: str) -> bytes:
        return b"\x10\x00" * 2205  # 0.1 s


class FakeBrain:
    stop_listening_requested = False

    def __init__(self) -> None:
        self.heard: list[str] = []

    async def respond(self, text: str, *, event: bool = False):
        self.heard.append(text)
        yield "It's three o'clock. "
        yield "Anything else?"


# ---- Device-side fakes -----------------------------------------------------------------------
class FakeMic:
    def __init__(self) -> None:
        self.frames: asyncio.Queue[bytes] = asyncio.Queue()

    def say(self, speech_frames: int, silence_frames: int) -> None:
        for _ in range(speech_frames):
            self.frames.put_nowait(b"\x01" + bytes(FRAME_BYTES - 1))
        for _ in range(silence_frames):
            self.frames.put_nowait(bytes(FRAME_BYTES))


class FakePlayer:
    """Plays instantly: reports playback done as soon as the stream ends."""

    def __init__(self) -> None:
        self.device: Device | None = None
        self.audio = 0
        self.earcons: list[str] = []
        self._id: int | None = None

    @property
    def busy(self) -> bool:
        return self._id is not None

    def begin_speech(self, stream_id: int, rate: int) -> None:
        assert rate == 22050
        self._id = stream_id

    def feed_speech(self, pcm: bytes) -> None:
        self.audio += len(pcm)

    def end_speech(self, stream_id: int) -> None:
        self._id = None
        assert self.device is not None
        self.device.playback_finished(stream_id)

    def earcon(self, name: str) -> None:
        self.earcons.append(name)

    def stop(self) -> None:
        self._id = None


class ScriptedVad:
    def is_speech(self, frame: bytes, rate: int) -> bool:
        return frame[0] == 1


async def wait_for(cond, timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not cond():
            await asyncio.sleep(0.02)


async def test_pi_client_talks_to_the_gateway(pki: Path, unused_tcp_port: int, monkeypatch) -> None:
    import intelligence_client.app as app_mod

    monkeypatch.setattr(app_mod, "ECHO_GUARD_SECONDS", 0.0)
    FakeSTT.followup = False
    server_settings = replace(
        Settings(), listen_host="127.0.0.1", listen_port=unused_tcp_port,
        tls_cert=pki / "server.crt", tls_key=pki / "server.key", tls_ca=pki / "ca.crt",
        allowed_devices=("pi",),
    )
    brain = FakeBrain()
    gateway = Gateway(server_settings, brain, FakeTTS(), stt_factory=FakeSTT)
    await gateway.start()

    client_settings = replace(
        ClientSettings(), server_url=f"wss://127.0.0.1:{unused_tcp_port}",
        ca_cert=pki / "ca.crt", client_cert=pki / "device-pi.crt", client_key=pki / "device-pi.key",
    )
    mic, player = FakeMic(), FakePlayer()
    gate = VadGate(ScriptedVad(), hangover_ms=90)
    device = Device(client_settings, mic, player, gate)  # type: ignore[arg-type]
    player.device = device
    runner = asyncio.create_task(device.run_forever())
    try:
        await asyncio.wait_for(device.connected.wait(), 5)

        # "Hey Claude, what time is it?" -> reply plays -> follow-up window opens
        mic.say(speech_frames=10, silence_frames=5)
        await wait_for(lambda: device.state == "listening")
        assert brain.heard == ["What time is it?"]
        assert player.audio > 0 and "wake" in player.earcons

        # A follow-up without the wake phrase is answered too.
        mic.say(speech_frames=8, silence_frames=5)
        await wait_for(lambda: len(brain.heard) == 2)
        assert brain.heard[1] == "And in Tokyo?"
    finally:
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)
        await gateway.close()
