import json
from urllib.parse import unquote

import pytest
from starlette.testclient import TestClient

from learning_coach import reports
from learning_coach.service import Coach
from learning_coach.store import Store
from learning_coach.web import make_app


@pytest.fixture
def coach(tmp_path):
    return Coach(Store(str(tmp_path / "w.db")), "1234")


@pytest.fixture
def client(coach):
    return TestClient(make_app(coach, "s3cret"), follow_redirects=False)


def signup(client, name="The Adeyemis", pin="4321", child="Ola", email="p@example.com"):
    return client.post("/signup", data={"name": name, "pin": pin, "child": child, "email": email})


def practise(coach, fam, child="ola", n=4, wrong=1):
    for i in range(n):
        coach.next_question(child, "abacus", family=fam)
        a = coach.store.peek_pending(child, fam)["answer"]
        coach.check_answer(child, "99999" if i < wrong else a, family=fam)


def test_signup_shows_tokens_once_and_logs_in(client, coach):
    r = signup(client)
    assert r.status_code == 200 and "lc_tv_" in r.text and "lc_mcp_" in r.text and "adeyemis" in r.text.lower()
    assert "httponly" in r.headers["set-cookie"].lower() and "samesite=strict" in r.headers["set-cookie"].lower()
    home = client.get("/")
    assert "The Adeyemis" in home.text and "lc_tv_" not in home.text and "Ola" in home.text
    assert client.post("/logout").status_code == 303
    client.cookies.clear()
    assert "Parent sign-in" in client.get("/").text


def test_signup_errors_and_disabled(client, monkeypatch):
    r = client.post("/signup", data={"name": "x", "pin": "12"})
    assert r.status_code == 400 and "PIN" in r.text or "name" in r.text
    monkeypatch.setenv("ALLOW_SIGNUP", "0")
    c2 = TestClient(make_app(client.app.state.__dict__.get("c") or Coach(Store(":memory:"), "1234"), "k"), follow_redirects=False)
    assert c2.post("/signup", data={"name": "Fine Name", "pin": "1234"}).status_code == 403
    assert "New family" not in c2.get("/").text


def test_login_by_family_code_and_isolated_views(client, coach):
    signup(client, "Alpha Fam", "1111", "Ada")
    fa = coach.store.family_by("slug", "alpha-fam")["id"]
    client.cookies.clear()
    signup(client, "Beta Fam", "2222", "Ben")
    client.cookies.clear()
    assert client.post("/login", data={"family": "alpha-fam", "pin": "2222"}).status_code == 403
    assert client.post("/login", data={"family": "alpha-fam", "pin": "1111"}).status_code == 303
    page = client.get("/").text
    assert "Ada" in page and "Ben" not in page and "Alpha Fam" in page
    # a cookie forged for another family id is rejected
    client.cookies.set("session", f"{fa}.9999999999.deadbeef")
    assert "Parent sign-in" in client.get("/").text


def test_dashboard_charts_and_content(client, coach):
    signup(client)
    fam = coach.store.family_by("slug", "the-adeyemis")["id"]
    practise(coach, fam, n=6, wrong=2)
    html = client.get("/").text
    assert html.count("<svg") >= 3 and 'role="img"' in html and "Last 14 days" in html and "Skills" in html
    assert "bar-c" in html and "Weekly goal 20 percent" in html and "day streak" in html
    assert "First steps" in html  # badge chip
    assert "Tricky ones coming back" in html
    # contains no script and no external resources
    assert "<script" not in html and "http://" not in html.replace("http://www.w3.org", "") and "https://" not in html


def test_dashboard_escapes_child_names_and_msg(client, coach):
    signup(client)
    r = client.get("/?msg=%3Cscript%3Ealert(1)%3C/script%3E")
    assert "<script>alert" not in r.text and "&lt;script&gt;" in r.text
    r = client.post("/child/add", data={"name": "<img src=x>"})
    assert "<img" not in unquote(r.headers["location"]) and "bad=1" in unquote(r.headers["location"])


def test_topic_lock_unlock_from_dashboard(client, coach):
    signup(client)
    fam = coach.store.family_by("slug", "the-adeyemis")["id"]
    r = client.post("/unlock", data={"child": "ola", "act": "unlock:fractions", "pin": "4321"})
    assert "Unlocked" in unquote(r.headers["location"]) and "fractions" in coach.available("ola", fam)
    assert "Wrong" in unquote(client.post("/unlock", data={"child": "ola", "act": "lock:fractions", "pin": "0000"}).headers["location"])
    r = client.post("/unlock", data={"child": "ola", "act": "lock:fractions", "pin": "4321"})
    assert "Locked" in unquote(r.headers["location"]) and "fractions" not in coach.available("ola", fam)
    assert "locked" in coach.next_question("ola", "fractions", family=fam)["error"]
    assert "locked" in client.get("/").text


def test_limits_goal_and_settings(client, coach):
    signup(client)
    fam = coach.store.family_by("slug", "the-adeyemis")["id"]
    r = client.post("/child/settings", data={"child": "ola", "daily_minutes": "20", "weekly_goal": "50"})
    assert "Saved" in unquote(r.headers["location"])
    c = coach.store.child(fam, "ola")
    assert c["daily_minutes"] == 20 and c["weekly_goal"] == 50
    assert "bad=1" in unquote(client.post("/child/settings", data={"child": "ola", "daily_minutes": "9999"}).headers["location"])
    assert "bad=1" in unquote(client.post("/child/settings", data={"child": "ola", "daily_minutes": "abc"}).headers["location"])
    client.post("/settings", data={"name": "Renamed", "email": "z@example.com", "tz": "60"})
    assert coach.family_info(fam)["name"] == "Renamed" and coach.family_info(fam)["tz_offset_min"] == 60
    assert "bad=1" in unquote(client.post("/settings", data={"name": "ok name", "email": "bad", "tz": "0"}).headers["location"])


