"""WebSocket API for the voice container. Only reachable on the internal
Docker network, and every connection must present the shared bearer token."""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
from http import HTTPStatus
from typing import Any

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from .config import AgentSettings
from .runner import Job, run_job

log = logging.getLogger(__name__)


class AgentServer:
    def __init__(self, settings: AgentSettings, run=run_job) -> None:
        if len(settings.token) < 32:
            raise ValueError("INTEL_AGENT_TOKEN must be at least 32 characters")
        self._s = settings
        self._run = run
        self._background = asyncio.Semaphore(settings.max_background_jobs)
        self._server: Server | None = None

    def _authorize(self, connection: ServerConnection, request: Request) -> Response | None:
        given = request.headers.get("Authorization", "")
        expected = f"Bearer {self._s.token}"
        if not hmac.compare_digest(given.encode(), expected.encode()):
            log.warning("refused unauthenticated connection from %s", connection.remote_address)
            return connection.respond(HTTPStatus.UNAUTHORIZED, "unauthorized\n")
        return None

    async def start(self) -> None:
        self._server = await serve(
            self._handle,
            self._s.listen_host,
            self._s.listen_port,
            process_request=self._authorize,
            max_size=1024 * 1024,
            compression=None,
            server_header=None,
        )
        log.info("agent service listening on %s:%d", self._s.listen_host, self._s.listen_port)

    async def close(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, ws: ServerConnection) -> None:
        async def emit(msg: dict[str, Any]) -> None:
            with contextlib.suppress(ConnectionClosed):
                await ws.send(json.dumps(msg))

        try:
            job = Job.parse(json.loads(await asyncio.wait_for(ws.recv(), 10)))
        except (asyncio.TimeoutError, ValueError, KeyError, TypeError, json.JSONDecodeError) as e:
            await emit({"type": "error", "message": f"bad request: {e}"})
            return
        except ConnectionClosed:
            return

        cancel = asyncio.Event()

        async def listen_for_cancel() -> None:
            # If the voice server disconnects, the job is cancelled too, so
            # nothing keeps running (and spending) without anyone to report to.
            with contextlib.suppress(ConnectionClosed):
                async for raw in ws:
                    if isinstance(raw, str) and json.loads(raw).get("type") == "cancel":
                        break
            cancel.set()

        listener = asyncio.create_task(listen_for_cancel())
        log.info("task #%d: %s %s on %s/%s", job.task_id, job.mode, job.kind, job.model, job.effort)
        try:
            if job.mode == "background":
                async with self._background:
                    await self._run(self._s, job, emit, cancel)
            else:
                await self._run(self._s, job, emit, cancel)
        finally:
            listener.cancel()
