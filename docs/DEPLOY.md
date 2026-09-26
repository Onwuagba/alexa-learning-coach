# Deploy and operate

Legend: **[MANUAL]** you must do it (needs your AWS/Amazon account). **[TODO-verify]** not checked against current AWS/Amazon docs.
**[VERIFIED]** actually run while building this.

## What was verified here
- **[VERIFIED]** Full test suite on SQLite (Python 3.11, `pytest`).
- **[VERIFIED]** Store, service, TV events, reports, export/delete and the legacy-database migration against a **real local PostgreSQL 16.12** (Homebrew, throwaway databases): `TEST_PG_ADMIN_URL=postgresql://user@127.0.0.1:PORT/postgres pytest tests/test_postgres.py`. One connection per process, autocommit; not load tested.
- **[VERIFIED]** Two families over the real MCP streamable-HTTP transport (token selects the tenant, cross-family access is refused).
- **[VERIFIED]** Docker images build: `Dockerfile` (also with `--build-arg WITH_STRANDS=1`) and the ARM64 `Dockerfile.agentcore`; the latter ran locally and served stateless MCP with the family token in a custom header. `infra/aws-stack.yaml` and `infra/agentcore-runtime.yaml` pass `cfn-lint` 1.57.0. Bedrock/Secrets Manager request shapes are checked against the real service models with botocore's Stubber. See [AWS_STACK.md](AWS_STACK.md).
- **Not run:** any real AWS call (Bedrock, App Runner / CloudFormation deploy, ECR push, Secrets Manager, AgentCore), SMTP/SES, a real Alexa+ device.

## Local, production-like
```bash
docker build -t lc .            # [not run while writing this]
docker run -p 8000:8000 -e PARENT_PIN=1234 -e REQUIRE_MCP_AUTH=1 -e MCP_AUTH_TOKEN=$(openssl rand -hex 24) lc
```
Database: SQLite file by default (`PROGRESS_DB`), or `DATABASE_URL=postgresql://...`. Migrations run automatically at start;
`python -m learning_coach.migrate --status` shows the version (currently 2: v1 legacy tables, v2 families and learning data;
an old single-household database is moved into the `default` family, keeping its PIN hash, progress and TV event ids).

## AWS: recommended path (CloudFormation, `infra/aws-stack.yaml`)
Creates App Runner, three Secrets Manager secrets, an optional Bedrock guardrail, a least-privilege instance role and CloudWatch
alarms. **Never deployed** (cfn-lint clean only); read the TODO-verify notes in the template and [AWS_STACK.md](AWS_STACK.md).
1. **[MANUAL]** Push the image to ECR (commands below; add `--build-arg WITH_STRANDS=1` for the Strands tutor).
2. **[MANUAL]** Bedrock console: enable access to the model you will use in your Region.
3. **[MANUAL]**
   ```bash
   aws cloudformation deploy --stack-name learning-coach --template-file infra/aws-stack.yaml --capabilities CAPABILITY_IAM \
     --parameter-overrides ImageUri=<acct>.dkr.ecr.<region>.amazonaws.com/learning-coach:latest \
       DatabaseUrl='postgresql://...?sslmode=require' ParentPin=1234 BedrockEnabled=true
   ```
   Leave `BedrockEnabled=false` to run without any AI feature (the default). Outputs: `McpUrl`, `GuardrailId`, `AppSecretArn`.
4. **[MANUAL]** Publish a numbered guardrail version and redeploy with that version (CloudFormation creates DRAFT only).
5. **[MANUAL]** Create an AWS Budgets alert; subscribe an SNS topic (`AlarmTopicArn`) to the alarms.
6. Optional MCP-on-AgentCore: see [AWS_STACK.md](AWS_STACK.md) section 5 (`Dockerfile.agentcore`, `infra/agentcore-runtime.yaml`). It is not reachable from Alexa+ with a static bearer token.

IAM in one line: the container can read three named secrets, invoke one named Bedrock model, apply one named guardrail. Nothing else.

## AWS App Runner (manual / older template)
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

