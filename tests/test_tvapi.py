import pytest
from starlette.testclient import TestClient

from learning_coach import service
from learning_coach.service import Coach
from learning_coach.store import Store
from learning_coach.tvapi import make_tv_app

H = {"Authorization": "Bearer tvtok"}


@pytest.fixture(autouse=True)
def fast_kdf(monkeypatch):
    monkeypatch.setattr(service, "SCRYPT_N", 2**4)


@pytest.fixture
def coach(tmp_path):
    return Coach(Store(str(tmp_path / "p.db")), "1234")


@pytest.fixture
def client(coach):
    return TestClient(make_tv_app(coach, "tvtok"))


def ev(i, correct=True, topic="abacus", at=None):
    return {"id": f"e{i}", "topic": topic, "correct": correct, "at": at or 1000 + i}


def test_auth_required(client):
    assert client.get("/progress/ada").status_code == 401
    assert client.get("/progress/ada", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.post("/progress/ada/events", json={"events": [ev(1)]}).status_code == 401


def test_disabled_without_token(coach, monkeypatch):
    monkeypatch.delenv("TV_API_TOKEN", raising=False)
    assert TestClient(make_tv_app(coach)).get("/progress/ada", headers=H).status_code == 503


def test_get_progress_reflects_coach(client, coach):
    coach.next_question("ada", "abacus")
    coach.check_answer("ada", coach.store.peek_pending("ada")["answer"])
    coach.next_question("ada", "abacus")
    coach.check_answer("ada", coach.store.peek_pending("ada")["answer"])  # 2 right -> level 2
    j = client.get("/progress/Ada", headers=H).json()
    assert j["child"] == "ada" and j["available"] == ["abacus"]
    assert j["progress"]["abacus"]["level"] == 2 and j["progress"]["abacus"]["attempts"] == 2
    assert j["progress"]["abacus"]["updatedAt"] > 0


def test_events_apply_with_coach_rules_and_are_idempotent(client, coach):
    r = client.post("/progress/ada/events", headers=H, json={"events": [ev(1), ev(2)]})
    assert r.status_code == 200 and r.json()["applied"] == 2
    assert r.json()["progress"]["abacus"]["level"] == 2
    r = client.post("/progress/ada/events", headers=H, json={"events": [ev(1), ev(2)]})
    assert r.json()["applied"] == 0 and r.json()["duplicates"] == 2
    assert client.get("/progress/ada", headers=H).json()["progress"]["abacus"]["attempts"] == 2
    assert coach.get_progress("ada")["progress"][0]["level"] == 2  # same store the MCP tools use


def test_cannot_claim_level_or_unlock(client, coach):
    e = ev(1, topic="algebra"); e["level"] = 5
    r = client.post("/progress/ada/events", headers=H, json={"events": [e]}).json()
    assert r["rejected"] == [{"id": "e1", "reason": "locked"}] and r["applied"] == 0
    assert "algebra" not in r["available"] and "locked" in coach.next_question("ada", "algebra")["error"]
    assert "algebra" not in client.get("/progress/ada", headers=H).json()["progress"]
    coach.unlock_topic("ada", "algebra", "1234")
    r = client.post("/progress/ada/events", headers=H, json={"events": [ev(2, topic="algebra")]}).json()
    assert r["applied"] == 1 and r["progress"]["algebra"]["level"] == 1


@pytest.mark.parametrize("body", [
    {}, {"events": []}, {"events": "x"}, {"events": [1]}, {"events": [ev(1)] * 2},
    {"events": [{**ev(1), "topic": "nope"}]}, {"events": [{**ev(1), "correct": "yes"}]},
    {"events": [{**ev(1), "at": -1}]}, {"events": [{**ev(1), "at": True}]},
    {"events": [{**ev(1), "id": "bad id!"}]}, {"events": [{**ev(1), "id": None}]},
    {"events": [ev(i) for i in range(101)]},
])
def test_validation_400(client, body):
    assert client.post("/progress/ada/events", headers=H, json=body).status_code == 400


def test_bad_json_bad_child_and_size(client):
    assert client.post("/progress/ada/events", headers=H, content=b"{nope").status_code == 400
    assert client.get("/progress/1234", headers=H).status_code == 400
    assert client.post("/progress/ada/events", headers=H, content=b"x" * 70000).status_code == 413


def test_children_isolated(client):
    client.post("/progress/ada/events", headers=H, json={"events": [ev(1)]})
    assert client.get("/progress/bo", headers=H).json()["progress"] == {}


def test_cors(client):
    r = client.options("/progress/ada", headers={"Origin": "http://localhost:8082",
                       "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization"})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "http://localhost:8082"
    r = client.options("/progress/ada", headers={"Origin": "http://evil.example",
                       "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in r.headers


def test_mounted_in_server_and_mcp_auth_untouched(tmp_path, monkeypatch):
    monkeypatch.setenv("PROGRESS_DB", str(tmp_path / "s.db"))
    monkeypatch.setenv("PARENT_PIN", "1234")
    monkeypatch.setenv("MCP_AUTH_TOKEN", "mcptok")
    monkeypatch.setenv("TV_API_TOKEN", "tvtok")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import importlib
    import learning_coach.server as srv
    importlib.reload(srv)
    c = TestClient(srv.build_app())
    assert c.get("/api/progress/ada", headers=H).status_code == 200
    assert c.get("/api/progress/ada", headers={"Authorization": "Bearer mcptok"}).status_code == 401  # tokens not interchangeable
    assert c.post("/mcp/", headers=H).status_code == 401  # TV token is not an MCP token
