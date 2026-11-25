"""End-to-end tests of the device server over real mutual TLS.

Speech-to-text, text-to-speech and the model are replaced by fakes; the TLS
handshake, certificate checks, protocol and conversation logic are real.
A fake "audio" frame b"SAY:<text>" makes the fake STT hear <text>.
"""

from __future__ import annotations

import asyncio
import json
import ssl
import subprocess
import time
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidMessage

from intelligence import conversation as conversation_mod
from intelligence.config import Settings
from intelligence.gateway import Gateway
from intelligence.memory import Task
from intelligence.stt import Heard


# ---- PKI -------------------------------------------------------------------------------------
def _openssl(*args: str, cwd: Path) -> None:
    subprocess.run(["openssl", *args], cwd=cwd, check=True, capture_output=True)


def _make_ca(d: Path, name: str) -> None:
    _openssl("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", f"{name}.key", cwd=d)
    _openssl("req", "-x509", "-new", "-key", f"{name}.key", "-days", "2", "-subj", f"/CN={name}",
             "-out", f"{name}.crt", cwd=d)


def _make_cert(d: Path, name: str, ca: str, san: str | None = None) -> None:
    _openssl("ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", f"{name}.key", cwd=d)
    _openssl("req", "-new", "-key", f"{name}.key", "-subj", f"/CN={name}", "-out", f"{name}.csr", cwd=d)
    ext = d / f"{name}.ext"
    ext.write_text(f"subjectAltName={san}\n" if san else "basicConstraints=CA:FALSE\n")
    _openssl("x509", "-req", "-in", f"{name}.csr", "-CA", f"{ca}.crt", "-CAkey", f"{ca}.key",
             "-CAcreateserial", "-days", "2", "-extfile", str(ext), "-out", f"{name}.crt", cwd=d)


@pytest.fixture(scope="module")
def pki(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("pki")
    _make_ca(d, "ca")
    _make_cert(d, "server", "ca", san="DNS:localhost,IP:127.0.0.1")
    _make_cert(d, "pi", "ca")
    _make_ca(d, "rogue-ca")
    _make_cert(d, "rogue", "rogue-ca")
    return d


def client_ctx(pki: Path, cert: str | None = "pi", ca: str = "ca") -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=str(pki / f"{ca}.crt"))
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    if cert:
        ctx.load_cert_chain(pki / f"{cert}.crt", pki / f"{cert}.key")
    return ctx


# ---- Fakes -----------------------------------------------------------------------------------
class FakeSTT:
    def __init__(self, settings, on_sentence, name) -> None:
        self._on_sentence = on_sentence

    async def start(self) -> None: ...

    def push(self, data: bytes) -> None:
        if data.startswith(b"SAY:"):
            heard = Heard(data[4:].decode(), "", False, 500, 700, time.monotonic())
            asyncio.get_running_loop().create_task(self._on_sentence(heard))

    def end_of_speech(self) -> None: ...

    async def close(self) -> None: ...


class FakeTTS:
    sample_rate = 16000

    async def synthesize(self, text: str) -> bytes:
        return b"\x01\x00" * len(text)


class FakeBrain:
    def __init__(self) -> None:
        self.heard: list[tuple[str, bool]] = []
        self.stop_listening_requested = False

    async def respond(self, text: str, *, event: bool = False) -> AsyncIterator[str]:
        self.heard.append((text, event))
        for word in f"You said {len(text)} characters. Anything else?".split(" "):
            yield word + " "


@pytest.fixture
async def server(pki: Path, unused_tcp_port: int):
    async def make(**kw: Any) -> tuple[Gateway, FakeBrain, str]:
        settings = replace(
            Settings(),
            listen_host="127.0.0.1",
            listen_port=unused_tcp_port,
            tls_cert=pki / "server.crt",
            tls_key=pki / "server.key",
            tls_ca=pki / "ca.crt",
            **kw,
        )
        brain = FakeBrain()
        gw = Gateway(settings, brain, FakeTTS(), stt_factory=FakeSTT)
        await gw.start()
        made.append(gw)
        return gw, brain, f"wss://localhost:{unused_tcp_port}"

    made: list[Gateway] = []
    yield make
    for gw in made:
        await gw.close()


async def hello(ws) -> None:
    await ws.send(json.dumps({"type": "hello", "version": 1, "device": "pi"}))
    assert json.loads(await ws.recv())["type"] == "ready"
    assert json.loads(await ws.recv()) == {"type": "state", "state": "idle"}


async def recv_until(ws, type_: str, timeout: float = 3.0) -> tuple[list[dict], list[bytes]]:
    """Collects messages until a JSON message of ``type_`` arrives."""
    msgs: list[dict] = []
    audio: list[bytes] = []
    async with asyncio.timeout(timeout):
        while True:
            frame = await ws.recv()
            if isinstance(frame, bytes):
                audio.append(frame)
                continue
            msg = json.loads(frame)
            msgs.append(msg)
            if msg["type"] == type_:
                return msgs, audio


async def assert_silent(ws, seconds: float = 0.3) -> None:
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(seconds):
            await ws.recv()


