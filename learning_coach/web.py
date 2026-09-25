"""Parent web page: sign in with the PIN, see every child's progress, unlock topics.

Session = HMAC-signed cookie (15 min, HttpOnly, SameSite=Strict). Forms are POST-only, so
SameSite=Strict is the CSRF defence. Serve behind HTTPS in production (cookie is Secure
when COOKIE_SECURE=1).
"""
from __future__ import annotations

import hashlib
import hmac
import html
import os
import secrets
import time

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse
from starlette.routing import Route

from . import curriculum
from .service import Coach, InvalidInput

SESSION_SECONDS = 15 * 60
E = html.escape

PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Learning Coach - Parent</title>
<style>body{{font:16px system-ui;max-width:42rem;margin:2rem auto;padding:0 1rem;color:#222}}
table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #ddd;padding:.4rem;text-align:left}}
.err{{color:#b00020}}.ok{{color:#0a6b2d}}button{{padding:.4rem .8rem}}</style>{body}"""


def make_app(coach: Coach, secret: str | None = None) -> Starlette:
    key = (secret or os.getenv("SESSION_SECRET") or secrets.token_hex(32)).encode()
    secure = os.getenv("COOKIE_SECURE") == "1"

    def sign(exp: int) -> str:
        return f"{exp}.{hmac.new(key, str(exp).encode(), hashlib.sha256).hexdigest()}"

    def authed(request: Request) -> bool:
        v = request.cookies.get("session", "")
        exp, _, sig = v.partition(".")
        return exp.isdigit() and int(exp) > time.time() and hmac.compare_digest(v, sign(int(exp)))

    def render(body: str, status: int = 200) -> HTMLResponse:
        return HTMLResponse(PAGE.format(body=body), status_code=status,
                            headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"})

    def login_form(msg: str = "", status: int = 200) -> HTMLResponse:
        return render(f"<h1>Parent sign-in</h1><p class=err>{E(msg)}</p>"
                      "<form method=post action=login><label>Parent PIN "
                      "<input name=pin type=password inputmode=numeric autocomplete=current-password></label> "
                      "<button>Sign in</button></form>", status)

    async def home(request: Request):
        if not authed(request):
            return login_form()
        msg = request.query_params.get("msg", "")
        rows = ""
        for c in coach.overview():
            for p in c["progress"]:
                locked = p["topic"] not in c["available"]
                acc = f"{100 * p['correct'] // p['attempts']}%" if p["attempts"] else "-"
                rows += (f"<tr><td>{E(c['child'])}</td><td>{E(curriculum.TOPICS[p['topic']]['title'])}</td>"
                         f"<td>{p['level']}/{curriculum.max_level(p['topic'])}</td><td>{p['attempts']} ({acc})</td>"
                         f"<td>{'locked' if locked else 'open'}</td></tr>")
        opts = "".join(f"<option value={E(t)}>{E(v['title'])}</option>" for t, v in curriculum.TOPICS.items())
        kids = "".join(f"<option>{E(c)}</option>" for c in coach.store.children())
        return render(
            f"<h1>Learning Coach</h1><p class=ok>{E(msg)}</p>"
            "<table><tr><th>Child<th>Topic<th>Level<th>Answers<th>Status</tr>"
            f"{rows or '<tr><td colspan=5>No practice yet.</td></tr>'}</table>"
            "<h2>Unlock a topic</h2><form method=post action=unlock>"
            f"<label>Child <input name=child list=kids required maxlength=32></label><datalist id=kids>{kids}</datalist> "
            f"<label>Topic <select name=topic>{opts}</select></label> "
            "<label>PIN <input name=pin type=password inputmode=numeric required></label> "
            "<button>Unlock</button></form>"
            "<form method=post action=logout><button>Sign out</button></form>")

    async def login(request: Request):
        form = await request.form()
        err = coach.check_pin(str(form.get("pin", "")))
        if err:
            return login_form(err, 403)
        r = RedirectResponse("./", status_code=303)
        r.set_cookie("session", sign(int(time.time()) + SESSION_SECONDS), max_age=SESSION_SECONDS,
                     httponly=True, samesite="strict", secure=secure)
        return r

    async def unlock(request: Request):
        if not authed(request):
            return login_form("Please sign in.", 401)
        form = await request.form()
        try:
            res = coach.unlock_topic(str(form.get("child", "")), str(form.get("topic", "")), str(form.get("pin", "")))
        except InvalidInput as e:
            res = {"error": str(e)}
        from urllib.parse import quote
        msg = res.get("error") or f"Unlocked {res['unlocked']}."
        return RedirectResponse(f"./?msg={quote(msg)}", status_code=303)

    async def logout(request: Request):
        r = RedirectResponse("./", status_code=303)
        r.delete_cookie("session")
        return r

    return Starlette(routes=[Route("/", home), Route("/login", login, methods=["POST"]),
                             Route("/unlock", unlock, methods=["POST"]),
                             Route("/logout", logout, methods=["POST"])])
