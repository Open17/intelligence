from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, ToolUseBlock
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from agent_service.config import AgentSettings
from agent_service.runner import Job, build_options, run_job
from agent_service.server import AgentServer

# The voice container's client, to test both ends of the protocol together.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "voice"))
try:
    from intelligence.brain.agent_client import AgentClient, AgentJob  # noqa: E402

    HAVE_VOICE = True
except ImportError:  # voice package not on this checkout
    HAVE_VOICE = False
needs_voice = pytest.mark.skipif(not HAVE_VOICE, reason="voice package not available")

TOKEN = "t" * 48


def settings(tmp_path: Path, **kw: Any) -> AgentSettings:
    return replace(AgentSettings(), token=TOKEN, workspace=tmp_path, mcp_config=tmp_path / "mcp.json", **kw)


def job(**kw: Any) -> Job:
    base = dict(task_id=1, model="claude-opus-5-5", effort="high", mode="background", kind="build",
                prompt="Build a todo app", memory="[1] Likes Python")
    return Job(**{**base, **kw})


def result(text: str = "Done.", subtype: str = "success", is_error: bool = False) -> ResultMessage:
    return ResultMessage(subtype=subtype, duration_ms=1, duration_api_ms=1, is_error=is_error, num_turns=3,
                         session_id="s", result=text, total_cost_usd=0.5)


class FakeClient:
    def __init__(self, options: Any, messages: list[Any], hang: bool = False) -> None:
        self.options = options
        self.messages = messages
        self.hang = hang
        self.interrupted = asyncio.Event()
        self.prompt = ""

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...

    async def interrupt(self) -> None:
        self.interrupted.set()

    async def query(self, prompt: str) -> None:
        self.prompt = prompt

    async def receive_response(self):
        for m in self.messages:
            yield m
        if self.hang:
            await self.interrupted.wait()
            yield result("", subtype="success")


# ---- Job / options -------------------------------------------------------------------------
def test_job_validation() -> None:
    ok = {"task_id": 1, "model": "claude-sonnet-5-5", "effort": "high", "mode": "background",
          "kind": "action", "prompt": "x"}
    assert Job.parse(ok).model == "claude-sonnet-5-5"
    for bad in ({"model": "gpt-4"}, {"effort": "extreme"}, {"mode": "sideways"}, {"prompt": "  "}):
        with pytest.raises(ValueError):
            Job.parse({**ok, **bad})


def test_options_for_a_build(tmp_path) -> None:
    (tmp_path / "mcp.json").write_text(json.dumps({"mcpServers": {"notes": {"command": "notes-mcp"}}}))
    opts = build_options(settings(tmp_path), job())
    assert (opts.model, opts.effort) == ("claude-opus-5-5", "high")
    assert opts.permission_mode == "bypassPermissions" and opts.cwd == str(tmp_path)
    assert opts.setting_sources == ["user"]
    assert opts.mcp_servers == {"notes": {"command": "notes-mcp"}}
    assert opts.max_budget_usd == 15.0 and not opts.include_partial_messages
    append = opts.system_prompt["append"]
    assert "build task" in append and "Likes Python" in append and "READ ALOUD" not in append


def test_options_for_a_question(tmp_path) -> None:
    opts = build_options(settings(tmp_path), job(mode="foreground", kind="question", model="claude-sonnet-5-5"))
    assert opts.include_partial_messages and opts.max_budget_usd == 1.0
    assert "READ ALOUD" in opts.system_prompt["append"]


# ---- Running jobs ---------------------------------------------------------------------------
async def run_collect(tmp_path, j: Job, messages: list[Any], cancel: asyncio.Event | None = None, hang=False):
    out: list[dict] = []

    async def emit(m: dict) -> None:
        out.append(m)

    clients: list[FakeClient] = []

    def factory(opts):
        clients.append(FakeClient(opts, messages, hang))
        return clients[-1]

    await run_job(settings(tmp_path), j, emit, cancel or asyncio.Event(), client_factory=factory)
    return out, clients[0]


