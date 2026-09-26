"""Parent dashboard: family sign-in/sign-up, per-child progress with inline-SVG charts, topic lock/unlock,
daily time limits and weekly goals, weekly report, data export and deletion.

Session = HMAC-signed cookie `family.expiry.sig` (15 min, HttpOnly, SameSite=Strict). Every state change is a
POST, so SameSite=Strict is the CSRF defence. Sensitive actions (topic access, delete, token rotation) ask for
the PIN again. Serve behind HTTPS in production (cookie is Secure when COOKIE_SECURE=1).
"""
from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import secrets
import time
from urllib.parse import quote

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

from . import curriculum, learning, reports
from .ops import get_secret
from .service import Coach, InvalidInput
from .store import DEFAULT_FAMILY

SESSION_SECONDS = 15 * 60
E = html.escape

CSS = """
:root{--bg:#f6f3ec;--card:#fffdf8;--ink:#1f2a24;--mute:#66706a;--line:#e3ddd0;--accent:#2f7d5b;--accent-ink:#fff;
--warn:#d98324;--bad:#b3261e;--good:#1d6b45;--chip:#fff1cf;--bar:#e6e0d2}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#141a17;--card:#1c2420;--ink:#e9efe9;--mute:#9aa8a0;
--line:#2c3832;--accent:#5fbf92;--accent-ink:#0d1712;--warn:#f0a24c;--bad:#ff8a80;--good:#7ad7a6;--chip:#3a3220;--bar:#2b3630}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
main{max-width:64rem;margin:0 auto;padding:1rem 16px 4rem}
h1,h2,h3{font-family:Georgia,'Iowan Old Style',serif;font-weight:600;line-height:1.2}h1{font-size:1.9rem;margin:.6rem 0}
h2{font-size:1.35rem;margin:0}h3{font-size:1rem;margin:1.2rem 0 .4rem;font-family:system-ui;text-transform:uppercase;letter-spacing:.06em;color:var(--mute)}
header.top{display:flex;justify-content:space-between;align-items:center;gap:1rem;flex-wrap:wrap}
.brand{font:600 1.1rem Georgia,serif}.brand span{color:var(--accent)}
nav a{color:var(--ink);margin-right:1rem;text-decoration:none;border-bottom:2px solid transparent}nav a:hover{border-color:var(--accent)}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:1.2rem;margin:1rem 0}
.row{display:flex;gap:1rem;flex-wrap:wrap;align-items:center}.grow{flex:1}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(7rem,1fr));gap:.6rem;margin:.8rem 0}
.stat{background:var(--bg);border-radius:12px;padding:.6rem .8rem}.stat b{display:block;font:600 1.6rem Georgia,serif}
.stat small{color:var(--mute)}
.chip{display:inline-block;background:var(--chip);border-radius:999px;padding:.1rem .7rem;margin:.15rem .2rem .15rem 0;font-size:.85rem}
.two{display:grid;grid-template-columns:repeat(auto-fit,minmax(18rem,1fr));gap:1.2rem}
table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid var(--line);padding:.45rem .3rem;text-align:left;font-size:.95rem}
.err{color:var(--bad)}.ok{color:var(--good)}.mute{color:var(--mute)}
input,select,button{font:inherit;padding:.5rem .7rem;border-radius:10px;border:1px solid var(--line);background:var(--card);color:var(--ink)}
button{cursor:pointer;background:var(--accent);color:var(--accent-ink);border-color:var(--accent);font-weight:600}
button.ghost{background:transparent;color:var(--ink);border-color:var(--line)}button.danger{background:var(--bad);border-color:var(--bad);color:#fff}
button:focus-visible,input:focus-visible,select:focus-visible,a:focus-visible{outline:3px solid var(--warn);outline-offset:2px}
label{display:inline-flex;flex-direction:column;gap:.2rem;font-size:.85rem;color:var(--mute)}
.pill{padding:.1rem .6rem;border-radius:999px;font-size:.8rem;border:1px solid var(--line)}.pill.open{color:var(--good);border-color:var(--good)}
.pill.locked{color:var(--mute)}
svg text{fill:var(--mute);font:11px system-ui}.bar-c{fill:var(--accent)}.bar-w{fill:var(--warn)}.track{fill:var(--bar)}
details summary{cursor:pointer;color:var(--mute)}
.tokens code{display:block;padding:.5rem;background:var(--bg);border-radius:8px;word-break:break-all;margin:.3rem 0 .8rem}
@media (max-width:520px){h1{font-size:1.5rem}}
"""


