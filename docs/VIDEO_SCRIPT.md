# 3-minute demo video script (DRAFT, v2)

Total ~3:00. Screen: split view. Left: Alexa device/app or the terminal running `python scripts/demo.py`. Right: the parent dashboard
seeded with `python scripts/seed_demo.py --db demo.db` (sign in with family code `demo`, PIN `4321`) and the Fire TV app.
**[TODO-verify]** On-device Alexa+ footage depends on your integration working; otherwise say plainly that Alexa is simulated by an MCP client and show `scripts/demo.py`.

**0:00-0:15 Hook (idea + impact).** "Kids ask Alexa for everything, but nobody checks whether they are actually learning. Learning Coach turns Alexa+ into a patient tutor that remembers every child, revisits what they struggle with, and tells parents what happened this week."

**0:15-0:40 Families, not a demo.** Two family sign-ups on screen. Point at: each family gets its own PIN, an Alexa link token and a Fire TV token (shown once, hashed at rest). Say "many households, one server, strict isolation". Quick cut: the Smiths ask for "Ada" and are refused (Coach: "I don't know Ada yet. Is it Ben?").

**0:40-1:30 The voice experience (the core).**
- "Alexa, ask Learning Coach to start." Coach: "Who is practising? Is it Ada or Tobi?" Child: "Toby." Matched by sound.
- "Let's do times tables." Question. Child: "hint please." Coach gives a hint. Child answers "for" (STT for four): accepted. Show partial credit for hinted answers.
- "I don't know." Coach: "That's okay. The answer is 120." Two questions later: "Here's one to remember" (spaced repetition brings it back).
- Fractions: child says "three quarters"; spelling: child spells "c a t".
- "I'm done." Spoken recap with questions, accuracy, streak and a badge.

**1:30-2:10 The parent dashboard.** Seeded dashboard: 14-day activity chart, skill mastery bars, streak, goal ring, badges, "Tricky ones coming back". Lock Times tables with the PIN; the child is told to ask a parent. Set a 20-minute daily limit. Open the weekly report (HTML email view) and note that sending is stubbed until SMTP is configured.

**2:10-2:30 Fire TV link.** Same child on the TV app (web target) shows the level just earned by voice; a TV answer shows up in the dashboard. Backward-compatible REST API with per-family tokens.

**2:30-2:50 Under the hood (tech).** One diagram: Alexa+ -> MCP over streamable HTTP -> family from bearer token -> service -> SQLite/Postgres (tested on Postgres 16). Mention: shared curriculum JSON with 8 topics and 60 countries, forgiving speech matching, spaced repetition, migrations, rate limiting, metrics, ~180 tests. Say "hosted on AWS App Runner" only if actually deployed.

**2:50-3:00 Close + honesty.** "Voice-first practice that grows with the child and keeps parents in control. Privacy by design: nicknames only, export and delete in one click." Show repo + MIT license. Mention what is simulated.

Recording checklist **[MANUAL]**: record voiceover; confirm length and rules on the hackathon page; capture the dashboard in both light and dark mode; make sure no real tokens are on screen (regenerate with Rotate tokens after filming).
