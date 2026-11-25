"""Client settings, read from /etc/intelligence/client.env (written by local_setup.sh)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ENV_FILE = Path("/etc/intelligence/client.env")


def load_env_file(path: Path) -> dict[str, str]:
    """Parses KEY=VALUE lines (comments and blank lines ignored)."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class ClientSettings:
    server_url: str = "wss://10.77.0.1:8765"
    server_name: str = "intelligence-server"  # name in the server's certificate
    ca_cert: Path = Path("/etc/intelligence/ca.crt")
    client_cert: Path = Path("/etc/intelligence/device.crt")
    client_key: Path = Path("/etc/intelligence/device.key")
    device_name: str = "pi"
    input_device: str = ""  # sounddevice index or name substring; "" = system default
    output_device: str = ""
    vad_aggressiveness: int = 2  # 0 (lenient) .. 3 (strict about what counts as speech)
    vad_hangover_ms: int = 900  # keep sending this long after speech stops
    volume: float = 1.0
    earcons: bool = True
    log_level: str = "INFO"

    @classmethod
    def load(cls, env_file: Path = DEFAULT_ENV_FILE) -> "ClientSettings":
        v = {**load_env_file(env_file), **{k: val for k, val in os.environ.items() if k.startswith("INTEL_")}}
        g = lambda key, default: v.get(key, str(default))  # noqa: E731
        return cls(
            server_url=g("INTEL_SERVER_URL", cls.server_url),
            server_name=g("INTEL_SERVER_NAME", cls.server_name),
            ca_cert=Path(g("INTEL_CA_CERT", cls.ca_cert)),
            client_cert=Path(g("INTEL_CLIENT_CERT", cls.client_cert)),
            client_key=Path(g("INTEL_CLIENT_KEY", cls.client_key)),
            device_name=g("INTEL_DEVICE_NAME", cls.device_name),
            input_device=g("INTEL_INPUT_DEVICE", cls.input_device),
            output_device=g("INTEL_OUTPUT_DEVICE", cls.output_device),
            vad_aggressiveness=int(g("INTEL_VAD_AGGRESSIVENESS", cls.vad_aggressiveness)),
            vad_hangover_ms=int(g("INTEL_VAD_HANGOVER_MS", cls.vad_hangover_ms)),
            volume=float(g("INTEL_VOLUME", cls.volume)),
            earcons=g("INTEL_EARCONS", "true").lower() in {"1", "true", "yes", "on"},
            log_level=g("INTEL_LOG_LEVEL", cls.log_level).upper(),
        )