def test_report_pages_and_send_stub(client, coach, monkeypatch):
    signup(client)
    fam = coach.store.family_by("slug", "the-adeyemis")["id"]
    practise(coach, fam, n=5)
    html = client.get("/report?child=ola")
    assert html.status_code == 200 and "Ola" in html.text and "weekly report" in html.text.lower()
    txt = client.get("/report?format=txt").text
    assert "Questions this week: 5" in txt
    monkeypatch.delenv("REPORT_MODE", raising=False)
    r = client.post("/report/send")
    assert "not sent" in unquote(r.headers["location"]) and "bad=1" in unquote(r.headers["location"])
    client.cookies.clear()
    assert client.get("/report").status_code == 401


def test_export_download_and_tokens_rotation(client, coach):
    signup(client)
    fam = coach.store.family_by("slug", "the-adeyemis")["id"]
    old = coach.store.family(fam)["tv_token_hash"]
    r = client.get("/export")
    assert r.headers["content-disposition"].startswith("attachment") and json.loads(r.text)["family"]["slug"] == "the-adeyemis"
    assert "pin_hash" not in r.text and "token_hash" not in r.text
    assert "Wrong" in unquote(client.post("/tokens", data={"pin": "0000"}).headers["location"])
    r = client.post("/tokens", data={"pin": "4321"})
    assert "lc_tv_" in r.text and coach.store.family(fam)["tv_token_hash"] != old


def test_family_delete_needs_name_and_pin(client, coach):
    signup(client)
    fam = coach.store.family_by("slug", "the-adeyemis")["id"]
    assert "did not match" in unquote(client.post("/family/delete", data={"confirm": "nope", "pin": "4321"}).headers["location"])
    assert "Wrong" in unquote(client.post("/family/delete", data={"confirm": "The Adeyemis", "pin": "0000"}).headers["location"])
    r = client.post("/family/delete", data={"confirm": "The Adeyemis", "pin": "4321"})
    assert "deleted" in r.text and coach.store.family(fam) is None


def test_all_mutations_require_login(client):
    for path in ["/unlock", "/child/add", "/child/settings", "/settings", "/tokens", "/report/send", "/family/delete"]:
        assert client.post(path, data={}).status_code == 401, path
    for path in ["/report", "/export"]:
        assert client.get(path).status_code == 401


def test_security_headers(client):
    h = client.get("/").headers
    assert h["x-frame-options"] == "DENY" and "default-src 'none'" in h["content-security-policy"] and h["cache-control"] == "no-store"


# ---- reports -----------------------------------------------------------------------------------------------
def test_report_content_text_and_html(coach):
    fam = coach.create_family("Reporters", "1111", "mum@example.com", "Ada")["family_id"]
    coach.set_child_limits("ada", fam, weekly_goal=5)
    practise(coach, fam, "ada", n=6, wrong=2)
    rep = reports.family_report(coach, fam)
    t, h = rep["text"], rep["html"]
    assert "Questions this week: 6" in t and "Accuracy: 67%" in t and "Weekly goal: 6/5" in t and "Ada" in t
    assert "Reporters" in rep["subject"]
    assert "<table" in h and "<script" not in h.lower() and "<svg" not in h.lower() and "src=" not in h.lower()
    assert "Ada" in h and "67%" in h


def test_report_escapes_html(coach):
    fam = coach.create_family("Fine", "1111")["family_id"]
    coach.store.update_family(fam, name="A&B <b>")
    assert "<b>" not in reports.family_report(coach, fam)["html"]


def test_send_report_stub_and_missing_email(coach, monkeypatch):
    monkeypatch.delenv("REPORT_MODE", raising=False)
    fam = coach.create_family("Mailers", "1111", "m@example.com", "Ada")["family_id"]
    rep = reports.family_report(coach, fam)
    r = reports.send_report(rep)
    assert r["sent"] is False and r["mode"] == "stub"
    assert reports.send_report({**rep, "family": {**rep["family"], "email": ""}})["reason"].startswith("no email")


def test_send_report_smtp_failure_is_contained(coach, monkeypatch):
    monkeypatch.setenv("REPORT_MODE", "smtp")
    monkeypatch.setenv("REPORT_SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("REPORT_SMTP_PORT", "1")  # nothing listens here
    fam = coach.create_family("Mailers", "1111", "m@example.com", "Ada")["family_id"]
    assert reports.send_report(reports.family_report(coach, fam))["sent"] is False


def test_report_cli(tmp_path, monkeypatch, capsys):
    db = str(tmp_path / "cli.db")
    monkeypatch.setenv("PROGRESS_DB", db)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("PARENT_PIN", "1234")
    c = Coach(Store(db), "1234")
    c.create_family("Cli Fam", "1111", "", "Ada")
    assert reports.main(["--family", "cli-fam"]) == 0
    assert "weekly learning report" in capsys.readouterr().out
    assert reports.main(["--family", "nope"]) == 1
