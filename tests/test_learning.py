import random
import time

import pytest

from learning_coach import learning
from learning_coach.service import Coach
from learning_coach.store import Store


@pytest.fixture
def coach(tmp_path):
    return Coach(Store(str(tmp_path / "l.db")), "1234", rng=random.Random(7))


def ans(coach, child="ada", fam="default"):
    return coach.store.peek_pending(child, fam)["answer"]


def ask_right(coach, topic="abacus", child="ada"):
    coach.next_question(child, topic)
    return coach.check_answer(child, ans(coach, child))


def ask_wrong(coach, topic="abacus", child="ada"):
    coach.next_question(child, topic)
    return coach.check_answer(child, "99999")


def test_leitner_boxes():
    assert learning.next_box(1, True, False) == 2 and learning.next_box(5, True, False) == 5
    assert learning.next_box(4, False, False) == 1
    assert learning.next_box(3, True, True) == 3  # hinted: no promotion
    assert learning.due_at(2, 0) > learning.due_at(1, 0)


def test_srs_scale_env(monkeypatch):
    monkeypatch.setenv("SRS_SCALE", "0.001")
    assert learning.due_at(2, 0) == pytest.approx(86.4)


def test_wrong_answer_creates_weak_item_and_it_returns(coach, monkeypatch):
    monkeypatch.setenv("SRS_SCALE", "0")  # "in 5 minutes" == now
    monkeypatch.setattr(learning, "REVISIT_PROBABILITY", 2.0)
    coach.next_question("ada", "abacus")
    first = coach.store.peek_pending("ada")
    r = coach.check_answer("ada", "99999")
    assert r["correct"] is False and str(first["answer"]) in r["say"]
    weak = coach.store.weak_items("default", "ada")
    assert len(weak) == 1 and weak[0]["box"] == 1
    # another question is answered; then the weak item is served again (not immediately repeated)
    coach.next_question("ada", "abacus")
    coach.check_answer("ada", ans(coach))
    seen_revisit = False
    for _ in range(6):
        q = coach.next_question("ada", "abacus")
        if q["revisit"]:
            assert coach.store.peek_pending("ada")["prompt"] == first["prompt"]
            r = coach.check_answer("ada", ans(coach))
            assert r["correct"] and r["revisit_at_box"] == 2
            seen_revisit = True
            break
        coach.check_answer("ada", ans(coach))
    assert seen_revisit


def test_revisit_not_served_before_due(coach, monkeypatch):
    monkeypatch.setattr(learning, "REVISIT_PROBABILITY", 2.0)
    ask_wrong(coach)  # due in 5 minutes with default scale
    for _ in range(5):
        assert coach.next_question("ada", "abacus")["revisit"] is False
        coach.check_answer("ada", ans(coach))


def test_comeback_badge(coach, monkeypatch):
    monkeypatch.setenv("SRS_SCALE", "0")
    monkeypatch.setattr(learning, "REVISIT_PROBABILITY", 2.0)
    ask_wrong(coach)
    for _ in range(8):
        q = coach.next_question("ada", "abacus")
        r = coach.check_answer("ada", ans(coach))
        if q["revisit"]:
            assert "comeback" in [b["id"] for b in r["badges_earned"]] or coach.store.badges("default", "ada")
            break
    assert "comeback" in [b["badge"] for b in coach.store.badges("default", "ada")]


def test_stretch_from_results():
    assert learning.stretch_from_results([1, 1]) == 0.3
    assert learning.stretch_from_results([1] * 8) == 1.0
    assert learning.stretch_from_results([0] * 8) == 0.0
    assert 0 < learning.stretch_from_results([1, 1, 1, 0, 1, 0, 1, 1]) < 1


def test_skill_mastery_moves(coach):
    for _ in range(4):
        ask_right(coach)
    m = {s["skill"]: s for s in coach.child_stats("default", "ada")["skills"]}
    assert max(v["mastery"] for v in m.values()) > 0.55
    for _ in range(6):
        ask_wrong(coach)
    m2 = {s["skill"]: s for s in coach.child_stats("default", "ada")["skills"]}
    assert min(v["mastery"] for v in m2.values()) < 0.5


def test_hints_progressive_and_partial_credit(coach):
    coach.next_question("ada", "abacus")
    h1 = coach.get_hint("ada")
    assert h1["hints_used"] == 1 and h1["hint"] and "say" in h1
    coach.get_hint("ada"); h3 = coach.get_hint("ada")
    assert h3["hints_used"] == 3
    r = coach.check_answer("ada", ans(coach))
    assert r["correct"] and r["hinted"]
    sk = coach.store.skills("default", "ada")[0]
    assert sk["mastery"] < 0.5 + 0.15 * 0.5 + 1e-9  # a hinted answer earns partial credit only
    assert coach.store.item("default", "ada", "abacus", coach.store.recent_keys("default", "ada", "abacus", 1)[0])["box"] == 1


def test_hint_without_question(coach):
    assert "No question" in coach.get_hint("ada")["error"]


def test_voice_intents_inside_check_answer(coach):
    coach.next_question("ada", "abacus")
    prompt = coach.store.peek_pending("ada")["prompt"]
    r = coach.check_answer("ada", "can you say that again")
    assert prompt in r["say"] and coach.store.peek_pending("ada")  # question stays pending
    assert coach.check_answer("ada", "give me a hint")["hint"]
    r = coach.check_answer("ada", "I don't know")
    assert r["skipped"] and r["correct"] is False and "No question" in coach.check_answer("ada", "5")["error"]


