"""Weekly progress report for parents: plain text + email-ready HTML (table layout, inline styles only, no
scripts, no SVG, no remote images: works in strict mail clients). Sending is STUBBED behind config.

  REPORT_MODE=stub (default)  -> logs a JSON line "report_stub_sent" (no PII), returns sent=False
  REPORT_MODE=smtp            -> smtplib using REPORT_SMTP_HOST/PORT/USER/PASSWORD, REPORT_FROM (UNVERIFIED: never
                                 run against a real mail server, TODO-verify)

CLI: python -m learning_coach.reports --family home [--child ada] [--send]
"""
from __future__ import annotations

import argparse
import html
import os
import smtplib
import time
from email.message import EmailMessage

from . import bedrock, curriculum, learning
from .ops import get_secret, log_event
from .service import Coach

E = html.escape


def _delta(now_v: int, prev_v: int, unit: str = "") -> str:
    if not prev_v:
        return "first full week" if now_v else ""
    d = now_v - prev_v
    return f"{'+' if d >= 0 else ''}{d}{unit} vs last week"


def child_report(coach: Coach, family: str, child: str, now: float | None = None) -> dict:
    now = now or time.time()
    st = coach.child_stats(family, child, now)
    w, pw = st["week"], st["prev_week"]
    week_start = now - 6 * 86400
    new_badges = [b for b in st["badges"] if b["earned"] >= week_start]
    strong = [s for s in st["skills"] if s["label"] in ("strong", "mastered")][:3]
    focus = sorted([s for s in st["skills"] if s["label"] in ("needs help", "practising")], key=lambda s: s["mastery"])[:3]
    topics = [{"topic": t, "title": curriculum.TOPICS[t]["title"], "level": lv, "max": curriculum.max_level(t)}
              for t, lv in st["levels"].items() if st["topics"].get(t) == "open"]
    tips = []
    if focus:
        tips.append(f"Ask {child.title()} to teach you how to do a {focus[0]['skill']} question: explaining it out loud "
                    "is one of the best ways to learn.")
    if w["active_days"] < 3:
        tips.append("Try a short 5-minute practice after dinner: little and often beats one long session.")
    if st["goal"]["weekly"] and st["goal"]["pct"] >= 100:
        tips.append("Goal reached! Celebrate it, then consider unlocking a new topic from the dashboard.")
    elif st["weak_items"]:
        tips.append("A few tricky questions will come back automatically in later sessions (spaced repetition).")
    return {"child": child, "name": child.title(), "week": w, "prev_week": pw, "streak": st["streak"],
            "goal": st["goal"], "badges_new": new_badges, "strong": strong, "focus": focus, "topics": topics,
            "weak_items": st["weak_items"], "tips": tips[:3], "series": st["series"]}


def family_report(coach: Coach, family: str, child: str | None = None, now: float | None = None) -> dict:
    now = now or time.time()
    fam = coach.family_info(family)
    kids = [c for c in coach.children(family) if not child or c == child.lower()]
    reports = [child_report(coach, family, c, now) for c in kids]
    ai = bedrock.get_ai()
    for r in reports:  # aggregate numbers only go to the model; falls back to a rule-based paragraph
        r["narrative"], r["narrative_source"] = bedrock.report_narrative(ai, family, r)
    subject = f"{fam['name']} weekly learning report"
    return {"family": fam, "children": reports, "generated": now, "subject": subject,
            "text": render_text(fam, reports, now), "html": render_html(fam, reports, now)}


def _date(now: float) -> str:
    return time.strftime("%d %b %Y", time.gmtime(now))


