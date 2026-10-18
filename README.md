# Alexa+ Learning Coach

Amazon Developer Hackathon 2026, **Alexa+ track**. A voice-first practice coach for kids, built as a self-hosted
MCP server, with a parent dashboard and a REST API for a Fire TV companion app. It is designed as a product for
many families, not a single-household demo.

**What makes it a product**

| Area | What is built |
| --- | --- |
| Families as tenants | Sign-up, per-family PIN, per-family Alexa link token and TV token (hashed at rest, rotatable), several children per family, spoken-name matching ("Toby" finds Tobi), strict tenant isolation (tested over real MCP and REST). The env vars `PARENT_PIN`, `MCP_AUTH_TOKEN`, `TV_API_TOKEN` keep working as a single-family fallback (`default` family). |
| Learning science | Leitner spaced repetition (tricky items return later), per-skill mastery, adaptive difficulty inside a level ("stretch" from recent accuracy) on top of level up/down, progressive hints with partial credit, varied encouraging feedback. |
| Content | 8 topics: abacus, algebra, geography (60 countries, 4 levels), times tables, fractions, spelling bee, reading stories, science. Validated by tests and a script. |
| Voice UX | Every reply has `say` (plain, TTS-safe) and `ssml`; "ask again" on unintelligible answers; number words, homophones ("to" is 2), fractions ("three quarters"), spelled letters ("c a t"), fillers ("it's..."), fuzzy names; intents: hint, repeat, don't know, stop; end-of-session recap. |
| Parent value | Dashboard with inline-SVG charts (14-day activity, skill mastery, goal ring), streaks, badges, weekly goals, daily time limits, topic lock/unlock, weekly report (email-ready HTML + text; sending stubbed), data export and delete. |
| AWS AI stack (optional) | **Amazon Bedrock** (Converse + Guardrails): second opinion on free-text answers, varied explanations, weekly report narrative, all with rule-based fallbacks, validation, caps and no child PII in prompts. **Strands Agents** tutor that plans topics from mastery data. **AgentCore Runtime** packaging for the MCP server. CloudFormation for App Runner + Secrets Manager + guardrail + least-privilege IAM + CloudWatch alarms. SageMaker, AgentCore Memory/Gateway deliberately not used. Everything is off by default; see [docs/AWS_STACK.md](docs/AWS_STACK.md). |
| Hardening | Rate limiting, JSON logs with no child data, `/metrics` counters, secrets from env or AWS Secrets Manager (unverified), versioned migrations, Postgres 16 tested locally, per-family PIN lockout, CSP and security headers. |

## Run

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
PARENT_PIN=1234 .venv/bin/python -m learning_coach.server
#  MCP          http://localhost:8000/mcp/
#  Parent page  http://localhost:8000/parent/   (sign up a family, or sign in with a blank family code + PIN 1234)
#  Alexa+ simulator http://localhost:8000/alexa/  (a child-facing Alexa+ screen; every turn is a real MCP call to /mcp)
#  Health       http://localhost:8000/healthz     Metrics http://localhost:8000/metrics
.venv/bin/python -m pytest -q                       # ~240 tests; 2 Postgres tests and 1 Strands test skip unless enabled (below)
.venv/bin/python scripts/demo.py                    # offline scripted demo (shows every feature)
.venv/bin/python scripts/seed_demo.py --db demo.db  # 2 weeks of synthetic history for dashboard screenshots
.venv/bin/python -m learning_coach.validate         # content validation
.venv/bin/python -m learning_coach.reports --family demo --html > report.html
.venv/bin/python -m learning_coach.migrate --status
```

AWS features offline: `pip install -r requirements-dev.txt` covers boto3 (tests use botocore's Stubber, no credentials). The real Strands
loop test runs when `pip install -r requirements-aws.txt` is done (keep `mcp<2`).

Postgres tests: `TEST_PG_ADMIN_URL=postgresql://user@127.0.0.1:5432/postgres .venv/bin/python -m pytest tests/test_postgres.py`
(creates and drops a throwaway database; needs `psycopg[binary]`).