def test_reprompt_once_then_wrong(coach):
    coach.next_question("ada", "abacus")
    r = coach.check_answer("ada", "banana")
    assert r["reprompt"] is True and coach.store.peek_pending("ada")
    r = coach.check_answer("ada", "banana")  # second time: counted
    assert r["correct"] is False


def test_empty_answer_reprompts_without_consuming(coach):
    coach.next_question("ada", "abacus")
    r = coach.check_answer("ada", "  ")
    assert r["reprompt"] and coach.store.peek_pending("ada")


def test_spoken_number_words_accepted_end_to_end(coach):
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}
    coach.next_question("ada", "abacus")
    a = int(ans(coach))
    assert coach.check_answer("ada", f"it's {words[a]}")["correct"]


def test_stop_ends_session_with_recap(coach):
    ask_right(coach); ask_right(coach); ask_wrong(coach)
    coach.next_question("ada", "abacus")
    r = coach.check_answer("ada", "I'm done")
    assert r["stopped"] and r["recap"]["asked"] == 3 and r["recap"]["correct"] == 2
    assert "3 questions" in r["say"] and "2 right" in r["say"]


def test_session_recap_and_badges(coach):
    for _ in range(5):
        ask_right(coach)
    r = coach.end_session("ada")
    assert r["recap"]["asked"] == 5 and r["recap"]["correct"] == 5 and r["recap"]["accuracy_pct"] == 100
    ids = {b["badge"] for b in coach.store.badges("default", "ada")}
    assert {"first_answer", "perfect_session", "level_up"} <= ids
    assert coach.end_session("ada")["recap"]["asked"] == 0  # session closed


def test_feedback_varies_across_answers(coach):
    said = set()
    for _ in range(12):
        said.add(ask_right(coach)["say"].split(".")[0].split("!")[0])
    assert len(said) >= 3


def test_streak_math():
    days = {"2026-03-05", "2026-03-04", "2026-03-03", "2026-03-01"}
    assert learning.streak(days, "2026-03-05") == 3
    assert learning.streak(days, "2026-03-06") == 3  # still alive until today ends
    assert learning.streak(days, "2026-03-08") == 0
    assert learning.local_day(0, -300) == "1969-12-31"


def test_streak_badges_and_dashboard_streak(coach):
    now = time.time()
    for d in range(3):
        coach.store.log_attempt("default", "ada", "abacus", "addition", f"k{d}", True, False, now - d * 86400)
    assert coach.child_stats("default", "ada")["streak"] == 3
    coach.store.row("ada", "abacus")
    assert "streak_3" in learning.evaluate_badges(coach.store, "default", "ada", now, 0)


def test_active_seconds():
    assert learning.active_seconds([0, 30, 60]) == 90  # lone first (30) + two 30s gaps
    assert learning.active_seconds([0, 10_000]) == 60


def test_daily_time_limit_blocks_and_resets(coach, monkeypatch):
    coach.start_session("ada")
    coach.set_child_limits("ada", "default", daily_minutes=1)
    now = time.time()
    for i in (300, 200, 100):  # about 4 minutes of activity today
        coach.store.log_attempt("default", "ada", "abacus", "addition", f"k{i}", True, False, now - i)
    r = coach.next_question("ada", "abacus")
    assert r.get("limit_reached") and "tomorrow" in r["say"]
    real = time.time
    monkeypatch.setattr("learning_coach.service.time.time", lambda: real() + 86400 * 2)
    assert "prompt" in coach.next_question("ada", "abacus")


def test_topic_lock_unlock_via_service(coach):
    assert coach.unlock_topic("ada", "fractions", "1234")["unlocked"] == "fractions"
    assert "fractions" in coach.available("ada")
    assert coach.lock_topic("ada", "fractions", "0000")["error"]
    assert coach.lock_topic("ada", "fractions", "1234")["locked"] == "fractions"
    assert "locked" in coach.next_question("ada", "fractions")["error"]
    coach.lock_topic("ada", "abacus", "1234")  # even the default topic can be locked
    assert "locked" in coach.next_question("ada", "abacus")["error"]
    assert coach.unlock_topic("ada", "abacus", "1234")["unlocked"]


def test_spoken_topic_names_and_smart_pick(coach):
    for t in ("times_tables", "spelling", "geography"):
        coach.unlock_topic("ada", t, "1234")
    assert coach.next_question("ada", "times tables")["topic"] == "times_tables"
    assert coach.next_question("ada", "spelling bee")["topic"] == "spelling"
    assert coach.next_question("ada", "capitals")["topic"] == "geography"
    assert coach.next_question("ada", "let's do some abacus please")["topic"] == "abacus"
    assert coach.next_question("ada", "")["topic"] in coach.available("ada")  # coach chooses
    assert "can't find" in coach.next_question("ada", "zzzzzz")["say"]


def test_new_topics_play_through(coach):
    for t in ("times_tables", "fractions", "spelling", "reading", "science"):
        coach.unlock_topic("ada", t, "1234")
        q = coach.next_question("ada", t)
        assert q["prompt"] and q["say"] and q["ssml"].startswith("<speak>")
        assert coach.check_answer("ada", ans(coach))["correct"]


def test_responses_are_voice_ready(coach):
    r = coach.next_question("ada", "abacus")
    assert r["say"].endswith("?") and r["ssml"].startswith("<speak>")
    r = coach.check_answer("ada", ans(coach))
    assert "<" not in r["say"] and len(r["say"].split()) < 45
