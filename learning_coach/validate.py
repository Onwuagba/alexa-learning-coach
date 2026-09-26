"""Content validation: exercise every level of every topic many times. `python -m learning_coach.validate`.
A generated question must (1) have a non-empty prompt, (2) accept its own answer spoken plainly, (3) reject an
obviously wrong answer, (4) carry a skill and item key, (5) have answers that TTS can say."""
from __future__ import annotations

import random
import sys

from . import curriculum, voice


def validate(samples: int = 200, seed: int = 0) -> list[str]:
    rng, errs = random.Random(seed), []
    for topic, spec in curriculum.TOPICS.items():
        for level in range(1, curriculum.max_level(topic) + 1):
            for _ in range(samples):
                q = curriculum.make_question(topic, level, rng, stretch=rng.random())
                where = f"{topic} L{level} {q.prompt!r}"
                if not q.prompt.strip() or not q.answer.strip():
                    errs.append(f"{where}: empty prompt/answer")
                elif not q.check(q.answer):
                    errs.append(f"{where}: rejects its own answer {q.answer!r}")
                elif q.check("zzz qqq nonsense"):
                    errs.append(f"{where}: accepts nonsense")
                if not q.skill or not q.item_key:
                    errs.append(f"{where}: missing skill/item_key")
                if voice.speakable(q.prompt) != " ".join(q.prompt.split()):
                    errs.append(f"{where}: prompt has characters TTS may mangle")
                for a in q.accept:
                    if not q.check(a):
                        errs.append(f"{where}: accept {a!r} rejected")
    for c in curriculum.DATA["countries"]:
        if not c["capital"] or c["region"] not in ("africa", "world"):
            errs.append(f"bad country {c}")
    return sorted(set(errs))


if __name__ == "__main__":
    problems = validate()
    print("\n".join(problems) or "content OK")
    sys.exit(1 if problems else 0)
