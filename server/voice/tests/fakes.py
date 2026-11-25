"""Test doubles for the Claude Agent SDK client and the agent service."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

from claude_agent_sdk import ResultMessage, StreamEvent

from intelligence.brain.agent_client import AgentJob


def text_event(text: str) -> StreamEvent:
    return StreamEvent(
        uuid="u", session_id="s", event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}
    )


def block_start() -> StreamEvent:
    return StreamEvent(
        uuid="u", session_id="s", event={"type": "content_block_start", "content_block": {"type": "text"}}
    )


def result(text: str = "", is_error: bool = False) -> ResultMessage:
    return ResultMessage(
        subtype="success", duration_ms=5, duration_api_ms=4, is_error=is_error, num_turns=1,
        session_id="s", result=text, total_cost_usd=0.0001,
    )


Script = Callable[[str, Any], AsyncIterator[Any]]


class FakeSDKClient:
    """Plays a script instead of running the Claude Code CLI. The script gets
    the prompt and the VoiceSession, so it can call real tool handlers."""

    instances: list["FakeSDKClient"] = []

    def __init__(self, options: Any, script: Script, session_ref: Callable[[], Any]) -> None:
        self.options = options
        self._script = script
        self._session_ref = session_ref
        self.prompts: list[str] = []
        self.connected = False
        self.interrupted = False
        FakeSDKClient.instances.append(self)

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def interrupt(self) -> None:
        self.interrupted = True

    async def query(self, prompt: str) -> None:
        self.prompts.append(prompt)

    async def receive_response(self) -> AsyncIterator[Any]:
        async for msg in self._script(self.prompts[-1], self._session_ref()):
            if self.interrupted:
                yield result("interrupted")
                return
            yield msg


def fake_query_factory(summary: str) -> Callable[..., AsyncIterator[Any]]:
    async def fake_query(*, prompt: str, options: Any) -> AsyncIterator[Any]:
        fake_query.prompts.append(prompt)  # type: ignore[attr-defined]
        yield result(summary)

    fake_query.prompts = []  # type: ignore[attr-defined]
    return fake_query


class FakeAgent:
    """The agent service: records jobs and answers with canned text."""

    def __init__(self, answer: str = "The answer is 42.", delay: float = 0.0, ok: bool = True) -> None:
        self.jobs: list[AgentJob] = []
        self.answer = answer
        self.delay = delay
        self.ok = ok
        self.cancelled: list[int] = []

    async def run(self, job: AgentJob, cancel: asyncio.Event) -> AsyncIterator[dict[str, Any]]:
        self.jobs.append(job)
        if self.delay:
            try:
                await asyncio.wait_for(cancel.wait(), self.delay)
                self.cancelled.append(job.task_id)
                yield {"type": "done", "ok": False, "result": "Cancelled.", "cost_usd": 0.0}
                return
            except asyncio.TimeoutError:
                pass
        if job.mode == "foreground":
            for word in self.answer.split(" "):
                yield {"type": "delta", "text": word + " "}
        yield {"type": "done", "ok": self.ok, "result": self.answer, "cost_usd": 0.01}


async def collect(gen: AsyncIterator[str]) -> str:
    return "".join([x async for x in gen])


async def until(cond: Callable[[], bool], timeout: float = 3.0) -> None:
    async def wait() -> None:
        while not cond():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(wait(), timeout)