def page(title: str, body: str, status: int = 200) -> HTMLResponse:
    doc = (f'<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">'
           f"<title>{E(title)}</title><style>{CSS}</style><main>{body}</main></html>")
    return HTMLResponse(doc, status_code=status, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY",
                        "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff",
                        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"})


# -- charts (inline SVG: no JS, no external assets) -------------------------------------------------------
def activity_chart(series: list[dict]) -> str:
    w, h, pad = 560, 150, 22
    top = max([d["answered"] for d in series] + [4])
    bw = (w - 2 * pad) / len(series)
    bars = ""
    for i, d in enumerate(series):
        x = pad + i * bw + 3
        hc, hw = (h - 2 * pad) * d["correct"] / top, (h - 2 * pad) * (d["answered"] - d["correct"]) / top
        base = h - pad
        bars += (f'<rect class="bar-c" x="{x:.1f}" y="{base - hc:.1f}" width="{bw - 6:.1f}" height="{hc:.1f}" rx="3"><title>{E(d["day"])}: {d["correct"]} right</title></rect>'
                 f'<rect class="bar-w" x="{x:.1f}" y="{base - hc - hw:.1f}" width="{bw - 6:.1f}" height="{hw:.1f}" rx="3"><title>{E(d["day"])}: {d["answered"] - d["correct"]} to review</title></rect>')
        if i % 2 == 0 or len(series) < 8:
            bars += f'<text x="{x + (bw - 6) / 2:.1f}" y="{h - 6}" text-anchor="middle">{E(d["day"][8:])}</text>'
    total = sum(d["answered"] for d in series)
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="Questions answered per day over {len(series)} days: {total} in total">'
            f'<line x1="{pad}" x2="{w - pad}" y1="{h - pad}" y2="{h - pad}" stroke="currentColor" opacity=".2"/>'
            f'<text x="{pad}" y="12">{top}</text>{bars}</svg>'
            '<p class="mute" style="margin:.2rem 0 0;font-size:.8rem"><span class="chip" style="background:none;padding:0">'
            '<svg width=10 height=10><rect class="bar-c" width=10 height=10 rx=2 /></svg> right</span> '
            '<svg width=10 height=10><rect class="bar-w" width=10 height=10 rx=2 /></svg> to review</p>')


def skill_chart(skills: list[dict]) -> str:
    if not skills:
        return '<p class="mute">Skills appear after a few answers.</p>'
    row_h, w = 30, 420
    rows = ""
    for i, s in enumerate(skills):
        y = i * row_h
        rows += (f'<text x="0" y="{y + 15}">{E(s["skill"])}</text><rect class="track" x="110" y="{y + 5}" width="230" height="12" rx="6"/>'
                 f'<rect class="bar-c" x="110" y="{y + 5}" width="{230 * s["mastery"]:.0f}" height="12" rx="6"><title>{round(100 * s["mastery"])}%</title></rect>'
                 f'<text x="348" y="{y + 15}">{E(s["label"])}</text>')
    return (f'<svg viewBox="0 0 {w} {len(skills) * row_h}" width="100%" role="img" aria-label="Skill mastery: '
            + E(", ".join(f"{s['skill']} {s['label']}" for s in skills)) + f'">{rows}</svg>')


