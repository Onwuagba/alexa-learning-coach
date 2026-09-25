import random

from learning_coach import curriculum
from learning_coach.store import Store


def test_every_level_generates_a_checkable_question():
    rng = random.Random(0)
    for topic in curriculum.TOPICS:
        for level in range(1, curriculum.max_level(topic) + 1):
            q = curriculum.make_question(topic, level, rng)
            assert q.prompt and q.check(q.answer)


def test_answers_are_forgiving():
    q = curriculum.Question("geography", 3, "", "Brasília")
    assert q.check("brasilia") and q.check("  BRASILIA ")


def test_adaptive_levels(tmp_path):
    s = Store(tmp_path / "p.db")
    assert s.record("ada", "abacus", True, 5) == 1
    assert s.record("ada", "abacus", True, 5) == 2   # two right -> up
    assert s.record("ada", "abacus", False, 5) == 2
    assert s.record("ada", "abacus", False, 5) == 1  # two wrong -> down