def render_text(fam: dict, reports: list[dict], now: float) -> str:
    out = [f"{fam['name']}: weekly learning report ({_date(now)})", "=" * 44, ""]
    for r in reports:
        w = r["week"]
        out += [f"{r['name']}", "-" * len(r["name"])]
        if r.get("narrative"):
            out += [r["narrative"] + (" (Written with Amazon Bedrock.)" if r.get("narrative_source") == "bedrock" else ""), ""]
        out += [f"Questions this week: {w['answered']}" + (f" ({d})" if (d := _delta(w['answered'], r['prev_week']['answered'])) else ""),
                f"Accuracy: {w['accuracy_pct']}%   Active days: {w['active_days']}/7   Time: about {w['minutes']} min",
                f"Practice streak: {r['streak']} day(s)"]
        if r["goal"]["weekly"]:
            out.append(f"Weekly goal: {r['goal']['done']}/{r['goal']['weekly']} questions ({r['goal']['pct']}%)")
        if r["badges_new"]:
            out.append("New badges: " + ", ".join(b["title"] for b in r["badges_new"]))
        if r["strong"]:
            out.append("Going well: " + ", ".join(f"{s['skill']} ({s['label']})" for s in r["strong"]))
        if r["focus"]:
            out.append("Needs a little more practice: " + ", ".join(s["skill"] for s in r["focus"]))
        if r["topics"]:
            out.append("Levels: " + ", ".join(f"{t['title']} {t['level']}/{t['max']}" for t in r["topics"]))
        for t in r["tips"]:
            out.append(f"Tip: {t}")
        out.append("")
    out += ["Manage topics, time limits and privacy on your Learning Coach dashboard.",
            "You can export or delete your family's data there at any time."]
    return "\n".join(out)


def _bar(pct: int, color: str = "#2f7d5b") -> str:
    pct = max(0, min(100, pct))
    return (f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#e8ece9;'
            f'border-radius:4px"><tr><td width="{pct}%" style="background:{color};height:10px;border-radius:4px;'
            f'font-size:1px;line-height:10px">&nbsp;</td><td style="font-size:1px">&nbsp;</td></tr></table>')


def render_html(fam: dict, reports: list[dict], now: float) -> str:
    cards = ""
    for r in reports:
        w = r["week"]
        maxn = max([d["answered"] for d in r["series"][-7:]] + [1])
        cols = "".join(
            f'<td valign="bottom" align="center" style="padding:0 2px"><div style="background:#2f7d5b;height:{max(2, round(48 * d["answered"] / maxn))}px;'
            f'border-radius:3px 3px 0 0"></div><div style="font-size:10px;color:#667">{E(d["day"][8:])}</div></td>'
            for d in r["series"][-7:])
        skills = "".join(
            f'<tr><td style="padding:3px 0;font-size:13px;width:38%">{E(s["skill"])}</td><td style="width:44%">{_bar(round(s["mastery"] * 100))}</td>'
            f'<td style="padding-left:8px;font-size:12px;color:#556">{E(s["label"])}</td></tr>'
            for s in (r["strong"] + r["focus"]))
        badges = "".join(f'<span style="display:inline-block;background:#fff4d6;border:1px solid #f0d58a;border-radius:12px;'
                         f'padding:2px 10px;margin:2px;font-size:12px">{E(b["title"])}</span>' for b in r["badges_new"])
        tips = "".join(f'<li style="margin:4px 0">{E(t)}</li>' for t in r["tips"])
        goal = (f'<p style="margin:10px 0 4px;font-size:13px">Weekly goal: {r["goal"]["done"]} of {r["goal"]["weekly"]} questions</p>{_bar(r["goal"]["pct"], "#d98324")}'
                if r["goal"]["weekly"] else "")
        story = (f'<p style="margin:0 0 10px;font-size:14px;color:#334;line-height:1.5">{E(r["narrative"])}'
                 + ('<br><span style="font-size:11px;color:#889">Written with Amazon Bedrock</span>' if r.get("narrative_source") == "bedrock" else "")
                 + "</p>") if r.get("narrative") else ""
        cards += f"""
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#fff;border:1px solid #dfe5e1;border-radius:12px;margin:14px 0"><tr><td style="padding:18px">
<h2 style="margin:0 0 8px;font:600 20px Georgia,serif;color:#1d3a2f">{E(r['name'])}</h2>
{story}
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>
 <td align="center" style="padding:6px"><div style="font-size:26px;font-weight:700;color:#1d3a2f">{w['answered']}</div><div style="font-size:12px;color:#667">questions<br>{E(_delta(w['answered'], r['prev_week']['answered']))}</div></td>
 <td align="center" style="padding:6px"><div style="font-size:26px;font-weight:700;color:#1d3a2f">{w['accuracy_pct']}%</div><div style="font-size:12px;color:#667">correct</div></td>
 <td align="center" style="padding:6px"><div style="font-size:26px;font-weight:700;color:#1d3a2f">{r['streak']}</div><div style="font-size:12px;color:#667">day streak</div></td>
 <td align="center" style="padding:6px"><div style="font-size:26px;font-weight:700;color:#1d3a2f">{w['minutes']}</div><div style="font-size:12px;color:#667">minutes</div></td></tr></table>
<table role="presentation" cellspacing="0" cellpadding="0" style="margin:10px 0 0;height:60px"><tr>{cols}</tr></table>
{goal}
{f'<p style="margin:12px 0 4px;font-size:13px;color:#556">New badges</p>{badges}' if badges else ''}
{f'<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin-top:12px">{skills}</table>' if skills else ''}
{f'<ul style="padding-left:18px;font-size:13px;color:#334;margin:12px 0 0">{tips}</ul>' if tips else ''}
</td></tr></table>"""
    return f"""<!doctype html><html><body style="margin:0;background:#f3f6f4;font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;color:#223">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center" style="padding:20px 10px">
<table role="presentation" width="600" cellspacing="0" cellpadding="0" style="max-width:600px;width:100%"><tr><td>
<p style="margin:0;font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#667">Learning Coach weekly report</p>
<h1 style="margin:4px 0 0;font:600 26px Georgia,serif;color:#1d3a2f">{E(fam['name'])}</h1>
<p style="margin:2px 0 8px;font-size:13px;color:#667">Week ending {E(_date(now))}</p>
{cards}
<p style="font-size:12px;color:#667">Manage topics, time limits and privacy on your dashboard. You can export or delete your family's data there at any time.</p>
</td></tr></table></td></tr></table></body></html>"""


