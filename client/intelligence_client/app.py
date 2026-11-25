"""The device loop: connect, stream speech up, play replies, reconnect forever."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import ssl
import time

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from .audio import Microphone, Player
from .config import ClientSettings
from .vad import VadGate, webrtc_detector

log = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
ECHO_GUARD_SECONDS = 0.35  # keep the mic muted this long after playback (room echo)


def tls_context(s: ClientSettings) -> ssl.SSLContext:
    """TLS 1.3 only, trusting only our own CA, presenting this device's certificate."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.load_verify_locations(cafile=str(s.ca_cert))  # NOT the system store: pinned CA
    ctx.load_cert_chain(s.client_cert, s.client_key)
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


class Device:
    def __init__(self, settings: ClientSettings, mic: Microphone, player: Player, gate: VadGate) -> None:
        self._s = settings
        self._mic = mic
        self._player = player
        self._gate = gate
        self._muted_until = 0.0
        self._ws: ClientConnection | None = None
        self.state = "idle"
        self.connected = asyncio.Event()  # set while a session is up (after `ready`)

    def playback_finished(self, stream_id: int) -> None:
        self._muted_until = time.monotonic() + ECHO_GUARD_SECONDS
        if self._ws is not None:
            asyncio.ensure_future(self._send({"type": "playback_done", "id": stream_id}))

    async def _send(self, msg: dict) -> None:
        if self._ws is not None:
            with contextlib.suppress(ConnectionClosed):
                await self._ws.send(json.dumps(msg))

    async def run_forever(self) -> None:
        backoff = 1.0
        ctx = tls_context(self._s)
        while True:
            try:
                async with connect(
                    self._s.server_url,
                    ssl=ctx,
                    server_hostname=self._s.server_name,
                    compression=None,
                    max_size=1024 * 1024,
                    open_timeout=10,
                    ping_interval=15,
                    ping_timeout=15,
                    proxy=None,
                ) as ws:
                    self._ws = ws
                    await ws.send(json.dumps({"type": "hello", "version": PROTOCOL_VERSION,
                                              "device": self._s.device_name}))
                    ready = json.loads(await asyncio.wait_for(ws.recv(), 10))
                    if ready.get("type") != "ready":
                        raise RuntimeError(f"unexpected reply: {ready}")
                    log.info("connected to %s", self._s.server_url)
                    self.connected.set()
                    backoff = 1.0
                    await self._session(ws)
            except (OSError, ConnectionClosed, InvalidStatus, asyncio.TimeoutError, RuntimeError) as e:
                log.warning("connection lost (%s); retrying in %.0f s", e, backoff)
            finally:
                self.connected.clear()
                self._ws = None
                self._gate.reset()
                self._player.stop()
            await asyncio.sleep(backoff + random.random())
            backoff = min(backoff * 2, 30.0)

    async def _session(self, ws: ClientConnection) -> None:
        uplink = asyncio.create_task(self._uplink(ws))
        try:
            async for frame in ws:
                if isinstance(frame, bytes):
                    self._player.feed_speech(frame)
                    continue
                msg = json.loads(frame)
                kind = msg.get("type")
                if kind == "audio_start":
                    self._player.begin_speech(int(msg["id"]), int(msg["sample_rate"]))
                elif kind == "audio_end":
                    self._player.end_speech(int(msg["id"]))
                elif kind == "earcon" and self._s.earcons:
                    self._player.earcon(str(msg.get("name")))
                elif kind == "stop_audio":
                    self._player.stop()
                elif kind == "state":
                    self.state = str(msg.get("state"))
                    log.info("state: %s", self.state)
                elif kind == "error":
                    log.error("server: %s", msg.get("message"))
        finally:
            uplink.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await uplink

    async def _uplink(self, ws: ClientConnection) -> None:
        """Microphone -> VAD gate -> server. Muted while the speaker is talking
        (half duplex), so the assistant never hears itself."""
        # Drop audio captured before we were connected.
        while not self._mic.frames.empty():
            self._mic.frames.get_nowait()
        while True:
            frame = await self._mic.frames.get()
            muted = self._player.busy or self.state in ("thinking", "speaking") or time.monotonic() < self._muted_until
            if muted:
                if self._gate.open:
                    await ws.send(json.dumps({"type": "speech_end"}))
                self._gate.reset()
                continue
            out = self._gate.process(frame)
            if out.started:
                await ws.send(json.dumps({"type": "speech_start"}))
            for f in out.frames:
                await ws.send(f)
            if out.ended:
                await ws.send(json.dumps({"type": "speech_end"}))


async def run(settings: ClientSettings) -> None:
    loop = asyncio.get_running_loop()
    device: Device | None = None

    def on_done(stream_id: int) -> None:
        if device is not None:
            device.playback_finished(stream_id)

    mic = Microphone(settings.input_device, loop)
    player = Player(settings.output_device, loop, on_done, volume=settings.volume)
    gate = VadGate(webrtc_detector(settings.vad_aggressiveness), hangover_ms=settings.vad_hangover_ms)
    device = Device(settings, mic, player, gate)
    player.start()
    mic.start()
    try:
        await device.run_forever()
    finally:
        mic.stop()
        player.close()
