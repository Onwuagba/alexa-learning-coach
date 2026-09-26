"""Business logic shared by the MCP tools, the parent web page and the TV REST API. Returns plain dicts.

Multi-tenant: every method takes `family=` (default: the legacy single-family `default`). Voice responses
carry `say` (plain, TTS-safe text) and `ssml` (`<speak>` document) next to the structured fields."""
from __future__ import annotations

import base64
import difflib
import hashlib
import hmac
import os
import random
import re
import secrets
import sys
import time
import uuid

from . import curriculum, learning, voice
from .ops import METRICS
from .store import DEFAULT_FAMILY, PROMOTE_AFTER, Store

DEFAULT_TOPICS = ["abacus"]  # always available; others need a parent unlock
MAX_FAILS = 5
LOCK_SECONDS = 5 * 60
NAME_RE = re.compile(r"^[a-z][a-z '\-]{0,31}$")
PIN_RE = re.compile(r"^\d{4,8}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
MAX_ANSWER_LEN = 100
MAX_CHILDREN = 8
SESSION_IDLE_SECONDS = 30 * 60
TOPIC_ALIASES = {"capitals": "geography", "countries": "geography", "geo": "geography", "maps": "geography",
                 "spell": "spelling", "spelling bee": "spelling", "times": "times_tables", "multiplication": "times_tables",
                 "timetables": "times_tables", "fraction": "fractions", "story": "reading", "stories": "reading",
                 "read": "reading", "comprehension": "reading", "balance": "algebra", "scales": "algebra",
                 "counting": "abacus", "beads": "abacus", "numbers": "abacus", "maths": "abacus", "math": "abacus",
                 "sciences": "science", "curious science": "science"}


class InvalidInput(ValueError):
    pass


def clean_child(child: object) -> str:
    """Normalise a child's name to a lowercase id. Raises InvalidInput."""
    s = " ".join(str(child or "").lower().split())
    if not NAME_RE.match(s):
        raise InvalidInput("Child name must be 1-32 letters (spaces, ' and - allowed).")
    return s


SCRYPT_N = 2**14  # cost; stored with each hash so it can be raised later


def _hash_pin(pin: str, salt: bytes | None = None, n: int | None = None) -> str:
    salt, n = salt or os.urandom(16), n or SCRYPT_N
    d = hashlib.scrypt(pin.encode(), salt=salt, n=n, r=8, p=1)
    return f"{n}${base64.b64encode(salt).decode()}${base64.b64encode(d).decode()}"


def _verify_pin(pin: str, stored: str) -> bool:
    try:
        n, salt_b64, _ = stored.split("$")
        return hmac.compare_digest(_hash_pin(pin, base64.b64decode(salt_b64), int(n)), stored)
    except Exception:
        return False


def hash_token(tok: str) -> str:
    """Tokens are 192-bit random, so a plain SHA-256 is enough (no salt/KDF needed) and allows lookup."""
    return hashlib.sha256(tok.encode()).hexdigest()


class Coach:
    def __init__(self, store: Store, parent_pin: str | None = None, rng: random.Random | None = None):
        self.store = store
        self.rng = rng or random.Random()
        store.ensure_default_family()
        fam = store.family(DEFAULT_FAMILY)
        if parent_pin:
            self.set_pin(parent_pin)
        elif not fam["pin_hash"]:
            pin = f"{secrets.randbelow(10**6):06d}"
            self.set_pin(pin)
            print(f"[learning-coach] PARENT_PIN not set; generated one-time PIN: {pin}", file=sys.stderr)

    # -- families & PIN ---------------------------------------------------------
    def set_pin(self, pin: str, family: str = DEFAULT_FAMILY) -> None:
        if not PIN_RE.match(str(pin)):
            raise InvalidInput("PIN must be 4-8 digits.")
        cur = self.store.family(family)["pin_hash"]
        if cur and _verify_pin(pin, cur):
            return  # unchanged; keep lockout state
        self.store.update_family(family, pin_hash=_hash_pin(pin))

    def create_family(self, name: str, pin: str, email: str = "", first_child: str = "") -> dict:
        """Sign up a new family. Tokens are returned ONCE (only their hashes are stored)."""
        name = " ".join(str(name or "").split())
        if not 2 <= len(name) <= 40 or not re.match(r"^[\w .'\-]+$", name):
            raise InvalidInput("Family name must be 2-40 letters or numbers.")
        if not PIN_RE.match(str(pin or "")):
            raise InvalidInput("PIN must be 4-8 digits.")
        email = (email or "").strip()
        if email and not EMAIL_RE.match(email):
            raise InvalidInput("That email address doesn't look right.")
        base = re.sub(r"[^a-z0-9]+", "-", voice.strip_accents(name).lower()).strip("-") or "family"
        slug = base
        while self.store.family_by("slug", slug):
            slug = f"{base}-{secrets.token_hex(2)}"
        fid = uuid.uuid4().hex
        tv, link = "lc_tv_" + secrets.token_urlsafe(24), "lc_mcp_" + secrets.token_urlsafe(24)
        self.store.create_family(fid, slug, name, email, _hash_pin(pin), hash_token(tv), hash_token(link))
        if first_child.strip():
            self.add_child(clean_child(first_child), family=fid)
        METRICS.inc("families_created_total")
        return {"family_id": fid, "slug": slug, "name": name, "tv_token": tv, "link_token": link}

    def rotate_tokens(self, family: str) -> dict:
        tv, link = "lc_tv_" + secrets.token_urlsafe(24), "lc_mcp_" + secrets.token_urlsafe(24)
        self.store.update_family(family, tv_token_hash=hash_token(tv), link_token_hash=hash_token(link))
        return {"tv_token": tv, "link_token": link}

    def family_for_tv_token(self, token: str) -> str | None:
        f = self.store.family_by("tv_token_hash", hash_token(token)) if token else None
        return f["id"] if f else None

    def family_for_link_token(self, token: str) -> str | None:
        f = self.store.family_by("link_token_hash", hash_token(token)) if token else None
        return f["id"] if f else None

    def authenticate(self, slug: str, pin: object) -> tuple[str | None, str | None]:
        """(family_id, None) or (None, error). Empty slug = the default family."""
        slug = (slug or "").strip().lower()
        fam = self.store.family_by("slug", slug) if slug else self.store.family(DEFAULT_FAMILY)
        if not fam:  # burn comparable time, don't reveal whether the family exists
            _verify_pin(str(pin), _hash_pin("0000"))
            METRICS.inc("login_failures_total")
            return None, "Wrong family code or PIN."
        err = self.check_pin(pin, fam["id"])
        return (None, err) if err else (fam["id"], None)

    def check_pin(self, pin: object, family: str = DEFAULT_FAMILY) -> str | None:
        """Return None if OK, else an error message. Locks the family out after repeated failures."""
        now = time.time()
        fam = self.store.family(family)
        if not fam:
            return "Unknown family."
        if now < fam["locked_until"]:
            return f"Too many wrong PINs. Try again in {int(fam['locked_until'] - now) + 1} seconds."
        if isinstance(pin, str) and PIN_RE.match(pin) and _verify_pin(pin, fam["pin_hash"]):
            if fam["fails"]:
                self.store.update_family(family, fails=0)
            return None
        fails = fam["fails"] + 1
        METRICS.inc("pin_failures_total")
        if fails >= MAX_FAILS:
            self.store.update_family(family, locked_until=now + LOCK_SECONDS, fails=0)
        else:
            self.store.update_family(family, fails=fails)
        return "Wrong parent PIN."

    def family_info(self, family: str) -> dict:
        f = self.store.family(family)
        return {"id": f["id"], "slug": f["slug"], "name": f["name"], "email": f["email"],
                "tz_offset_min": f["tz_offset_min"], "created": f["created"]}

    def update_family_settings(self, family: str, name: str | None = None, email: str | None = None,
                               tz_offset_min: int | None = None) -> None:
        cols = {}
        if name is not None:
            if not 2 <= len(name.strip()) <= 40:
                raise InvalidInput("Family name must be 2-40 characters.")
            cols["name"] = name.strip()
        if email is not None:
            if email.strip() and not EMAIL_RE.match(email.strip()):
                raise InvalidInput("That email address doesn't look right.")
            cols["email"] = email.strip()
        if tz_offset_min is not None:
            if not -720 <= int(tz_offset_min) <= 840:
                raise InvalidInput("Time zone offset must be between -720 and 840 minutes.")
            cols["tz_offset_min"] = int(tz_offset_min)
        self.store.update_family(family, **cols)

    # -- children ----------------------------------------------------------------
    def add_child(self, name: str, family: str = DEFAULT_FAMILY) -> str:
        name = clean_child(name)
        if name not in self.store.children(family):
            if len(self.store.children(family)) >= MAX_CHILDREN:
                raise InvalidInput(f"A family can have at most {MAX_CHILDREN} children.")
            self.store.add_child(family, name)
        return name

    def children(self, family: str = DEFAULT_FAMILY) -> list[str]:
        return self.store.children(family)

    def list_children(self, family: str = DEFAULT_FAMILY) -> dict:
        kids = [k.title() for k in self.children(family)]
        say = ("I can practise with " + _join(kids) + ".") if kids else \
            "No children are set up yet. Tell me a name to get started, or ask a parent to add one."
        return {"children": kids, "say": say, "ssml": voice.ssml(say)}

    def _resolve(self, child: object, family: str, fuzzy: bool = True, create: bool = True) -> tuple[str | None, dict | None]:
        """Turn what was said into a child of this family. Returns (name, None) or (None, response-to-say)."""
        kids = self.store.children(family)
        raw = " ".join(str(child or "").lower().split())
        if not raw:
            if len(kids) == 1:
                return kids[0], None
            return None, self._ask_child(kids)
        name = clean_child(raw)
        if name in kids:
            return name, None
        if fuzzy and kids:
            close = difflib.get_close_matches(name, kids, n=3, cutoff=0.7)
            key = voice.phonetic_key(name)
            close += [k for k in kids if k not in close and len(key) >= 2 and voice.phonetic_key(k) == key]
            if len(close) == 1:
                return close[0], None
            if len(close) > 1:
                return None, self._ask_child(close, f"I heard {name.title()}. Did you mean")
        fam = self.store.family(family)
        if (create or fam["auto_create"]) and (fam["auto_create"] or not kids):
            if len(kids) >= MAX_CHILDREN:
                raise InvalidInput(f"A family can have at most {MAX_CHILDREN} children.")
            return name, None
        if not create:
            return None, {"error": f"Unknown child {name!r}.", "say": f"I don't know {name.title()} yet."}
        return None, self._ask_child(kids, f"I don't know {name.title()} yet. Is it")

    @staticmethod
    def _ask_child(kids: list[str], lead: str = "Who is practising? Is it") -> dict:
        titles = [k.title() for k in kids]
        say = f"{lead} {_join(titles, 'or')}?" if titles else "Who is practising? Tell me your name."
        return {"need_child": True, "options": titles, "say": say, "ssml": voice.ssml(say)}

    def resolve_topic(self, spoken: object, child: str, family: str) -> str | None:
        s = re.sub(r"\b(let'?s|please|do|practice|practise|some|the|start|with|my|i want|i'd like|can we)\b", " ", str(spoken or "").lower())
        s = " ".join(s.replace("_", " ").split())
        if not s:
            return None
        ids = {t: t for t in curriculum.TOPICS}
        names = {t.replace("_", " "): t for t in curriculum.TOPICS}
        names.update({v["title"].lower(): t for t, v in curriculum.TOPICS.items()})
        names.update(TOPIC_ALIASES)
        if s in names:
            return names[s]
        for n, t in names.items():
            if len(s) >= 4 and (s in n or n in s):
                return t
        m = difflib.get_close_matches(s, list(names), n=1, cutoff=0.7)
        return names[m[0]] if m else None

    # -- topic access -----------------------------------------------------------
    def available(self, child: str, family: str = DEFAULT_FAMILY) -> list[str]:
        st = self.store
        return sorted((set(DEFAULT_TOPICS) | set(st.unlocked_topics(child, family))) - set(st.blocked_topics(child, family)))

    def topic_status(self, child: str, family: str = DEFAULT_FAMILY) -> dict[str, str]:
        av = set(self.available(child, family))
        return {t: ("open" if t in av else "locked") for t in curriculum.TOPICS}

    # -- sessions ----------------------------------------------------------------
    def _session(self, family: str, child: str, now: float) -> dict:
        s = self.store.open_session(family, child)
        if s and now - s["last_ts"] > SESSION_IDLE_SECONDS:
            self.store.end_session(s["id"], s["last_ts"])
            s = None
        if not s:
            sid = uuid.uuid4().hex[:16]
            self.store.new_session(sid, family, child, now)
            s = self.store.session(sid)
            METRICS.inc("sessions_started_total")
        return s

    def start_session(self, child: str | None = None, family: str = DEFAULT_FAMILY) -> dict:
        who, ask = self._resolve(child, family)
        if ask:
            return ask
        now = time.time()
        self._session(family, who, now)
        st = self.child_stats(family, who, now)
        topics = [{"topic": t, "title": curriculum.TOPICS[t]["title"], "level": self.store.row(who, t, family)["level"]}
                  for t in self.available(who, family)]
        parts = [f"Hi {who.title()}!"]
        if st["streak"] >= 2:
            parts.append(f"You're on a {st['streak']} day streak.")
        due = len(self.store.due_items(family, who, None, now))
        if due:
            parts.append("I have a few tricky ones from before to try again.")
        parts.append("What would you like to practise? " + _join([t["title"] for t in topics], "or") + "?")
        say = " ".join(parts)
        return {"child": who, "topics": topics, "streak_days": st["streak"], "due_reviews": due, "say": say,
                "ssml": voice.ssml(*parts)}

    # -- child-facing: questions ---------------------------------------------------
    def _limit_reached(self, family: str, who: str, now: float) -> bool:
        c = self.store.child(family, who)
        if not c or not c["daily_minutes"]:
            return False
        tz = self.store.family(family)["tz_offset_min"]
        ts = [a["ts"] for a in self.store.attempts_since(family, who, learning.day_start(now, tz))]
        return learning.active_seconds(ts) >= c["daily_minutes"] * 60

    def suggest_topic(self, who: str, family: str, now: float) -> str:
        av = self.available(who, family)
        due = self.store.due_items(family, who, None, now)
        for d in due:
            if d["topic"] in av:
                return d["topic"]
        stale = sorted(av, key=lambda t: self.store.row(who, t, family)["updated_ms"])
        return stale[0]

    def next_question(self, child: str | None = None, topic: str | None = None, rng=None,
                      family: str = DEFAULT_FAMILY) -> dict:
        who, ask = self._resolve(child, family)
        if ask:
            return ask
        rng = rng or self.rng
        now = time.time()
        if self._limit_reached(family, who, now):
            say = f"That's all the practice time for today, {who.title()}. Great work! See you tomorrow."
            METRICS.inc("limit_reached_total")
            return {"limit_reached": True, "error": "Daily practice time reached.", "say": say, "ssml": voice.ssml(say)}
        if not topic:
            topic = self.suggest_topic(who, family, now)
        elif topic not in curriculum.TOPICS:
            guess = self.resolve_topic(topic, who, family)
            if not guess:
                av = self.available(who, family)
                say = "I can't find that one. You can practise " + _join([curriculum.TOPICS[t]["title"] for t in av], "or") + "."
                return {"error": f"Unknown topic. Choose one of: {', '.join(av)}.", "say": say, "ssml": voice.ssml(say)}
            topic = guess
        if topic not in self.available(who, family):
            title = curriculum.TOPICS[topic]["title"]
            say = f"{title} is locked. Ask a parent to unlock it."
            return {"error": f"{topic} is locked. Ask a parent to unlock it.", "say": say, "ssml": voice.ssml(say)}
        level = self.store.row(who, topic, family)["level"]
        q, revisit = self._pick(family, who, topic, level, rng, now)
        self.store.set_pending(who, q, family)
        self._session(family, who, now)
        METRICS.inc("questions_asked_total", topic=topic, revisit=str(revisit).lower())
        intro = voice.pick(voice.REVISIT if revisit else voice.INTROS, rng)
        return {"topic": topic, "level": level, "prompt": q.prompt, "skill": q.skill, "revisit": revisit,
                "hints_available": len(q.hints), "say": f"{intro} {q.prompt}", "ssml": voice.ssml(intro, q.prompt)}

    def _pick(self, family, who, topic, level, rng, now):
        """Spaced repetition first (due weak items), else a fresh question stretched to recent accuracy."""
        due = self.store.due_items(family, who, topic, now, max_level=level)
        recent = self.store.recent_keys(family, who, topic, 1)
        due = [d for d in due if d["item_key"] not in recent] or []
        if due and rng.random() < learning.REVISIT_PROBABILITY:
            return curriculum.Question.from_row(due[0]), True
        stretch = learning.stretch_from_results(self.store.recent_results(family, who, topic))
        avoid = set(self.store.recent_keys(family, who, topic, 4))
        q = None
        for _ in range(6):
            q = curriculum.make_question(topic, level, rng, stretch)
            if q.item_key not in avoid:
                break
        return q, False

    def repeat_question(self, child: str | None = None, family: str = DEFAULT_FAMILY) -> dict:
        who, ask = self._resolve(child, family, create=False)
        if ask:
            return ask
        p = self.store.live_pending(who, family)
        if not p:
            say = "There's no question right now. Say next question to get one."
            return {"error": "No question pending. Call next_question first.", "say": say, "ssml": voice.ssml(say)}
        return {"prompt": p["prompt"], "say": f"Sure. {p['prompt']}", "ssml": voice.ssml("Sure.", p["prompt"])}

    def get_hint(self, child: str | None = None, family: str = DEFAULT_FAMILY) -> dict:
        who, ask = self._resolve(child, family, create=False)
        if ask:
            return ask
        p = self.store.live_pending(who, family)
        if not p:
            say = "There's no question to help with yet."
            return {"error": "No question pending. Call next_question first.", "say": say, "ssml": voice.ssml(say)}
        q = curriculum.Question.from_row(p)
        used = p["hints_used"]
        hint = q.hints[min(used, len(q.hints) - 1)] if q.hints else "Take your time and break it into small steps."
        if used >= max(len(q.hints), 1) and not q.answer.isdigit():
            hint = f"It starts with the letter {q.answer[0].upper()}."
        self.store.bump_pending(who, "hints_used", family)
        METRICS.inc("hints_total")
        say = f"{hint} Have a go!"
        return {"hint": hint, "hints_used": used + 1, "say": say, "ssml": voice.ssml(hint, "Have a go!")}

    def check_answer(self, child: str | None = None, answer: str = "", family: str = DEFAULT_FAMILY) -> dict:
        who, ask = self._resolve(child, family, create=False)
        if ask:
            return ask
        answer = str(answer or "")
        if not answer.strip() or len(answer) > MAX_ANSWER_LEN:
            return {"error": f"Answer must be 1-{MAX_ANSWER_LEN} characters.", "reprompt": True,
                    "say": "Sorry, I didn't catch that. Can you say your answer again?",
                    "ssml": voice.ssml("Sorry, I didn't catch that. Can you say your answer again?")}
        p = self.store.live_pending(who, family)
        if p is None:
            self.store.pop_pending(who, family)
            if voice.detect_intent(answer) == "stop":
                return {**self.end_session(who, family), "stopped": True}
            say = "There's no question yet. Say next question when you're ready."
            return {"error": "No question pending. Call next_question first.", "say": say, "ssml": voice.ssml(say)}
        q = curriculum.Question.from_row(p)
        verdict = q.verdict(answer)
        intent = None if verdict == voice.CORRECT else voice.detect_intent(answer)
        if intent == "repeat":
            return self.repeat_question(who, family)
        if intent == "hint":
            return self.get_hint(who, family)
        if intent == "stop":
            return {**self.end_session(who, family), "stopped": True}
        if verdict == voice.UNCLEAR and intent is None and p["tries"] == 0:  # ask again once before marking wrong
            self.store.bump_pending(who, "tries", family)
            METRICS.inc("reprompts_total")
            say = "Hmm, I didn't quite catch that. " + q.prompt
            return {"reprompt": True, "prompt": q.prompt, "say": say, "ssml": voice.ssml("Hmm, I didn't quite catch that.", q.prompt)}
        self.store.pop_pending(who, family)
        revealed = intent == "skip"
        ok = verdict == voice.CORRECT
        return self._apply(family, who, q, ok, p["hints_used"] > 0, revealed, self.rng)

    def _apply(self, family, who, q, ok, hinted, revealed, rng) -> dict:
        now = time.time()
        st, tz = self.store, self.store.family(family)["tz_offset_min"]
        before = st.row(who, q.topic, family)["level"]
        new_level = st.record(who, q.topic, ok, curriculum.max_level(q.topic), family, now)
        row = st.row(who, q.topic, family)
        old_item = st.item(family, who, q.topic, q.item_key) if q.item_key else None
        box = learning.next_box(old_item["box"] if old_item else 1, ok, hinted)
        if q.item_key:
            st.upsert_item(family, who, q, ok, hinted, box, learning.due_at(box, now), now)
        mastery = st.update_skill(family, who, q.skill, learning.credit(ok, hinted, revealed))
        sess = self._session(family, who, now)
        st.log_attempt(family, who, q.topic, q.skill, q.item_key, ok, hinted, now, sess["id"])
        st.touch_session(sess["id"], 1, int(ok), int(hinted), q.topic, now)
        up, down = new_level > before, new_level < before
        comeback = bool(ok and not hinted and old_item and old_item["box"] == 1 and old_item["seen"] > 0
                        and old_item["seen"] > old_item["correct"])
        badges = learning.evaluate_badges(st, family, who, now, tz, levelled_up=up, comeback=comeback,
                                          session=st.session(sess["id"]))
        METRICS.inc("answers_total", topic=q.topic, result="correct" if ok else "wrong")
        # spoken feedback
        parts = []
        if ok:
            parts.append(voice.pick(voice.HINT_PRAISE if hinted else voice.PRAISE, rng))
            sk = st.session(sess["id"])
            if sk["asked"] >= 3 and sk["asked"] == sk["correct"]:
                parts.append(voice.pick(voice.PRAISE_STREAK, rng, n=sk["asked"]))
        else:
            parts.append("That's okay." if revealed else voice.pick(voice.GENTLE, rng))
            parts.append(voice.pick(voice.REVEAL, rng, a=_speak_answer(q.answer)))
        if up:
            parts.append(voice.pick(voice.LEVEL_UP, rng, n=new_level))
        elif down:
            parts.append(voice.pick(voice.LEVEL_DOWN, rng))
        titles = [voice.speakable(learning.BADGES[b][0]) for b in badges]
        if titles:
            parts.append("New badge: " + _join(titles) + "!")
        parts.append("Want another one?")
        return {"correct": ok, "expected": q.answer, "level": new_level, "levelled_up": up, "levelled_down": down,
                "skipped": revealed, "hinted": hinted, "streak": max(row["streak"], 0), "skill": q.skill,
                "skill_mastery": round(mastery, 2), "revisit_at_box": box,
                "badges_earned": [{"id": b, "title": learning.BADGES[b][0]} for b in badges],
                "say": " ".join(parts), "ssml": voice.ssml(*parts)}

    def end_session(self, child: str | None = None, family: str = DEFAULT_FAMILY) -> dict:
        who, ask = self._resolve(child, family, create=False)
        if ask:
            return ask
        now = time.time()
        s = self.store.open_session(family, who)
        if not s or s["asked"] == 0:
            if s:
                self.store.end_session(s["id"], now)
            say = f"Bye {who.title()}! Come back soon."
            return {"recap": {"asked": 0, "correct": 0}, "say": say, "ssml": voice.ssml(say)}
        self.store.end_session(s["id"], now)
        import json
        topics = [curriculum.TOPICS[t]["title"] for t in json.loads(s["topics"]) if t in curriculum.TOPICS]
        st = self.child_stats(family, who, now)
        acc = round(100 * s["correct"] / s["asked"])
        parts = [f"Great practice, {who.title()}!",
                 f"You answered {s['asked']} question{'s' if s['asked'] != 1 else ''} and got {s['correct']} right."]
        if topics:
            parts.append("You worked on " + _join(topics) + ".")
        if s["hinted"]:
            parts.append(f"You used {s['hinted']} hint{'s' if s['hinted'] != 1 else ''}, and that's a smart way to learn.")
        weak = self.store.weak_items(family, who, 1)
        if weak:
            parts.append("We'll practise a tricky one again next time.")
        if st["streak"] >= 2:
            parts.append(f"That's a {st['streak']} day streak!")
        parts.append("See you next time!")
        recap = {"asked": s["asked"], "correct": s["correct"], "accuracy_pct": acc, "hints": s["hinted"],
                 "topics": topics, "streak_days": st["streak"],
                 "badges": [{"id": b["badge"], "title": learning.BADGES.get(b["badge"], (b["badge"],))[0]}
                            for b in self.store.badges(family, who) if b["earned"] >= s["started"]]}
        return {"recap": recap, "say": " ".join(parts), "ssml": voice.ssml(*parts)}

    # -- parent-facing -------------------------------------------------------------------
    def get_progress(self, child: str | None = None, family: str = DEFAULT_FAMILY) -> dict:
        who, ask = self._resolve(child, family, create=False)
        if ask:
            return ask
        now = time.time()
        st = self.child_stats(family, who, now)
        prog = self.store.progress(who, family)
        say = f"{who.title()} has answered {st['week']['answered']} questions this week"
        if st["week"]["answered"]:
            say += f", {st['week']['accuracy_pct']} percent right"
        say += f", with a {st['streak']} day streak." if st["streak"] else "."
        return {"child": who, "progress": prog, "skills": st["skills"], "badges": st["badges"],
                "streak_days": st["streak"], "week": st["week"], "weak_items": st["weak_items"],
                "say": say, "ssml": voice.ssml(say)}

    def unlock_topic(self, child: str | None, topic: str, parent_pin: str, family: str = DEFAULT_FAMILY) -> dict:
        return self._set_access(child, topic, parent_pin, family, True)

    def lock_topic(self, child: str | None, topic: str, parent_pin: str, family: str = DEFAULT_FAMILY) -> dict:
        return self._set_access(child, topic, parent_pin, family, False)

    def _set_access(self, child, topic, pin, family, open_: bool) -> dict:
        who, ask = self._resolve(child, family, create=False)
        if ask:
            return ask
        err = self.check_pin(pin, family)
        if err:
            return {"error": err, "say": err}
        if topic not in curriculum.TOPICS:
            topic = self.resolve_topic(topic, who, family) or ""
        if topic not in curriculum.TOPICS:
            return {"error": f"Unknown topic {str(topic)[:40]!r}"}
        (self.store.unlock if open_ else self.store.block)(who, topic, family)
        title = curriculum.TOPICS[topic]["title"]
        say = f"{title} is now {'unlocked' if open_ else 'locked'} for {who.title()}."
        return {("unlocked" if open_ else "locked"): topic, "topics": self.available(who, family), "say": say,
                "ssml": voice.ssml(say)}

    def set_child_limits(self, child: str, family: str, daily_minutes: int | None = None,
                         weekly_goal: int | None = None) -> None:
        who = clean_child(child)
        if not self.store.child(family, who):
            raise InvalidInput("Unknown child.")
        cols = {}
        if daily_minutes is not None:
            if not 0 <= int(daily_minutes) <= 240:
                raise InvalidInput("Daily minutes must be 0 (no limit) to 240.")
            cols["daily_minutes"] = int(daily_minutes)
        if weekly_goal is not None:
            if not 0 <= int(weekly_goal) <= 1000:
                raise InvalidInput("Weekly goal must be 0 to 1000 questions.")
            cols["weekly_goal"] = int(weekly_goal)
        self.store.update_child(family, who, **cols)

    # -- stats, dashboard, report data ------------------------------------------------------
    def child_stats(self, family: str, who: str, now: float | None = None, days: int = 14) -> dict:
        now = now or time.time()
        st = self.store
        tz = st.family(family)["tz_offset_min"]
        today0 = learning.day_start(now, tz)
        att = st.attempts_since(family, who, today0 - (days - 1) * 86400)
        by_day: dict[str, dict] = {}
        for i in range(days):
            d = learning.local_day(today0 + 3600 - (days - 1 - i) * 86400, tz)
            by_day[d] = {"day": d, "answered": 0, "correct": 0}
        for a in att:
            d = learning.local_day(a["ts"], tz)
            if d in by_day:
                by_day[d]["answered"] += 1
                by_day[d]["correct"] += a["correct"]
        series = list(by_day.values())
        all_ts = [a["ts"] for a in st.attempts_since(family, who, now - 60 * 86400)]
        strk = learning.streak(learning.practice_days(all_ts, tz), learning.local_day(now, tz))
        week_ts0, prev_ts0 = today0 - 6 * 86400, today0 - 13 * 86400
        wk = [a for a in att if a["ts"] >= week_ts0]
        pw = [a for a in att if prev_ts0 <= a["ts"] < week_ts0]

        def agg(rows):
            n, c = len(rows), sum(r["correct"] for r in rows)
            return {"answered": n, "correct": c, "accuracy_pct": round(100 * c / n) if n else 0,
                    "minutes": round(learning.active_seconds([r["ts"] for r in rows]) / 60),
                    "active_days": len({learning.local_day(r["ts"], tz) for r in rows})}
        kid = st.child(family, who) or {"daily_minutes": 0, "weekly_goal": 30}
        today_min = round(learning.active_seconds([a["ts"] for a in att if a["ts"] >= today0]) / 60)
        skills = [{"skill": s["skill"], "mastery": round(s["mastery"], 2), "attempts": s["attempts"],
                   "label": learning.mastery_label(s["mastery"], s["attempts"])} for s in st.skills(family, who)]
        return {"child": who, "streak": strk, "series": series, "week": agg(wk), "prev_week": agg(pw),
                "skills": skills, "badges": [{"id": b["badge"], "title": learning.BADGES.get(b["badge"], (b["badge"], ""))[0],
                                              "desc": learning.BADGES.get(b["badge"], ("", ""))[1], "earned": b["earned"]}
                                             for b in st.badges(family, who)],
                "weak_items": [{"topic": w["topic"], "prompt": w["prompt"], "answer": w["answer"]}
                               for w in st.weak_items(family, who, 5)],
                "goal": {"weekly": kid["weekly_goal"], "done": len(wk),
                         "pct": min(100, round(100 * len(wk) / kid["weekly_goal"])) if kid["weekly_goal"] else 0},
                "limit": {"daily_minutes": kid["daily_minutes"], "today_minutes": today_min},
                "topics": self.topic_status(who, family),
                "levels": {p["topic"]: p["level"] for p in st.progress(who, family)}}

    def overview(self, family: str = DEFAULT_FAMILY) -> list[dict]:
        return [{"child": c, "available": self.available(c, family), "progress": self.store.progress(c, family)}
                for c in self.store.children(family)]

    def dashboard(self, family: str) -> dict:
        now = time.time()
        return {"family": self.family_info(family),
                "children": [self.child_stats(family, c, now) for c in self.store.children(family)]}

    # -- privacy ------------------------------------------------------------------------------
    def export_family(self, family: str) -> dict:
        return self.store.export_family(family)

    def delete_child(self, child: str, family: str) -> None:
        self.store.delete_child(family, clean_child(child))

    def delete_family(self, family: str, pin: str) -> dict:
        if family == DEFAULT_FAMILY:
            return {"error": "The default family is configured by environment variables and cannot be deleted here."}
        err = self.check_pin(pin, family)
        if err:
            return {"error": err}
        self.store.delete_family(family)
        METRICS.inc("families_deleted_total")
        return {"deleted": True}

    def purge_old(self, days: int = 365) -> int:
        return self.store.purge_attempts_older_than(time.time() - days * 86400)

    # -- TV client (REST API; see tvapi.py) ------------------------------------------------------
    def tv_progress(self, child: str, family: str = DEFAULT_FAMILY) -> dict:
        """Progress in the TV's shape. Read-only; never changes what is unlocked."""
        child = clean_child(child)
        if not self._tv_child_ok(child, family):
            raise UnknownChild(child)
        av = self.available(child, family)
        out = {}
        for r in self.store.progress(child, family):
            if r["topic"] in av:
                out[r["topic"]] = {"level": r["level"], "correct": r["correct"], "attempts": r["attempts"],
                                   "streak": max(r["streak"], 0), "updatedAt": int(r.get("updated_ms") or 0)}
        return {"child": child, "progress": out, "available": av, "promoteAfter": PROMOTE_AFTER}

    def _tv_child_ok(self, child: str, family: str) -> bool:
        kids = self.store.children(family)
        return child in kids or bool(self.store.family(family)["auto_create"]) or not kids

    def tv_events(self, child: str, events: list[dict], family: str = DEFAULT_FAMILY) -> dict:
        """Apply validated TV events (oldest first). The coach's own level rules decide levels; the TV's
        claimed level is ignored. Events for locked topics are rejected, never unlocking anything."""
        child = clean_child(child)
        if not self._tv_child_ok(child, family):
            raise UnknownChild(child)
        applied, duplicates, rejected = 0, 0, []
        with self.store.lock:
            for e in sorted(events, key=lambda e: e["at"]):
                if e["topic"] not in self.available(child, family):
                    rejected.append({"id": e["id"], "reason": "locked"})
                elif not self.store.claim_event(child, e["id"], family):
                    duplicates += 1
                else:
                    level = self.store.row(child, e["topic"], family)["level"]
                    self.store.record(child, e["topic"], e["correct"], curriculum.max_level(e["topic"]), family)
                    self.store.log_attempt(family, child, e["topic"], curriculum.skill_for(e["topic"], level), "tv",
                                           e["correct"], False, min(e["at"] / 1000, time.time()))
                    self.store.update_skill(family, child, curriculum.skill_for(e["topic"], level), 1.0 if e["correct"] else 0.0)
                    applied += 1
        if applied:
            learning.evaluate_badges(self.store, family, child, time.time(), self.store.family(family)["tz_offset_min"])
        METRICS.inc("tv_events_applied_total", applied)
        return {**self.tv_progress(child, family), "applied": applied, "duplicates": duplicates, "rejected": rejected}


class UnknownChild(InvalidInput):
    pass


def _join(items: list[str], word: str = "and") -> str:
    items = [str(i) for i in items]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f" {word} " + items[-1]


def _speak_answer(a: str) -> str:
    """Make an answer easy for TTS to read (fractions as words)."""
    m = re.fullmatch(r"(\d+)/(\d+)", a)
    if m and int(m.group(2)) in voice.DENOM_SPOKEN:
        return voice.say_fraction(int(m.group(1)), int(m.group(2)))
    return a
