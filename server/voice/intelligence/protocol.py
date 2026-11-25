"""The wire protocol between a device (the Pi) and the voice server.

One WebSocket per device, over mutual TLS, inside the WireGuard tunnel.

Binary frames
    device -> server : microphone audio, PCM s16le, 16 kHz, mono
    server -> device : speech audio, PCM s16le, mono, at the rate given in
                       the preceding ``audio_start`` message

Text frames are JSON objects with a ``type`` field:

    device -> server
        hello          {version, device}                    first message
        speech_start   {}       local VAD heard speech; audio follows
        speech_end     {}       local VAD gate closed; audio pauses
        playback_done  {id}     finished playing audio stream ``id``
        ping           {}

    server -> device
        ready          {version, followup_seconds}
        state          {state}  idle | listening | thinking | speaking
        earcon         {name}   wake | ok | error  (played locally, no latency)
        audio_start    {id, sample_rate}
        audio_end      {id}
        stop_audio     {}       drop anything queued for playback
        pong           {}
        error          {message}

docs/protocol.md has the same table with timing diagrams.
"""

from __future__ import annotations

import json
from typing import Any

PROTOCOL_VERSION = 1
MIC_SAMPLE_RATE = 16000
MAX_FRAME_BYTES = 64 * 1024  # 2 s of 16 kHz audio; anything bigger is refused
MAX_TEXT_FRAME_BYTES = 4 * 1024

STATES = ("idle", "listening", "thinking", "speaking")
EARCONS = ("wake", "ok", "error")


class ProtocolError(ValueError):
    """A device sent something that does not follow the protocol."""


def encode(type_: str, **fields: Any) -> str:
    return json.dumps({"type": type_, **fields}, separators=(",", ":"))


def decode(frame: str) -> dict[str, Any]:
    if len(frame) > MAX_TEXT_FRAME_BYTES:
        raise ProtocolError("text frame too large")
    try:
        msg = json.loads(frame)
    except json.JSONDecodeError as e:
        raise ProtocolError(f"invalid JSON: {e}") from None
    if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
        raise ProtocolError("message must be an object with a string 'type'")
    return msg
