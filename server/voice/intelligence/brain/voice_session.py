"""One Haiku chat, run through the Claude Agent SDK.

A VoiceSession is a ClaudeSDKClient with:
  * a custom system prompt (voice rules + routing rules + long-term memory),
  * no Claude Code tools except WebSearch,
  * in-process tools (an SDK MCP server) to hand work to Sonnet/Opus,
    manage tasks, and remember things,
  * partial-message streaming, so text reaches text-to-speech token by token.

When Haiku calls ask_expert, the expert's answer is streamed into the same
output as Haiku's own words, so the user hears one continuous reply; Haiku is
then muted for the rest of that turn, so it doesn't repeat the answer.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    StreamEvent,
    create_sdk_mcp_server,
    query,
    tool,
)

from ..config import Settings
from ..memory import Memory
from .prompts import SUMMARIZE, voice_system_prompt
from .routing import (
    QUESTION_DIFFICULTIES,
    TASK_KINDS,
    classify_task,
    model_for_question,
    model_for_task,
    short_name,
)
from .tasks import TaskManager

log = logging.getLogger(__name__)

TOOL_SERVER = "assistant"


def _text(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


class VoiceSession:
    def __init__(
        self,
        settings: Settings,
        memory: Memory,
        tasks: TaskManager,
        client_factory: Callable[[ClaudeAgentOptions], Any] = ClaudeSDKClient,
        query_fn: Callable[..., Any] = query,
    ) -> None:
        self._s = settings
        self._memory = memory
        self._tasks = tasks
        self._client_factory = client_factory
        self._query = query_fn
        self._client: Any = None
        self._lock = asyncio.Lock()
        self._out: asyncio.Queue[str | None] | None = None
        self._muted = False
        self._emitted = False
        self.broken = False
        self.stop_listening_requested = False
        self.created_at = time.monotonic()
        self.last_used = self.created_at
        self.started_wall = time.time()
        self.transcript: list[tuple[str, str]] = []
        try:
            self._tz = ZoneInfo(settings.timezone)
        except Exception:
            self._tz = ZoneInfo("UTC")
        self.tools = self._make_tools()

    # ---- Setup ------------------------------------------------------------------------
    def options(self) -> ClaudeAgentOptions:
        server = create_sdk_mcp_server(TOOL_SERVER, tools=self.tools)
        work_dir = self._s.data_dir / "voice-agent"
        work_dir.mkdir(parents=True, exist_ok=True)
        return ClaudeAgentOptions(
            model=self._s.voice.model,
            effort=self._s.voice.effort,  # type: ignore[arg-type]
            system_prompt=voice_system_prompt(
                self._s.assistant_name, self._s.user_name, self._memory.context()
            ),
            mcp_servers={TOOL_SERVER: server},
            tools=["WebSearch"],
            allowed_tools=["WebSearch"] + [f"mcp__{TOOL_SERVER}__{t.name}" for t in self.tools],
            permission_mode="dontAsk",  # anything not allowed above is refused, never prompted
            setting_sources=[],  # ignore any settings files on disk
            include_partial_messages=True,
            max_turns=8,
            cwd=str(work_dir),
            stderr=lambda line: log.debug("voice cli: %s", line),
        )

    async def connect(self) -> None:
        t0 = time.monotonic()
        self._client = self._client_factory(self.options())
        await self._client.connect()
        log.info("voice session ready in %.1f s (%s)", time.monotonic() - t0, self._s.voice.model)

    async def close(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.disconnect()
            self._client = None

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    @property
    def idle_seconds(self) -> float:
        return time.monotonic() - self.last_used

    # ---- A turn ----------------------------------------------------------------------------
    async def respond(self, text: str, *, event: bool = False) -> AsyncIterator[str]:
        """Sends the user's words (or an event such as a finished task) and
        yields the reply as it streams. Newlines separate separate thoughts."""
        async with self._lock:
            self.last_used = time.monotonic()
            self.stop_listening_requested = False
            self._muted = False
            self._emitted = False
            out: asyncio.Queue[str | None] = asyncio.Queue()
            self._out = out
            now = datetime.now(self._tz).strftime("%A %d %B %Y, %H:%M")
            self.transcript.append(("event" if event else "user", text))
            producer = asyncio.create_task(self._produce(f"[{now}] {text}", out))
            said: list[str] = []
            finished = False
            try:
                while (item := await out.get()) is not None:
                    said.append(item)
                    yield item
                finished = True
            finally:
                self._out = None
                if not finished and not producer.done():
                    # The listener went away mid-reply (e.g. the device disconnected).
                    with contextlib.suppress(Exception):
                        await self._client.interrupt()
                    try:
                        await asyncio.wait_for(producer, 10)
                    except (asyncio.TimeoutError, Exception):
                        producer.cancel()
                        self.broken = True  # unknown CLI state: don't reuse this session
                self.transcript.append(("assistant", "".join(said).strip()))
                self.last_used = time.monotonic()

    async def _produce(self, prompt: str, out: asyncio.Queue[str | None]) -> None:
        try:
            await self._client.query(prompt)
            async for msg in self._client.receive_response():
                if isinstance(msg, StreamEvent):
                    if msg.parent_tool_use_id is not None:
                        continue
                    ev = msg.event
                    etype = ev.get("type")
                    if etype == "content_block_start" and ev.get("content_block", {}).get("type") == "text":
                        self._emit("\n")
                    elif etype == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                        self._emit(ev["delta"]["text"])
                elif isinstance(msg, ResultMessage):
                    if msg.is_error:
                        log.error("voice turn failed: %s %s", msg.subtype, msg.errors or msg.result)
                        if not self._emitted:
                            self._emit("\nSorry, something went wrong on my end.")
                    log.debug(
                        "voice turn: %d ms, $%.4f", msg.duration_ms, msg.total_cost_usd or 0.0
                    )
        except Exception:
            log.exception("voice session error")
            self.broken = True
            self._emit("\nSorry, I lost my train of thought. Please try again.")
        finally:
            out.put_nowait(None)

    def _emit(self, text: str, *, force: bool = False) -> None:
        if self._out is not None and (force or not self._muted):
            self._out.put_nowait(text)
            if text.strip():
                self._emitted = True

    # ---- Tools Haiku can call ------------------------------------------------------------------
    def _make_tools(self) -> list[Any]:
        s, memory, tasks = self._s, self._memory, self._tasks

        @tool(
            "ask_expert",
            "Ask a more capable model a question while the user waits; its answer is spoken to "
            "the user directly. difficulty 'medium' uses Claude Sonnet (careful reasoning, or "
            "anything needing the computer or the user's connected accounts, like their calendar "
            "or email); 'hard' uses Claude Opus (genuinely hard problems). Write the question so "
            "it stands on its own, with all context from the conversation.",
            {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "difficulty": {"type": "string", "enum": list(QUESTION_DIFFICULTIES)},
                },
                "required": ["question", "difficulty"],
            },
        )
        async def ask_expert(args: dict[str, Any]) -> dict[str, Any]:
            choice = model_for_question(s, args.get("difficulty", "medium"))
            self._emit("\n", force=True)
            answer = await tasks.ask(
                str(args["question"]), choice, lambda t: self._emit(t, force=True)
            )
            self._muted = True  # the user heard the answer; don't let Haiku repeat it
            return _text(
                f"{short_name(choice.model)} answered, and the answer has ALREADY been spoken to "
                f"the user in full:\n<expert_answer>\n{answer}\n</expert_answer>\n"
                "Do not repeat or summarize it. End your turn now."
            )

        @tool(
            "start_task",
            "Start a background job on a more capable model with access to a computer and the "
            "user's connected accounts. kind 'action' (Claude Sonnet): do something, such as "
            "email, calendar, files, or research. 'complex' (Claude Opus): difficult multi-step "
            "jobs. 'build' (Claude Opus, high effort): ALWAYS for building any app, script, "
            "program, website, bot, tool or automation. The task text must be a complete, "
            "self-contained instruction.",
            {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "kind": {"type": "string", "enum": list(TASK_KINDS)},
                },
                "required": ["task", "kind"],
            },
        )
        async def start_task(args: dict[str, Any]) -> dict[str, Any]:
            request = str(args["task"])
            kind = classify_task(str(args.get("kind", "action")), request)
            choice = model_for_task(s, kind)
            task_id = tasks.start(request, kind, choice)
            log.info("task #%d started: %s on %s/%s", task_id, kind, choice.model, choice.effort)
            return _text(
                f"Task #{task_id} is running in the background on {short_name(choice.model)} "
                f"({choice.effort} effort). Tell the user in a few words and ask if they need "
                "anything else."
            )

        @tool("list_tasks", "List running and recently finished background tasks.", {})
        async def list_tasks(args: dict[str, Any]) -> dict[str, Any]:
            active = memory.active_tasks()
            recent = [t for t in memory.recent_tasks(6) if t.status not in ("queued", "running")]
            lines = [f"#{t.id} running ({t.kind}, {short_name(t.model)}): {t.request[:160]}" for t in active]
            lines += [f"#{t.id} {t.status}: {t.request[:120]} -> {t.result[:200]}" for t in recent]
            return _text("\n".join(lines) or "No tasks yet.")

        @tool(
            "cancel_task",
            "Stop a running background task.",
            {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]},
        )
        async def cancel_task(args: dict[str, Any]) -> dict[str, Any]:
            ok = await tasks.cancel(int(args["task_id"]))
            return _text("Cancelled." if ok else "That task is not running.")

        @tool(
            "remember",
            "Save a lasting fact or preference about the user to long-term memory.",
            {"type": "object", "properties": {"fact": {"type": "string"}}, "required": ["fact"]},
        )
        async def remember(args: dict[str, Any]) -> dict[str, Any]:
            fid = memory.add_fact(str(args["fact"]))
            return _text(f"Saved as memory [{fid}].")

        @tool(
            "forget",
            "Delete a remembered fact by its number.",
            {"type": "object", "properties": {"fact_id": {"type": "integer"}}, "required": ["fact_id"]},
        )
        async def forget(args: dict[str, Any]) -> dict[str, Any]:
            ok = memory.forget_fact(int(args["fact_id"]))
            return _text("Forgotten." if ok else "No memory with that number.")

        @tool(
            "stop_listening",
            "Stop listening for follow-ups right after this reply (the user is done). The wake "
            "phrase will be needed again.",
            {},
        )
        async def stop_listening(args: dict[str, Any]) -> dict[str, Any]:
            self.stop_listening_requested = True
            return _text("OK. Say your short goodbye now.")

        return [ask_expert, start_task, list_tasks, cancel_task, remember, forget, stop_listening]

    def tool(self, name: str) -> Any:
        return next(t for t in self.tools if t.name == name)

    # ---- End of the chat -----------------------------------------------------------------------
    async def summarize(self) -> str | None:
        """A short memory note about this chat, written by Haiku at low effort."""
        turns = [(r, t) for r, t in self.transcript if t]
        if not any(r == "user" for r, _ in turns):
            return None
        transcript = "\n".join(f"{r}: {t}" for r, t in turns)[-20000:]
        options = ClaudeAgentOptions(
            model=self._s.voice.model,
            effort="low",
            system_prompt="You write brief, factual memory notes.",
            tools=[],
            setting_sources=[],
            permission_mode="dontAsk",
            max_turns=1,
            cwd=str(self._s.data_dir / "voice-agent"),
        )
        result = None
        async for msg in self._query(prompt=SUMMARIZE.format(transcript=transcript), options=options):
            if isinstance(msg, ResultMessage) and not msg.is_error:
                result = msg.result
        return result
