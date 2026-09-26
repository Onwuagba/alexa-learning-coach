import asyncio
import importlib
import json
import socket
import threading
import time

import pytest
from starlette.testclient import TestClient

from learning_coach.service import Coach, InvalidInput
from learning_coach.store import Store
from learning_coach.tvapi import make_tv_app


@pytest.fixture
def coach(tmp_path):
    return Coach(Store(str(tmp_path / "f.db")), "1234")


@pytest.fixture
def two(coach):
    a = coach.create_family("Okafor Family", "1111", "a@example.com", "Ada")
    b = coach.create_family("Smith", "2222", "", "Ben")
    return coach, a, b


def test_create_family_basics(two):
    coach, a, b = two
    assert a["slug"] == "okafor-family" and a["tv_token"].startswith("lc_tv_") and a["link_token"].startswith("lc_mcp_")
    assert coach.children(a["family_id"]) == ["ada"] and coach.children(b["family_id"]) == ["ben"]
    assert coach.create_family("Okafor Family", "3333")["slug"].startswith("okafor-family-")  # slug collision handled


def test_tokens_stored_hashed(two):
    coach, a, _ = two
    f = coach.store.family(a["family_id"])
    assert a["tv_token"] not in json.dumps(f) and a["link_token"] not in json.dumps(f)
    assert coach.family_for_tv_token(a["tv_token"]) == a["family_id"]
    assert coach.family_for_link_token(a["link_token"]) == a["family_id"]
    assert coach.family_for_tv_token(a["link_token"]) is None  # tokens are not interchangeable
    assert coach.family_for_tv_token("") is None and coach.family_for_link_token("nope") is None


def test_rotate_tokens(two):
    coach, a, _ = two
    new = coach.rotate_tokens(a["family_id"])
    assert coach.family_for_tv_token(a["tv_token"]) is None and coach.family_for_tv_token(new["tv_token"]) == a["family_id"]


@pytest.mark.parametrize("kw", [dict(name="x", pin="1234"), dict(name="Fine", pin="12"), dict(name="Fine", pin="abcd"),
                                dict(name="Fine", pin="1234", email="not-an-email"), dict(name="<script>", pin="1234")])
def test_signup_validation(coach, kw):
    with pytest.raises(InvalidInput):
        coach.create_family(**kw)


def test_per_family_pin_and_lockout_is_per_family(two):
    coach, a, b = two
    fa, fb = a["family_id"], b["family_id"]
    assert coach.authenticate("okafor-family", "1111")[0] == fa
    assert coach.authenticate("okafor-family", "2222")[0] is None      # Smith's PIN does not open Okafor
    assert coach.authenticate("smith", "2222")[0] == fb
    assert coach.authenticate("nope", "1111")[1] == "Wrong family code or PIN."
    assert coach.authenticate("", "1234")[0] == "default"              # legacy single-family login
    for _ in range(5):
        coach.authenticate("okafor-family", "0000")
    assert "Too many" in coach.authenticate("okafor-family", "1111")[1]
    assert coach.authenticate("smith", "2222")[0] == fb                # other family not locked


def test_data_isolation_between_families(two):
    coach, a, b = two
    fa, fb = a["family_id"], b["family_id"]
    coach.next_question("ada", "abacus", family=fa)
    coach.check_answer("ada", coach.store.peek_pending("ada", fa)["answer"], family=fa)
    assert coach.store.progress("ada", fb) == [] and coach.children(fb) == ["ben"]
    # Smith cannot address Ada; ambiguous/unknown names get a spoken question, never someone else's child
    r = coach.next_question("ada", "abacus", family=fb)
    assert r.get("need_child") and r["options"] == ["Ben"] and "Ben" in r["say"]
    assert coach.store.attempts_since(fb, "ada", 0) == []
    coach.unlock_topic("ada", "fractions", "1111", family=fa)
    assert "fractions" in coach.available("ada", fa) and "fractions" not in coach.available("ada", fb)
    assert coach.unlock_topic("ben", "fractions", "1111", family=fb)["error"]  # wrong family's PIN


def test_child_resolution_by_voice(two):
    coach, a, _ = two
    fa = a["family_id"]
    coach.add_child("Tobi", family=fa)
    coach.add_child("Ada Grace", family=fa)
    # single child of a family: name optional
    solo = coach.create_family("Solo", "5555", "", "Zed")["family_id"]
    assert coach.start_session("", family=solo)["child"] == "zed"
    assert coach.start_session("", family=fa)["need_child"]                       # several children: ask who
    assert coach.start_session("toby", family=fa)["child"] == "tobi"              # near-miss spelling
    assert coach.start_session("ADA GRACE", family=fa)["child"] == "ada grace"
    r = coach.start_session("Mo", family=fa)                                       # unknown: ask, don't create
    assert r["need_child"] and "Mo" in r["say"] and "mo" not in coach.children(fa)
    assert coach.list_children(fa)["children"] == ["Ada", "Tobi", "Ada Grace"]


def test_first_child_can_be_created_by_voice(coach):
    fam = coach.create_family("Fresh", "4444")["family_id"]
    assert coach.start_session("Lola", family=fam)["child"] == "lola"
    assert coach.start_session("Mia", family=fam).get("need_child")  # now the roster is set: no silent creation


