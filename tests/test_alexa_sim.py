"""The simulated Alexa+ screen: serves a page, and only forwards the child-safe MCP tools."""
from starlette.testclient import TestClient

from learning_coach.alexa_sim import ALLOWED_TOOLS, make_alexa_app


def test_page_is_served():
    r = TestClient(make_alexa_app()).get("/")
    assert r.status_code == 200 and "Alexa+" in r.text and "Learning Coach" in r.text


def test_parent_only_tools_are_not_reachable_from_the_child_screen():
    assert not {"unlock_topic", "lock_topic"} & ALLOWED_TOOLS
    c = TestClient(make_alexa_app())
    for name in ("unlock_topic", "lock_topic", "nope"):
        r = c.post("/tool", json={"name": name, "args": {"parent_pin": "1234", "topic": "x"}})
        assert r.status_code == 400


def test_bad_requests_are_rejected():
    c = TestClient(make_alexa_app())
    assert c.post("/tool", content=b"not json").status_code == 400
    assert c.post("/tool", json={"name": "start_session", "args": "oops"}).status_code == 400
