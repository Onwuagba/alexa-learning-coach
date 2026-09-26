"""Question generation from the shared curriculum (learning_coach/curriculum.json).

A `Question` carries what the learning engine needs: a `skill` (per-skill mastery), a stable `item_key`
(spaced repetition), progressive `hints`, and matching hints for spoken answers."""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

from . import voice

DATA = json.loads((Path(__file__).parent / "curriculum.json").read_text())
TOPICS = DATA["topics"]

SKILLS = {"count": "counting", "add": "addition", "subtract": "subtraction", "x_plus_a": "algebra",
          "a_times_x": "algebra", "a_times_x_plus_b": "algebra", "continent": "continents", "capital": "capitals",
          "times_table": "multiplication", "division_facts": "division", "half_of": "fractions",
          "fraction_of": "fractions", "add_fractions": "fractions", "simplify_fraction": "fractions"}


@dataclass
class Question:
    topic: str
    level: int
    prompt: str
    answer: str
    skill: str = "general"
    item_key: str = ""
    hints: list[str] = field(default_factory=list)
    accept: list[str] = field(default_factory=list)
    match: str = "auto"

    def verdict(self, given: str) -> str:
        return voice.judge(given, self.answer, self.accept, self.match)

    def check(self, given: str) -> bool:
        return self.verdict(given) == voice.CORRECT

    def extra(self) -> dict:
        return {"skill": self.skill, "item_key": self.item_key, "hints": self.hints, "accept": self.accept,
                "match": self.match}

    @classmethod
    def from_row(cls, r: dict) -> "Question":
        x = json.loads(r.get("extra") or "{}")
        return cls(r["topic"], r["level"], r["prompt"], r["answer"], x.get("skill", "general"), x.get("item_key", ""),
                   x.get("hints", []), x.get("accept", []), x.get("match", "auto"))


def max_level(topic: str) -> int:
    return len(TOPICS[topic]["levels"])


def _lo(hi: int, stretch: float, floor: int = 1) -> int:
    """Raise the lower bound of an operand range as `stretch` (0..1) grows: same level, harder numbers."""
    return max(floor, min(hi, floor + int((hi - floor) * 0.6 * stretch)))


