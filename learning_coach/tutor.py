"""Optional Strands Agents tutor: picks which topics to work on next from mastery data. OFF by default.

  TUTOR_MODE=strands   enable (needs `pip install strands-agents`; not in requirements.txt on purpose)
  TUTOR_MODEL_ID       model for the tutor (default: BEDROCK_MODEL_ID / amazon.nova-micro-v1:0)
  TUTOR_TIMEOUT_S      give up on a plan after this long (default 12); voice never waits for it
  TUTOR_PLAN_TTL_S     how long a plan stays valid (default 1800)

How it fits: spaced repetition (learning.py) still decides what is DUE. Only when nothing is due does
`Coach.suggest_topic` ask this module for a topic, instead of "the least recently practised one". The plan is
computed in the background when a session starts (`start_session`), because an agent loop with tool calls takes
seconds and a spoken turn must not wait. `planned_topic` is a cache lookup and never blocks. Every plan is
validated against the topics the child may actually use; anything else is discarded.

Privacy: the agent's tool returns topic ids, levels and 0/1 recent results only. No names, no ids, no answers.

Honest note: this beats "least recently practised" only when mastery differs a lot between topics; it is a real
agent (tools + Bedrock model via Strands) but a modest gain. It is kept optional. Not run against Bedrock here.
"""
from __future__ import annotations

import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import curriculum
from .ops import METRICS, log_event

SYSTEM_PROMPT = (
    "You are a learning coach choosing what a child should practise next. Call the topic_snapshot tool once. "
    "Prefer topics with low recent accuracy that are not at their top level, then topics not practised for a long "
    "time; avoid repeating the same topic. Reply with ONLY up to three topic ids from the snapshot, most useful "
    "first, separated by commas. No other words.")


def parse_plan(text: str, available: list[str], limit: int = 3) -> list[str]:
    """Topic ids in the reply that the child may use, in order, no repeats. Garbage in -> []."""
    out: list[str] = []
    for tok in re.findall(r"[a-z_]+", str(text or "").lower()):
        if tok in available and tok not in out:
            out.append(tok)
        if len(out) >= limit:
            break
    return out


def snapshot(coach, family: str, who: str, available: list[str]) -> list[dict]:
    """Plain data for the agent. Built on the caller's thread (DB access), then handed over."""
    rows = []
    now_ms = time.time() * 1000
    for t in available:
        r = coach.store.row(who, t, family)
        res = coach.store.recent_results(family, who, t)
        rows.append({"topic": t, "level": r["level"], "max_level": curriculum.max_level(t),
                     "recent_results_newest_first_1_is_correct": list(res),
                     "days_since_practised": round((now_ms - r["updated_ms"]) / 86_400_000, 1) if r["updated_ms"] else None})
    return rows


def default_agent_factory(snap: list[dict], cfg: dict):
    """Build a real Strands agent. Imports lazily so the package stays optional.
    API as inspected in strands-agents 1.57.1: Agent(model=, tools=, system_prompt=, callback_handler=),
    BedrockModel(model_id=, region_name=, boto_client_config=, temperature=, max_tokens=, guardrail_id=,
    guardrail_version=), `from strands import tool`."""
    from botocore.config import Config
    from strands import Agent, tool
    from strands.models import BedrockModel

    @tool
    def topic_snapshot() -> list:
        """Per-topic practice snapshot for the child: level, max level, recent results (newest first, 1 = correct) and days since practised."""
        return snap

    kw = dict(model_id=cfg["model_id"], region_name=cfg["region"], temperature=0.2, max_tokens=60, streaming=False,
              boto_client_config=Config(read_timeout=cfg["timeout_s"], connect_timeout=3,
                                        retries={"max_attempts": 1, "mode": "standard"}))
    if cfg.get("guardrail_id"):
        kw.update(guardrail_id=cfg["guardrail_id"], guardrail_version=cfg.get("guardrail_version", "DRAFT"))
    return Agent(model=BedrockModel(**kw), tools=[topic_snapshot], system_prompt=SYSTEM_PROMPT,
                 callback_handler=None)


