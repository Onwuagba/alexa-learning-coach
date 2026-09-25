"""Business logic shared by the MCP tools and the parent web page. Returns plain dicts."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import sys
import time

from . import curriculum
from .store import Store

DEFAULT_TOPICS = ["abacus"]  # always available; others need a parent unlock
MAX_FAILS = 5
LOCK_SECONDS = 5 * 60
NAME_RE = re.compile(r"^[a-z][a-z '\-]{0,31}$")
PIN_RE = re.compile(r"^\d{4,8}$")
MAX_ANSWER_LEN = 100


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
    n, salt_b64, _ = stored.split("$")
    return hmac.compare_digest(_hash_pin(pin, base64.b64decode(salt_b64), int(n)), stored)


class Coach:
    def __init__(self, store: Store, parent_pin: str | None = None):
        self.store = store
        if parent_pin:
            self.set_pin(parent_pin)
        elif store.get_setting("pin_hash") is None:
            pin = f"{secrets.randbelow(10**6):06d}"
            self.set_pin(pin)
            print(f"[learning-coach] PARENT_PIN not set; generated one-time PIN: {pin}", file=sys.stderr)

    # -- PIN ---------------------------------------------------------------
    def set_pin(self, pin: str) -> None:
        if not PIN_RE.match(pin):
            raise InvalidInput("PIN must be 4-8 digits.")
        cur = self.store.get_setting("pin_hash")
        if cur and _verify_pin(pin, cur):
            return  # unchanged; keep lockout state
        self.store.set_setting("pin_hash", _hash_pin(pin))

    def check_pin(self, pin: object) -> str | None:
        """Return None if OK, else an error message. Locks out after repeated failures."""
        now = time.time()
        locked_until = float(self.store.get_setting("locked_until", "0"))
        if now < locked_until:
            return f"Too many wrong PINs. Try again in {int(locked_until - now) + 1} seconds."
        if isinstance(pin, str) and PIN_RE.match(pin) and _verify_pin(pin, self.store.get_setting("pin_hash")):
            self.store.set_setting("fails", "0")
            return None
        fails = int(self.store.get_setting("fails", "0")) + 1
        if fails >= MAX_FAILS:
            self.store.set_setting("locked_until", str(now + LOCK_SECONDS))
            fails = 0
        self.store.set_setting("fails", str(fails))
        return "Wrong parent PIN."

    # -- child-facing ----------------------------------------------------------
    def available(self, child: str) -> list[str]:
        return sorted(set(DEFAULT_TOPICS) | set(self.store.unlocked_topics(child)))

    def start_session(self, child: str) -> dict:
        child = clean_child(child)
        return {"child": child, "topics": [
            {"topic": t, "title": curriculum.TOPICS[t]["title"], "level": self.store.row(child, t)["level"]}
            for t in self.available(child)]}

    def next_question(self, child: str, topic: str, rng=None) -> dict:
        child = clean_child(child)
        if topic not in curriculum.TOPICS:
            return {"error": f"Unknown topic. Choose one of: {', '.join(self.available(child))}."}
        if topic not in self.available(child):
            return {"error": f"{topic} is locked. Ask a parent to unlock it."}
        level = self.store.row(child, topic)["level"]
        q = curriculum.make_question(topic, level, rng)
        self.store.set_pending(child, q)
        return {"topic": topic, "level": level, "prompt": q.prompt}

    def check_answer(self, child: str, answer: str) -> dict:
        child = clean_child(child)
        answer = str(answer or "")
        if not answer.strip() or len(answer) > MAX_ANSWER_LEN:
            return {"error": f"Answer must be 1-{MAX_ANSWER_LEN} characters."}
        p = self.store.pop_pending(child)
        if p is None:
            return {"error": "No question pending. Call next_question first."}
        q = curriculum.Question(p["topic"], p["level"], p["prompt"], p["answer"])
        ok = q.check(answer)
        new_level = self.store.record(child, q.topic, ok, curriculum.max_level(q.topic))
        return {"correct": ok, "expected": q.answer, "level": new_level,
                "levelled_up": new_level > q.level, "levelled_down": new_level < q.level}

    # -- parent-facing ---------------------------------------------------------
    def get_progress(self, child: str) -> dict:
        child = clean_child(child)
        return {"child": child, "progress": self.store.progress(child)}

    def unlock_topic(self, child: str, topic: str, parent_pin: str) -> dict:
        child = clean_child(child)
        err = self.check_pin(parent_pin)
        if err:
            return {"error": err}
        if topic not in curriculum.TOPICS:
            return {"error": f"Unknown topic {str(topic)[:40]!r}"}
        self.store.unlock(child, topic)
        return {"unlocked": topic, "topics": self.available(child)}

    def overview(self) -> list[dict]:
        return [{"child": c, "available": self.available(c), "progress": self.store.progress(c)}
                for c in self.store.children()]
