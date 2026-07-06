"""
eleon Memory — durable SQLite store for facts and conversation history.

Two tables:
  facts         — durable things eleon should remember about Boss / the
                  machine (preferences, paths, names, running projects…).
  conversations — a rolling transcript so eleon has continuity across runs.

No embeddings: recall is substring + recency ranked. That is plenty for a
personal assistant and keeps eleon dependency-light and fully offline-capable.
The DB lives at config.DB_PATH (eleon.db beside the code).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from config import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts    TEXT NOT NULL,
    key   TEXT,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    role    TEXT NOT NULL,
    content TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_facts_value ON facts(value);
"""


class Memory:
    def __init__(self, path=DB_PATH):
        self.path = str(path)
        with self._conn() as c:
            c.executescript(_SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=5)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    # ── Facts ──────────────────────────────────────────────────────
    def remember(self, value: str, key: str | None = None) -> bool:
        """Store a fact. Returns False if an identical value already exists."""
        value = (value or "").strip()
        if not value:
            return False
        with self._conn() as c:
            if c.execute("SELECT 1 FROM facts WHERE value=?", (value,)).fetchone():
                return False
            c.execute("INSERT INTO facts(ts,key,value) VALUES(?,?,?)",
                      (self._now(), key, value))
        return True

    def recall(self, query: str = "", limit: int = 10) -> list[str]:
        """Facts matching `query` (substring, in value or key), newest first.
        Empty query returns the most recent facts."""
        with self._conn() as c:
            if query:
                like = f"%{query}%"
                rows = c.execute(
                    "SELECT value FROM facts WHERE value LIKE ? OR key LIKE ? "
                    "ORDER BY id DESC LIMIT ?", (like, like, limit)).fetchall()
            else:
                rows = c.execute("SELECT value FROM facts ORDER BY id DESC "
                                 "LIMIT ?", (limit,)).fetchall()
        return [r[0] for r in rows]

    def forget(self, query: str) -> int:
        """Delete facts whose value/key matches `query`. Returns count removed."""
        if not query:
            return 0
        like = f"%{query}%"
        with self._conn() as c:
            cur = c.execute("DELETE FROM facts WHERE value LIKE ? OR key LIKE ?",
                            (like, like))
            return cur.rowcount

    # ── Conversation ───────────────────────────────────────────────
    def log_turn(self, role: str, content: str):
        content = (content or "").strip()
        if not content:
            return
        try:
            with self._conn() as c:
                c.execute("INSERT INTO conversations(ts,role,content) "
                          "VALUES(?,?,?)", (self._now(), role, content))
        except Exception:
            pass  # memory logging must never crash a turn

    def recent_turns(self, limit: int = 6) -> list[tuple[str, str]]:
        """Last `limit` (role, content) pairs, oldest→newest."""
        with self._conn() as c:
            rows = c.execute("SELECT role,content FROM conversations "
                             "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return list(reversed(rows))

    # ── Preamble for the system prompt ─────────────────────────────
    def context_block(self, facts_limit: int = 12, turns_limit: int = 6) -> str:
        """Formatted facts + recent history for injection into the system
        prompt, so eleon starts each session already knowing Boss."""
        parts: list[str] = []
        facts = self.recall(limit=facts_limit)
        if facts:
            parts.append("Known facts about Boss / this machine (from memory):\n"
                         + "\n".join(f"- {f}" for f in facts))
        turns = self.recent_turns(limit=turns_limit)
        if turns:
            convo = "\n".join(f"{r}: {c[:200]}" for r, c in turns)
            parts.append("Recent conversation (earlier sessions):\n" + convo)
        return "\n\n".join(parts)
