from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest

from intelligence.brain import Brain
from intelligence.brain.routing import classify_task, looks_like_build
from intelligence.brain.voice_session import VoiceSession
from intelligence.config import Settings
from intelligence.memory import Memory, Task

from .fakes import FakeAgent, FakeSDKClient, block_start, collect, fake_query_factory, result, text_event, until


def make_brain(tmp_path, script, agent=None, **settings_kw) -> tuple[Brain, FakeAgent]:
    settings = replace(Settings(), data_dir=tmp_path, **settings_kw)
    agent = agent or FakeAgent()
    memory = Memory(tmp_path / "memory.db")

    def factory(brain: Brain) -> VoiceSession:
        holder: dict[str, VoiceSession] = {}
        s = VoiceSession(
            settings, brain.memory, brain.tasks,
            client_factory=lambda opts: FakeSDKClient(opts, script, lambda: holder["s"]),
            query_fn=fake_query_factory("User asked about the weather."),
        )
        holder["s"] = s
        return s

    return Brain(settings, memory, agent, session_factory=factory), agent


async def say(*parts: str):
    yield block_start()
    for p in parts:
        yield text_event(p)


# ---- Routing ---------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "request_",
    [
        "Build me a website for my bakery",
        "write a python script that renames my photos by date",
        "Create a Discord bot that posts the weather",
        "make an app to track my workouts",
        "Set up a cron job that backs up the database",
    ],
)
def test_build_requests_always_become_build(request_: str) -> None:
    assert looks_like_build(request_)
    assert classify_task("action", request_) == "build"


@pytest.mark.parametrize(
    "request_", ["Email Sam that I'm running late", "Find a good Thai restaurant nearby", "organize my downloads folder"]
)
def test_actions_stay_actions(request_: str) -> None:
    assert classify_task("action", request_) == "action"
    assert classify_task("nonsense", request_) == "action"


# ---- Voice session ---------------------------------------------------------------------------
async def test_reply_streams_and_options_are_locked_down(tmp_path) -> None:
    async def script(prompt: str, session: Any):
        async for e in say("It's sunny", ", about twenty degrees."):
            yield e
        yield result()

    brain, _ = make_brain(tmp_path, script)
    reply = await collect(brain.respond("What's the weather?"))
    assert reply.strip() == "It's sunny, about twenty degrees."

    opts = FakeSDKClient.instances[-1].options
    assert opts.model == "claude-haiku-5-5" and opts.effort == "medium"
    assert opts.tools == ["WebSearch"] and opts.permission_mode == "dontAsk"
    assert opts.setting_sources == [] and opts.include_partial_messages
    assert "mcp__assistant__start_task" in opts.allowed_tools
    prompt = FakeSDKClient.instances[-1].prompts[-1]
    assert prompt.startswith("[") and prompt.endswith("] What's the weather?")
    await brain.close()


@pytest.mark.parametrize(("difficulty", "model", "effort"), [("medium", "claude-sonnet-5-5", "high"), ("hard", "claude-opus-5-5", "medium")])
async def test_ask_expert_speaks_the_answer_and_mutes_haiku(tmp_path, difficulty, model, effort) -> None:
    async def script(prompt: str, session: Any):
        async for e in say("Let me think about that."):
            yield e
        out = await session.tool("ask_expert").handler({"question": "Prove it", "difficulty": difficulty})
        assert "ALREADY been spoken" in out["content"][0]["text"]
        async for e in say("To repeat: the answer is 42."):  # must not be heard
            yield e
        yield result()

    brain, agent = make_brain(tmp_path, script)
    reply = await collect(brain.respond("Hard question"))
    assert "Let me think about that." in reply
    assert "The answer is 42." in reply
    assert "To repeat" not in reply
    job = agent.jobs[0]
    assert (job.model, job.effort, job.mode, job.kind) == (model, effort, "foreground", "question")
    assert brain.memory.recent_tasks()[-1].status == "done"
    await brain.close()


