"""Runs jobs on Sonnet/Opus through the agent service and keeps their records.

* ask()   - foreground: the user is waiting; text streams back to be spoken.
* start() - background: returns immediately; ``on_finished`` is called when
            the job ends, so the assistant can announce the result.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

from ..config import ModelChoice
from ..memory import Memory, Task
from .agent_client import AgentBackend, AgentJob

log = logging.getLogger(__name__)

FinishedHandler = Callable[[Task], Awaitable[None]]


class TaskManager:
    def __init__(self, memory: Memory, agent: AgentBackend, on_finished: FinishedHandler) -> None:
        self._memory = memory
        self._agent = agent
        self._on_finished = on_finished
        self._running: dict[int, tuple[asyncio.Task[None], asyncio.Event]] = {}

    async def ask(self, question: str, choice: ModelChoice, speak: Callable[[str], None]) -> str:
        """Asks Sonnet/Opus a question; ``speak`` receives the answer as it streams."""
        task_id = self._memory.create_task("question", choice.model, choice.effort, question)
        cancel = asyncio.Event()
        job = AgentJob(
            task_id, choice.model, choice.effort, "foreground", "question", question, self._memory.context()
        )
        self._memory.update_task(task_id, "running")
        spoken: list[str] = []
        try:
            async for msg in self._agent.run(job, cancel):
                kind = msg.get("type")
                if kind == "delta":
                    spoken.append(msg["text"])
                    speak(msg["text"])
                elif kind == "done":
                    result = msg.get("result") or "".join(spoken)
                    if not spoken and result:  # service sent no deltas: speak the result
                        speak(result)
                    self._memory.update_task(
                        task_id, "done" if msg.get("ok") else "failed", result, msg.get("cost_usd", 0)
                    )
                    return result if msg.get("ok") else f"(The expert failed: {result})"
                elif kind == "error":
                    raise RuntimeError(msg.get("message", "unknown error"))
            raise RuntimeError("agent service ended without a result")
        except asyncio.CancelledError:
            cancel.set()
            self._memory.update_task(task_id, "cancelled", "".join(spoken))
            raise
        except Exception as e:
            log.exception("expert question #%d failed", task_id)
            self._memory.update_task(task_id, "failed", str(e))
            return f"(The expert could not be reached: {e})"

    def start(self, request: str, kind: str, choice: ModelChoice) -> int:
        task_id = self._memory.create_task(kind, choice.model, choice.effort, request)
        cancel = asyncio.Event()
        job = AgentJob(task_id, choice.model, choice.effort, "background", kind, request, self._memory.context())
        runner = asyncio.create_task(self._run_background(job, cancel), name=f"task-{task_id}")
        self._running[task_id] = (runner, cancel)
        return task_id

    async def _run_background(self, job: AgentJob, cancel: asyncio.Event) -> None:
        status, result, cost = "failed", "", 0.0
        self._memory.update_task(job.task_id, "running")
        try:
            async for msg in self._agent.run(job, cancel):
                if msg.get("type") == "done":
                    status = "done" if msg.get("ok") else "failed"
                    result, cost = msg.get("result", ""), msg.get("cost_usd", 0.0)
                elif msg.get("type") == "error":
                    result = msg.get("message", "unknown error")
            if cancel.is_set():
                status = "cancelled"
        except Exception as e:
            log.exception("task #%d failed", job.task_id)
            result = f"Could not run the task: {e}"
        finally:
            self._running.pop(job.task_id, None)
            self._memory.update_task(job.task_id, status, result, cost)
        log.info("task #%d finished: %s ($%.2f)", job.task_id, status, cost)
        task = self._memory.task(job.task_id)
        if task and status != "cancelled":
            try:
                await self._on_finished(task)
            except Exception:
                log.exception("announcing task #%d failed", job.task_id)

    async def cancel(self, task_id: int) -> bool:
        entry = self._running.get(task_id)
        if not entry:
            return False
        runner, cancel = entry
        cancel.set()
        try:
            await asyncio.wait_for(asyncio.shield(runner), 15)
        except asyncio.TimeoutError:
            runner.cancel()
        return True

    async def close(self) -> None:
        for runner, cancel in list(self._running.values()):
            cancel.set()
            runner.cancel()
            with contextlib.suppress(BaseException):
                await runner
