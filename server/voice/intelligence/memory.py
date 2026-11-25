"""Long-term memory, kept in SQLite on the server's data volume.

Three kinds of memory survive the 5-minute chat reset:

* facts          things the assistant was told to remember ("I take my
                 coffee black"), saved through the ``remember`` tool
* conversations  a short summary of every finished chat
* tasks          every job handed to Sonnet/Opus, with its result

``context()`` renders them as a block that goes into the system prompt of
every new chat, so a fresh Haiku session starts out knowing what the last
one knew, at a fraction of the tokens of replaying old transcripts.
"""

from __future__ import annotations

import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id         INTEGER PRIMARY KEY,
    text       TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    id         INTEGER PRIMARY KEY,
    started_at REAL NOT NULL,
    ended_at   REAL NOT NULL,
    summary    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,
    model       TEXT NOT NULL,
    effort      TEXT NOT NULL,
    request     TEXT NOT NULL,
    status      TEXT NOT NULL,
    result      TEXT NOT NULL DEFAULT '',
    cost_usd    REAL NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    finished_at REAL
);
"""

ACTIVE = ("queued", "running")
FINISHED = ("done", "failed", "cancelled")


@dataclass(frozen=True)
class Task:
    id: int
    kind: str
    model: str
    effort: str
    request: str
    status: str
    result: str
    cost_usd: float
    created_at: float
    finished_at: float | None


class Memory:
    def __init__(self, path: Path, timezone: str = "UTC") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        self._db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        if new:
            os.chmod(path, 0o600)  # your memories are readable by the service only
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        try:
            self._tz = ZoneInfo(timezone)
        except Exception:
            self._tz = ZoneInfo("UTC")
        # Tasks that were running when the server stopped will never finish.
        self._db.execute(
            "UPDATE tasks SET status='failed', result='Interrupted by a server restart.', finished_at=?"
            " WHERE status IN ('queued','running')",
            (time.time(),),
        )

    # ---- Facts ---------------------------------------------------------------------
    def add_fact(self, text: str) -> int:
        text = " ".join(text.split())[:500]
        cur = self._db.execute(
            "INSERT INTO facts(text, created_at) VALUES(?, ?) ON CONFLICT(text) DO NOTHING",
            (text, time.time()),
        )
        if cur.lastrowid and cur.rowcount:
            return cur.lastrowid
        return self._db.execute("SELECT id FROM facts WHERE text=?", (text,)).fetchone()[0]

    def forget_fact(self, fact_id: int) -> bool:
        return self._db.execute("DELETE FROM facts WHERE id=?", (fact_id,)).rowcount > 0

    def facts(self, limit: int = 200) -> list[tuple[int, str]]:
        rows = self._db.execute("SELECT id, text FROM facts ORDER BY id DESC LIMIT ?", (limit,))
        return list(reversed(rows.fetchall()))

    # ---- Conversations --------------------------------------------------------------------
    def add_conversation(self, started_at: float, ended_at: float, summary: str) -> None:
        self._db.execute(
            "INSERT INTO conversations(started_at, ended_at, summary) VALUES(?, ?, ?)",
            (started_at, ended_at, summary.strip()[:2000]),
        )

    def recent_conversations(self, n: int = 6) -> list[tuple[float, str]]:
        rows = self._db.execute(
            "SELECT ended_at, summary FROM conversations ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
        return list(reversed(rows))

    # ---- Tasks ------------------------------------------------------------------------------
    def create_task(self, kind: str, model: str, effort: str, request: str) -> int:
        cur = self._db.execute(
            "INSERT INTO tasks(kind, model, effort, request, status, created_at) VALUES(?,?,?,?,?,?)",
            (kind, model, effort, request, "queued", time.time()),
        )
        assert cur.lastrowid is not None
        return cur.lastrowid

    def update_task(self, task_id: int, status: str, result: str | None = None, cost_usd: float = 0) -> None:
        finished = time.time() if status in FINISHED else None
        if result is None:
            self._db.execute(
                "UPDATE tasks SET status=?, finished_at=? WHERE id=?", (status, finished, task_id)
            )
        else:
            self._db.execute(
                "UPDATE tasks SET status=?, result=?, cost_usd=?, finished_at=? WHERE id=?",
                (status, result[:20000], cost_usd, finished, task_id),
            )

    def task(self, task_id: int) -> Task | None:
        row = self._db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return Task(*row) if row else None

    def recent_tasks(self, n: int = 8) -> list[Task]:
        rows = self._db.execute("SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return [Task(*r) for r in reversed(rows)]

    def active_tasks(self) -> list[Task]:
        rows = self._db.execute(
            "SELECT * FROM tasks WHERE status IN ('queued','running') ORDER BY id"
        ).fetchall()
        return [Task(*r) for r in rows]

    # ---- Prompt rendering ------------------------------------------------------------------------
    def _when(self, ts: float) -> str:
        return datetime.fromtimestamp(ts, self._tz).strftime("%a %d %b %H:%M")

    def context(self) -> str:
        """Everything worth knowing at the start of a new chat, as prompt text."""
        parts: list[str] = []
        facts = self.facts()
        if facts:
            parts.append(
                "<remembered_facts>\n"
                + "\n".join(f"[{fid}] {text}" for fid, text in facts)
                + "\n</remembered_facts>"
            )
        convs = self.recent_conversations()
        if convs:
            parts.append(
                "<recent_conversations>\n"
                + "\n".join(f"- {self._when(ts)}: {s}" for ts, s in convs)
                + "\n</recent_conversations>"
            )
        tasks = self.recent_tasks()
        if tasks:
            lines = []
            for t in tasks:
                result = " ".join(t.result.split())[:300]
                lines.append(
                    f"- #{t.id} [{t.status}] {self._when(t.created_at)} ({t.kind}): {t.request[:200]}"
                    + (f" -> {result}" if result else "")
                )
            parts.append("<recent_tasks>\n" + "\n".join(lines) + "\n</recent_tasks>")
        return "\n\n".join(parts) if parts else "(Nothing remembered yet.)"

    def close(self) -> None:
        self._db.close()