async def test_build_task_runs_on_opus_high_in_background_and_is_announced(tmp_path) -> None:
    finished: list[Task] = []

    async def script(prompt: str, session: Any):
        out = await session.tool("start_task").handler(
            {"task": "Build a website for my bakery with a menu page", "kind": "action"}
        )
        assert "background" in out["content"][0]["text"]
        async for e in say("I've got that running in the background. Anything else?"):
            yield e
        yield result()

    brain, agent = make_brain(tmp_path, script, agent=FakeAgent(answer="Built it in /workspace/projects/bakery."))

    async def on_finished(task: Task) -> None:
        finished.append(task)

    brain.on_task_finished = on_finished
    reply = await collect(brain.respond("Build me a bakery website"))
    assert "background" in reply
    await until(lambda: bool(finished))
    job = agent.jobs[0]
    assert (job.model, job.effort, job.mode, job.kind) == ("claude-opus-5-5", "high", "background", "build")
    assert finished[0].status == "done" and "bakery" in finished[0].result
    await brain.close()


async def test_action_task_uses_sonnet_high(tmp_path) -> None:
    async def script(prompt: str, session: Any):
        await session.tool("start_task").handler({"task": "Email Sam that I'm late", "kind": "action"})
        yield result()

    brain, agent = make_brain(tmp_path, script)
    await collect(brain.respond("Tell Sam I'm late"))
    await until(lambda: bool(agent.jobs))
    assert (agent.jobs[0].model, agent.jobs[0].effort) == ("claude-sonnet-5-5", "high")
    await brain.close()


async def test_cancel_task(tmp_path) -> None:
    async def script(prompt: str, session: Any):
        if "start" in prompt:
            await session.tool("start_task").handler({"task": "Research laptops", "kind": "action"})
        else:
            out = await session.tool("cancel_task").handler({"task_id": 1})
            assert out["content"][0]["text"] == "Cancelled."
        yield result()

    brain, agent = make_brain(tmp_path, script, agent=FakeAgent(delay=30))
    await collect(brain.respond("start it"))
    await until(lambda: bool(agent.jobs))
    await collect(brain.respond("cancel it"))
    assert agent.cancelled == [1]
    assert brain.memory.task(1).status == "cancelled"
    await brain.close()


async def test_memory_tools_and_stop_listening(tmp_path) -> None:
    async def script(prompt: str, session: Any):
        if "remember" in prompt:
            await session.tool("remember").handler({"fact": "Takes coffee black"})
        else:
            await session.tool("stop_listening").handler({})
        yield result()

    brain, _ = make_brain(tmp_path, script)
    await collect(brain.respond("remember I take coffee black"))
    assert brain.memory.facts() == [(1, "Takes coffee black")]
    assert not brain.stop_listening_requested
    await collect(brain.respond("that's all, thanks"))
    assert brain.stop_listening_requested
    await brain.close()


async def test_error_result_is_spoken_as_an_apology(tmp_path) -> None:
    async def script(prompt: str, session: Any):
        yield result(is_error=True)

    brain, _ = make_brain(tmp_path, script)
    assert "Sorry" in await collect(brain.respond("hi"))
    await brain.close()


async def test_listener_leaving_mid_reply_interrupts(tmp_path) -> None:
    async def script(prompt: str, session: Any):
        for i in range(100):
            yield text_event(f"word{i} ")
            await asyncio.sleep(0.01)
        yield result()

    brain, _ = make_brain(tmp_path, script)
    gen = brain.respond("Tell me a long story")
    first = await gen.__anext__()
    assert first.startswith("word0")
    await gen.aclose()
    assert FakeSDKClient.instances[-1].interrupted
    await brain.close()


# ---- Chat lifetime -------------------------------------------------------------------------------
async def test_idle_chat_is_summarized_and_replaced(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("intelligence.brain.IDLE_CHECK_SECONDS", 0.05)

    async def script(prompt: str, session: Any):
        async for e in say("Sure."):
            yield e
        yield result()

    brain, _ = make_brain(tmp_path, script, session_idle_seconds=0.2)
    await brain.start()
    await collect(brain.respond("What's the weather?"))
    first_client = FakeSDKClient.instances[-1]

    # After the idle limit the chat is summarized into memory and a new spare,
    # whose system prompt contains that summary, is connected in advance.
    await until(lambda: brain.memory.recent_conversations() != [])
    assert brain.memory.recent_conversations()[-1][1] == "User asked about the weather."
    await until(lambda: brain._spare is not None)
    assert not first_client.connected

    await collect(brain.respond("And tomorrow?"))
    new_client = FakeSDKClient.instances[-1]
    assert new_client is not first_client
    assert "User asked about the weather." in new_client.options.system_prompt
    await brain.close()