## Amazon Bedrock and Strands (all optional, default off)
Env: `BEDROCK_ENABLED=1`, `BEDROCK_MODEL_ID`, `BEDROCK_REGION`, `BEDROCK_GUARDRAIL_ID`/`_VERSION`, `BEDROCK_FEATURES` (`judge,feedback,report`),
`BEDROCK_TIMEOUT_S`, `BEDROCK_DAILY_CALLS_PER_FAMILY`, `BEDROCK_DAILY_CALLS_GLOBAL`; `TUTOR_MODE=strands`; `EMF_NAMESPACE=LearningCoach` for
CloudWatch metrics. Install locally with `pip install -r requirements-aws.txt`. Design, safety, cost and what is verified:
[AWS_STACK.md](AWS_STACK.md). Smoke test with real credentials: `BEDROCK_ENABLED=1 python -m learning_coach.reports --family <slug>` **[MANUAL]**.
Do not enable Bedrock model-invocation logging for this app (it would store prompts, including a child's spoken answer).

## Secrets
Plain environment variables work everywhere. Optionally set `AWS_SECRETS_ID` to a Secrets Manager secret holding JSON
`{"PARENT_PIN": "...", "MCP_AUTH_TOKEN": "...", "TV_API_TOKEN": "...", "METRICS_TOKEN": "...", "REPORT_SMTP_PASSWORD": "..."}`;
lookups fall back to it when the env var is unset (`learning_coach/ops.py: get_secret`, now also used for `DATABASE_URL` and `SESSION_SECRET`).
The request shape is checked against the real Secrets Manager model (Stubber) but **never run against AWS**; the role needs
`secretsmanager:GetSecretValue` on that secret and `boto3` installed (the Dockerfile installs it) **[TODO-verify]**.

## Multi-tenant operation
- Families sign up at `/parent/` (disable with `ALLOW_SIGNUP=0` and create families by script instead). Each gets a family code, PIN, Alexa link token and TV token, shown once.
- Configure Alexa+ with the family's link token as the bearer token **[MANUAL] [TODO-verify]**; the Fire TV app gets the family's TV token.
- The single-family env vars (`PARENT_PIN`, `MCP_AUTH_TOKEN`, `TV_API_TOKEN`) still work and map to the `default` family.
- Public deployment checklist: `REQUIRE_MCP_AUTH=1`, `COOKIE_SECURE=1`, `SESSION_SECRET` set, `TRUST_PROXY=1` only behind a proxy you control, `METRICS_TOKEN` set, HTTPS only.

## Observability
- Logs: JSON lines on stderr (`ts, level, msg, rid, method, route, status, ms`). Routes are whitelisted labels, never child names.
- `/metrics`: Prometheus text: `http_requests_total`, `questions_asked_total{topic,revisit}`, `answers_total{topic,result}`, `hints_total`, `reprompts_total`, `rate_limited_total`, `pin_failures_total`, `login_failures_total`, `tv_events_applied_total`, `families_created_total`, `bedrock_calls_total{feature,outcome}`, `bedrock_*_tokens_total`, `tutor_plans_total`, uptime. Counters are per process and reset on restart.
- Rate limiting: per-IP token bucket (240/min general, 10/min for sign-in and sign-up, `/healthz` exempt), `429` with `Retry-After`. Per process: with N instances the effective limit is N times higher; add an edge/WAF limit for a hard bound.

## Weekly reports
`python -m learning_coach.reports --family <slug> [--html] [--send]` (includes an optional Bedrock-written narrative paragraph; a deterministic one otherwise). Sending is **stubbed** (logs a line, sends nothing) unless
`REPORT_MODE=smtp` plus `REPORT_SMTP_HOST/PORT/USER/PASSWORD` and `REPORT_FROM`. The SMTP path (STARTTLS, `smtplib`) is
**unverified**; an SES SMTP endpoint should work **[TODO-verify]**. Schedule it weekly with cron/EventBridge **[MANUAL]**.
There is no scheduler inside the app.

## Data lifecycle
Export: `/parent/export`. Delete: `/parent/family/delete`. Retention: `Coach.purge_old(days)` (call from a cron script). See [PRIVACY.md](PRIVACY.md).