## Configuration

| Env | Meaning |
| --- | --- |
| `DATABASE_URL` / `PROGRESS_DB` | Postgres URL (needs `psycopg[binary]`) or SQLite path (default `progress.db`) |
| `PARENT_PIN` | PIN of the `default` family (4-8 digits). Unset: a random one is printed once. |
| `MCP_AUTH_TOKEN`, `TV_API_TOKEN` | Single-family fallback bearer tokens (map to `default`). Families get their own tokens at sign-up. |
| `REQUIRE_MCP_AUTH=1` | Reject anonymous `/mcp` even when `MCP_AUTH_TOKEN` is unset (recommended for any public deployment) |
| `ALLOW_SIGNUP=0` | Disable self-service family sign-up |
| `SESSION_SECRET`, `COOKIE_SECURE=1` | Dashboard cookie signing / Secure flag |
| `RATE_LIMIT_PER_MIN` (240), `AUTH_RATE_LIMIT_PER_MIN` (10), `TRUST_PROXY=1` | Per-IP limits (in-memory, per process); trust `X-Forwarded-For` behind a proxy |
| `METRICS_TOKEN` | Bearer token for `/metrics` |
| `LOG_LEVEL` | JSON logs to stderr |
| `SRS_SCALE` | Multiplier for spaced-repetition intervals (demo/testing) |
| `REPORT_MODE=smtp`, `REPORT_SMTP_HOST/PORT/USER/PASSWORD`, `REPORT_FROM` | Real report sending (**unverified**); default is a stub |
| `AWS_SECRETS_ID` | Read secrets (same names as the env vars) from an AWS Secrets Manager JSON secret (**unverified**, needs `boto3`) |
| `TV_CORS_ORIGINS` | Allowed browser origins for the TV REST API |
| `BEDROCK_ENABLED=1`, `BEDROCK_MODEL_ID`, `BEDROCK_REGION`, `BEDROCK_GUARDRAIL_ID`/`_VERSION`, `BEDROCK_FEATURES`, `BEDROCK_TIMEOUT_S`, `BEDROCK_DAILY_CALLS_PER_FAMILY`/`_GLOBAL` | Optional Amazon Bedrock features (**unverified live**, default off) |
| `TUTOR_MODE=strands`, `TUTOR_MODEL_ID` | Optional Strands Agents topic planner (needs `requirements-aws.txt`) |
| `EMF_NAMESPACE` | Emit CloudWatch Embedded Metric Format lines for Bedrock calls |
| `MCP_STATELESS=1`, `MCP_ONLY=1`, `MCP_LINK_TOKEN_HEADER` | AgentCore Runtime mode: stateless MCP, MCP only, family token from a custom header |

## MCP tools

The bearer token identifies the family; no tool takes a family argument. `child` is optional when a family has one child.
Every tool returns `say` (speak this) and `ssml` where useful.

| Tool | What it does |
| --- | --- |
| `list_children()` | Roster, so the agent can ask "who is practising?" |
| `start_session(child?)` | Fuzzy-matched child, streak, topics, reviews waiting. `need_child: true` means "ask which child". |
| `next_question(child?, topic?)` | Empty topic: the coach picks (due reviews first). Spoken topics work ("times tables", "capitals"). Revisits tricky items (spaced repetition). Respects locks and daily time limit. |
| `check_answer(answer, child?)` | Forgiving grading; adapts level and difficulty; `reprompt: true` = ask again; understands "hint", "again", "I don't know", "I'm done" |
| `get_hint(child?)`, `repeat_question(child?)` | Progressive hints (partial credit), repeat the question |
| `end_session(child?)` | Spoken recap: questions, accuracy, topics, streak, badges |
| `get_progress(child?)` | Parent summary: skills, badges, streak, week |
| `unlock_topic(topic, parent_pin, child?)`, `lock_topic(...)` | Parent-only, needs the family PIN (5 misses lock that family out for 5 minutes) |

