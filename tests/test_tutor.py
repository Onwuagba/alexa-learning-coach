"""Strands tutor planner, offline: the agent factory is faked (the real Strands/Bedrock path is not run here)."""
import json

import pytest

from learning_coach import tutor
from learning_coach.service import Coach
from learning_coach.store import Store
from learning_coach.tutor import TutorPlanner, parse_plan


class Sync:
    def submit(self, fn, *a, **k):
        fn(*a, **k)


def fake_factory(reply, seen=None, boom=None):
    def factory(snap, cfg):
        if seen is not None:
            seen.append((snap, cfg))

        def agent(prompt):
            if boom:
                raise boom
            return reply
        return agent
    return factory


@pytest.fixture
def coach(tmp_path):
    c = Coach(Store(str(tmp_path / "t.db")), "1234")
    c.add_child("ada")
    c.unlock_topic("ada", "times_tables", "1234")
    c.unlock_topic("ada", "geography", "1234")
    return c


def test_parse_plan_keeps_only_available_topics_in_order():
    av = ["abacus", "geography", "times_tables"]
    assert parse_plan("times_tables, geography.", av) == ["times_tables", "geography"]
    assert parse_plan("I suggest ALGEBRA then abacus", av) == ["abacus"]        # algebra is not available
    assert parse_plan("abacus abacus abacus", av) == ["abacus"]
    assert parse_plan("rm -rf / ; drop table", av) == [] and parse_plan(None, av) == []
    assert len(parse_plan("abacus geography times_tables", av, limit=2)) == 2


def test_disabled_by_default_does_nothing(coach):
    p = TutorPlanner(mode="off", agent_factory=fake_factory("abacus"), executor=Sync())
    assert not p.refresh(coach, "default", "ada") and p.next_topic("default", "ada", ["abacus"]) is None


def test_plan_is_used_then_falls_back(coach):
    seen = []
    p = TutorPlanner(mode="strands", agent_factory=fake_factory("geography, times_tables", seen), executor=Sync())
    assert p.refresh(coach, "default", "ada")
    snap, cfg = seen[0]
    assert {r["topic"] for r in snap} == set(coach.available("ada", "default"))
    blob = json.dumps(snap)
    assert "ada" not in blob and "default" not in blob and cfg["model_id"].startswith("amazon.nova")  # no names/ids
    av = coach.available("ada", "default")
    assert p.next_topic("default", "ada", av) == "geography"
    assert p.next_topic("default", "ada", av) == "times_tables"
    assert p.next_topic("default", "ada", av) is None            # exhausted -> caller uses the rule-based choice


def test_plan_expires_and_is_not_refreshed_while_fresh(coach):
    t = [0.0]
    p = TutorPlanner(mode="strands", agent_factory=fake_factory("geography"), executor=Sync(), ttl_s=100, clock=lambda: t[0])
    assert p.refresh(coach, "default", "ada") and not p.refresh(coach, "default", "ada")
    t[0] = 101
    assert p.next_topic("default", "ada", ["geography"]) is None
    assert p.refresh(coach, "default", "ada")


def test_invalid_or_failing_agent_is_harmless(coach):
    for factory in (fake_factory("dance party"), fake_factory("", boom=RuntimeError("no creds")),
                    fake_factory("abacus", boom=TimeoutError())):
        p = TutorPlanner(mode="strands", agent_factory=factory, executor=Sync())
        p.refresh(coach, "default", "ada")
        assert p.next_topic("default", "ada", coach.available("ada", "default")) is None
        assert not p.pending


def test_single_topic_needs_no_agent(tmp_path):
    c = Coach(Store(str(tmp_path / "s.db")), "1234")
    c.add_child("ben")
    p = TutorPlanner(mode="strands", agent_factory=fake_factory("abacus"), executor=Sync())
    assert not p.refresh(c, "default", "ben")


def test_topic_locked_after_planning_is_skipped(coach):
    p = TutorPlanner(mode="strands", agent_factory=fake_factory("geography, times_tables"), executor=Sync())
    p.refresh(coach, "default", "ada")
    assert p.next_topic("default", "ada", ["abacus", "times_tables"]) == "times_tables"


def test_coach_uses_plan_only_when_nothing_is_due(coach):
    tutor.set_planner(TutorPlanner(mode="strands", agent_factory=fake_factory("geography"), executor=Sync()))
    coach.start_session("ada")                        # kicks off the (synchronous, fake) plan
    out = coach.next_question("ada")
    assert out["topic"] == "geography"
    # a due review beats the plan
    tutor.set_planner(TutorPlanner(mode="strands", agent_factory=fake_factory("geography"), executor=Sync()))
    coach.start_session("ada")
    import time
    from learning_coach import curriculum
    q = curriculum.make_question("times_tables", 1)
    coach.store.upsert_item("default", "ada", q, False, False, 1, time.time() - 10, time.time() - 100)
    assert coach.next_question("ada")["topic"] == "times_tables"


def test_coach_unchanged_when_tutor_off(coach):
    assert coach.next_question("ada")["topic"] in coach.available("ada", "default")


def test_real_strands_factory_import_error_is_contained(coach, monkeypatch):
    import builtins
    real = builtins.__import__

    def no_strands(name, *a, **k):
        if name.startswith("strands"):
            raise ImportError("strands not installed")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", no_strands)
    p = TutorPlanner(mode="strands", executor=Sync())     # default factory
    assert p.refresh(coach, "default", "ada")
    assert p.next_topic("default", "ada", coach.available("ada", "default")) is None


def test_env_config():
    p = TutorPlanner.from_env({"TUTOR_MODE": "strands", "BEDROCK_MODEL_ID": "us.amazon.nova-micro-v1:0", "AWS_REGION": "eu-west-1"})
    assert p.enabled and p.cfg["model_id"] == "us.amazon.nova-micro-v1:0" and p.cfg["region"] == "eu-west-1"
    assert not TutorPlanner.from_env({}).enabled


def test_real_strands_agent_loop_against_stubbed_bedrock(coach, monkeypatch):
    """Runs the REAL Strands Agent (tool call -> final answer) with botocore's Stubber standing in for Bedrock.
    Skipped unless `strands-agents` is installed (pip install -r requirements-aws.txt)."""
    pytest.importorskip("strands")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "x")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "x")
    from botocore.stub import Stubber

    built = {}

    def factory(snap, cfg):
        agent = tutor.default_agent_factory(snap, cfg)
        built["stub"] = stub = Stubber(agent.model.client)
        use = {"output": {"message": {"role": "assistant", "content": [
            {"toolUse": {"toolUseId": "t1", "name": "topic_snapshot", "input": {}}}]}}, "stopReason": "tool_use",
            "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}, "metrics": {"latencyMs": 1}}
        end = {"output": {"message": {"role": "assistant", "content": [{"text": "geography, times_tables"}]}},
               "stopReason": "end_turn", "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
               "metrics": {"latencyMs": 1}}
        stub.add_response("converse", use)
        stub.add_response("converse", end)
        stub.activate()
        return agent
    p = TutorPlanner(mode="strands", agent_factory=factory, executor=Sync())
    assert p.refresh(coach, "default", "ada")
    built["stub"].assert_no_pending_responses()
    assert p.next_topic("default", "ada", coach.available("ada", "default")) == "geography"