def goal_ring(goal: dict) -> str:
    if not goal["weekly"]:
        return ""
    r, c = 26, 2 * 3.14159 * 26
    return (f'<svg width="64" height="64" viewBox="0 0 64 64" role="img" aria-label="Weekly goal {goal["pct"]} percent">'
            f'<circle cx="32" cy="32" r="{r}" fill="none" stroke="var(--bar)" stroke-width="7"/>'
            f'<circle cx="32" cy="32" r="{r}" fill="none" stroke="var(--warn)" stroke-width="7" stroke-linecap="round" '
            f'stroke-dasharray="{c * goal["pct"] / 100:.1f} {c:.1f}" transform="rotate(-90 32 32)"/>'
            f'<text x="32" y="36" text-anchor="middle" style="font:600 13px system-ui;fill:var(--ink)">{goal["pct"]}%</text></svg>')


def make_app(coach: Coach, secret: str | None = None) -> Starlette:
    key = (secret or get_secret("SESSION_SECRET") or secrets.token_hex(32)).encode()
    secure = os.getenv("COOKIE_SECURE") == "1"
    allow_signup = os.getenv("ALLOW_SIGNUP", "1") == "1"

    def sign(fid: str, exp: int) -> str:
        return f"{fid}.{exp}.{hmac.new(key, f'{fid}.{exp}'.encode(), hashlib.sha256).hexdigest()}"

    def family_of(request: Request) -> str | None:
        v = request.cookies.get("session", "")
        parts = v.split(".")
        if len(parts) == 3 and parts[1].isdigit() and int(parts[1]) > time.time() and hmac.compare_digest(v, sign(parts[0], int(parts[1]))):
            fid = parts[0]
            return fid if coach.store.family(fid) else None
        return None

    def start(fid: str, to: str = "./") -> RedirectResponse:
        r = RedirectResponse(to, status_code=303)
        r.set_cookie("session", sign(fid, int(time.time()) + SESSION_SECONDS), max_age=SESSION_SECONDS,
                     httponly=True, samesite="strict", secure=secure)
        return r

    def back(msg: str, ok: bool = False) -> RedirectResponse:
        return RedirectResponse(f"./?msg={quote(msg)}" + ("" if ok else "&bad=1"), status_code=303)

    def signed_out(msg: str = "", status: int = 200) -> HTMLResponse:
        signup = ""
        if allow_signup:
            signup = ("<div class=card><h2>New family</h2><p class=mute>Create a private space for your children. "
                      "You get a family code, a PIN, a TV token and an Alexa link token.</p>"
                      "<form method=post action=signup class=row>"
                      "<label>Family name<input name=name required maxlength=40 autocomplete=off></label>"
                      "<label>Email (for weekly reports, optional)<input name=email type=email maxlength=120></label>"
                      "<label>First child's name<input name=child maxlength=32></label>"
                      "<label>Parent PIN (4-8 digits)<input name=pin type=password inputmode=numeric pattern='[0-9]{4,8}' required></label>"
                      "<button>Create family</button></form>"
                      "<p class=mute style='font-size:.85rem'>We store first names you type, practice results and your email. "
                      "You can export or delete everything from the dashboard. Not legal advice: see docs/DEPLOY.md privacy notes.</p></div>")
        return page("Learning Coach - Parent",
                    "<header class=top><div class=brand>Learning <span>Coach</span></div></header><h1>Parent sign-in</h1>"
                    f"<p class=err>{E(msg)}</p><div class=card><form method=post action=login class=row>"
                    "<label>Family code<input name=family autocomplete=username placeholder='blank if you only have one'></label>"
                    "<label>Parent PIN<input name=pin type=password inputmode=numeric autocomplete=current-password></label>"
                    f"<button>Sign in</button></form></div>{signup}", status)

    # ---- dashboard ---------------------------------------------------------------------------
    def child_card(c: dict, fam: dict) -> str:
        name = c["child"]
        n = E(name.title())
        w, pw = c["week"], c["prev_week"]
        chips = "".join(f'<span class=chip title="{E(b["desc"])}">{E(b["title"])}</span>' for b in c["badges"]) or \
            '<span class=mute>No badges yet. The first one arrives after the first answer.</span>'
        rows = ""
        for t, spec in curriculum.TOPICS.items():
            st = c["topics"][t]
            lv = c["levels"].get(t, 1)
            act = ("lock", "Lock") if st == "open" else ("unlock", "Unlock")
            rows += (f"<tr><td>{E(spec['title'])}<br><small class=mute>ages {spec['min_age']}+</small></td><td>{lv}/{curriculum.max_level(t)}</td>"
                     f"<td><span class='pill {st}'>{st}</span></td>"
                     f"<td><button class=ghost name=act value='{act[0]}:{E(t)}'>{act[1]}</button></td></tr>")
        weak = "".join(f"<li>{E(x['prompt'])} <span class=mute>({E(x['answer'])})</span></li>" for x in c["weak_items"]) or \
            "<li class=mute>Nothing tricky right now.</li>"
        delta = w["answered"] - pw["answered"]
        lim = c["limit"]
        return f"""<section class=card aria-labelledby="c-{E(name)}">
<div class=row><h2 id="c-{E(name)}" class=grow>{n}</h2>{goal_ring(c['goal'])}
<span class=chip>{c['streak']} day streak</span></div>
<div class=stats><div class=stat><b>{w['answered']}</b><small>questions this week ({'+' if delta >= 0 else ''}{delta})</small></div>
<div class=stat><b>{w['accuracy_pct']}%</b><small>correct</small></div>
<div class=stat><b>{w['active_days']}/7</b><small>active days</small></div>
<div class=stat><b>{lim['today_minutes']}m</b><small>today{f" of {lim['daily_minutes']}m" if lim['daily_minutes'] else ""}</small></div></div>
<div class=two><div><h3>Last 14 days</h3>{activity_chart(c['series'])}</div><div><h3>Skills</h3>{skill_chart(c['skills'])}</div></div>
<h3>Badges</h3><div>{chips}</div>
<div class=two><div><h3>Tricky ones coming back</h3><ul>{weak}</ul></div>
<div><h3>Goal and time limit</h3><form method=post action=child/settings class=row>
<input type=hidden name=child value="{E(name)}"><label>Questions per week<input name=weekly_goal type=number min=0 max=1000 value={c['goal']['weekly']}></label>
<label>Daily minutes (0 = none)<input name=daily_minutes type=number min=0 max=240 value={lim['daily_minutes']}></label><button class=ghost>Save</button></form></div></div>
<h3>Topics</h3><form method=post action=unlock><input type=hidden name=child value="{E(name)}">
<table><tr><th>Topic<th>Level<th>Status<th></tr>{rows}</table>
<p><label>Parent PIN to change access<input name=pin type=password inputmode=numeric required></label></p></form>
<p><a href="report?child={quote(name)}">Weekly report</a> · <a href="report?child={quote(name)}&format=txt">plain text</a></p></section>"""

    async def home(request: Request):
        fid = family_of(request)
        if not fid:
            return signed_out()
        msg, bad = request.query_params.get("msg", ""), request.query_params.get("bad")
        d = coach.dashboard(fid)
        fam = d["family"]
        cards = "".join(child_card(c, fam) for c in d["children"]) or \
            "<div class=card><p>No children yet. Add one below, or just say a name to Alexa the first time.</p></div>"
        tz = fam["tz_offset_min"]
        return page(f"{fam['name']} - Learning Coach", f"""
<header class=top><div class=brand>Learning <span>Coach</span></div>
<nav><a href="report">Weekly report</a><a href="export">Export data</a>
<form method=post action=logout style="display:inline"><button class=ghost>Sign out</button></form></nav></header>
<h1>{E(fam['name'])}</h1><p class="{'err' if bad else 'ok'}" role=status>{E(msg)}</p>
<p class=mute>Family code: <b>{E(fam['slug'])}</b></p>
{cards}
<div class=card><h2>Add a child</h2><form method=post action=child/add class=row>
<label>First name<input name=name required maxlength=32></label><button>Add</button></form></div>
<div class=card><h2>Family settings</h2><form method=post action=settings class=row>
<label>Family name<input name=name value="{E(fam['name'])}" maxlength=40></label>
<label>Report email<input name=email type=email value="{E(fam['email'])}" maxlength=120></label>
<label>Time zone (minutes from UTC)<input name=tz type=number min=-720 max=840 value={tz}></label><button class=ghost>Save</button></form>
<form method=post action=report/send style="margin-top:1rem"><button class=ghost>Send this week's report to my email</button>
<span class=mute> (sending is stubbed unless REPORT_MODE=smtp)</span></form></div>
<div class=card><h2>Connections</h2><p class=mute>Alexa+ and Fire TV use tokens shown only once. Rotating invalidates the old ones.</p>
<form method=post action=tokens class=row><label>Parent PIN<input name=pin type=password inputmode=numeric required></label>
<button class=ghost>Rotate tokens</button></form></div>
<div class=card><h2>Privacy</h2><p>Download everything we hold about your family, or delete it permanently.</p>
<p><a href="export">Download JSON export</a></p>
<details><summary>Delete this family</summary><form method=post action=family/delete class=row>
<label>Type the family name<input name=confirm required></label><label>Parent PIN<input name=pin type=password inputmode=numeric required></label>
<button class=danger>Delete everything</button></form></details></div>""")

    async def login(request: Request):
        form = await request.form()
        fid, err = coach.authenticate(str(form.get("family", "")), str(form.get("pin", "")))
        if err:
            return signed_out(err, 403)
        return start(fid)

    async def signup(request: Request):
        if not allow_signup:
            return signed_out("Sign-up is disabled on this server.", 403)
        f = await request.form()
        try:
            res = coach.create_family(str(f.get("name", "")), str(f.get("pin", "")), str(f.get("email", "")), str(f.get("child", "")))
        except InvalidInput as e:
            return signed_out(str(e), 400)
        r = page("Family created", f"""<h1>Welcome, {E(res['name'])}!</h1><div class="card tokens">
<p class=err><b>Save these now. They are shown once.</b></p>
<p>Family code (for sign-in)</p><code>{E(res['slug'])}</code>
<p>Alexa link token (bearer token for the MCP endpoint)</p><code>{E(res['link_token'])}</code>
<p>Fire TV token</p><code>{E(res['tv_token'])}</code>
<form method=post action=continue><button>I saved them, go to my dashboard</button></form></div>""")
        r.set_cookie("session", sign(res["family_id"], int(time.time()) + SESSION_SECONDS), max_age=SESSION_SECONDS,
                     httponly=True, samesite="strict", secure=secure)
        return r

    async def cont(request: Request):
        return RedirectResponse("./", status_code=303)

    async def unlock(request: Request):
        fid = family_of(request)
        if not fid:
            return signed_out("Please sign in.", 401)
        f = await request.form()
        child, pin = str(f.get("child", "")), str(f.get("pin", ""))
        act, topic = str(f.get("act", "")), str(f.get("topic", ""))
        verb = "unlock"
        if ":" in act:
            verb, topic = act.split(":", 1)
        try:
            fn = coach.lock_topic if verb == "lock" else coach.unlock_topic
            res = fn(child, topic, pin, family=fid)
        except InvalidInput as e:
            res = {"error": str(e)}
        if res.get("error"):
            return back(res["error"])
        return back(f"{'Unlocked' if verb != 'lock' else 'Locked'} {res.get('unlocked') or res.get('locked')}.", True)

    async def child_add(request: Request):
        fid = family_of(request)
        if not fid:
            return signed_out("Please sign in.", 401)
        try:
            n = coach.add_child(str((await request.form()).get("name", "")), family=fid)
            return back(f"Added {n.title()}.", True)
        except InvalidInput as e:
            return back(str(e))

    async def child_settings(request: Request):
        fid = family_of(request)
        if not fid:
            return signed_out("Please sign in.", 401)
        f = await request.form()
        try:
            coach.set_child_limits(str(f.get("child", "")), fid, int(f.get("daily_minutes") or 0), int(f.get("weekly_goal") or 0))
            return back("Saved.", True)
        except (InvalidInput, ValueError) as e:
            return back(str(e) if isinstance(e, InvalidInput) else "Please enter whole numbers.")

    async def settings(request: Request):
        fid = family_of(request)
        if not fid:
            return signed_out("Please sign in.", 401)
        f = await request.form()
        try:
            coach.update_family_settings(fid, str(f.get("name", "")), str(f.get("email", "")), int(f.get("tz") or 0))
            return back("Saved.", True)
        except (InvalidInput, ValueError) as e:
            return back(str(e) if isinstance(e, InvalidInput) else "Please enter whole numbers.")

    async def tokens(request: Request):
        fid = family_of(request)
        if not fid:
            return signed_out("Please sign in.", 401)
        err = coach.check_pin(str((await request.form()).get("pin", "")), fid)
        if err:
            return back(err)
        t = coach.rotate_tokens(fid)
        return page("New tokens", f"""<h1>New tokens</h1><div class="card tokens"><p class=err><b>Shown once. The old tokens no longer work.</b></p>
<p>Alexa link token</p><code>{E(t['link_token'])}</code><p>Fire TV token</p><code>{E(t['tv_token'])}</code>
<p><a href="./">Back to dashboard</a></p></div>""")

    async def report(request: Request):
        fid = family_of(request)
        if not fid:
            return signed_out("Please sign in.", 401)
        rep = reports.family_report(coach, fid, request.query_params.get("child") or None)
        if request.query_params.get("format") == "txt":
            return PlainTextResponse(rep["text"], headers={"Cache-Control": "no-store"})
        return HTMLResponse(rep["html"], headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY",
                            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'"})

    async def report_send(request: Request):
        fid = family_of(request)
        if not fid:
            return signed_out("Please sign in.", 401)
        res = reports.send_report(reports.family_report(coach, fid))
        return back("Report sent." if res["sent"] else f"Report not sent: {res['reason']}.", res["sent"])

    async def export(request: Request):
        fid = family_of(request)
        if not fid:
            return signed_out("Please sign in.", 401)
        body = json.dumps(coach.export_family(fid), indent=1, default=str)
        return Response(body, media_type="application/json", headers={
            "Content-Disposition": 'attachment; filename="learning-coach-export.json"', "Cache-Control": "no-store"})

    async def family_delete(request: Request):
        fid = family_of(request)
        if not fid:
            return signed_out("Please sign in.", 401)
        f = await request.form()
        fam = coach.family_info(fid)
        if str(f.get("confirm", "")).strip() != fam["name"]:
            return back("The family name did not match; nothing was deleted.")
        res = coach.delete_family(fid, str(f.get("pin", "")))
        if res.get("error"):
            return back(res["error"])
        r = signed_out("Your family and all its data were deleted.")
        r.delete_cookie("session")
        return r

    async def logout(request: Request):
        r = RedirectResponse("./", status_code=303)
        r.delete_cookie("session")
        return r

    return Starlette(routes=[
        Route("/", home), Route("/login", login, methods=["POST"]), Route("/signup", signup, methods=["POST"]),
        Route("/continue", cont, methods=["POST"]), Route("/unlock", unlock, methods=["POST"]),
        Route("/child/add", child_add, methods=["POST"]), Route("/child/settings", child_settings, methods=["POST"]),
        Route("/settings", settings, methods=["POST"]), Route("/tokens", tokens, methods=["POST"]),
        Route("/report", report), Route("/report/send", report_send, methods=["POST"]),
        Route("/export", export), Route("/family/delete", family_delete, methods=["POST"]),
        Route("/logout", logout, methods=["POST"])])
