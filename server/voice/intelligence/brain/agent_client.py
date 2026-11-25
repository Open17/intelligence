"""Client for the agent service (the sandboxed container where Sonnet and
Opus run with a full toolchain and the user's connectors).

One WebSocket per job, authenticated with a shared bearer token, on the
Docker network that only the two containers share.

    -> {"type":"run", "task_id", "model", "effort", "mode", "kind", "prompt", "memory"}
    <- {"type":"delta", "text"}        foreground jobs: text to speak, as it streams
    <- {"type":"progress", "text"}     e.g. "Running Bash"
    <- {"type":"done", "ok", "result", "cost_usd"}
    -> {"type":"cancel"}
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol

from websockets.asyncio.client import connect

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentJob:
    task_id: int
    model: str
    effort: str
    mode: str  # "foreground" (streamed and spoken) | "background"
    kind: str  # "question" | "action" | "complex" | "build"
    prompt: str
    memory: str


class AgentBackend(Protocol):
    def run(self, job: AgentJob, cancel: asyncio.Event) -> AsyncIterator[dict[str, Any]]: ...


class AgentClient:
    def __init__(self, url: str, token: str) -> None:
        self._url = url
        self._token = token

    async def run(self, job: AgentJob, cancel: asyncio.Event) -> AsyncIterator[dict[str, Any]]:
        """Runs ``job``; yields the service's messages until 'done'. Setting
        ``cancel`` asks the service to stop the job."""
        async with connect(
            self._url,
            additional_headers={"Authorization": f"Bearer {self._token}"},
            max_size=4 * 1024 * 1024,
            open_timeout=15,
            proxy=None,
        ) as ws:
            await ws.send(json.dumps({"type": "run", **job.__dict__}))

            async def watch_cancel() -> None:
                await cancel.wait()
                await ws.send(json.dumps({"type": "cancel"}))

            watcher = asyncio.create_task(watch_cancel())
            try:
                async for raw in ws:
                    msg = json.loads(raw)
                    yield msg
                    if msg.get("type") in ("done", "error"):
                        return
                yield {"type": "error", "message": "agent service closed the connection"}
            finally:
                watcher.cancel()
