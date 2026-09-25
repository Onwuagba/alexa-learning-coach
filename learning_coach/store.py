"""Progress store.

`Store` is a small SQL store that runs on SQLite (default) or Postgres. Pick with
`make_store(url)`: a file path / `sqlite:///path` -> SQLite; `postgres(ql)://...` -> Postgres
(needs `pip install "psycopg[binary]"`). All SQL is the common subset, `?` placeholders are
rewritten to `%s` for Postgres. NOTE: the Postgres path is written but has NOT been run
against a real Postgres in this repo's tests.
"""
from __future__ import annotations

import sqlite3
import threading
import time

SCHEMA = [
    """create table if not exists progress (
      child text not null, topic text not null,
      level integer not null default 1, streak integer not null default 0,
      correct integer not null default 0, attempts integer not null default 0,
      unlocked integer not null default 0,
      primary key (child, topic))""",
    """create table if not exists pending (
      child text primary key, topic text, level integer, prompt text, answer text,
      created double precision)""",
    "create table if not exists settings (k text primary key, v text not null)",
]

# Two correct answers in a row -> level up; two wrong in a row -> level down.
PROMOTE_AFTER = 2
DEMOTE_AFTER = -2
PENDING_TTL_SECONDS = 15 * 60


class Store:
    def __init__(self, url="progress.db"):
        url = str(url)
        self.lock = threading.RLock()
        self.pg = url.startswith(("postgres://", "postgresql://"))
        if self.pg:
            import psycopg  # optional dependency
            from psycopg.rows import dict_row
            self.db = psycopg.connect(url, row_factory=dict_row, autocommit=True)
        else:
            path = url[len("sqlite:///"):] if url.startswith("sqlite:///") else url
            self.db = sqlite3.connect(path, check_same_thread=False)
            self.db.row_factory = sqlite3.Row
        for stmt in SCHEMA:
            self._x(stmt)

    # -- plumbing ---------------------------------------------------------
    def _x(self, sql: str, args: tuple = ()) -> list[dict]:
        if self.pg:
            sql = sql.replace("?", "%s")
        with self.lock:
            cur = self.db.execute(sql, args)
            rows = [dict(r) for r in cur.fetchall()] if cur.description else []
            if not self.pg:
                self.db.commit()
            return rows

    # -- progress ---------------------------------------------------------
    def row(self, child: str, topic: str) -> dict:
        with self.lock:
            self._x("insert into progress(child, topic) values (?, ?) on conflict do nothing", (child, topic))
            return self._x("select * from progress where child=? and topic=?", (child, topic))[0]

    def unlock(self, child: str, topic: str) -> None:
        self.row(child, topic)
        self._x("update progress set unlocked=1 where child=? and topic=?", (child, topic))

    def unlocked_topics(self, child: str) -> list[str]:
        return [r["topic"] for r in self._x("select topic from progress where child=? and unlocked=1", (child,))]

    def set_pending(self, child, q) -> None:
        self._x("""insert into pending(child, topic, level, prompt, answer, created) values (?,?,?,?,?,?)
                   on conflict(child) do update set topic=excluded.topic, level=excluded.level,
                   prompt=excluded.prompt, answer=excluded.answer, created=excluded.created""",
                (child, q.topic, q.level, q.prompt, q.answer, time.time()))

    def pop_pending(self, child):
        with self.lock:
            rows = self._x("select * from pending where child=?", (child,))
            self._x("delete from pending where child=?", (child,))
        if not rows or time.time() - rows[0]["created"] > PENDING_TTL_SECONDS:
            return None
        return rows[0]

    def peek_pending(self, child):
        rows = self._x("select * from pending where child=?", (child,))
        return rows[0] if rows else None

    def record(self, child: str, topic: str, correct: bool, max_level: int) -> int:
        with self.lock:
            r = self.row(child, topic)
            streak = max(r["streak"], 0) + 1 if correct else min(r["streak"], 0) - 1
            level = r["level"]
            if streak >= PROMOTE_AFTER and level < max_level:
                level, streak = level + 1, 0
            elif streak <= DEMOTE_AFTER and level > 1:
                level, streak = level - 1, 0
            self._x("update progress set level=?, streak=?, correct=correct+?, attempts=attempts+1 "
                    "where child=? and topic=?", (level, streak, int(correct), child, topic))
            return level

    def progress(self, child: str) -> list[dict]:
        return self._x("select * from progress where child=? order by topic", (child,))

    def children(self) -> list[str]:
        return [r["child"] for r in self._x("select distinct child from progress order by child")]

    # -- settings (PIN hash, lockout state) --------------------------------
    def get_setting(self, k: str, default: str | None = None) -> str | None:
        rows = self._x("select v from settings where k=?", (k,))
        return rows[0]["v"] if rows else default

    def set_setting(self, k: str, v: str) -> None:
        self._x("insert into settings(k, v) values (?, ?) on conflict(k) do update set v=excluded.v", (k, v))


def make_store(url: str | None = None) -> Store:
    return Store(url or "progress.db")
