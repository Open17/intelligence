"""Entry point: ``python -m agent_service`` (the agent container's command)."""

from __future__ import annotations

import asyncio
import logging
import signal

from .config import AgentSettings
from .server import AgentServer


async def run(settings: AgentSettings) -> None:
    settings.workspace.mkdir(parents=True, exist_ok=True)
    (settings.workspace / "projects").mkdir(exist_ok=True)
    server = AgentServer(settings)
    await server.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    await server.close()


def main() -> None:
    settings = AgentSettings.from_env()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    for noisy in ("websockets", "claude_agent_sdk"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
