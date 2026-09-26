import logging
import sqlite3
import sys
import types

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from learning_coach import ops, service
from learning_coach.ops import METRICS, OpsMiddleware, RateLimiter
from learning_coach.service import Coach
from learning_coach.store import SCHEMA_VERSION, Store


def test_rate_limiter_token_bucket():
    t = [0.0]
    rl = RateLimiter(60, burst=3, clock=lambda: t[0])  # 1/sec, burst 3
    assert [rl.allow("a")[0] for _ in range(4)] == [True, True, True, False]
    ok, wait = rl.allow("a")
    assert not ok and 0 < wait <= 1
    assert rl.allow("b")[0]              # independent keys
    t[0] += 2
    assert rl.allow("a")[0]              # refilled


def _app(**kw):
    async def hi(request):
        return PlainTextResponse("hi")
    return TestClient(OpsMiddleware(Starlette(routes=[Route("/x/{name}", hi), Route("/api/progress/{name}", hi), Route("/healthz", hi),
                                                       Route("/parent/login", hi, methods=["POST"])]), **kw))


def test_middleware_rate_limits_and_exempts_health():
    c = _app(general_per_min=5, strict_per_min=2)
    codes = [c.get("/x/ada").status_code for _ in range(8)]
    assert codes[:5] == [200] * 5 and 429 in codes[5:]
    r = c.get("/x/ada")
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1
    assert all(c.get("/healthz").status_code == 200 for _ in range(20))
    c2 = _app(general_per_min=100, strict_per_min=2)
    assert [c2.post("/parent/login").status_code for _ in range(4)] == [200, 200, 429, 429]


def test_request_id_metrics_and_no_pii_in_logs():
    records = []

    class H(logging.Handler):
        def emit(self, r):
            records.append(r.getMessage() + str(getattr(r, "fields", {})))
    log = ops.get_logger()
    h = H(); log.addHandler(h)
    try:
        c = _app()
        r = c.get("/api/progress/secretchildname?answer=42")
        assert len(r.headers["x-request-id"]) == 12
    finally:
        log.removeHandler(h)
    text = " ".join(records)
    assert "secretchildname" not in text and "answer" not in text
    assert '"/api/progress"' in text.replace("'", '"')
    assert METRICS.get("http_requests_total", route="/api/progress", method="GET", status="200") >= 1
    assert "secretchildname" not in METRICS.render()


def test_metrics_render_format():
    m = ops.Metrics()
    m.inc("things_total", 2, kind='a"b')
    m.inc("things_total", kind='a"b')
    out = m.render()
    assert "# TYPE learning_coach_things_total counter" in out and 'learning_coach_things_total{kind="ab"} 3' in out
    assert "learning_coach_uptime_seconds" in out


def test_json_formatter():
    import json
    rec = logging.LogRecord("n", logging.INFO, "f", 1, "hello", None, None)
    rec.fields = {"a": 1}
    d = json.loads(ops.JsonFormatter().format(rec))
    assert d["msg"] == "hello" and d["a"] == 1 and d["level"] == "INFO"


def test_get_secret_env_then_aws(monkeypatch):
    monkeypatch.setenv("FOO_SECRET", "fromenv")
    assert ops.get_secret("FOO_SECRET") == "fromenv"
    monkeypatch.delenv("FOO_SECRET")
    assert ops.get_secret("FOO_SECRET", "dflt") == "dflt"
    calls = []
    fake = types.ModuleType("boto3")
    fake.client = lambda name: types.SimpleNamespace(get_secret_value=lambda SecretId: calls.append(SecretId) or {"SecretString": '{"FOO_SECRET": "fromaws"}'})
    monkeypatch.setitem(sys.modules, "boto3", fake)
    monkeypatch.setenv("AWS_SECRETS_ID", "prod/learning-coach")
    ops.get_secret.__dict__.pop("_cache", None)
    assert ops.get_secret("FOO_SECRET") == "fromaws" and ops.get_secret("FOO_SECRET") == "fromaws" and calls == ["prod/learning-coach"]
    ops.get_secret.__dict__.pop("_cache", None)


def test_get_secret_survives_aws_failure(monkeypatch):
    monkeypatch.delenv("BAR_SECRET", raising=False)
    monkeypatch.setenv("AWS_SECRETS_ID", "x")
    monkeypatch.setitem(sys.modules, "boto3", None)  # import fails
    ops.get_secret.__dict__.pop("_cache", None)
    assert ops.get_secret("BAR_SECRET", "d") == "d"
    ops.get_secret.__dict__.pop("_cache", None)


