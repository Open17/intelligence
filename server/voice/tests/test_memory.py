import os
import stat

from intelligence.memory import Memory


def test_facts_roundtrip_and_dedupe(tmp_path) -> None:
    m = Memory(tmp_path / "m.db")
    a = m.add_fact("Prefers   metric units")
    b = m.add_fact("Prefers metric units")
    assert a == b
    m.add_fact("Dog is called Biscuit")
    assert [t for _, t in m.facts()] == ["Prefers metric units", "Dog is called Biscuit"]
    assert m.forget_fact(a) and not m.forget_fact(a)
    assert [t for _, t in m.facts()] == ["Dog is called Biscuit"]


def test_database_is_private(tmp_path) -> None:
    Memory(tmp_path / "m.db")
    assert stat.S_IMODE(os.stat(tmp_path / "m.db").st_mode) == 0o600


def test_tasks_lifecycle(tmp_path) -> None:
    m = Memory(tmp_path / "m.db")
    tid = m.create_task("build", "claude-opus-5-5", "high", "Write a backup script")
    assert [t.id for t in m.active_tasks()] == [tid]
    m.update_task(tid, "running")
    m.update_task(tid, "done", "Saved to /workspace/projects/backup", cost_usd=0.42)
    t = m.task(tid)
    assert t and t.status == "done" and t.finished_at and t.cost_usd == 0.42
    assert m.active_tasks() == []


def test_restart_fails_interrupted_tasks(tmp_path) -> None:
    m = Memory(tmp_path / "m.db")
    tid = m.create_task("action", "claude-sonnet-5-5", "high", "x")
    m.close()
    m2 = Memory(tmp_path / "m.db")
    assert m2.task(tid).status == "failed"


def test_context_contains_everything(tmp_path) -> None:
    m = Memory(tmp_path / "m.db", timezone="Europe/London")
    assert m.context() == "(Nothing remembered yet.)"
    m.add_fact("Lives in Leeds")
    m.add_conversation(1.0, 2.0, "Talked about the weather.")
    m.create_task("action", "claude-sonnet-5-5", "high", "Check my calendar")
    ctx = m.context()
    assert "[1] Lives in Leeds" in ctx
    assert "Talked about the weather." in ctx
    assert "#1 [queued]" in ctx and "Check my calendar" in ctx
