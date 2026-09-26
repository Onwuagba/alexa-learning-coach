# Deploy and operate

Legend: **[MANUAL]** you must do it (needs your AWS/Amazon account). **[TODO-verify]** not checked against current AWS/Amazon docs.
**[VERIFIED]** actually run while building this.

## What was verified here
- **[VERIFIED]** Full test suite on SQLite (Python 3.11, `pytest`).
- **[VERIFIED]** Store, service, TV events, reports, export/delete and the legacy-database migration against a **real local PostgreSQL 16.12** (Homebrew, throwaway databases): `TEST_PG_ADMIN_URL=postgresql://user@127.0.0.1:PORT/postgres pytest tests/test_postgres.py`. One connection per process, autocommit; not load tested.
- **[VERIFIED]** Two families over the real MCP streamable-HTTP transport (token selects the tenant, cross-family access is refused).
- **Not run:** Docker build, App Runner / CloudFormation, ECR, AWS Secrets Manager, SMTP/SES, a real Alexa+ device.

## Local, production-like
```bash
docker build -t lc .            # [not run while writing this]
docker run -p 8000:8000 -e PARENT_PIN=1234 -e REQUIRE_MCP_AUTH=1 -e MCP_AUTH_TOKEN=$(openssl rand -hex 24) lc
```
Database: SQLite file by default (`PROGRESS_DB`), or `DATABASE_URL=postgresql://...`. Migrations run automatically at start;
`python -m learning_coach.migrate --status` shows the version (currently 2: v1 legacy tables, v2 families and learning data;
an old single-household database is moved into the `default` family, keeping its PIN hash, progress and TV event ids).

## AWS App Runner
1. **[MANUAL]** AWS account, AWS CLI, `aws configure`.
2. **[MANUAL]** Postgres (RDS or a host such as Neon). App Runner has no persistent disk, so SQLite would reset. Use `sslmode=require` in the URL.
3. Build and push (adjust region/account):
   ```bash
   aws ecr create-repository --repository-name learning-coach
   aws ecr get-login-password | docker login --username AWS --password-stdin <acct>.dkr.ecr.<region>.amazonaws.com
   docker build -t learning-coach . && docker tag learning-coach <acct>.dkr.ecr.<region>.amazonaws.com/learning-coach:latest
   docker push <acct>.dkr.ecr.<region>.amazonaws.com/learning-coach:latest
   ```
4. **[MANUAL] [TODO-verify]** IAM role App Runner assumes to pull ECR (`AWSAppRunnerServicePolicyForECRAccess`).
5. **[TODO-verify]** Deploy `infra/apprunner.yaml` (CloudFormation, never validated):
   ```bash
   aws cloudformation deploy --stack-name learning-coach --template-file infra/apprunner.yaml \
     --parameter-overrides ImageUri=... EcrAccessRoleArn=... DatabaseUrl=... ParentPin=... \
       McpAuthToken=$(openssl rand -hex 24) SessionSecret=$(openssl rand -hex 32)
   ```
6. Check: `/healthz` returns `ok` (503 if the DB is down), `/parent/` shows sign-in and sign-up, `/mcp/` answers 401 without a bearer token, `/metrics`.

## Secrets
Plain environment variables work everywhere. Optionally set `AWS_SECRETS_ID` to a Secrets Manager secret holding JSON
`{"PARENT_PIN": "...", "MCP_AUTH_TOKEN": "...", "TV_API_TOKEN": "...", "METRICS_TOKEN": "...", "REPORT_SMTP_PASSWORD": "..."}`;
lookups fall back to it when the env var is unset (`learning_coach/ops.py: get_secret`). **Unverified**: never run against
AWS; the task role needs `secretsmanager:GetSecretValue` and `boto3` installed **[TODO-verify]**. `DATABASE_URL`,
`SESSION_SECRET` and `PROGRESS_DB` are read directly from env: put them in the service's secret env config instead.

## Multi-tenant operation
- Families sign up at `/parent/` (disable with `ALLOW_SIGNUP=0` and create families by script instead). Each gets a family code, PIN, Alexa link token and TV token, shown once.
- Configure Alexa+ with the family's link token as the bearer token **[MANUAL] [TODO-verify]**; the Fire TV app gets the family's TV token.
- The single-family env vars (`PARENT_PIN`, `MCP_AUTH_TOKEN`, `TV_API_TOKEN`) still work and map to the `default` family.
- Public deployment checklist: `REQUIRE_MCP_AUTH=1`, `COOKIE_SECURE=1`, `SESSION_SECRET` set, `TRUST_PROXY=1` only behind a proxy you control, `METRICS_TOKEN` set, HTTPS only.

## Observability
- Logs: JSON lines on stderr (`ts, level, msg, rid, method, route, status, ms`). Routes are whitelisted labels, never child names.
- `/metrics`: Prometheus text: `http_requests_total`, `questions_asked_total{topic,revisit}`, `answers_total{topic,result}`, `hints_total`, `reprompts_total`, `rate_limited_total`, `pin_failures_total`, `login_failures_total`, `tv_events_applied_total`, `families_created_total`, uptime. Counters are per process and reset on restart.
- Rate limiting: per-IP token bucket (240/min general, 10/min for sign-in and sign-up, `/healthz` exempt), `429` with `Retry-After`. Per process: with N instances the effective limit is N times higher; add an edge/WAF limit for a hard bound.

## Weekly reports
`python -m learning_coach.reports --family <slug> [--html] [--send]`. Sending is **stubbed** (logs a line, sends nothing) unless
`REPORT_MODE=smtp` plus `REPORT_SMTP_HOST/PORT/USER/PASSWORD` and `REPORT_FROM`. The SMTP path (STARTTLS, `smtplib`) is
**unverified**; an SES SMTP endpoint should work **[TODO-verify]**. Schedule it weekly with cron/EventBridge **[MANUAL]**.
There is no scheduler inside the app.

## Data lifecycle
Export: `/parent/export`. Delete: `/parent/family/delete`. Retention: `Coach.purge_old(days)` (call from a cron script). See [PRIVACY.md](PRIVACY.md).
