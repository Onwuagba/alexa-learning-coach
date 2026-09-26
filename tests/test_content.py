import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

from learning_coach import curriculum, validate

SHARED = Path(__file__).resolve().parents[2] / "shared-curriculum"


def test_generated_content_validates():
    assert validate.validate(samples=60) == []


def test_topic_breadth():
    assert {"abacus", "algebra", "geography", "times_tables", "fractions", "spelling", "reading", "science"} <= set(curriculum.TOPICS)
    assert len(curriculum.DATA["countries"]) >= 50


def test_old_contract_unchanged():
    """The Fire TV app generates from the same JSON: old kinds, topics and countries must stay."""
    T = curriculum.TOPICS
    assert [l["kind"] for l in T["abacus"]["levels"]][:5] == ["count", "add", "add", "subtract", "add"]
    assert [l["kind"] for l in T["algebra"]["levels"]] == ["x_plus_a", "x_plus_a", "a_times_x", "a_times_x_plus_b"]
    assert [l["kind"] for l in T["geography"]["levels"]][:3] == ["continent", "capital", "capital"]
    names = {c["name"] for c in curriculum.DATA["countries"]}
    assert {"Nigeria", "Ghana", "Kenya", "Brazil", "India"} <= names and curriculum.DATA["version"] == 1


def test_math_answers_are_actually_correct():
    rng = random.Random(3)
    for _ in range(300):
        q = curriculum.make_question("times_tables", rng.randint(1, 4), rng)
        a, b = map(int, q.prompt.replace("What is ", "").replace("?", "").split(" times "))
        assert int(q.answer) == a * b
        q = curriculum.make_question("times_tables", 5, rng)
        n, d = map(int, q.prompt.replace("What is ", "").replace("?", "").split(" divided by "))
        assert int(q.answer) * d == n
        q = curriculum.make_question("abacus", 4, rng)
        a, b = map(int, q.prompt.replace("What is ", "").replace("?", "").split(" minus "))
        assert int(q.answer) == a - b and b < a


def test_fraction_answers_are_reduced_and_correct():
    import math
    rng = random.Random(4)
    for _ in range(200):
        q = curriculum.make_question("fractions", 4, rng)
        n, d = map(int, q.answer.split("/"))
        assert math.gcd(n, d) == 1 and n < d
        q = curriculum.make_question("fractions", 3, rng)
        n, d = map(int, q.answer.split("/"))
        assert n < d


def test_stretch_makes_numbers_harder_not_invalid():
    rng = random.Random(5)
    lo = [int(curriculum.make_question("abacus", 3, rng, 0.0).answer) for _ in range(200)]
    hi = [int(curriculum.make_question("abacus", 3, rng, 1.0).answer) for _ in range(200)]
    assert sum(hi) / 200 > sum(lo) / 200 and max(hi) <= 20


def test_static_items_are_unique_and_answerable():
    ids = set()
    for topic, spec in curriculum.TOPICS.items():
        for lv in spec["levels"]:
            for it in lv.get("items", []):
                assert it["id"] not in ids
                ids.add(it["id"])
                q = curriculum.Question(topic, lv["level"], it["prompt"], it["answer"], accept=it.get("accept", []),
                                        match=it.get("match", "auto"))
                assert q.check(it["answer"]) and all(q.check(a) for a in it.get("accept", []))


def test_shared_and_local_curriculum_in_sync_and_valid():
    if not SHARED.exists():
        pytest.skip("shared-curriculum not next to this repo")
    assert json.loads((SHARED / "curriculum.json").read_text()) == curriculum.DATA, "run: cp ../shared-curriculum/curriculum.json learning_coach/"
    r = subprocess.run([sys.executable, str(SHARED / "validate_curriculum.py")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_skill_for():
    assert curriculum.skill_for("abacus", 2) == "addition" and curriculum.skill_for("spelling", 1) == "spelling"