def send_report(report: dict, to: str | None = None) -> dict:
    """Send (or stub-send) a family report. Mode from REPORT_MODE; never raises."""
    to = to or report["family"]["email"]
    mode = os.getenv("REPORT_MODE", "stub")
    if not to:
        return {"sent": False, "mode": mode, "reason": "no email address on file"}
    if mode != "smtp":
        log_event("report_stub_sent", family=report["family"]["id"], bytes=len(report["html"]))
        return {"sent": False, "mode": "stub", "reason": "REPORT_MODE is not smtp; nothing was sent"}
    try:  # UNVERIFIED path (TODO-verify against a real SMTP server / SES)
        msg = EmailMessage()
        msg["Subject"], msg["To"] = report["subject"], to
        msg["From"] = os.getenv("REPORT_FROM", "reports@localhost")
        msg.set_content(report["text"])
        msg.add_alternative(report["html"], subtype="html")
        with smtplib.SMTP(os.environ["REPORT_SMTP_HOST"], int(os.getenv("REPORT_SMTP_PORT", "587")), timeout=15) as s:
            s.starttls()
            user = os.getenv("REPORT_SMTP_USER")
            if user:
                s.login(user, get_secret("REPORT_SMTP_PASSWORD", "") or "")
            s.send_message(msg)
        return {"sent": True, "mode": "smtp"}
    except Exception as e:
        log_event("report_send_failed", error=type(e).__name__)
        return {"sent": False, "mode": "smtp", "reason": type(e).__name__}


def main(argv=None) -> int:
    from .store import make_store
    p = argparse.ArgumentParser(description="Print or send a weekly report")
    p.add_argument("--family", default="home", help="family slug")
    p.add_argument("--child")
    p.add_argument("--send", action="store_true")
    p.add_argument("--html", action="store_true", help="print HTML instead of text")
    a = p.parse_args(argv)
    store = make_store(get_secret("DATABASE_URL") or os.getenv("PROGRESS_DB", "progress.db"))
    coach = Coach(store, os.getenv("PARENT_PIN"))
    fam = store.family_by("slug", a.family)
    if not fam:
        print("unknown family slug"); return 1
    rep = family_report(coach, fam["id"], a.child)
    print(send_report(rep) if a.send else (rep["html"] if a.html else rep["text"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
