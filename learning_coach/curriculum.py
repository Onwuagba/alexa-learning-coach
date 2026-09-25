"""Question generation from the shared curriculum."""
from __future__ import annotations

import json
import random
import unicodedata
from dataclasses import dataclass
from pathlib import Path

DATA = json.loads((Path(__file__).parent / "curriculum.json").read_text())
TOPICS = DATA["topics"]


@dataclass
class Question:
    topic: str
    level: int
    prompt: str
    answer: str

    def check(self, given: str) -> bool:
        return _norm(given) == _norm(self.answer)


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


def max_level(topic: str) -> int:
    return len(TOPICS[topic]["levels"])


def make_question(topic: str, level: int, rng: random.Random | None = None) -> Question:
    rng = rng or random.Random()
    spec = TOPICS[topic]["levels"][level - 1]
    kind = spec["kind"]

    if kind == "count":
        n = rng.randint(1, spec["max"])
        return Question(topic, level, f"I slide {n} beads across. How many beads is that?", str(n))
    if kind == "add":
        a = rng.randint(1, spec["max"] // 2)
        b = rng.randint(1, spec["max"] - a)
        return Question(topic, level, f"What is {a} plus {b}?", str(a + b))
    if kind == "subtract":
        a = rng.randint(2, spec["max"])
        b = rng.randint(1, a - 1)
        return Question(topic, level, f"What is {a} minus {b}?", str(a - b))
    if kind == "x_plus_a":
        x = rng.randint(1, spec["max"])
        a = rng.randint(1, spec["max"])
        return Question(topic, level, f"x plus {a} equals {x + a}. What is x?", str(x))
    if kind == "a_times_x":
        x = rng.randint(1, spec["max"])
        a = rng.randint(2, 9)
        return Question(topic, level, f"{a} times x equals {a * x}. What is x?", str(x))
    if kind == "a_times_x_plus_b":
        x = rng.randint(1, spec["max"])
        a = rng.randint(2, 9)
        b = rng.randint(1, 20)
        return Question(topic, level, f"{a} times x, plus {b}, equals {a * x + b}. What is x?", str(x))
    if kind in ("continent", "capital"):
        pool = [c for c in DATA["countries"] if spec["region"] == "world" or c["region"] == spec["region"]]
        c = rng.choice(pool)
        if kind == "continent":
            return Question(topic, level, f"Which continent is {c['name']} in?", c["continent"])
        return Question(topic, level, f"What is the capital of {c['name']}?", c["capital"])
    raise ValueError(f"unknown level kind: {kind}")