## Fire TV REST API (backward compatible)

Unchanged contract, now per family (the family's TV token selects the tenant). Defined by us, not by Amazon.

| Request | Effect |
| --- | --- |
| `GET /api/progress/{child}` | `{child, progress:{topic:{level,correct,attempts,streak,updatedAt}}, available:[...], promoteAfter}` (unlocked topics only) |
| `POST /api/progress/{child}/events` body `{"events":[{"id","topic","correct","at"}]}` | Applies answers with the coach's rules; idempotent per event id; returns the same shape + `{applied, duplicates, rejected}` |

New: an unknown child of a non-default family answers 404 (the default family still creates children on first use, as before). TV answers now also feed the parent dashboard and streaks.
**Curriculum note for the TV app:** new topics use new question kinds (`times_table`, `division_facts`, `half_of`,
`fraction_of`, `add_fractions`, `simplify_fraction`, `items`), see `../shared-curriculum/README.md`. A TV build that only knows the
old kinds must skip topics it cannot generate. Geography gained a level 4 and ~45 countries (old levels/kinds unchanged).

## Architecture

```
Alexa+ --MCP/HTTP--> server.py (per-request family from bearer token) --> service.py (Coach) --> store.py --> SQLite | Postgres
Parent browser ----> web.py (signed cookie, family scoped) ------------/        |-- voice.py (matching, feedback, SSML)
Fire TV app ------> tvapi.py (per-family token) ------------------------/       |-- learning.py (SRS, mastery, badges, time)
                                         ops.py: rate limit, JSON logs, metrics, EMF, secrets     reports.py: weekly report
                                         bedrock.py: optional Converse features (guardrail, caps, validation, fallbacks)
                                         tutor.py: optional Strands topic planner (background, validated)
```

## Deploy, privacy, and connecting to Alexa+

- [docs/DEPLOY.md](docs/DEPLOY.md): Docker, App Runner, Postgres, secrets, operations. Manual steps are marked **[MANUAL]**.
- [docs/AWS_STACK.md](docs/AWS_STACK.md): which AWS AI services are used (Bedrock, Guardrails, Strands, AgentCore packaging) and which are deliberately not (SageMaker, AgentCore Memory/Gateway), with sources and a verified/unverified ledger. IaC: `infra/aws-stack.yaml`, `infra/agentcore-runtime.yaml`.
- [docs/PRIVACY.md](docs/PRIVACY.md): what data is stored, COPPA/GDPR-minded design notes (not legal advice).
- Connecting to Alexa+ **[MANUAL] [TODO-verify]**: how Alexa+ registers third-party MCP servers, its auth options and how a child/profile identity reaches a tool were not verified. The design assumes a bearer token per family on `/mcp`; `list_children` and `need_child` exist because a profile id may not be passed. Test with `npx @modelcontextprotocol/inspector`.

## Honest limits

Not verified against a real Alexa+ device; the dashboard was checked through HTTP tests and HTML review, not on real phones; email sending, AWS Secrets Manager, App Runner, Bedrock, Strands-on-Bedrock and AgentCore are unverified against live AWS (no credentials were available; offline stubs and lint only); rate limits are per process; single Postgres connection per process; spaced-repetition intervals and mastery weights are reasonable defaults, not tuned on real children. Details in docs/DEPLOY.md.

Submission material: [docs/VIDEO_SCRIPT.md](docs/VIDEO_SCRIPT.md), [docs/AMAZON_FEEDBACK.md](docs/AMAZON_FEEDBACK.md). Content: `learning_coach/curriculum.json` is a copy of `../shared-curriculum/curriculum.json` (a test checks they match).

License: MIT
