"""Scripted offline demo (temp database, no network):  python scripts/demo.py [--fast]

Shows what the judges should see: families as tenants, a child spoken to in plain language, mishearings
("to" for "two"), hints, spaced repetition bringing a tricky item back, a session recap with badges, a parent
locking/unlocking topics, and the weekly report. Answers are simulated; the coach's replies are real output."""
import os
import random
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["SRS_SCALE"] = "0"  # demo: "come back in 5 minutes" becomes "right away"
from learning_coach import learning, reports  # noqa: E402
from learning_coach.service import Coach  # noqa: E402
from learning_coach.store import Store  # noqa: E402

FAST = "--fast" in sys.argv
learning.REVISIT_PROBABILITY = 1.0


def say(who, text):
    print(f"{who:>9}: {text}")
    if not FAST:
        time.sleep(0.4)


def spoken(n):
    return {1: "one", 2: "to", 3: "three", 4: "for", 5: "five", 6: "six", 7: "seven", 8: "ate"}.get(int(n), str(n))


with tempfile.TemporaryDirectory() as d:
    store = Store(os.path.join(d, "demo.db"))
    coach = Coach(store, "0000", rng=random.Random(11))
    okafor = coach.create_family("The Okafors", "4321", "mum@example.com", "Ada")
    smith = coach.create_family("The Smiths", "8765", "", "Ben")
    fa, fb = okafor["family_id"], smith["family_id"]
    coach.add_child("Tobi", family=fa)
    print("== Two families, one server. Each has its own PIN, TV token and Alexa link token. ==")
    print(f"   Okafors' tokens: {okafor['tv_token'][:12]}... / {okafor['link_token'][:12]}... (only hashes are stored)\n")

    say("Child", "Alexa, ask Learning Coach to start")
    r = coach.start_session("", family=fa)
    say("Coach", r["say"])
    say("Child", "It's Toby.")
    r = coach.start_session("Toby", family=fa)
    say("Coach", r["say"] + "   <- fuzzy name match, no profile screen needed")
    say("Ben", "(in the Smiths' house) Alexa, start with Ada")
    say("Coach", coach.start_session("Ada", family=fb)["say"] + "   <- tenants cannot see each other")

    print("\n== Practice: mishearings, hints, spaced repetition ==")
    coach.unlock_topic("tobi", "times_tables", "4321", family=fa)
    say("Child", "Let's do times tables")
    peek = lambda: store.peek_pending("tobi", fa)
    q = coach.next_question("tobi", "times tables", family=fa)
    say("Coach", q["say"])
    say("Tobi", "um... hint please")
    say("Coach", coach.check_answer("tobi", "hint please", family=fa)["say"])
    say("Tobi", spoken(int(peek()["answer"])) if int(peek()["answer"]) < 9 else peek()["answer"])
    r = coach.check_answer("tobi", spoken(int(peek()["answer"])) if int(peek()["answer"]) < 9 else peek()["answer"], family=fa)
    say("Coach", r["say"])
    q = coach.next_question("tobi", "times tables", family=fa)
    say("Coach", q["say"])
    say("Tobi", "I don't know")
    say("Coach", coach.check_answer("tobi", "I don't know", family=fa)["say"])
    for i in range(4):
        q = coach.next_question("tobi", "times tables", family=fa)
        if q["revisit"]:
            say("Coach", "(spaced repetition) " + q["say"])
        else:
            say("Coach", q["say"])
        r = coach.check_answer("tobi", peek()["answer"], family=fa)
        say("Tobi", "(answers correctly)")
        say("Coach", r["say"])

    print("\n== Voice quirks: speech recognition hears 'to' for 'two' ==")
    for _ in range(40):
        q = coach.next_question("tobi", "abacus", family=fa)
        if not q["revisit"] and int(peek()["answer"]) in (2, 4, 8) and "beads" in q["prompt"]:
            break
        coach.check_answer("tobi", peek()["answer"], family=fa)
    say("Coach", q["say"])
    heard = spoken(int(peek()["answer"]))
    say("Tobi", f"\"{heard}\"  (transcript)")
    say("Coach", coach.check_answer("tobi", f"it's {heard}", family=fa)["say"])
    say("Tobi", "I'm done")
    say("Coach", coach.check_answer("tobi", "I'm done", family=fa)["say"])

    print("\n== Parent: control and visibility ==")
    say("Parent", "locks Times tables (dashboard or by voice with PIN)")
    say("Coach", coach.lock_topic("tobi", "times_tables", "4321", family=fa)["say"])
    say("Tobi", "more times tables!")
    say("Coach", coach.next_question("tobi", "times_tables", family=fa)["say"])
    coach.set_child_limits("tobi", fa, daily_minutes=20, weekly_goal=40)
    print("   dashboard: run `python scripts/seed_demo.py` then open /parent/ for charts, badges, goals.\n")
    print(reports.family_report(coach, fa, "tobi")["text"])