async def test_foreground_streams_text(tmp_path) -> None:
    ev = lambda t: StreamEvent("u", "s", {"type": "content_block_delta", "delta": {"type": "text_delta", "text": t}})
    sub = StreamEvent("u", "s", {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "SUB"}},
                      parent_tool_use_id="x")
    out, _ = await run_collect(tmp_path, job(mode="foreground", kind="question"),
                               [ev("Two "), sub, ev("o'clock."), result("Two o'clock.")])
    assert "".join(m["text"] for m in out if m["type"] == "delta") == "Two o'clock."
    assert out[-1] == {"type": "done", "ok": True, "result": "Two o'clock.", "cost_usd": 0.5}


async def test_background_reports_progress_and_result(tmp_path) -> None:
    msgs = [AssistantMessage([ToolUseBlock("1", "Bash", {"command": "ls"})], "claude-opus-5-5"), result("Built it.")]
    out, client = await run_collect(tmp_path, job(), msgs)
    assert {"type": "progress", "text": "Using Bash"} in out
    assert out[-1]["ok"] and out[-1]["result"] == "Built it."
    assert client.prompt == "Build a todo app"


async def test_budget_exhaustion_is_explained(tmp_path) -> None:
    out, _ = await run_collect(tmp_path, job(), [result("", subtype="error_max_budget_usd", is_error=True)])
    assert not out[-1]["ok"] and "spending limit" in out[-1]["result"]


async def test_cancel_interrupts(tmp_path) -> None:
    cancel = asyncio.Event()
    task = asyncio.create_task(run_collect(tmp_path, job(), [], cancel=cancel, hang=True))
    await asyncio.sleep(0.05)
    cancel.set()
    out, client = await asyncio.wait_for(task, 2)
    assert client.interrupted.is_set()
    assert out[-1]["ok"] is False and "Cancelled" in out[-1]["result"]


# ---- Server -------------------------------------------------------------------------------------
@pytest.fixture
async def server(tmp_path, unused_tcp_port):
    jobs: list[Job] = []

    async def fake_run(s, j: Job, emit, cancel) -> None:
        jobs.append(j)
        if j.prompt == "slow":
            await cancel.wait()
            await emit({"type": "done", "ok": False, "result": "Cancelled by the user.", "cost_usd": 0})
            return
        await emit({"type": "delta", "text": "Hi "})
        await emit({"type": "done", "ok": True, "result": "Hi there", "cost_usd": 0.1})

    srv = AgentServer(settings(tmp_path, listen_host="127.0.0.1", listen_port=unused_tcp_port), run=fake_run)
    await srv.start()
    yield f"ws://127.0.0.1:{unused_tcp_port}", jobs
    await srv.close()


async def test_wrong_token_is_refused(server) -> None:
    url, _ = server
    for headers in ({}, {"Authorization": "Bearer nope"}):
        with pytest.raises(InvalidStatus) as e:
            async with connect(url, additional_headers=headers, proxy=None):
                pass
        assert e.value.response.status_code == 401


@needs_voice
async def test_voice_client_round_trip(server) -> None:
    url, jobs = server
    client = AgentClient(url, TOKEN)
    j = AgentJob(5, "claude-sonnet-5-5", "high", "foreground", "question", "Say hi", "")
    msgs = [m async for m in client.run(j, asyncio.Event())]
    assert msgs[0] == {"type": "delta", "text": "Hi "}
    assert msgs[-1]["type"] == "done" and msgs[-1]["result"] == "Hi there"
    assert jobs[0].task_id == 5


@needs_voice
async def test_voice_client_cancel(server) -> None:
    url, _ = server
    cancel = asyncio.Event()
    j = AgentJob(6, "claude-opus-5-5", "high", "background", "build", "slow", "")

    async def go():
        return [m async for m in AgentClient(url, TOKEN).run(j, cancel)]

    t = asyncio.create_task(go())
    await asyncio.sleep(0.1)
    cancel.set()
    msgs = await asyncio.wait_for(t, 3)
    assert msgs[-1]["result"] == "Cancelled by the user."


def test_short_token_is_rejected(tmp_path) -> None:
    with pytest.raises(ValueError):
        AgentServer(replace(settings(tmp_path), token="short"))