def test_child_limit(coach):
    fam = coach.create_family("Big", "4444")["family_id"]
    for i in "abcdefgh":
        coach.add_child(f"kid {i}", family=fam)
    with pytest.raises(InvalidInput):
        coach.add_child("one more", family=fam)


def test_export_and_delete(two):
    coach, a, b = two
    fa = a["family_id"]
    coach.next_question("ada", "abacus", family=fa)
    coach.check_answer("ada", coach.store.peek_pending("ada", fa)["answer"], family=fa)
    ex = coach.export_family(fa)
    assert ex["family"]["slug"] == "okafor-family" and ex["children"][0]["name"] == "ada" and len(ex["attempts"]) == 1
    text = json.dumps(ex, default=str)
    assert "pin_hash" not in text and "token_hash" not in text and "Ben" not in text and "ben" not in text.lower().replace("ben ", "")
    assert coach.delete_family(fa, "9999")["error"]
    assert coach.delete_family(fa, "1111") == {"deleted": True}
    assert coach.store.family(fa) is None and coach.store.progress("ada", fa) == []
    assert coach.store.attempts_since(fa, "ada", 0) == [] and coach.children(b["family_id"]) == ["ben"]
    assert coach.delete_family("default", "1234")["error"]


def test_delete_child_and_purge(two):
    coach, a, _ = two
    fa = a["family_id"]
    coach.next_question("ada", "abacus", family=fa)
    coach.check_answer("ada", coach.store.peek_pending("ada", fa)["answer"], family=fa)
    assert coach.purge_old(days=-1) >= 1 and coach.store.attempts_since(fa, "ada", 0) == []
    coach.delete_child("ada", fa)
    assert coach.children(fa) == [] and coach.store.progress("ada", fa) == []


# ---- TV API is per family ---------------------------------------------------------------------------
def test_tv_api_tokens_are_per_family(two):
    coach, a, b = two
    c = TestClient(make_tv_app(coach, None))
    ha, hb = {"Authorization": f"Bearer {a['tv_token']}"}, {"Authorization": f"Bearer {b['tv_token']}"}
    ev = {"events": [{"id": "e1", "topic": "abacus", "correct": True, "at": 1000}]}
    assert c.post("/progress/ada/events", headers=ha, json=ev).json()["applied"] == 1
    assert c.get("/progress/ada", headers=hb).status_code == 404                      # Smith has no Ada
    assert c.get("/progress/ben", headers=ha).status_code == 404
    assert c.get("/progress/ada", headers={"Authorization": f"Bearer {a['link_token']}"}).status_code == 401
    assert c.get("/progress/ada", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.get("/progress/ada", headers=ha).json()["progress"]["abacus"]["attempts"] == 1
    # same event id in another family is independent (dedupe is per family)
    assert c.post("/progress/ben/events", headers=hb, json=ev).json()["applied"] == 1
    assert coach.child_stats(a["family_id"], "ada")["week"]["answered"] in (0, 1)  # TV answers feed the parent stats


def test_env_tv_token_still_maps_to_default_family(two):
    coach, a, _ = two
    c = TestClient(make_tv_app(coach, "envtok"))
    assert c.get("/progress/ada", headers={"Authorization": "Bearer envtok"}).status_code == 200
    assert c.get("/progress/ada", headers={"Authorization": f"Bearer {a['tv_token']}"}).status_code == 200


# ---- real MCP: two families over one server -------------------------------------------------------------
def _serve(tmp_path, monkeypatch):
    import uvicorn
    monkeypatch.setenv("PROGRESS_DB", str(tmp_path / "m.db"))
    monkeypatch.setenv("PARENT_PIN", "1234")
    monkeypatch.setenv("MCP_AUTH_TOKEN", "envtok")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import learning_coach.server as srv
    importlib.reload(srv)
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
    server = uvicorn.Server(uvicorn.Config(srv.build_app(), port=port, log_level="error", log_config=None))
    t = threading.Thread(target=server.run, daemon=True); t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    return srv, server, t, port


def test_mcp_family_scoping_over_http(tmp_path, monkeypatch):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    import httpx
    srv, server, t, port = _serve(tmp_path, monkeypatch)
    try:
        a = srv.coach.create_family("Alpha", "1111", "", "Ada")
        b = srv.coach.create_family("Beta", "2222", "", "Ben")
        url = f"http://127.0.0.1:{port}/mcp/"
        assert httpx.post(url, headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert httpx.post(url).status_code == 401  # env token set: anonymous not allowed

        async def call(token, tool, **args):
            async with streamablehttp_client(url, headers={"Authorization": f"Bearer {token}"}) as (r, w, _):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    res = await s.call_tool(tool, args)
                    return res.structuredContent or json.loads(res.content[0].text)

        out = asyncio.run(call(a["link_token"], "next_question", topic="abacus"))   # no child: Alpha has only Ada
        assert "prompt" in out and out["say"]
        assert srv.store.peek_pending("ada", a["family_id"]) and srv.store.peek_pending("ada", b["family_id"]) is None
        out = asyncio.run(call(b["link_token"], "start_session", child="Ada"))       # Beta cannot reach Alpha's Ada
        assert out.get("need_child") and out["options"] == ["Ben"]
        out = asyncio.run(call("envtok", "list_children"))                            # env fallback = default family
        assert out["children"] == []
        out = asyncio.run(call(a["link_token"], "list_children"))
        assert out["children"] == ["Ada"]
    finally:
        server.should_exit = True; t.join(5)
