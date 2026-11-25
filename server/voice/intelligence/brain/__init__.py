"""The brain: Haiku chats, the 5-minute reset, and the jobs Sonnet/Opus run.

    Brain.respond(text)     -> streamed reply text
    Brain.on_task_finished  -> set by the conversation layer to announce results

Chat lifetime
    A chat that has been idle for ``session_idle_seconds`` (5 minutes) is
    retired: Haiku writes a short summary into long-term memory, and the next
    request starts a brand-new chat whose system prompt carries that memory.
    Long chats get expensive because every turn resends the history; a fresh
    chat with a summary costs a fraction and remembers the same things.

    A spare chat is connected ahead of time, so waking the assistant never
    waits for the Agent SDK to start up.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from ..config import Settings
from ..memory import Memory, Task
from .agent_client import AgentBackend
from .tasks import TaskManager
from .voice_session import VoiceSession

log = logging.getLogger(__name__)

IDLE_CHECK_SECONDS = 5.0

SessionFactory = Callable[[], VoiceSession]


class Brain:
    def __init__(
        self,
        settings: Settings,
        memory: Memory,
        agent: AgentBackend,
        session_factory: Callable[["Brain"], VoiceSession] | None = None,
    ) -> None:
        self._s = settings
        self.memory = memory
        self.tasks = TaskManager(memory, agent, self._task_finished)
        self._factory = session_factory or (lambda b: VoiceSession(b._s, b.memory, b.tasks))
        self._current: VoiceSession | None = None
        self._spare: VoiceSession | None = None
        self._spare_task: asyncio.Task[None] | None = None
        self._watch_task: asyncio.Task[None] | None = None
        self._background: set[asyncio.Task[Any]] = set()
        self.on_task_finished: Callable[[Task], Awaitable[None]] | None = None

    async def start(self) -> None:
        self._prepare_spare()
        self._watch_task = asyncio.create_task(self._watch_idle(), name="brain-idle")

    # ---- Sessions ---------------------------------------------------------------------
    def _prepare_spare(self) -> None:
        if self._spare is None and (self._spare_task is None or self._spare_task.done()):
            self._spare_task = asyncio.create_task(self._connect_spare(), name="brain-spare")

    async def _connect_spare(self) -> None:
        session = self._factory(self)
        try:
            await session.connect()
        except Exception:
            log.exception("could not start a voice session (check the API key / network)")
            await session.close()
            return
        self._spare = session

    async def _session(self) -> VoiceSession:
        cur = self._current
        if cur is not None and (cur.broken or cur.idle_seconds > self._s.session_idle_seconds):
            self._retire()
        if self._current is None:
            if self._spare is None and self._spare_task is not None:
                with contextlib.suppress(Exception):
                    await self._spare_task  # a spare is on its way: faster than starting another
            if self._spare is not None:
                self._current, self._spare = self._spare, None
            else:
                session = self._factory(self)
                await session.connect()
                self._current = session
            log.info("new chat started")
        return self._current

    def _retire(self) -> None:
        """Ends the current chat; its summary is saved and a fresh spare prepared."""
        old, self._current = self._current, None
        if old is None:
            return
        log.info("chat retired after %.0f s idle", old.idle_seconds)
        t = asyncio.create_task(self._finish(old), name="brain-retire")
        self._background.add(t)
        t.add_done_callback(self._background.discard)

    async def _finish(self, session: VoiceSession) -> None:
        try:
            summary = await session.summarize()
            if summary:
                self.memory.add_conversation(session.started_wall, time.time(), summary)
        except Exception:
            log.exception("could not summarize the chat")
        finally:
            await session.close()
        # The spare is built after the summary is saved so it starts with it.
        if self._spare is not None:
            stale, self._spare = self._spare, None
            await stale.close()
        self._prepare_spare()

    async def _watch_idle(self) -> None:
        while True:
            await asyncio.sleep(IDLE_CHECK_SECONDS)
            cur = self._current
            if cur is not None and not cur.busy and cur.idle_seconds > self._s.session_idle_seconds:
                self._retire()

    # ---- Talking --------------------------------------------------------------------------
    async def respond(self, text: str, *, event: bool = False) -> AsyncIterator[str]:
        session = await self._session()
        # aclosing: if our caller stops early, the session's turn is closed (and
        # the model interrupted) right away instead of at garbage collection.
        async with contextlib.aclosing(session.respond(text, event=event)) as reply:
            async for piece in reply:
                yield piece

    @property
    def stop_listening_requested(self) -> bool:
        return bool(self._current and self._current.stop_listening_requested)

    async def _task_finished(self, task: Task) -> None:
        if self.on_task_finished is not None:
            await self.on_task_finished(task)

    async def close(self) -> None:
        if self._watch_task:
            self._watch_task.cancel()
        await self.tasks.close()
        for s in (self._current, self._spare):
            if s is not None:
                await s.close()
        for t in list(self._background):
            t.cancel()
