"""Learning science: spaced repetition (Leitner boxes), per-skill mastery, adaptive stretch, time/streak maths, badges.

All pure functions except `evaluate_badges`, which reads the store. Intervals are scaled by env SRS_SCALE
(e.g. 0.001 makes "tomorrow" happen in ~86 seconds, useful for demos and tests)."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from .store import Store

# Leitner boxes: 1 = shaky (seen again soon) ... 5 = solid (seen again in 3 weeks).
INTERVALS = {1: 5 * 60, 2: 24 * 3600, 3: 3 * 24 * 3600, 4: 7 * 24 * 3600, 5: 21 * 24 * 3600}
REVISIT_PROBABILITY = 0.5  # chance to serve a due weak item instead of a fresh question
DAY = 86400


def scale() -> float:
    try:
        return float(os.getenv("SRS_SCALE", "1"))
    except ValueError:
        return 1.0


def next_box(box: int, correct: bool, hinted: bool) -> int:
    if not correct:
        return 1
    if hinted:  # got there with help: keep the item in rotation, don't promote
        return max(box, 1)
    return min(box + 1, 5)


def due_at(box: int, now: float) -> float:
    return now + INTERVALS[box] * scale()


def credit(correct: bool, hinted: bool, revealed: bool = False) -> float:
    """How much a single answer counts towards skill mastery."""
    if revealed:
        return 0.0
    return (0.6 if hinted else 1.0) if correct else 0.0


def mastery_label(mastery: float, attempts: int) -> str:
    if attempts < 4:
        return "getting started"
    if mastery >= 0.85:
        return "mastered"
    if mastery >= 0.65:
        return "strong"
    if mastery >= 0.4:
        return "practising"
    return "needs help"


def stretch_from_results(results: list[int]) -> float:
    """0..1 difficulty stretch within a level from the last answers (newest first).
    Below 50% accuracy: easiest numbers. Above 85%: hardest numbers of the level."""
    if len(results) < 4:
        return 0.3
    acc = sum(results) / len(results)
    return max(0.0, min(1.0, (acc - 0.5) / 0.35))


def active_seconds(timestamps: list[float], gap_cap: float = 120, lone: float = 30) -> float:
    """Estimated screen/voice time from answer timestamps: gaps up to `gap_cap` seconds count fully;
    a longer gap starts a new burst that counts `lone` seconds."""
    ts = sorted(timestamps)
    total = 0.0
    for i, t in enumerate(ts):
        total += min(t - ts[i - 1], gap_cap) if i and t - ts[i - 1] <= gap_cap else lone
    return total


def local_day(ts: float, tz_offset_min: int) -> str:
    return (datetime.fromtimestamp(ts, timezone.utc) + timedelta(minutes=tz_offset_min)).date().isoformat()


def day_start(ts: float, tz_offset_min: int) -> float:
    d = datetime.fromtimestamp(ts, timezone.utc) + timedelta(minutes=tz_offset_min)
    mid = d.replace(hour=0, minute=0, second=0, microsecond=0)
    return (mid - timedelta(minutes=tz_offset_min)).timestamp()


def practice_days(attempt_ts: list[float], tz_offset_min: int) -> set[str]:
    return {local_day(t, tz_offset_min) for t in attempt_ts}


def streak(days: set[str], today: str) -> int:
    """Consecutive practice days ending today (or yesterday: the streak is still alive until today ends)."""
    d = datetime.fromisoformat(today).date()
    if d.isoformat() not in days:
        d -= timedelta(days=1)
    n = 0
    while d.isoformat() in days:
        n += 1
        d -= timedelta(days=1)
    return n


# -- badges ---------------------------------------------------------------------------------------------
BADGES = {
    "first_answer": ("First steps", "Answered a first question."),
    "correct_10": ("Ten right", "10 correct answers."),
    "correct_50": ("Fifty right", "50 correct answers."),
    "correct_100": ("Centurion", "100 correct answers."),
    "streak_3": ("Three-day streak", "Practised 3 days in a row."),
    "streak_7": ("Week warrior", "Practised 7 days in a row."),
    "streak_14": ("Fortnight fighter", "Practised 14 days in a row."),
    "level_up": ("Level up", "Moved up a level."),
    "explorer": ("Explorer", "Tried 3 different topics."),
    "perfect_session": ("Perfect session", "5 or more questions in a session, all right."),
    "comeback": ("Comeback kid", "Got a tricky item right after getting it wrong."),
    "skill_master": ("Skill master", "Mastered a skill."),
}


def evaluate_badges(store: Store, family: str, child: str, now: float, tz: int, *, levelled_up: bool = False,
                    comeback: bool = False, session: dict | None = None) -> list[str]:
    """Award any newly earned badges; return their ids."""
    earned = []

    def give(b):
        if store.award(family, child, b, now):
            earned.append(b)

    tot = store.attempt_totals(family, child)
    if tot["attempts"] >= 1:
        give("first_answer")
    for n in (10, 50, 100):
        if tot["correct"] >= n:
            give(f"correct_{n}")
    ts = [a["ts"] for a in store.attempts_since(family, child, now - 30 * DAY)]
    s = streak(practice_days(ts, tz), local_day(now, tz))
    for n in (3, 7, 14):
        if s >= n:
            give(f"streak_{n}")
    if levelled_up:
        give("level_up")
    if comeback:
        give("comeback")
    if len([p for p in store.progress(family=family, child=child) if p["attempts"]]) >= 3:
        give("explorer")
    if session and session["asked"] >= 5 and session["asked"] == session["correct"]:
        give("perfect_session")
    if any(k["mastery"] >= 0.85 and k["attempts"] >= 8 for k in store.skills(family, child)):
        give("skill_master")
    return earned
