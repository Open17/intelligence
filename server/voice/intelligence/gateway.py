"""The device-facing server: WebSocket over mutual TLS 1.3.

Security layers, outermost first (see docs/security.md):
  1. WireGuard: the port is only reachable through the VPN tunnel.
  2. TLS 1.3 with a private CA: the device pins our CA; we require a client
     certificate signed by the same CA. No certificate, no handshake.
  3. Device allow-list: the certificate's common name must be allowed
     (INTEL_ALLOWED_DEVICES), so a revoked device can be shut out by name.
  4. Strict protocol: size-limited frames, a hello within 5 s, no compression.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import ssl
from collections.abc import Awaitable, Callable
from typing import Any

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from . import protocol
from .config import Settings
from .conversation import Conversation, Speaker, Thinker
from .memory import Task
from .stt import Heard, SpeechToText

log = logging.getLogger(__name__)

SttFactory = Callable[[Settings, Callable[[Heard], Awaitable[None]], str], Any]


def server_tls_context(settings: Settings) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.load_cert_chain(settings.tls_cert, settings.tls_key)
    ctx.load_verify_locations(cafile=str(settings.tls_ca))
    ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


def peer_common_name(ws: ServerConnection) -> str | None:
    cert = ws.transport.get_extra_info("peercert") if ws.transport else None
    if not cert:
        return None
    for rdn in cert.get("subject", ()):
        for key, value in rdn:
            if key == "commonName":
                return str(value)
    return None


class Gateway:
    def __init__(
        self,
        settings: Settings,
        brain: Thinker,
        tts: Speaker,
        stt_factory: SttFactory = SpeechToText,
    ) -> None:
        self._s = settings
        self._brain = brain
        self._tts = tts
        self._stt_factory = stt_factory
        self._conversations: dict[str, Conversation] = {}
        self._connections: dict[str, ServerConnection] = {}
        self._server: Server | None = None

    async def start(self) -> None:
        self._server = await serve(
            self._handle,
            self._s.listen_host,
            self._s.listen_port,
            ssl=server_tls_context(self._s),
            max_size=protocol.MAX_FRAME_BYTES,
            compression=None,  # audio doesn't compress, and compression + TLS leaks length
            ping_interval=15,
            ping_timeout=15,
            server_header=None,
        )
        log.info("listening on %s:%d (mutual TLS 1.3)", self._s.listen_host, self._s.listen_port)

    async def close(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def announce(self, task: Task) -> None:
        """Speaks a finished task on the device that was used most recently."""
        if not self._conversations:
            log.info("task #%d finished while no device was connected", task.id)
            return
        conv = max(self._conversations.values(), key=lambda c: c.last_active)
        await conv.announce(task)

    async def _handle(self, ws: ServerConnection) -> None:
        name = peer_common_name(ws)
        if not name or (self._s.allowed_devices and name not in self._s.allowed_devices):
            log.warning("refused device %r from %s", name, ws.remote_address)
            await ws.close(4003, "device not allowed")
            return

        try:
            first = await asyncio.wait_for(ws.recv(), 5)
            hello = protocol.decode(first) if isinstance(first, str) else {}
            if hello.get("type") != "hello" or hello.get("version") != protocol.PROTOCOL_VERSION:
                raise protocol.ProtocolError("expected hello with a supported version")
        except (asyncio.TimeoutError, protocol.ProtocolError, ConnectionClosed) as e:
            log.warning("[%s] bad handshake: %s", name, e)
            with contextlib.suppress(Exception):
                await ws.close(4002, "bad hello")
            return

        # One connection per device: a reconnecting Pi replaces its old socket.
        old = self._connections.get(name)
        if old is not None:
            await old.close(4000, "replaced by a new connection")

        async def send_json(type_: str, **fields: Any) -> None:
            with contextlib.suppress(ConnectionClosed):
                await ws.send(protocol.encode(type_, **fields))

        async def send_bytes(data: bytes) -> None:
            await ws.send(data)

        conv = Conversation(self._s, self._brain, self._tts, send_json, send_bytes, name=name)
        stt = self._stt_factory(self._s, conv.on_heard, name)
        try:
            await stt.start()
        except Exception:
            log.exception("[%s] could not start speech-to-text", name)
            await send_json("error", message="speech-to-text failed to start")
            await ws.close(1011, "stt failed")
            return

        self._connections[name] = ws
        self._conversations[name] = conv
        log.info("[%s] connected from %s", name, ws.remote_address[0] if ws.remote_address else "?")
        await send_json("ready", version=protocol.PROTOCOL_VERSION, followup_seconds=self._s.followup_seconds)
        await send_json("state", state="idle")

        try:
            async for frame in ws:
                if isinstance(frame, bytes):
                    stt.push(frame)
                    continue
                try:
                    msg = protocol.decode(frame)
                except protocol.ProtocolError as e:
                    log.warning("[%s] %s", name, e)
                    continue
                kind = msg["type"]
                if kind == "speech_end":
                    stt.end_of_speech()
                elif kind == "playback_done":
                    await conv.on_playback_done(int(msg.get("id", -1)))
                elif kind == "ping":
                    await send_json("pong")
        except ConnectionClosed:
            pass
        finally:
            log.info("[%s] disconnected", name)
            if self._connections.get(name) is ws:
                del self._connections[name]
                del self._conversations[name]
            await conv.close()
            await stt.close()
