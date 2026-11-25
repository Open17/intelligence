"""Runs one job on Sonnet/Opus with the Claude Agent SDK."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    StreamEvent,
    ToolUseBlock,
)

from .config import AgentSettings
from .prompts import worker_prompt

log = logging.getLogger(__name__)

ALLOWED_MODELS = {"claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-2"}
ALLOWED_EFFORTS = {"low", "medium", "high", "xhigh", "max"}
ALLOWED_MODES = {"foreground", "background"}
ALLOWED_KINDS = {"question", "action", "complex", "build"}

Emit = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class Job:
    task_id: int
    model: str
    effort: str
    mode: str
    kind: str
    prompt: str
    memory: str

    @classmethod
    def parse(cls, msg: dict[str, Any]) -> "Job":
        job = cls(
            task_id=int(msg["task_id"]),
            model=str(msg["model"]),
            effort=str(msg["effort"]),
            mode=str(msg["mode"]),
            kind=str(msg["kind"]),
            prompt=str(msg["prompt"])[:50_000],
            memory=str(msg.get("memory", ""))[:50_000],
        )
        if job.model not in ALLOWED_MODELS:
            raise ValueError(f"model not allowed: {job.model}")
        if job.effort not in ALLOWED_EFFORTS or job.mode not in ALLOWED_MODES or job.kind not in ALLOWED_KINDS:
            raise ValueError("bad effort/mode/kind")
        if not job.prompt.strip():
            raise ValueError("empty prompt")
        return job


def load_mcp_servers(settings: AgentSettings) -> dict[str, Any]:
    """Extra MCP servers from /config/mcp.json ({"mcpServers": {...}} or {...})."""
    path = settings.mcp_config
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
        servers = data.get("mcpServers", data)
        return servers if isinstance(servers, dict) else {}
    except (OSError, json.JSONDecodeError):
        log.exception("could not read %s", path)
        return {}


def build_options(settings: AgentSettings, job: Job) -> ClaudeAgentOptions:
    foreground = job.mode == "foreground"
    budget = (
        settings.budget_question_usd if foreground
        else settings.budget_build_usd if job.kind == "build"
        else settings.budget_task_usd
    )
    return ClaudeAgentOptions(
        model=job.model,
        effort=job.effort,  # type: ignore[arg-type]
        system_prompt={
            "type": "preset",
            "preset": "claude_code",
            "append": worker_prompt(settings.assistant_name, job.memory, job.mode, job.kind),
        },
        # The container is the sandbox: the worker may use its computer freely.
        permission_mode="bypassPermissions",
        cwd=str(settings.workspace),
        # "user" loads ~/.claude (persistent volume): connectors you authorized
        # with scripts/connect.sh, plus your own settings and skills.
        setting_sources=["user"],
        mcp_servers=load_mcp_servers(settings),
        include_partial_messages=foreground,
        max_turns=settings.max_turns_question if foreground else settings.max_turns_task,
        max_budget_usd=budget,
        stderr=lambda line: log.debug("[cli] %s", line),
    )


async def run_job(
    settings: AgentSettings,
    job: Job,
    emit: Emit,
    cancel: asyncio.Event,
    client_factory: Callable[[ClaudeAgentOptions], Any] = ClaudeSDKClient,
) -> None:
    """Runs ``job`` to completion (or cancellation) and emits progress, text
    deltas (foreground) and exactly one final 'done' message."""
    started = time.monotonic()
    client = client_factory(build_options(settings, job))
    result: ResultMessage | None = None
    cancelled = False

    async def watch_cancel() -> None:
        nonlocal cancelled
        await cancel.wait()
        cancelled = True
        log.info("task #%d: cancelling", job.task_id)
        with contextlib.suppress(Exception):
            await client.interrupt()

    watcher: asyncio.Task[None] | None = None
    try:
        await client.connect()
        watcher = asyncio.create_task(watch_cancel())
        await client.query(job.prompt)
        async for msg in client.receive_response():
            if isinstance(msg, StreamEvent):
                if msg.parent_tool_use_id is not None:
                    continue
                ev = msg.event
                if ev.get("type") == "content_block_start" and ev.get("content_block", {}).get("type") == "text":
                    await emit({"type": "delta", "text": "\n"})
                elif ev.get("type") == "content_block_delta" and ev.get("delta", {}).get("type") == "text_delta":
                    await emit({"type": "delta", "text": ev["delta"]["text"]})
            elif isinstance(msg, AssistantMessage) and msg.parent_tool_use_id is None:
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        await emit({"type": "progress", "text": f"Using {block.name}"})
            elif isinstance(msg, ResultMessage):
                result = msg
    except Exception as e:
        log.exception("task #%d crashed", job.task_id)
        await emit({"type": "done", "ok": False, "result": f"The worker crashed: {e}", "cost_usd": 0.0})
        return
    finally:
        if watcher:
            watcher.cancel()
        with contextlib.suppress(Exception):
            await client.disconnect()

    elapsed = time.monotonic() - started
    cost = (result.total_cost_usd or 0.0) if result else 0.0
    if cancelled:
        await emit({"type": "done", "ok": False, "result": "Cancelled by the user.", "cost_usd": cost})
    elif result is None:
        await emit({"type": "done", "ok": False, "result": "The worker stopped without a result.", "cost_usd": 0.0})
    else:
        ok = not result.is_error and result.subtype == "success"
        text = result.result or ""
        if not ok:
            # API failures (bad key, overload) come back as subtype "success"
            # with is_error set; the result text then says what went wrong.
            reason = {
                "error_max_turns": "it ran out of steps",
                "error_max_budget_usd": "it hit its spending limit",
                "success": "",
            }.get(result.subtype, "; ".join(result.errors or []) or result.subtype)
            text = f"The task did not finish{': ' + reason if reason else ''}. {text}".strip()
        await emit({"type": "done", "ok": ok, "result": text, "cost_usd": cost})
    log.info(
        "task #%d (%s, %s/%s) finished in %.0f s, $%.3f", job.task_id, job.kind, job.model, job.effort, elapsed, cost
    )
