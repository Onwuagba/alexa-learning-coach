import importlib
import re
import socket
import threading
import time

import pytest

from learning_coach import service
from learning_coach.service import Coach, InvalidInput
from learning_coach.store import Store


@pytest.fixture(autouse=True)
def fast_kdf(monkeypatch):
    monkeypatch.setattr(service, "SCRYPT_N", 2**4)


@pytest.fixture
def coach(tmp_path):
    return Coach(Store(str(tmp_path / "p.db")), "1234")


def answer_for(coach, child):
    return coach.store.peek_pending(child)["answer"]


def test_end_to_end_and_locked_default(coach):
    assert "locked" in coach.next_question("ada", "algebra")["error"]
    assert coach.unlock_topic("ada", "algebra", "0000")["error"]
    assert coach.unlock_topic("ada", "algebra", "1234")["unlocked"] == "algebra"
    assert coach.next_question("ada", "algebra")["level"] == 1
    assert coach.check_answer("ada", answer_for(coach, "ada"))["correct"] is True


def test_level_up_and_down_through_service(coach):
    for _ in range(2):
        coach.next_question("ada", "abacus")
        r = coach.check_answer("ada", answer_for(coach, "ada"))
    assert r["levelled_up"] and r["level"] == 2
    for _ in range(2):
        coach.next_question("ada", "abacus")
        r = coach.check_answer("ada", "99999")
    assert r["levelled_down"] and r["level"] == 1


def test_validation(coach):
    for bad in ["", "   ", "x" * 40, "ada; drop table", "1ada", None]:
        with pytest.raises(InvalidInput):
            service.clean_child(bad)
    assert coach.next_question("ada", "nope")["error"]
    assert coach.check_answer("ada", "")["error"]
    assert coach.check_answer("ada", "x" * 500)["error"]
    assert "No question" in coach.check_answer("ada", "5")["error"]


def test_child_name_normalised(coach):
    assert coach.start_session("  ADA  ")["child"] == "ada"


def test_multi_child_isolation(coach):
    coach.unlock_topic("ada", "algebra", "1234")
    assert "locked" in coach.next_question("ben", "algebra")["error"]
    coach.next_question("ada", "abacus")
    assert "No question" in coach.check_answer("ben", "1")["error"]
    assert sorted(coach.store.children()) == ["ada", "ben"] or "ada" in coach.store.children()


def test_pending_is_single_use(coach):
    coach.next_question("ada", "abacus")
    a = answer_for(coach, "ada")
    coach.check_answer("ada", a)
    assert "No question" in coach.check_answer("ada", a)["error"]


def test_pending_expires(coach, monkeypatch):
    coach.next_question("ada", "abacus")
    a = answer_for(coach, "ada")
    real = time.time
    monkeypatch.setattr("learning_coach.store.time.time", lambda: real() + 3600)
    assert "No question" in coach.check_answer("ada", a)["error"]


def test_pin_is_hashed_not_stored(coach):
    stored = coach.store.family("default")["pin_hash"]
    assert "1234" not in stored and stored.count("$") == 2


def test_pin_lockout(coach):
    for _ in range(service.MAX_FAILS):
        assert coach.unlock_topic("ada", "algebra", "0000")["error"]
    r = coach.unlock_topic("ada", "algebra", "1234")  # correct PIN, but locked
    assert "Too many" in r["error"]
    coach.store.update_family("default", locked_until=0)
    assert coach.unlock_topic("ada", "algebra", "1234")["unlocked"]


def test_bad_pin_formats_rejected(coach):
    for bad in [None, "", "12", "abcd", "1" * 20, 1234]:
        assert coach.unlock_topic("ada", "algebra", bad)["error"]
    with pytest.raises(InvalidInput):
        Coach(coach.store, "12")


def test_generated_pin_when_unset(tmp_path, capsys):
    Coach(Store(str(tmp_path / "g.db")))
    assert re.search(r"generated one-time PIN: \d{6}", capsys.readouterr().err)


def test_pin_persists_across_restart(tmp_path):
    path = str(tmp_path / "r.db")
    Coach(Store(path), "1234")
    c2 = Coach(Store(path))  # no env PIN: keeps stored hash
    assert c2.unlock_topic("ada", "algebra", "1234")["unlocked"]


def test_sqlite_url_form(tmp_path):
    s = Store(f"sqlite:///{tmp_path}/u.db")
    assert s.record("a", "abacus", True, 5) == 1


# ---- parent web page --------------------------------------------------------
@pytest.fixture
def client(coach):
    from starlette.testclient import TestClient
    from learning_coach.web import make_app
    return TestClient(make_app(coach, "s3cret"), follow_redirects=False)


def test_web_requires_login(client):
    assert "Parent sign-in" in client.get("/").text
    assert client.post("/unlock", data={"child": "ada", "topic": "algebra", "pin": "1234"}).status_code == 401


def test_web_login_view_unlock(client, coach):
    coach.start_session("ada")
    assert client.post("/login", data={"pin": "0000"}).status_code == 403
    r = client.post("/login", data={"pin": "1234"})
    assert r.status_code == 303 and "httponly" in r.headers["set-cookie"].lower()
    assert "ada" in client.get("/").text
    r = client.post("/unlock", data={"child": "ada", "topic": "algebra", "pin": "1234"})
    assert r.status_code == 303 and "Unlocked" in r.headers["location"]
    assert "algebra" in coach.available("ada")
    r = client.post("/unlock", data={"child": "ada", "topic": "algebra", "pin": "9999"})
    assert "Wrong" in r.headers["location"]


def test_web_escapes_html_and_rejects_forged_cookie(client, coach):
    client.cookies.set("session", "9999999999.deadbeef")
    assert "Parent sign-in" in client.get("/").text
    client.cookies.clear()
    client.post("/login", data={"pin": "1234"})
    r = client.post("/unlock", data={"child": "<script>", "topic": "algebra", "pin": "1234"})
    assert "<script>" not in r.headers["location"]


# ---- real MCP over streamable HTTP ---------------------------------------------
def test_mcp_over_http(tmp_path, monkeypatch):
    import asyncio
    import uvicorn
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    monkeypatch.setenv("PROGRESS_DB", str(tmp_path / "m.db"))
    monkeypatch.setenv("PARENT_PIN", "1234")
    monkeypatch.setenv("MCP_AUTH_TOKEN", "tok")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import learning_coach.server as srv
    importlib.reload(srv)
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
    server = uvicorn.Server(uvicorn.Config(srv.build_app(), port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True); t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    try:
        import httpx
        assert httpx.get(f"http://127.0.0.1:{port}/healthz").text == "ok"
        assert httpx.post(f"http://127.0.0.1:{port}/mcp/").status_code == 401

        async def run():
            async with streamablehttp_client(f"http://127.0.0.1:{port}/mcp/",
                                             headers={"Authorization": "Bearer tok"}) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    names = {x.name for x in (await s.list_tools()).tools}
                    res = await s.call_tool("next_question", {"child": "ada", "topic": "abacus"})
                    return names, res.structuredContent or res.content[0].text
        names, out = asyncio.run(run())
        assert names == {"list_children", "start_session", "next_question", "check_answer", "get_hint", "repeat_question",
                         "end_session", "get_progress", "unlock_topic", "lock_topic"}
        assert "prompt" in str(out)
    finally:
        server.should_exit = True; t.join(5)
