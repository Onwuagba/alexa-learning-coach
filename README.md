# Alexa+ Learning Coach

Amazon Developer Hackathon 2026 — **Alexa+ track**. A voice practice coach for kids
(abacus, algebra, geography) built as a self-hosted MCP server. Difficulty adapts: two right
answers in a row levels up, two wrong levels down. Parents unlock new topics with a PIN.

## Run

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
PARENT_PIN=1234 .venv/bin/python -m learning_coach.server
#  MCP          http://localhost:8000/mcp/
#  Parent page  http://localhost:8000/parent/
#  Health       http://localhost:8000/healthz
.venv/bin/python -m pytest -q
.venv/bin/python scripts/demo.py     # offline scripted demo
```

Env: `PORT`, `DATABASE_URL` (Postgres URL, needs `psycopg[binary]`) or `PROGRESS_DB` (SQLite path, default),
`PARENT_PIN` (4-8 digits; if unset a random one is printed once and its hash stored),
`MCP_AUTH_TOKEN` (optional; require `Authorization: Bearer ...` on `/mcp`), `SESSION_SECRET`, `COOKIE_SECURE=1`.

## Tools

| Tool | What it does |
| --- | --- |
| `start_session(child)` | Topics the child can practise + current level |
| `next_question(child, topic)` | Generates a question; Alexa+ reads `prompt` aloud |
| `check_answer(child, answer)` | Grades, adapts level (2 right = up, 2 wrong = down) |
| `get_progress(child)` | Parent summary |
| `unlock_topic(child, topic, parent_pin)` | Parent-only unlock |

Hardening: child names validated/normalised, topics whitelisted, answer length capped, questions single-use and
expire after 15 min, PIN stored as scrypt hash, 5 wrong PINs locks unlock attempts for 5 min (global, not per child),
per-child isolation. The parent PIN is one PIN per deployment (a household), not per family.

## Architecture

`service.py` (logic) is used by `server.py` (MCP tools) and `web.py` (parent page). `store.py` is one SQL store
for SQLite (default) and Postgres (written, **not tested against a real Postgres**).

## Deploy and connect to Alexa+

See [docs/DEPLOY.md](docs/DEPLOY.md) (Docker, ECR, App Runner CloudFormation in `infra/`). Manual steps are marked **[MANUAL]**.

Connecting to Alexa+ **[MANUAL] [TODO-verify]**: I could not verify how Alexa+ registers third-party MCP servers.
Follow the hackathon Resources page ("Build with Agent Skills"). Expected inputs: your public HTTPS URL
`https://<host>/mcp/` and, if required, the bearer token set in `MCP_AUTH_TOKEN`. Confirm supported auth
(bearer vs OAuth) and how a child/profile id is passed. Meanwhile test with `npx @modelcontextprotocol/inspector`.

## Submission material

[docs/VIDEO_SCRIPT.md](docs/VIDEO_SCRIPT.md), [docs/AMAZON_FEEDBACK.md](docs/AMAZON_FEEDBACK.md) (drafts).

Content comes from `learning_coach/curriculum.json` — a copy of `shared-curriculum/curriculum.json`.

License: MIT