class TutorPlanner:
    def __init__(self, mode: str = "off", agent_factory=None, ttl_s: float = 1800, timeout_s: float = 12.0,
                 model_id: str = "amazon.nova-micro-v1:0", region: str = "us-east-1", guardrail_id: str = "",
                 guardrail_version: str = "DRAFT", executor=None, clock=time.time):
        self.mode, self.factory, self.ttl, self.timeout = mode, agent_factory or default_agent_factory, ttl_s, timeout_s
        self.cfg = {"model_id": model_id, "region": region, "timeout_s": timeout_s,
                    "guardrail_id": guardrail_id, "guardrail_version": guardrail_version}
        self.clock, self.lock = clock, threading.Lock()
        self.plans: dict[tuple[str, str], tuple[float, list[str]]] = {}
        self.pending: set[tuple[str, str]] = set()
        self.executor = executor or ThreadPoolExecutor(max_workers=2, thread_name_prefix="tutor")

    @classmethod
    def from_env(cls, env=None) -> "TutorPlanner":
        e = os.environ if env is None else env
        mode = "strands" if e.get("TUTOR_MODE", "").lower() == "strands" else "off"
        model = e.get("TUTOR_MODEL_ID") or e.get("BEDROCK_MODEL_ID") or "amazon.nova-micro-v1:0"
        return cls(mode=mode, model_id=model,
                   region=e.get("BEDROCK_REGION") or e.get("AWS_REGION") or e.get("AWS_DEFAULT_REGION") or "us-east-1",
                   guardrail_id=e.get("BEDROCK_GUARDRAIL_ID", ""), guardrail_version=e.get("BEDROCK_GUARDRAIL_VERSION", "DRAFT"),
                   ttl_s=float(e.get("TUTOR_PLAN_TTL_S", "1800")), timeout_s=float(e.get("TUTOR_TIMEOUT_S", "12")))

    @property
    def enabled(self) -> bool:
        return self.mode == "strands"

    def _run(self, key, snap, available) -> None:
        outcome = "error"
        try:
            agent = self.factory(snap, self.cfg)
            plan = parse_plan(str(agent("Which topics should the child practise next?")), available)
            outcome = "ok" if plan else "invalid"
            if plan:
                with self.lock:
                    self.plans[key] = (self.clock(), plan)
        except Exception as e:  # missing strands, no credentials, throttling, model error...
            log_event("tutor_plan_failed", error=type(e).__name__)
        finally:
            with self.lock:
                self.pending.discard(key)
            METRICS.inc("tutor_plans_total", outcome=outcome)

    def refresh(self, coach, family: str, who: str) -> bool:
        """Start a background plan unless one is fresh or running. Returns True if started."""
        if not self.enabled:
            return False
        key = (family, who)
        with self.lock:
            fresh = key in self.plans and self.clock() - self.plans[key][0] < self.ttl
            if fresh or key in self.pending:
                return False
            self.pending.add(key)
        try:
            available = coach.available(who, family)
            if len(available) < 2:  # nothing to choose between
                with self.lock:
                    self.pending.discard(key)
                return False
            snap = snapshot(coach, family, who, available)
            self.executor.submit(self._run, key, snap, available)
            return True
        except Exception as e:
            with self.lock:
                self.pending.discard(key)
            log_event("tutor_refresh_failed", error=type(e).__name__)
            return False

    def next_topic(self, family: str, who: str, available: list[str]) -> str | None:
        """Pop the next planned topic that is still available. Non-blocking."""
        key = (family, who)
        with self.lock:
            entry = self.plans.get(key)
            if not entry or self.clock() - entry[0] >= self.ttl:
                self.plans.pop(key, None)
                return None
            ts, plan = entry
            while plan:
                t = plan.pop(0)
                if t in available:
                    METRICS.inc("tutor_topics_used_total")
                    return t
            self.plans.pop(key, None)
            return None


_PLANNER: TutorPlanner | None = None


def get_planner() -> TutorPlanner:
    global _PLANNER
    if _PLANNER is None:
        _PLANNER = TutorPlanner.from_env()
    return _PLANNER


def set_planner(p: TutorPlanner | None) -> None:
    global _PLANNER
    _PLANNER = p


def refresh_async(coach, family: str, who: str) -> bool:
    return get_planner().refresh(coach, family, who)


def planned_topic(family: str, who: str, available: list[str], now: float | None = None) -> str | None:
    p = get_planner()
    return p.next_topic(family, who, available) if p.enabled else None
