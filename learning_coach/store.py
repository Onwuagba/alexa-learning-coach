"""Progress store: families (tenants), children, progress, spaced-repetition items, skills, attempts, sessions,
badges. Runs on SQLite (default) or Postgres.

`make_store(url)`: a file path / `sqlite:///path` -> SQLite; `postgres(ql)://...` -> Postgres
(`pip install "psycopg[binary]"`). All SQL is the common subset; `?` placeholders are rewritten to `%s` for
Postgres. Verified against Postgres 16 locally (see docs/DEPLOY.md); SQLite is what most tests use.

Schema changes are versioned migrations (`MIGRATIONS`, tracked in `schema_migrations`). Version 2 turned the
original single-household database into a multi-family one: legacy rows move into the family `default`.
Every family-owned table carries `family_id`; every query filters on it (tenant isolation).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time

DEFAULT_FAMILY = "default"
# Two correct answers in a row -> level up; two wrong in a row -> level down. (The TV app relies on this.)
PROMOTE_AFTER = 2
DEMOTE_AFTER = -2
PENDING_TTL_SECONDS = 15 * 60

_V1 = [
    """create table if not exists progress (
      child text not null, topic text not null,
      level integer not null default 1, streak integer not null default 0,
      correct integer not null default 0, attempts integer not null default 0,
      unlocked integer not null default 0, updated_ms double precision not null default 0,
      primary key (child, topic))""",
    """create table if not exists pending (
      child text primary key, topic text, level integer, prompt text, answer text,
      created double precision)""",
    """create table if not exists tv_events (
      child text not null, event_id text not null, primary key (child, event_id))""",
    "create table if not exists settings (k text primary key, v text not null)",
]

_V2 = [
    """create table if not exists families (
      id text primary key, slug text not null unique, name text not null, email text not null default '',
      pin_hash text not null default '', tv_token_hash text not null default '', link_token_hash text not null default '',
      tz_offset_min integer not null default 0, created double precision not null,
      fails integer not null default 0, locked_until double precision not null default 0,
      auto_create integer not null default 0)""",
    """create table if not exists children (
      family_id text not null, name text not null, daily_minutes integer not null default 0,
      weekly_goal integer not null default 30, created double precision not null,
      primary key (family_id, name))""",
    """create table if not exists kid_progress (
      family_id text not null, child text not null, topic text not null,
      level integer not null default 1, streak integer not null default 0,
      correct integer not null default 0, attempts integer not null default 0,
      unlocked integer not null default 0, blocked integer not null default 0,
      updated_ms double precision not null default 0,
      primary key (family_id, child, topic))""",
    """create table if not exists items (
      family_id text not null, child text not null, topic text not null, item_key text not null,
      prompt text not null, answer text not null, extra text not null default '{}', level integer not null default 1,
      box integer not null default 1, due double precision not null default 0,
      seen integer not null default 0, correct integer not null default 0, last_ts double precision not null default 0,
      primary key (family_id, child, topic, item_key))""",
    """create table if not exists skills (
      family_id text not null, child text not null, skill text not null,
      mastery double precision not null default 0.5, attempts integer not null default 0,
      correct integer not null default 0, primary key (family_id, child, skill))""",
    """create table if not exists attempts (
      family_id text not null, child text not null, topic text not null, skill text not null default '',
      item_key text not null default '', correct integer not null, hinted integer not null default 0,
      ts double precision not null, session text not null default '')""",
    "create index if not exists attempts_by_child on attempts (family_id, child, ts)",
    """create table if not exists sessions (
      id text primary key, family_id text not null, child text not null,
      started double precision not null, last_ts double precision not null,
      asked integer not null default 0, correct integer not null default 0, hinted integer not null default 0,
      topics text not null default '[]', ended integer not null default 0)""",
    """create table if not exists badges (
      family_id text not null, child text not null, badge text not null, earned double precision not null,
      primary key (family_id, child, badge))""",
    """create table if not exists pending2 (
      family_id text not null, child text not null, topic text, level integer, prompt text, answer text,
      extra text not null default '{}', hints_used integer not null default 0, tries integer not null default 0,
      created double precision, primary key (family_id, child))""",
    """create table if not exists tv_events2 (
      family_id text not null, child text not null, event_id text not null, primary key (family_id, child, event_id))""",
]


def _migrate_v1(st: "Store") -> None:
    for s in _V1:
        st._x(s)
    try:  # very old databases predate updated_ms
        st._x("alter table progress add column updated_ms double precision not null default 0")
    except Exception:
        pass


def _migrate_v2(st: "Store") -> None:
    for s in _V2:
        st._x(s)
    legacy = st._x("select * from progress")
    pin = st._x("select v from settings where k='pin_hash'")
    if legacy or pin:  # bring a single-household database into the default family
        st.ensure_default_family()
        if pin:
            st._x("update families set pin_hash=? where id=?", (pin[0]["v"], DEFAULT_FAMILY))
        now = time.time()
        for r in legacy:
            st._x("insert into children(family_id, name, created) values (?,?,?) on conflict do nothing",
                  (DEFAULT_FAMILY, r["child"], now))
            st._x("""insert into kid_progress(family_id, child, topic, level, streak, correct, attempts, unlocked, updated_ms)
                     values (?,?,?,?,?,?,?,?,?) on conflict do nothing""",
                  (DEFAULT_FAMILY, r["child"], r["topic"], r["level"], r["streak"], r["correct"], r["attempts"],
                   r["unlocked"], r["updated_ms"]))
        for r in st._x("select * from tv_events"):
            st._x("insert into tv_events2(family_id, child, event_id) values (?,?,?) on conflict do nothing",
                  (DEFAULT_FAMILY, r["child"], r["event_id"]))


MIGRATIONS = [(1, _migrate_v1), (2, _migrate_v2)]
SCHEMA_VERSION = MIGRATIONS[-1][0]


class Store:
    def __init__(self, url="progress.db", migrate: bool = True, target_version: int | None = None):
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
        if migrate:
            self.migrate(target_version)

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

    def migrate(self, target: int | None = None) -> list[int]:
        """Apply pending migrations in order. Returns the versions applied."""
        self._x("create table if not exists schema_migrations (version integer primary key, applied double precision not null)")
        done = {r["version"] for r in self._x("select version from schema_migrations")}
        # A pre-migration-framework database already has the v1 tables: mark v1 applied without re-running.
        applied = []
        for v, fn in MIGRATIONS:
            if v in done or (target is not None and v > target):
                continue
            fn(self)
            self._x("insert into schema_migrations(version, applied) values (?,?)", (v, time.time()))
            applied.append(v)
        return applied

    def schema_version(self) -> int:
        rows = self._x("select max(version) as v from schema_migrations")
        return int(rows[0]["v"] or 0)

    # -- families -----------------------------------------------------------
    def ensure_default_family(self) -> None:
        self._x("""insert into families(id, slug, name, created, auto_create) values (?,?,?,?,1)
                   on conflict do nothing""", (DEFAULT_FAMILY, "home", "Home", time.time()))

    def create_family(self, fid: str, slug: str, name: str, email: str, pin_hash: str, tv_hash: str, link_hash: str,
                      auto_create: int = 0) -> None:
        self._x("""insert into families(id, slug, name, email, pin_hash, tv_token_hash, link_token_hash, created, auto_create)
                   values (?,?,?,?,?,?,?,?,?)""",
                (fid, slug, name, email, pin_hash, tv_hash, link_hash, time.time(), auto_create))

    def family(self, fid: str) -> dict | None:
        r = self._x("select * from families where id=?", (fid,))
        return r[0] if r else None

    def family_by(self, column: str, value: str) -> dict | None:
        assert column in {"slug", "tv_token_hash", "link_token_hash"}
        if not value:
            return None
        r = self._x(f"select * from families where {column}=?", (value,))
        return r[0] if r else None

    def update_family(self, fid: str, **cols) -> None:
        allowed = {"name", "email", "pin_hash", "tv_token_hash", "link_token_hash", "tz_offset_min", "fails",
                   "locked_until", "auto_create"}
        assert set(cols) <= allowed
        if cols:
            sets = ", ".join(f"{k}=?" for k in cols)
            self._x(f"update families set {sets} where id=?", (*cols.values(), fid))

    def any_tv_tokens(self) -> bool:
        return bool(self._x("select 1 from families where tv_token_hash<>'' limit 1"))

    def families(self) -> list[dict]:
        return self._x("select id, slug, name, created from families order by created")

    def delete_family(self, fid: str) -> None:
        with self.lock:
            for t in ("children", "kid_progress", "items", "skills", "attempts", "sessions", "badges", "pending2",
                      "tv_events2"):
                self._x(f"delete from {t} where family_id=?", (fid,))
            self._x("delete from families where id=?", (fid,))

    # -- children -----------------------------------------------------------
    def add_child(self, fid: str, name: str) -> None:
        self._x("insert into children(family_id, name, created) values (?,?,?) on conflict do nothing",
                (fid, name, time.time()))

    def children(self, fid: str = DEFAULT_FAMILY) -> list[str]:
        return [r["name"] for r in self._x("select name from children where family_id=? order by created, name", (fid,))]

    def child(self, fid: str, name: str) -> dict | None:
        r = self._x("select * from children where family_id=? and name=?", (fid, name))
        return r[0] if r else None

    def update_child(self, fid: str, name: str, **cols) -> None:
        assert set(cols) <= {"daily_minutes", "weekly_goal"}
        if cols:
            sets = ", ".join(f"{k}=?" for k in cols)
            self._x(f"update children set {sets} where family_id=? and name=?", (*cols.values(), fid, name))

    def delete_child(self, fid: str, name: str) -> None:
        with self.lock:
            for t, col in (("children", "name"), ("kid_progress", "child"), ("items", "child"), ("skills", "child"),
                           ("attempts", "child"), ("sessions", "child"), ("badges", "child"), ("pending2", "child"),
                           ("tv_events2", "child")):
                self._x(f"delete from {t} where family_id=? and {col}=?", (fid, name))

    # -- progress ---------------------------------------------------------
    def row(self, child: str, topic: str, family: str = DEFAULT_FAMILY) -> dict:
        with self.lock:
            self.add_child(family, child)
            self._x("insert into kid_progress(family_id, child, topic) values (?,?,?) on conflict do nothing",
                    (family, child, topic))
            return self._x("select * from kid_progress where family_id=? and child=? and topic=?",
                           (family, child, topic))[0]

    def unlock(self, child: str, topic: str, family: str = DEFAULT_FAMILY) -> None:
        self.row(child, topic, family)
        self._x("update kid_progress set unlocked=1, blocked=0 where family_id=? and child=? and topic=?",
                (family, child, topic))

    def block(self, child: str, topic: str, family: str = DEFAULT_FAMILY) -> None:
        self.row(child, topic, family)
        self._x("update kid_progress set blocked=1, unlocked=0 where family_id=? and child=? and topic=?",
                (family, child, topic))

    def unlocked_topics(self, child: str, family: str = DEFAULT_FAMILY) -> list[str]:
        return [r["topic"] for r in self._x(
            "select topic from kid_progress where family_id=? and child=? and unlocked=1 and blocked=0", (family, child))]

    def blocked_topics(self, child: str, family: str = DEFAULT_FAMILY) -> list[str]:
        return [r["topic"] for r in self._x(
            "select topic from kid_progress where family_id=? and child=? and blocked=1", (family, child))]

    def record(self, child: str, topic: str, correct: bool, max_level: int, family: str = DEFAULT_FAMILY,
               at: float | None = None) -> int:
        with self.lock:
            r = self.row(child, topic, family)
            streak = max(r["streak"], 0) + 1 if correct else min(r["streak"], 0) - 1
            level = r["level"]
            if streak >= PROMOTE_AFTER and level < max_level:
                level, streak = level + 1, 0
            elif streak <= DEMOTE_AFTER and level > 1:
                level, streak = level - 1, 0
            self._x("update kid_progress set level=?, streak=?, correct=correct+?, attempts=attempts+1, updated_ms=? "
                    "where family_id=? and child=? and topic=?",
                    (level, streak, int(correct), (at or time.time()) * 1000, family, child, topic))
            return level

    def progress(self, child: str, family: str = DEFAULT_FAMILY) -> list[dict]:
        return self._x("select * from kid_progress where family_id=? and child=? order by topic", (family, child))

    # -- pending question ----------------------------------------------------
    def set_pending(self, child, q, family: str = DEFAULT_FAMILY) -> None:
        self._x("""insert into pending2(family_id, child, topic, level, prompt, answer, extra, hints_used, tries, created)
                   values (?,?,?,?,?,?,?,0,0,?)
                   on conflict(family_id, child) do update set topic=excluded.topic, level=excluded.level,
                   prompt=excluded.prompt, answer=excluded.answer, extra=excluded.extra, hints_used=0, tries=0,
                   created=excluded.created""",
                (family, child, q.topic, q.level, q.prompt, q.answer, json.dumps(q.extra()), time.time()))

    def peek_pending(self, child, family: str = DEFAULT_FAMILY):
        rows = self._x("select * from pending2 where family_id=? and child=?", (family, child))
        return rows[0] if rows else None

    def live_pending(self, child, family: str = DEFAULT_FAMILY):
        p = self.peek_pending(child, family)
        return p if p and time.time() - p["created"] <= PENDING_TTL_SECONDS else None

    def pop_pending(self, child, family: str = DEFAULT_FAMILY):
        with self.lock:
            p = self.peek_pending(child, family)
            self._x("delete from pending2 where family_id=? and child=?", (family, child))
        return p if p and time.time() - p["created"] <= PENDING_TTL_SECONDS else None

    def bump_pending(self, child, column: str, family: str = DEFAULT_FAMILY) -> int:
        assert column in {"hints_used", "tries"}
        self._x(f"update pending2 set {column}={column}+1 where family_id=? and child=?", (family, child))
        p = self.peek_pending(child, family)
        return p[column] if p else 0

    def claim_event(self, child: str, event_id: str, family: str = DEFAULT_FAMILY) -> bool:
        """Remember a TV event id; False if it was already seen (idempotent retries)."""
        with self.lock:
            if self._x("select 1 from tv_events2 where family_id=? and child=? and event_id=?", (family, child, event_id)):
                return False
            self._x("insert into tv_events2(family_id, child, event_id) values (?,?,?)", (family, child, event_id))
            return True

    def prune_events(self, family: str, keep_per_child: int = 5000) -> None:
        pass  # TODO: event ids carry no timestamp yet; bounded by the 100 events/request cap

    # -- spaced repetition items ---------------------------------------------
    def upsert_item(self, family: str, child: str, q, correct: bool, hinted: bool, box: int, due: float, now: float) -> None:
        self._x("""insert into items(family_id, child, topic, item_key, prompt, answer, extra, level, box, due, seen, correct, last_ts)
                   values (?,?,?,?,?,?,?,?,?,?,1,?,?)
                   on conflict(family_id, child, topic, item_key) do update set prompt=excluded.prompt,
                   answer=excluded.answer, extra=excluded.extra, level=excluded.level, box=excluded.box,
                   due=excluded.due, seen=items.seen+1, correct=items.correct+excluded.correct, last_ts=excluded.last_ts""",
                (family, child, q.topic, q.item_key, q.prompt, q.answer, json.dumps(q.extra()), q.level, box, due,
                 int(correct), now))

    def item(self, family: str, child: str, topic: str, key: str) -> dict | None:
        r = self._x("select * from items where family_id=? and child=? and topic=? and item_key=?",
                    (family, child, topic, key))
        return r[0] if r else None

    def due_items(self, family: str, child: str, topic: str | None, now: float, max_box: int = 2,
                  max_level: int | None = None, limit: int = 20) -> list[dict]:
        sql = "select * from items where family_id=? and child=? and box<=? and due<=?"
        args: list = [family, child, max_box, now]
        if topic:
            sql += " and topic=?"; args.append(topic)
        if max_level:
            sql += " and level<=?"; args.append(max_level)
        return self._x(sql + " order by due limit ?", (*args, limit))

    def weak_items(self, family: str, child: str, limit: int = 5) -> list[dict]:
        return self._x("select topic, item_key, prompt, answer, box, seen, correct from items where family_id=? and child=? "
                       "and box<=2 order by box, due limit ?", (family, child, limit))

    def item_counts(self, family: str, child: str) -> dict:
        r = self._x("select box, count(*) as n from items where family_id=? and child=? group by box", (family, child))
        return {x["box"]: x["n"] for x in r}

    # -- skills / attempts ---------------------------------------------------
    def update_skill(self, family: str, child: str, skill: str, credit: float, alpha: float = 0.15) -> float:
        with self.lock:
            self._x("insert into skills(family_id, child, skill) values (?,?,?) on conflict do nothing",
                    (family, child, skill))
            cur = self._x("select mastery from skills where family_id=? and child=? and skill=?", (family, child, skill))[0]
            m = (1 - alpha) * cur["mastery"] + alpha * credit
            self._x("update skills set mastery=?, attempts=attempts+1, correct=correct+? where family_id=? and child=? and skill=?",
                    (m, 1 if credit >= 0.75 else 0, family, child, skill))
            return m

    def skills(self, family: str, child: str) -> list[dict]:
        return self._x("select * from skills where family_id=? and child=? order by skill", (family, child))

    def log_attempt(self, family: str, child: str, topic: str, skill: str, key: str, correct: bool, hinted: bool,
                    ts: float, session: str = "") -> None:
        self._x("insert into attempts(family_id, child, topic, skill, item_key, correct, hinted, ts, session) values (?,?,?,?,?,?,?,?,?)",
                (family, child, topic, skill, key, int(correct), int(hinted), ts, session))

    def attempts_since(self, family: str, child: str, since: float, until: float | None = None) -> list[dict]:
        return self._x("select * from attempts where family_id=? and child=? and ts>=? and ts<? order by ts",
                       (family, child, since, until or 4e12))

    def recent_keys(self, family: str, child: str, topic: str, n: int = 4) -> list[str]:
        return [r["item_key"] for r in self._x(
            "select item_key from attempts where family_id=? and child=? and topic=? order by ts desc limit ?",
            (family, child, topic, n))]

    def recent_results(self, family: str, child: str, topic: str, n: int = 8) -> list[int]:
        return [r["correct"] for r in self._x(
            "select correct from attempts where family_id=? and child=? and topic=? order by ts desc limit ?",
            (family, child, topic, n))]

    def attempt_totals(self, family: str, child: str) -> dict:
        r = self._x("select count(*) as n, sum(correct) as c from attempts where family_id=? and child=?", (family, child))[0]
        return {"attempts": r["n"] or 0, "correct": int(r["c"] or 0)}

    # -- sessions -------------------------------------------------------------
    def session(self, sid: str) -> dict | None:
        r = self._x("select * from sessions where id=?", (sid,))
        return r[0] if r else None

    def open_session(self, family: str, child: str) -> dict | None:
        r = self._x("select * from sessions where family_id=? and child=? and ended=0 order by last_ts desc limit 1",
                    (family, child))
        return r[0] if r else None

    def new_session(self, sid: str, family: str, child: str, now: float) -> None:
        self._x("insert into sessions(id, family_id, child, started, last_ts) values (?,?,?,?,?)", (sid, family, child, now, now))

    def touch_session(self, sid: str, asked: int, correct: int, hinted: int, topic: str, now: float) -> None:
        with self.lock:
            s = self.session(sid)
            topics = json.loads(s["topics"]) if s else []
            if topic and topic not in topics:
                topics.append(topic)
            self._x("update sessions set last_ts=?, asked=asked+?, correct=correct+?, hinted=hinted+?, topics=? where id=?",
                    (now, asked, correct, hinted, json.dumps(topics), sid))

    def end_session(self, sid: str, now: float) -> None:
        self._x("update sessions set ended=1, last_ts=? where id=?", (now, sid))

    def sessions_since(self, family: str, child: str, since: float) -> list[dict]:
        return self._x("select * from sessions where family_id=? and child=? and started>=? order by started",
                       (family, child, since))

    # -- badges ---------------------------------------------------------------
    def badges(self, family: str, child: str) -> list[dict]:
        return self._x("select badge, earned from badges where family_id=? and child=? order by earned", (family, child))

    def award(self, family: str, child: str, badge: str, now: float) -> bool:
        with self.lock:
            if self._x("select 1 from badges where family_id=? and child=? and badge=?", (family, child, badge)):
                return False
            self._x("insert into badges(family_id, child, badge, earned) values (?,?,?,?)", (family, child, badge, now))
            return True

    # -- privacy: export / retention --------------------------------------------
    def export_family(self, fid: str) -> dict:
        fam = self.family(fid) or {}
        safe = {k: v for k, v in fam.items() if not k.endswith("_hash")}
        out = {"family": safe, "exported_at": time.time(), "schema_version": self.schema_version()}
        for t in ("children", "kid_progress", "items", "skills", "attempts", "sessions", "badges"):
            out[t] = self._x(f"select * from {t} where family_id=?", (fid,))
        return out

    def purge_attempts_older_than(self, cutoff_ts: float) -> int:
        n = self._x("select count(*) as n from attempts where ts<?", (cutoff_ts,))[0]["n"]
        self._x("delete from attempts where ts<?", (cutoff_ts,))
        self._x("delete from pending2 where created<?", (cutoff_ts,))
        return n

    # -- settings (legacy key/value) ---------------------------------------------
    def get_setting(self, k: str, default: str | None = None) -> str | None:
        rows = self._x("select v from settings where k=?", (k,))
        return rows[0]["v"] if rows else default

    def set_setting(self, k: str, v: str) -> None:
        self._x("insert into settings(k, v) values (?, ?) on conflict(k) do update set v=excluded.v", (k, v))


def make_store(url: str | None = None) -> Store:
    return Store(url or "progress.db")
