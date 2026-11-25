"""Every setting of the voice server, read once from environment variables.

The deploy scripts write these into ``/opt/intelligence/intelligence.env``;
the defaults below are what a fresh install uses. Secrets (API key, agent
token) are never given defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(_env(name, str(default)))


def _env_bool(name: str, default: bool) -> bool:
    return _env(name, "true" if default else "false").lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: str) -> tuple[str, ...]:
    return tuple(x.strip() for x in _env(name, default).split(",") if x.strip())


def _secret(name: str) -> str:
    """Reads NAME, or the file named by NAME_FILE (Docker secrets)."""
    path = os.environ.get(f"{name}_FILE")
    if path and Path(path).is_file():
        return Path(path).read_text(encoding="utf-8").strip()
    return os.environ.get(name, "").strip()


@dataclass(frozen=True)
class ModelChoice:
    """A Claude model plus the effort level it runs at."""

    model: str
    effort: str


@dataclass(frozen=True)
class Settings:
    # ---- Network / TLS -------------------------------------------------------------
    listen_host: str = "0.0.0.0"
    listen_port: int = 8765
    tls_cert: Path = Path("/run/secrets/server.crt")
    tls_key: Path = Path("/run/secrets/server.key")
    tls_ca: Path = Path("/run/secrets/ca.crt")
    # Client certificate common names allowed to connect (empty = any cert our CA signed).
    allowed_devices: tuple[str, ...] = ()

    # ---- Speech-to-text (echo) ------------------------------------------------------------
    echo_stream_bin: str = "/opt/echo/bin/echo-stream"
    whisper_model: str = "/opt/models/ggml-base.en.bin"
    vad_model: str = "/opt/models/silero_vad.onnx"
    stt_threads: int = 0
    stt_audio_ctx: int = 0
    stt_pause_ms: int = 700

    # ---- Text-to-speech (Piper) --------------------------------------------------------------
    piper_voice: str = "/opt/voices/voice.onnx"  # the image's default voice (en_US-hfc_female-medium)
    tts_length_scale: float = 0.95  # < 1 speaks a little faster

    # ---- Conversation behaviour ---------------------------------------------------------------
    followup_seconds: float = 10.0  # talk again within this long without the wake phrase
    session_idle_seconds: float = 300.0  # after this long, a fresh (cheaper) chat starts
    assistant_name: str = "Claude"
    user_name: str = ""
    timezone: str = "UTC"

    # ---- Models ----------------------------------------------------------------------------------
    voice: ModelChoice = ModelChoice("claude-haiku-5-5", "medium")
    action: ModelChoice = ModelChoice("claude-sonnet-5-5", "high")
    hard: ModelChoice = ModelChoice("claude-opus-5-5", "medium")
    build: ModelChoice = ModelChoice("claude-opus-5-5", "high")

    # ---- Agent service (the sandboxed "computer" container) ------------------------------------
    agent_url: str = "ws://agent:8800"
    agent_token: str = field(default="", repr=False)

    # ---- Storage / privacy -------------------------------------------------------------------------
    data_dir: Path = Path("/data")
    log_transcripts: bool = False  # never write what you say to the logs unless asked
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            listen_host=_env("INTEL_LISTEN_HOST", cls.listen_host),
            listen_port=_env_int("INTEL_LISTEN_PORT", cls.listen_port),
            tls_cert=Path(_env("INTEL_TLS_CERT", str(cls.tls_cert))),
            tls_key=Path(_env("INTEL_TLS_KEY", str(cls.tls_key))),
            tls_ca=Path(_env("INTEL_TLS_CA", str(cls.tls_ca))),
            allowed_devices=_env_list("INTEL_ALLOWED_DEVICES", ""),
            echo_stream_bin=_env("INTEL_ECHO_STREAM_BIN", cls.echo_stream_bin),
            whisper_model=_env("INTEL_WHISPER_MODEL", cls.whisper_model),
            vad_model=_env("INTEL_VAD_MODEL", cls.vad_model),
            stt_threads=_env_int("INTEL_STT_THREADS", cls.stt_threads),
            stt_audio_ctx=_env_int("INTEL_STT_AUDIO_CTX", cls.stt_audio_ctx),
            stt_pause_ms=_env_int("INTEL_STT_PAUSE_MS", cls.stt_pause_ms),
            piper_voice=_env("INTEL_PIPER_VOICE", cls.piper_voice),
            tts_length_scale=_env_float("INTEL_TTS_LENGTH_SCALE", cls.tts_length_scale),
            followup_seconds=_env_float("INTEL_FOLLOWUP_SECONDS", cls.followup_seconds),
            session_idle_seconds=_env_float("INTEL_SESSION_IDLE_SECONDS", cls.session_idle_seconds),
            assistant_name=_env("INTEL_ASSISTANT_NAME", cls.assistant_name),
            user_name=_env("INTEL_USER_NAME", cls.user_name),
            timezone=_env("TZ", cls.timezone),
            voice=ModelChoice(
                _env("INTEL_VOICE_MODEL", cls.voice.model), _env("INTEL_VOICE_EFFORT", cls.voice.effort)
            ),
            action=ModelChoice(
                _env("INTEL_ACTION_MODEL", cls.action.model), _env("INTEL_ACTION_EFFORT", cls.action.effort)
            ),
            hard=ModelChoice(
                _env("INTEL_HARD_MODEL", cls.hard.model), _env("INTEL_HARD_EFFORT", cls.hard.effort)
            ),
            build=ModelChoice(
                _env("INTEL_BUILD_MODEL", cls.build.model), _env("INTEL_BUILD_EFFORT", cls.build.effort)
            ),
            agent_url=_env("INTEL_AGENT_URL", cls.agent_url),
            agent_token=_secret("INTEL_AGENT_TOKEN"),
            data_dir=Path(_env("INTEL_DATA_DIR", str(cls.data_dir))),
            log_transcripts=_env_bool("INTEL_LOG_TRANSCRIPTS", cls.log_transcripts),
            log_level=_env("INTEL_LOG_LEVEL", cls.log_level).upper(),
        )
