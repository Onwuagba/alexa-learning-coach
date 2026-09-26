# 3-minute demo video script (DRAFT)

Total ~3:00. Screen: terminal + parent web page + Alexa device/app. **[TODO-verify]** any on-device Alexa+ footage depends on your integration working; fall back to `python scripts/demo.py` on screen.

**0:00-0:20 Hook.** "Kids ask Alexa for everything, but practice with feedback? Meet Learning Coach: 'Alexa, let's practice.'" Show a child speaking to the device.

**0:20-1:20 Child flow.** Child: "Let's practice abacus." Coach asks level 1 ("I slide 4 beads across. How many?"). Correct twice -> "Level up!" -> level 2 question ("What is 3 plus 3?"). Point out: adapts, two right = up, two wrong = down; forgiving answer matching.

**1:20-1:45 Locked topic.** Child asks for algebra: "Ask a parent to unlock it."

**1:45-2:20 Parent flow.** Open `/parent/`, sign in with PIN, show progress table (levels, accuracy), unlock algebra for Ada. Mention hashed PIN, lockout after 5 misses.

**2:20-2:35 Fire TV link.** Show the Fire TV app (web target) next to the terminal: the child's level on the TV matches what was just practised by voice. Live: `python scripts/demo_tv.py --answers 2` while the TV app polls. [TODO-verify] say it is a simulated Alexa+ unless real Alexa+ footage is used.

**2:35-2:50 Under the hood.** Diagram: Alexa+ -> MCP over streamable HTTP -> FastMCP server -> SQLite/Postgres; shared curriculum JSON with the Fire TV games. Hosted on AWS App Runner (say only if deployed).

**2:50-3:00 Close.** "Voice-first practice that grows with the child and keeps parents in control." Repo + MIT license.

Recording checklist: [MANUAL] record voiceover, confirm final length under limit, check hackathon rules for video requirements.