# ---- Tests -------------------------------------------------------------------------------------
async def test_wake_reply_and_followup_window(server, pki) -> None:
    gw, brain, url = await server()
    async with connect(url, ssl=client_ctx(pki), proxy=None) as ws:
        await hello(ws)

        await ws.send(b"SAY:What time is it?")  # not addressed to the assistant
        await assert_silent(ws)
        assert brain.heard == []

        await ws.send(b"SAY:Hey Claude, what time is it?")
        msgs, audio = await recv_until(ws, "audio_end")
        types = [m["type"] for m in msgs]
        assert types[:2] == ["earcon", "state"] and msgs[0]["name"] == "wake"
        assert "audio_start" in types and audio
        start = next(m for m in msgs if m["type"] == "audio_start")
        assert start["sample_rate"] == 16000
        assert brain.heard == [("What time is it?", False)]

        # The follow-up window only opens once the device has played the audio.
        await assert_silent(ws, 0.2)
        await ws.send(json.dumps({"type": "playback_done", "id": start["id"]}))
        msgs, _ = await recv_until(ws, "state")
        assert msgs[-1]["state"] == "listening"

        await ws.send(b"SAY:And in Paris?")  # no wake phrase needed now
        msgs, _ = await recv_until(ws, "audio_end")
        assert msgs[0] == {"type": "earcon", "name": "ok"}  # follow-ups get the soft tick
        assert brain.heard[-1] == ("And in Paris?", False)


async def test_wake_phrase_alone_opens_the_window(server, pki) -> None:
    _, brain, url = await server()
    async with connect(url, ssl=client_ctx(pki), proxy=None) as ws:
        await hello(ws)
        await ws.send(b"SAY:Hey Claude.")
        msgs, _ = await recv_until(ws, "state")
        assert msgs[0] == {"type": "earcon", "name": "wake"}
        assert msgs[-1]["state"] == "listening"
        await ws.send(b"SAY:Turn on the lights.")
        await recv_until(ws, "audio_end")
        assert brain.heard == [("Turn on the lights.", False)]


async def test_window_expires(server, pki, monkeypatch) -> None:
    monkeypatch.setattr(conversation_mod, "WINDOW_GRACE_SECONDS", 0.05)
    _, brain, url = await server(followup_seconds=0.2)
    async with connect(url, ssl=client_ctx(pki), proxy=None) as ws:
        await hello(ws)
        await ws.send(b"SAY:Claude.")
        msgs, _ = await recv_until(ws, "state")
        assert msgs[-1]["state"] == "listening"
        msgs, _ = await recv_until(ws, "state")
        assert msgs[-1]["state"] == "idle"
        await ws.send(b"SAY:Are you there?")
        await assert_silent(ws)
        assert brain.heard == []


async def test_announce_finished_task(server, pki) -> None:
    gw, brain, url = await server()
    async with connect(url, ssl=client_ctx(pki), proxy=None) as ws:
        await hello(ws)
        task = Task(7, "build", "claude-opus-5-5", "high", "Build a site", "done", "Done in /workspace", 1.0, 0, 1)
        announcing = asyncio.create_task(gw.announce(task))
        msgs, audio = await recv_until(ws, "audio_end")
        assert audio
        text, is_event = brain.heard[-1]
        assert is_event and "<task_update>" in text and "#7" in text and "Done in /workspace" in text
        start = next(m for m in msgs if m["type"] == "audio_start")
        await ws.send(json.dumps({"type": "playback_done", "id": start["id"]}))
        await asyncio.wait_for(announcing, 2)


async def test_no_client_certificate_is_rejected(server, pki) -> None:
    _, _, url = await server()
    with pytest.raises((ssl.SSLError, ConnectionError, InvalidMessage, ConnectionClosed, OSError)):
        async with connect(url, ssl=client_ctx(pki, cert=None), proxy=None) as ws:
            await ws.send(json.dumps({"type": "hello", "version": 1}))
            await ws.recv()


async def test_certificate_from_another_ca_is_rejected(server, pki) -> None:
    _, _, url = await server()
    ctx = client_ctx(pki, cert="rogue")
    with pytest.raises((ssl.SSLError, ConnectionError, InvalidMessage, ConnectionClosed, OSError)):
        async with connect(url, ssl=ctx, proxy=None) as ws:
            await ws.send(json.dumps({"type": "hello", "version": 1}))
            await ws.recv()


async def test_device_not_on_allow_list_is_refused(server, pki) -> None:
    _, _, url = await server(allowed_devices=("kitchen-pi",))
    async with connect(url, ssl=client_ctx(pki), proxy=None) as ws:
        with pytest.raises(ConnectionClosed) as e:
            await ws.recv()
        assert e.value.rcvd.code == 4003


async def test_bad_hello_is_refused(server, pki) -> None:
    _, _, url = await server()
    async with connect(url, ssl=client_ctx(pki), proxy=None) as ws:
        await ws.send(json.dumps({"type": "hello", "version": 999}))
        with pytest.raises(ConnectionClosed) as e:
            await ws.recv()
        assert e.value.rcvd.code == 4002


async def test_server_refuses_tls_1_2(server, pki) -> None:
    _, _, url = await server()
    ctx = client_ctx(pki)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    with pytest.raises((ssl.SSLError, ConnectionError, OSError)):
        async with connect(url, ssl=ctx, proxy=None):
            pass
