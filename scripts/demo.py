"""Scripted demo: 3 abacus questions (difficulty steps up), then a parent unlocks algebra.
Runs fully offline against a temp database:  python scripts/demo.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from learning_coach.service import Coach  # noqa: E402
from learning_coach.store import Store  # noqa: E402

with tempfile.TemporaryDirectory() as d:
    store = Store(os.path.join(d, "demo.db"))
    coach = Coach(store, "4321")
    say = lambda who, text: print(f"{who:>7}: {text}")

    say("Child", "Alexa, let's practice")
    s = coach.start_session("Ada")
    say("Coach", "Hi Ada! You can practise: " + ", ".join(t["title"] for t in s["topics"]))
    for i in range(3):
        q = coach.next_question("Ada", "abacus")
        say("Coach", f"[level {q['level']}] {q['prompt']}")
        pending = store.peek_pending("ada")  # demo cheat: a real child would answer aloud
        r = coach.check_answer("Ada", pending["answer"])
        say("Ada", pending["answer"])
        say("Coach", ("Correct!" if r["correct"] else "Not quite.")
            + (f" Level up to {r['level']}!" if r["levelled_up"] else ""))
    say("Child", "Alexa, let's do algebra")
    say("Coach", coach.next_question("Ada", "algebra")["error"])
    say("Parent", "(wrong PIN first)")
    say("Coach", coach.unlock_topic("Ada", "algebra", "0000")["error"])
    say("Parent", "unlocks algebra with the PIN")
    say("Coach", str(coach.unlock_topic("Ada", "algebra", "4321")))
    q = coach.next_question("Ada", "algebra")
    say("Coach", f"[level {q['level']}] {q['prompt']}")
    print("\nProgress:", coach.get_progress("Ada")["progress"])
