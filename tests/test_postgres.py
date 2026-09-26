"""Runs the store/service against a REAL Postgres. Skipped unless TEST_PG_ADMIN_URL is set, e.g.
   TEST_PG_ADMIN_URL=postgresql://lc@127.0.0.1:55432/postgres pytest tests/test_postgres.py
A throwaway database is created and dropped per test."""
import os
import uuid

import pytest

ADMIN = os.getenv("TEST_PG_ADMIN_URL")
psycopg = pytest.importorskip("psycopg") if ADMIN else None
pytestmark = pytest.mark.skipif(not ADMIN, reason="set TEST_PG_ADMIN_URL to run against a real Postgres")

from learning_coach import reports, service  # noqa: E402
from learning_coach.service import Coach  # noqa: E402
from learning_coach.store import Store  # noqa: E402


@pytest.fixture
def url():
    name = "lc_test_" + uuid.uuid4().hex[:8]
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute(f'create database "{name}"')
    base = ADMIN.rsplit("/", 1)[0]
    yield f"{base}/{name}"
    with psycopg.connect(ADMIN, autocommit=True) as c:
        c.execute(f'drop database "{name}" with (force)')


def test_full_flow_on_postgres(url):
    s = Store(url)
    assert s.pg and s.schema_version() == 2
    coach = Coach(s, "1234")
    fam = coach.create_family("Pg Family", "1111", "p@example.com", "Ada")
    a, f = "ada", fam["family_id"]
    coach.unlock_topic("ada", "times_tables", "1111", family=f)
    for i in range(5):
        q = coach.next_question("ada", "times_tables", family=f)
        assert "prompt" in q
        r = coach.check_answer("ada", "99999" if i == 0 else s.peek_pending(a, f)["answer"], family=f)
        assert r["correct"] is (i != 0)
    assert s.weak_items(f, a) and s.skills(f, a) and s.badges(f, a)
    assert coach.end_session("ada", family=f)["recap"]["asked"] == 5
    assert coach.family_for_tv_token(fam["tv_token"]) == f
    ev = [{"id": "e1", "topic": "abacus", "correct": True, "at": 1000}]
    assert coach.tv_events("ada", ev, f)["applied"] == 1 and coach.tv_events("ada", ev, f)["duplicates"] == 1
    assert reports.family_report(coach, f)["text"]
    assert coach.export_family(f)["attempts"]
    assert Store(url).migrate() == []                 # reopening is a no-op
    assert coach.delete_family(f, "1111") == {"deleted": True} and s.progress(a, f) == []


def test_legacy_migration_on_postgres(url):
    old = Store(url, target_version=1)
    old._x("insert into progress(child, topic, level, unlocked, updated_ms) values ('ada','abacus',3,0,5000)")
    old._x("insert into settings(k, v) values ('pin_hash', ?)", (service._hash_pin("7777"),))
    new = Store(url)
    assert new.children("default") == ["ada"] and new.progress("ada", "default")[0]["level"] == 3
    assert Coach(new).check_pin("7777") is None