# ---- server wiring ----------------------------------------------------------------------------------------------
@pytest.fixture
def srv(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv("PROGRESS_DB", str(tmp_path / "s.db"))
    monkeypatch.setenv("PARENT_PIN", "1234")
    for k in ("MCP_AUTH_TOKEN", "DATABASE_URL", "TV_API_TOKEN", "METRICS_TOKEN", "REQUIRE_MCP_AUTH"):
        monkeypatch.delenv(k, raising=False)
    import learning_coach.server as s
    importlib.reload(s)
    return s


def test_metrics_and_health_endpoints(srv, monkeypatch):
    c = TestClient(srv.build_app())
    assert c.get("/healthz").text == "ok"
    m = c.get("/metrics")
    assert m.status_code == 200 and "learning_coach_http_requests_total" in m.text and "learning_coach_uptime_seconds" in m.text
    monkeypatch.setenv("METRICS_TOKEN", "mt")
    assert c.get("/metrics").status_code == 401
    assert c.get("/metrics", headers={"Authorization": "Bearer mt"}).status_code == 200


def test_domain_counters_move(srv):
    b = METRICS.get("questions_asked_total", topic="abacus", revisit="false")
    srv.coach.next_question("ada", "abacus")
    assert METRICS.get("questions_asked_total", topic="abacus", revisit="false") == b + 1


def test_mcp_open_in_dev_but_bad_token_rejected(srv):
    with TestClient(srv.build_app()) as c:
        assert c.post("/mcp/", headers={"Authorization": "Bearer nope"}).status_code == 401
        assert c.post("/mcp/").status_code != 401  # dev mode: anonymous allowed (documented)


def test_require_mcp_auth_flag(srv, monkeypatch):
    monkeypatch.setenv("REQUIRE_MCP_AUTH", "1")
    assert TestClient(srv.build_app()).post("/mcp/").status_code == 401


def test_healthz_reports_db_down(srv, monkeypatch):
    c = TestClient(srv.build_app())
    monkeypatch.setattr(srv.store, "schema_version", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    assert c.get("/healthz").status_code == 503


# ---- migrations ----------------------------------------------------------------------------------------------------
def test_fresh_db_is_at_latest_and_reopen_is_noop(tmp_path):
    p = str(tmp_path / "m.db")
    s = Store(p)
    assert s.schema_version() == SCHEMA_VERSION == 2
    assert Store(p).migrate() == []


def test_legacy_single_household_db_migrates_into_default_family(tmp_path):
    p = str(tmp_path / "legacy.db")
    old = Store(p, target_version=1)  # the pre-family schema
    assert old.schema_version() == 1
    old._x("insert into progress(child, topic, level, streak, correct, attempts, unlocked, updated_ms) values ('ada','abacus',3,1,7,9,0,5000)")
    old._x("insert into progress(child, topic, level, unlocked) values ('ada','algebra',1,1)")
    old._x("insert into tv_events(child, event_id) values ('ada','e1')")
    old._x("insert into settings(k, v) values ('pin_hash', ?)", (service._hash_pin("7777"),))
    new = Store(p)
    assert new.schema_version() == 2
    assert new.children("default") == ["ada"]
    assert {r["topic"]: r["level"] for r in new.progress("ada", "default")} == {"abacus": 3, "algebra": 1}
    assert new.unlocked_topics("ada", "default") == ["algebra"]
    assert new.claim_event("ada", "e1", "default") is False       # TV idempotency survives the migration
    c = Coach(new)                                                 # no env PIN: the old PIN keeps working
    assert c.check_pin("7777") is None and c.check_pin("0000")
    tv = c.tv_progress("ada")
    assert tv["progress"]["abacus"]["level"] == 3 and tv["available"] == ["abacus", "algebra"] and tv["progress"]["abacus"]["updatedAt"] == 5000


def test_migrations_are_recorded(tmp_path):
    s = Store(str(tmp_path / "r.db"))
    assert [r["version"] for r in s._x("select version from schema_migrations order by version")] == [1, 2]


def test_migrate_cli_helpers_exist():
    from learning_coach import migrate
    assert callable(migrate.main)
