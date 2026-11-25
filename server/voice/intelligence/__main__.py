"""Entry point: ``python -m intelligence`` (the voice container's command)."""

from __future__ import annotations

import asyncio
import logging
import signal

from . import __version__
from .brain import Brain
from .brain.agent_client import AgentClient
from .config import Settings
from .gateway import Gateway
from .memory import Memory
from .tts import TextToSpeech

log = logging.getLogger("intelligence")


async def run(settings: Settings) -> None:
    if not settings.agent_token:
        raise SystemExit("INTEL_AGENT_TOKEN(_FILE) is not set; run server_setup.sh")

    memory = Memory(settings.data_dir / "memory.db", settings.timezone)
    brain = Brain(settings, memory, AgentClient(settings.agent_url, settings.agent_token))
    tts = TextToSpeech(settings.piper_voice, settings.tts_length_scale)
    gateway = Gateway(settings, brain, tts)
    brain.on_task_finished = gateway.announce

    await asyncio.gather(tts.load(), brain.start())
    await gateway.start()
    log.info(
        "intelligence %s ready: voice=%s/%s action=%s/%s hard=%s/%s build=%s/%s",
        __version__,
        settings.voice.model, settings.voice.effort,
        settings.action.model, settings.action.effort,
        settings.hard.model, settings.hard.effort,
        settings.build.model, settings.build.effort,
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()

    log.info("shutting down")
    await gateway.close()
    await brain.close()
    tts.close()
    memory.close()


def main() -> None:
    settings = Settings.from_env()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # Third-party libraries are chatty at INFO; keep the log about us.
    for noisy in ("websockets", "claude_agent_sdk", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
