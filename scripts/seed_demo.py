"""Seed a database with two weeks of realistic practice so the parent dashboard and weekly report look alive
(for screenshots / the demo video).

  python scripts/seed_demo.py [--db demo.db]
  PROGRESS_DB=demo.db PARENT_PIN=1234 python -m learning_coach.server   # sign in: family code `demo`, PIN 4321

Data is synthetic. Uses the real store/service code paths, then back-dates the attempts."""
import argparse
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from learning_coach import curriculum, learning  # noqa: E402
from learning_coach.service import Coach  # noqa: E402
from learning_coach.store import Store  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--db", default="demo.db")
a = p.parse_args()
rng = random.Random(42)
store = Store(a.db)
coach = Coach(store, os.getenv("PARENT_PIN") or "1234", rng=rng)
if store.family_by("slug", "demo"):
    sys.exit("demo family already exists in this database")
fam = coach.create_family("Demo", "4321", "parent@example.com", "Ada")
fid = fam["family_id"]
coach.add_child("Tobi", family=fid)
now = time.time()
plan = {"ada": (["abacus", "times_tables", "fractions", "spelling"], 0.78, 13), "tobi": (["abacus", "geography", "science"], 0.62, 8)}
for child, (topics, skill, days) in plan.items():
    for t in topics[1:]:
        store.unlock(child, t, fid)
    coach.set_child_limits(child, fid, daily_minutes=30, weekly_goal=40)
    for d in range(days, -1, -1):
        if rng.random() < 0.15 and d > 1:
            continue  # a missed day
        t0 = now - d * 86400 - rng.randint(0, 3600)
        for i in range(rng.randint(5, 14)):
            topic = rng.choice(topics)
            q = curriculum.make_question(topic, store.row(child, topic, fid)["level"], rng)
            ok = rng.random() < skill + (days - d) * 0.01
            ts = t0 + i * rng.randint(15, 50)
            lvl = store.record(child, topic, ok, curriculum.max_level(topic), fid, ts)
            box = learning.next_box(1, ok, False)
            store.upsert_item(fid, child, q, ok, False, box, ts + 3600, ts)
            store.update_skill(fid, child, q.skill, 1.0 if ok else 0.0)
            store.log_attempt(fid, child, topic, q.skill, q.item_key, ok, False, ts)
        learning.evaluate_badges(store, fid, child, t0 + 3000, 0)
print("seeded: family code `demo`, PIN 4321, children ada + tobi")
