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

**2:30-2:50 Under the hood (tech, the AWS stack).** One diagram: Alexa+ -> MCP over streamable HTTP -> family from bearer token -> service -> SQLite/Postgres (tested on Postgres 16). Then the AWS layer, say only what you actually ran:
- "Amazon Bedrock, through the Converse API with a Guardrail, gives a second opinion when a child answers in words the rules do not match, adds a one-line explanation of why, and writes the parent's weekly paragraph. Numbers and spelling never go to a model, prompts carry no names, and every call has a timeout, a daily cap and a rule-based fallback." Show the report with "Written with Amazon Bedrock" and `/metrics` `bedrock_calls_total`. **[TODO-verify]** record this only after a real call worked; otherwise say it is implemented and tested against stubs.
- "A Strands Agents tutor plans what to practise next from mastery data." Show the planner log/metric `tutor_plans_total` only if run for real.
- "The MCP server is packaged for AgentCore Runtime (ARM64, stateless), and the primary deployment is App Runner with Secrets Manager and a least-privilege role from CloudFormation." Show `infra/aws-stack.yaml`. We chose not to use SageMaker or AgentCore Memory, because there is no genuine model or memory need: say that in one sentence, it is a strength.
Mention: 8 topics and 60 countries, forgiving speech matching, spaced repetition, migrations, rate limiting, metrics, ~240 tests. Say "hosted on AWS App Runner" only if actually deployed.

**2:50-3:00 Close + honesty.** "Voice-first practice that grows with the child and keeps parents in control. Privacy by design: nicknames only, export and delete in one click." Show repo + MIT license. Mention what is simulated.

Recording checklist **[MANUAL]**: record voiceover; confirm length and rules on the hackathon page; capture the dashboard in both light and dark mode; make sure no real tokens, account ids or guardrail ids are on screen; keep Bedrock model-invocation logging off while filming (regenerate with Rotate tokens after filming).
