# AWS AI stack: what is used, what is not, and why

Written 2026-09-26 while integrating. **No AWS credentials existed in the build environment, so nothing here was run
against live AWS.** "Verified" below means one of: read in the official docs (URL given), or exercised offline
(botocore `Stubber` validates requests/responses against the real service models; `cfn-lint` validates CloudFormation
schemas; the real Strands package and a real ARM64 container were run locally). Anything else is marked
**TODO-verify**. Kiro is out of scope.

## 1. Decisions

| Service | Decision | Where | Why |
| --- | --- | --- | --- |
| **Amazon Bedrock** (Converse API) | **Used, opt-in** (`BEDROCK_ENABLED=1`) | `learning_coach/bedrock.py`, hooks in `service.py`, `reports.py` | Three places where language understanding really helps: free-text answers the rules reject (a child says "the big grey one" for elephant), varied explanations of *why* an answer is right, and a readable weekly parent narrative. Each has a rule-based fallback, so the product never depends on it. |
| **Bedrock Guardrails** | **Used** when `BEDROCK_GUARDRAIL_ID` is set; created by `infra/aws-stack.yaml` | `guardrailConfig` on every Converse call | Child audience: harmful-content filters, prompt-attack filter, PII masking. |
| **Strands Agents SDK** | **Used, opt-in** (`TUTOR_MODE=strands`, extra install) | `learning_coach/tutor.py` | A real agent (Bedrock model + tool) that plans which topics a child should practise when no spaced-repetition review is due. Honest assessment: a modest gain over "least recently practised"; kept optional and off the voice path (planned in the background). |
| **AgentCore Runtime** | **Packaged, optional, not the primary path** | `Dockerfile.agentcore`, `infra/agentcore-runtime.yaml`, `MCP_*` switches in `server.py` | Docs confirm it hosts MCP servers. But its inbound auth is IAM SigV4 or an OAuth JWT authorizer, and Alexa+ (as far as we know) sends a static bearer token, so Alexa cannot call it directly. It is useful for agent-to-tool traffic (Strands/AgentCore agents, Gateway). Container verified locally (below); never deployed. |
| **AgentCore Memory** | **Not used** | | Our store already holds mastery, spaced-repetition boxes and session history in relational form with export/delete for COPPA/GDPR-minded privacy (docs/PRIVACY.md). Memory's LLM-extracted long-term "facts" would copy child data into a third store with no product gain. Revisit for free-form conversational personalisation. |
| **AgentCore Gateway / Identity** | **Not used** | | Gateway converts APIs/Lambda into MCP tools and does inbound/outbound auth; we already *are* an MCP server with per-family tokens. No tool needs third-party OAuth. |
| **Amazon SageMaker** | **Not used, no fake model added** | | There is no training data (no real children's attempt logs; the demo data is synthetic) and no task that a small custom model beats our deterministic logic on. Serverless endpoints cold-start (docs) which is wrong for a spoken turn; a real-time endpoint is an always-on cost for nothing. See section 6 for what would change this. |
| **Secrets Manager, CloudWatch, IAM, App Runner, ECR, CloudFormation** | **Used** | `infra/aws-stack.yaml`, `ops.py` | AWS-native hosting (also the entry path for the AWS Builder challenge). |

## 2. Verified facts (with sources)

Bedrock
- Converse is on the `bedrock-runtime` endpoint; request fields include `modelId`, `messages`, `system`, `inferenceConfig` (`maxTokens`, ...), `guardrailConfig`; response has `output.message.content`, `stopReason`, `usage`, `metrics`. https://docs.aws.amazon.com/bedrock/latest/userguide/conversation-inference.html
- Guardrail with Converse: `guardrailConfig = {guardrailIdentifier, guardrailVersion, trace}`; a blocked call returns `stopReason == "guardrail_intervened"` with the configured blocked message in `output`; if any `guardContent` block is used, only those blocks are evaluated; tool results and tool definitions are not evaluated. https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-use-converse-api.html
- Nova Micro: model id `amazon.nova-micro-v1:0`; geo inference ids `us.amazon.nova-micro-v1:0`, `eu.amazon.nova-micro-v1:0`; in-Region in us-east-1, us-gov-west-1, eu-west-2, ap-southeast-2, geo profiles elsewhere (us-east-2, us-west-2, ...); supports Converse, Guardrails and client-side tool calling; text only; not "structured outputs". Model card lists lifecycle "Active", "EOL no sooner than Dec 05, 2025" (a moving target: check before relying on it). https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-amazon-nova-micro.md
- Sensitive-information filters detect NAME, EMAIL, PHONE, ADDRESS, ... and can BLOCK or ANONYMIZE; the trace `match` field returns the ORIGINAL PII value; invocation logs (if enabled) keep the original input, so do not enable model invocation logging on a child-facing app without CloudWatch data protection. https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-sensitive-filters.html
- IAM: `bedrock:InvokeModel` needs resource types `foundation-model` and `inference-profile`; `bedrock:ApplyGuardrail` needs `guardrail` (+ the model types). https://docs.aws.amazon.com/service-authorization/latest/reference/list_amazonbedrock.html (ARN shapes there were summarised, not copied; see TODO-verify).
- CloudFormation `AWS::Bedrock::Guardrail` properties and `ContentFilterConfig`/`PiiEntityConfig` values used in the template; `Version` attribute "will always be DRAFT". https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-bedrock-guardrail.html

AgentCore Runtime
- Supports MCP servers: container at `0.0.0.0:8000/mcp`, streamable-HTTP required, stateless recommended (`stateless_http=True`), platform adds `Mcp-Session-Id`, **ARM64 container**. https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-mcp-protocol-contract.html and .../runtime-mcp.html
- Inbound auth for MCP: SigV4 or OAuth 2.0 (JWT authorizer); SigV4 failures return 403; OAuth failures 401 with `WWW-Authenticate`. Invocation URL `https://bedrock-agentcore.<region>.amazonaws.com/runtimes/<URL-encoded runtime ARN>/invocations?qualifier=DEFAULT`. https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-mcp.html
- Custom request headers can be forwarded via an allowlist (up to 20; not `x-amz-*`/`x-amzn-*` except the documented `X-Amzn-Bedrock-AgentCore-Runtime-Custom-` prefix); `Authorization` is forwardable only with a JWT authorizer. https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-header-allowlist.html
- Execution role trust policy and permissions. https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-permissions.html
- CloudFormation `AWS::BedrockAgentCore::Runtime` (`AgentRuntimeName`, `RoleArn`, `AgentRuntimeArtifact.ContainerConfiguration.ContainerUri`, `ProtocolConfiguration: MCP|HTTP|A2A|AGUI`, `NetworkConfiguration.NetworkMode: PUBLIC|VPC`, `AuthorizerConfiguration.CustomJWTAuthorizer.{DiscoveryUrl,AllowedClients,...}`, `RequestHeaderConfiguration.RequestHeaderAllowlist`, `EnvironmentVariables`). https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-bedrockagentcore-runtime.html
- AgentCore Memory = managed short-term (per-session turns) and long-term (extracted preferences/facts/summaries) memory. https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html
- AgentCore Gateway = converts OpenAPI/Smithy/Lambda into MCP tools with inbound and outbound auth. https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway.html

Strands Agents SDK (docs + inspection of the installed package `strands-agents 1.57.1`)
- `pip install strands-agents`; Python 3.10+; default provider is Amazon Bedrock. https://pypi.org/project/strands-agents/
- Quickstart shows `from strands.models import BedrockModel; BedrockModel(model_id=..., temperature=..., streaming=...)`, `Agent(model=..., tools=[...])`, `MCPClient(...)` + `list_tools_sync()`. Same page.
- Inspected in the installed package: `BedrockModel(boto_session=, boto_client_config=, region_name=, endpoint_url=, api_key=, **config)` with config keys including `model_id, max_tokens, temperature, streaming, guardrail_id, guardrail_version`; `Agent(model=, tools=, system_prompt=, callback_handler=, ...)`; `from strands import tool`; default model id `global.anthropic.claude-sonnet-4-6` in `us-west-2` (the quickstart text says "Claude 4 Sonnet": docs and package disagree).
- Dependency trap: a fresh `pip install strands-agents` resolved `mcp 2.1.1`, while this project pins `mcp<2` (mcp 2 dropped `streamablehttp_client`; see https://github.com/aws/bedrock-agentcore-starter-toolkit/issues/580). With `mcp 1.30.0` Strands works (verified locally): `requirements-aws.txt` and the Dockerfile keep `mcp<2`.

SageMaker
- Options: real-time, serverless (scales to zero, "tolerate cold starts", 1-6 GB memory, no GPU/VPC config), asynchronous (up to 1 GB payload, up to 1 hour). https://docs.aws.amazon.com/sagemaker/latest/dg/deploy-model.html and .../serverless-endpoints.html

CloudWatch
- Embedded Metric Format structure used by `ops.emit_emf`. https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Embedded_Metric_Format_Specification.html

## 3. Bedrock design

`learning_coach/bedrock.py` (OFF unless `BEDROCK_ENABLED=1`; `boto3` is imported lazily).

| Feature (`BEDROCK_FEATURES`) | Trigger | What the model may do | Guard rails |
| --- | --- | --- | --- |
| `judge` | rules said wrong/unclear on a **free-text** answer (max 8 words) | reply exactly `correct` or `wrong`; only `correct` changes anything | never for numbers, fractions, spelled letters or the spelling topic (arithmetic and spelling stay deterministic); answer wrapped in `<answer>` as untrusted data; any non-enum reply is ignored |
| `feedback` | after every graded answer | one sentence (max 18 words) of encouragement or *why* the answer is right, tuned to level | validated: single line, no `?`, plain charset, no URL, **no number that is not in the question/answer**; rule-based lines always stay in |
| `report` | weekly report generation | 2-3 sentence narrative from aggregate numbers | validated the same way; falls back to a deterministic paragraph; labelled "Written with Amazon Bedrock" |

Cross-cutting: guardrail on every call (`BEDROCK_GUARDRAIL_ID/VERSION`), hard timeout `BEDROCK_TIMEOUT_S` (2.5 s) and **no retries** (a retry would double a spoken turn), `BEDROCK_MAX_TOKENS`, daily caps (`BEDROCK_DAILY_CALLS_PER_FAMILY` 200, `BEDROCK_DAILY_CALLS_GLOBAL` 5000; per-process counters), never raises.

Privacy: prompts contain curriculum text, our expected answer and, for `judge` only, the child's spoken answer after `scrub()` (emails/phones/URLs removed, 100 chars). No child name, no family id, no token. Reports send aggregates only ("your child"). Tests assert this. Metrics and logs carry feature and outcome only. Residual risk: a child can say something identifying inside a free-text answer that `scrub` misses; the guardrail's PII masking is the second layer, and `judge` can be switched off (`BEDROCK_FEATURES=feedback,report`). Do not enable Bedrock model-invocation logging (it stores the original prompt).

Config: `BEDROCK_ENABLED, BEDROCK_MODEL_ID (default amazon.nova-micro-v1:0), BEDROCK_REGION (else AWS_REGION), BEDROCK_GUARDRAIL_ID, BEDROCK_GUARDRAIL_VERSION (DRAFT), BEDROCK_FEATURES, BEDROCK_TIMEOUT_S, BEDROCK_MAX_TOKENS, BEDROCK_DAILY_CALLS_PER_FAMILY, BEDROCK_DAILY_CALLS_GLOBAL`. `EMF_NAMESPACE` turns on CloudWatch EMF metric lines (`BedrockCalls`, `BedrockInputTokens`, `BedrockOutputTokens`, `BedrockLatency`, dimension `Outcome` plus an undimensioned series). `/metrics` also exposes `learning_coach_bedrock_calls_total{feature,outcome}` and token counters.

Latency: `feedback` and `judge` add one Bedrock round trip (typically sub-second for a small model, **unmeasured here**) to a spoken turn, bounded by the timeout. TODO-verify against Alexa+'s tool-call latency budget, which is not documented in what we found.

## 4. Strands tutor design

`TUTOR_MODE=strands` (install `requirements-aws.txt` or build with `--build-arg WITH_STRANDS=1`). `start_session` schedules a background plan; the agent has one tool, `topic_snapshot` (topic ids, levels, 0/1 recent results, days since practised: no names), and replies with up to three topic ids. `next_question` uses the plan only when spaced repetition has nothing due; every id is validated against the child's unlocked topics; plans expire (30 min) and any failure means "least recently practised" as before. Verified: the real `strands.Agent` + `@tool` + `BedrockModel(streaming=False)` loop (tool call, then answer) runs against a botocore-stubbed Bedrock client in `tests/test_tutor.py::test_real_strands_agent_loop_against_stubbed_bedrock` (skipped unless strands is installed; run in a scratch venv with mcp 1.30.0). Not verified: a real model actually choosing well.

## 5. AgentCore Runtime packaging

`Dockerfile.agentcore` (ARM64, `MCP_STATELESS=1 MCP_ONLY=1`), `infra/agentcore-runtime.yaml` (runtime + least-privilege execution role, optional JWT authorizer, header allowlist).
- `MCP_STATELESS=1` -> FastMCP `stateless_http=True` (the documented recommendation). `MCP_ONLY=1` -> only `/mcp` and `/healthz` (no dashboard, no TV API). `MCP_LINK_TOKEN_HEADER=x-lc-link-token` -> the family token is read from that header, because `Authorization` carries the platform's JWT. Defaults keep today's behaviour and the `/api` and `/mcp` contracts unchanged.
- **Verified locally**: the ARM64 image built (emulated on x86_64), started, served stateless MCP on `:8000/mcp`, the MCP `initialize`, `list_tools` and a `list_children` call worked with the token in `x-lc-link-token`, a wrong token was rejected (401), `/parent/` returned 404 and `/healthz` 200. This is not AgentCore itself.
- **Not verified**: deployment, how the platform forwards the custom header, cold-start behaviour, and whether the runtime is reachable from Alexa+ at all (it is not with a static bearer token). Design consequence: a family token per request via header, with the platform authenticating the caller separately.
- To let Alexa+ reach it you would need Alexa+ to support AgentCore's OAuth flow, or an API Gateway/Lambda in front that signs requests; both out of scope and unverified.

## 6. SageMaker: why not, and when

Not integrated. A model would have to beat: deterministic grading (numbers, fractions, letters), Leitner spaced repetition and the accuracy-based "stretch". Candidates considered: (a) answer classifier: the LLM second opinion already covers the ambiguous free-text case and a classifier needs labelled child speech we do not have; (b) difficulty / forgetting-curve model: needs real longitudinal attempt data (the seeded history is synthetic); (c) recommender: the Strands planner covers it at this scale. If the app collects consented, de-identified attempt logs (docs/PRIVACY.md would need a lawful basis), a weekly SageMaker batch/async job could fit a per-skill forgetting model and write interval multipliers back (`SRS_SCALE` already exists as a knob). Serverless inference is unsuitable inside a voice turn (cold starts, per the docs); batch/async is suitable offline. No SageMaker code is included on purpose.

## 7. Deployment on AWS

`infra/aws-stack.yaml` (CloudFormation; **cfn-lint 1.57.0 clean, never deployed**): App Runner from ECR, three Secrets Manager secrets (app JSON with PIN + generated MCP token, database URL, session secret; App Runner gets ARNs via `RuntimeEnvironmentSecrets`), optional Bedrock guardrail, an **instance role limited to**: `secretsmanager:GetSecretValue` on those three secrets, `bedrock:InvokeModel` on one foundation model (+ its inference profile), `bedrock:ApplyGuardrail` on the one guardrail; three CloudWatch alarms on the EMF metrics (hourly call cost guard, Bedrock errors, guardrail interventions). `tests/test_aws_stack.py` asserts no wildcard actions, no `*` Bedrock resources, secrets not in plain env, Bedrock off by default.

Logs: JSON lines on stderr (no child data). App Runner ships container output to CloudWatch Logs (**TODO-verify** that EMF lines in that stream produce metrics; EMF is documented for CloudWatch Logs generally).

Cost notes (no prices quoted; check the current pricing pages: https://aws.amazon.com/bedrock/pricing/, https://aws.amazon.com/apprunner/pricing/, https://aws.amazon.com/cloudwatch/pricing/):
- Bedrock is per input/output token. Rough upper bound per call here: ~250 input and <=120 output tokens (`judge` <=4 output). With the global cap of 5000 calls/day per instance the worst case is roughly 1.2M input + 0.6M output tokens per day per instance; multiply by the model's current per-token price. Guardrail evaluation is billed separately per text unit. A typical family uses a few dozen calls a day.
- The caps are per process and reset on restart; with N instances the ceiling is N times higher. The CloudWatch alarm and an AWS Budgets budget (manual) are the real backstop.
- CloudWatch custom metrics are billed per metric series: the EMF design deliberately uses one dimension (`Outcome`) so it stays a handful of series.
- App Runner has no scale-to-zero cost model like Lambda: a provisioned instance bills for memory even when idle (TODO-verify current App Runner pricing behaviour). Postgres (RDS) is the largest fixed cost.

## 8. What is verified vs not

Verified offline in this repo (237 tests pass, 3 skipped: 2 Postgres, 1 Strands loop that ran green in a scratch venv):
- Converse request/response shapes (botocore Stubber against the real `bedrock-runtime` and `secretsmanager` models), guardrail intervention handling, error/timeout/empty handling, caps, validation, prompt privacy, EMF line structure.
- Feature behaviour end to end in the coach (upgrade, no-upgrade, injection, numbers/spelling excluded, fallbacks) and in reports.
- Real Strands `Agent` loop with our tool against a stubbed Bedrock client; Dockerfile with `WITH_STRANDS=1` resolves strands 1.57.1 + mcp 1.30.0.
- CloudFormation templates lint clean; least-privilege assertions.
- ARM64 AgentCore-style container runs the MCP contract locally.

NOT verified (needs your AWS account): any real Bedrock call (model access, region, latency, quality of `judge`, guardrail behaviour on real text), Strands against a real model, AgentCore deployment and header forwarding, App Runner deployment and `RuntimeEnvironmentSecrets`, EMF metrics appearing, alarms firing, Alexa+ calling anything.

## 9. Manual steps for you

1. **[MANUAL]** Bedrock console: request/enable access to the chosen model in your Region (model availability differs; use the geo id `us.amazon.nova-micro-v1:0` where in-Region is unavailable).
2. **[MANUAL]** Deploy `infra/aws-stack.yaml` (see docs/DEPLOY.md). Then publish a **numbered guardrail version** and set `BEDROCK_GUARDRAIL_VERSION` (CloudFormation only creates DRAFT).
3. **[MANUAL]** Smoke test: `python -m learning_coach.reports --family <slug>` with `BEDROCK_ENABLED=1` and real credentials; watch `/metrics` for `bedrock_calls_total{outcome="ok"}`.
4. **[MANUAL]** Measure latency and judge quality with real children's phrases; trim `BEDROCK_TIMEOUT_S`/features accordingly. Create an AWS Budgets alert.
5. **[MANUAL, optional]** AgentCore: build/push the ARM64 image, deploy `infra/agentcore-runtime.yaml`, test with the AgentCore MCP client snippet from the docs.
6. Fill the placeholders in docs/AMAZON_FEEDBACK.md with your live experience.

## 10. TODO-verify list

- `bedrock:InvokeModel` being the permission that authorises Converse (believed, not re-read), and exact ARN shapes for `foundation-model` (empty account field) and `inference-profile`.
- App Runner instance-role trust principal `tasks.apprunner.amazonaws.com`, ECR access role principal `build.apprunner.amazonaws.com`, whether the instance role needs `secretsmanager:GetSecretValue` for `RuntimeEnvironmentSecrets`, `X-Forwarded-For` behaviour.
- Guardrail `PROMPT_ATTACK` requires `OutputStrength: NONE`; whether a CloudFormation resource exists for numbered guardrail versions.
- AgentCore: behaviour when `AuthorizerConfiguration` is omitted (assumed IAM), custom-header forwarding in MCP mode, secrets access pattern (we use `AWS_SECRETS_ID`).
- Alexa+ tool-call latency budget and whether it can send anything other than a static bearer token.