def make_question(topic: str, level: int, rng: random.Random | None = None, stretch: float = 0.0) -> Question:
    rng = rng or random.Random()
    spec = TOPICS[topic]["levels"][level - 1]
    kind = spec["kind"]
    skill = spec.get("skill") or SKILLS.get(kind, topic)

    def Q(prompt, answer, key, hints, **kw):
        return Question(topic, level, prompt, str(answer), skill, f"{topic}:{key}", hints, **kw)

    if kind == "count":
        n = rng.randint(_lo(spec["max"], stretch), spec["max"])
        return Q(f"I slide {n} beads across. How many beads is that?", n, f"n{n}", ["Count the beads one by one."])
    if kind == "add":
        half = spec["max"] // 2
        a = rng.randint(_lo(half, stretch), half)
        b = rng.randint(_lo(spec["max"] - a, stretch), spec["max"] - a)
        return Q(f"What is {a} plus {b}?", a + b, f"{a}+{b}",
                 [f"Start at {a} and count up {b}.", f"Hold up {a} fingers, then add {b} more."])
    if kind == "subtract":
        a = rng.randint(_lo(spec["max"], stretch, 2), spec["max"])
        b = rng.randint(1, a - 1)
        return Q(f"What is {a} minus {b}?", a - b, f"{a}-{b}", [f"Start at {a} and count back {b}."])
    if kind == "x_plus_a":
        x = rng.randint(_lo(spec["max"], stretch), spec["max"])
        a = rng.randint(1, spec["max"])
        return Q(f"x plus {a} equals {x + a}. What is x?", x, f"x+{a}={x + a}", [f"Take {a} away from {x + a}."])
    if kind == "a_times_x":
        x = rng.randint(_lo(spec["max"], stretch), spec["max"])
        a = rng.randint(2, 9)
        return Q(f"{a} times x equals {a * x}. What is x?", x, f"{a}x={a * x}",
                 [f"Divide {a * x} by {a}.", f"How many groups of {a} make {a * x}?"])
    if kind == "a_times_x_plus_b":
        x = rng.randint(_lo(spec["max"], stretch), spec["max"])
        a, b = rng.randint(2, 9), rng.randint(1, 20)
        return Q(f"{a} times x, plus {b}, equals {a * x + b}. What is x?", x, f"{a}x+{b}={a * x + b}",
                 [f"First take away {b}.", f"Now you have {a * x}. Divide by {a}."])
    if kind in ("continent", "capital"):
        pool = [c for c in DATA["countries"] if spec["region"] == "world" or c["region"] == spec["region"]]
        c = rng.choice(pool)
        if kind == "continent":
            return Q(f"Which continent is {c['name']} in?", c["continent"], f"cont:{c['name']}",
                     [f"It starts with the letter {c['continent'][0]}."])
        return Q(f"What is the capital of {c['name']}?", c["capital"], f"cap:{c['name']}",
                 [f"The capital starts with {c['capital'][0]}.", f"It has {len(c['capital'].replace(' ', ''))} letters."],
                 accept=c.get("accept", []))
    if kind == "times_table":
        a = rng.choice(spec["tables"])
        b = rng.randint(_lo(12, stretch, 2), 12)
        lo, hi = sorted((a, b))
        return Q(f"What is {a} times {b}?", a * b, f"{lo}x{hi}",
                 [f"Think of {a} groups of {b}.", f"Try {a} times {max(b // 2, 1)} and double it."])
    if kind == "division_facts":
        a = rng.choice(spec["tables"])
        b = rng.randint(_lo(12, stretch, 2), 12)
        return Q(f"What is {a * b} divided by {a}?", b, f"{a * b}/{a}",
                 [f"How many {a}s fit into {a * b}?", f"Think of the {a} times table."])
    if kind == "half_of":
        n = 2 * rng.randint(_lo(spec["max"] // 2, stretch, 1), spec["max"] // 2)
        return Q(f"What is half of {n}?", n // 2, f"half:{n}", ["Share it into two equal groups."])
    if kind == "fraction_of":
        d = rng.choice(spec["denominators"])
        n = rng.randint(1, d - 1)
        whole = d * rng.randint(_lo(6, stretch, 1), 6)
        return Q(f"What is {voice.say_fraction(n, d)} of {whole}?", whole // d * n, f"{n}/{d}of{whole}",
                 [f"First find one part: split {whole} into {d} equal parts."])
    if kind == "add_fractions":
        for _ in range(50):
            d = rng.choice(spec["denominators"])
            a, b = rng.randint(1, d - 2), rng.randint(1, d - 2)
            if a + b < d and math.gcd(a + b, d) == 1:
                break
        else:
            d, a, b = 5, 1, 2
        return Q(f"What is {voice.say_fraction(a, d)} plus {voice.say_fraction(b, d)}? Say it as a fraction.",
                 f"{a + b}/{d}", f"{a}/{d}+{b}/{d}", ["The bottom numbers are the same, so just add the top numbers."],
                 match="fraction")
    if kind == "simplify_fraction":
        for _ in range(50):
            d = rng.choice(spec["denominators"])
            n = rng.randint(1, d - 1)
            if math.gcd(n, d) > 1:
                break
        else:
            n, d = 2, 4
        g = math.gcd(n, d)
        return Q(f"What is {voice.say_fraction(n, d)} in its simplest form?", f"{n // g}/{d // g}", f"simp:{n}/{d}",
                 [f"Both numbers can be divided by {g}."], match="fraction_exact")
    if kind == "items":
        it = rng.choice(spec["items"])
        hints = [it["hint"]] if it.get("hint") else []
        return Question(topic, level, it["prompt"], it["answer"], it.get("skill") or skill, f"{topic}:{it['id']}",
                        hints, list(it.get("accept", [])), it.get("match", "auto"))
    raise ValueError(f"unknown level kind: {kind}")


def skill_for(topic: str, level: int) -> str:
    """Skill name for a topic level (used when only topic+level are known, e.g. TV events)."""
    spec = TOPICS[topic]["levels"][max(1, min(level, max_level(topic))) - 1]
    return spec.get("skill") or SKILLS.get(spec["kind"], topic)
