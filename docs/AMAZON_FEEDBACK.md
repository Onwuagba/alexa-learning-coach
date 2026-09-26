# Feedback note for Amazon (structure + facts observed while integrating)

Hackathon requirement: feedback on the Amazon tools used. This file separates (A) things **actually observed** while
building the AWS integration (documentation and tooling gaps, from reading docs and running the SDKs offline on
2026-09-26) from (B) **placeholders for your live experience** (Alexa+, real Bedrock/AgentCore/App Runner runs).
Delete anything you did not experience. No AWS credentials were available when (A) was written, so it contains no claims
about live service behaviour.

## Tools used (fill in which you really used)
| Tool | Used how | Live-tested? |
| --- | --- | --- |
| Alexa+ (MCP) | MCP server with per-family bearer tokens | [ ] |
| Amazon Bedrock (Converse, Guardrails) | judge / feedback / report narrative | [ ] |
| Strands Agents SDK | topic planner agent | offline only (real Agent loop vs stubbed Bedrock) |
| Bedrock AgentCore Runtime | packaged MCP container + CloudFormation | [ ] (never deployed) |
| AWS App Runner, Secrets Manager, CloudWatch, CloudFormation | primary deployment | [ ] (cfn-lint only) |
| SageMaker, AgentCore Memory/Gateway | evaluated, deliberately not used | n/a |

## A. Observed while integrating (offline)
**What worked well**
- Converse is one uniform request shape across models, and the docs give a complete boto3 example with `guardrailConfig`; `stopReason == "guardrail_intervened"` is easy to handle.
- botocore's `Stubber` validates requests against the real service model, so a full Bedrock integration could be tested with no credentials.
- CloudFormation resource pages for `AWS::BedrockAgentCore::Runtime` and `AWS::Bedrock::Guardrail` are complete enough to write the templates, and `cfn-lint` validated them offline.
- The AgentCore MCP contract (0.0.0.0:8000/mcp, stateless streamable HTTP, ARM64, header allowlist) is short and precise; a container following it ran locally without changes to the MCP code.
- Strands: `Agent(model=BedrockModel(...), tools=[@tool fn])` ran a full tool-call loop against a stubbed Bedrock client on the first attempt.

**Documentation and tooling gaps hit**
1. Finding a model id takes several hops: the Converse page points to "models at a glance", which points to one card per model; the older "supported models" page just says "moved". A single table of model id / inference-profile id / Regions would save time. The Nova Micro card shows an "EOL no sooner than Dec 05, 2025" line next to "Active"; it is unclear how a developer should treat that.
2. Foundation-model vs inference-profile IAM: the docs list required resource types, but I found no copy-paste least-privilege policy for "Converse on one model through a geo inference profile" (needs the profile ARN plus foundation-model ARNs in the destination Regions). Ended up with a wildcard Region scoped to one model id. [confirm this works on a live account]
3. Guardrails: the Converse-with-guardrail page states that once any `guardContent` block is used only those blocks are evaluated, and that tool results/definitions are never evaluated. Both are easy to miss and matter for safety design. Also the trace `match` field returns the original PII, and CloudWatch invocation logs keep the original input: worth a prominent warning for child-directed apps.
4. CloudFormation `AWS::Bedrock::Guardrail` documents that `Version` is always `DRAFT`; I did not find a way to publish a numbered version from the same template. Production use needs a manual or scripted step.
5. Strands quickstart vs package: the quickstart says the default model is Claude 4 Sonnet in us-west-2; the installed 1.57.1 package defaults to `global.anthropic.claude-sonnet-4-6`. Docs URLs I guessed for the Strands docs site returned 404, so I relied on PyPI and reading the installed package.
6. Dependency trap: a fresh `pip install strands-agents` resolved `mcp 2.1.1`, which dropped `streamablehttp_client` (also reported in aws/bedrock-agentcore-starter-toolkit issue 580, and the AgentCore MCP docs' own client examples import it). Pinning `mcp<2` works (verified with mcp 1.30.0 and strands 1.57.1). A version note in the Strands and AgentCore docs would help.
7. AgentCore MCP docs: (a) the ARM64 requirement appears on the protocol-contract page but not on the "deploy your first MCP server" page, which uses a CLI flow; (b) the Auth0 example client shows an agent ARN of the form `arn:aws:bedrock:...:agent/ABCD1234` while the rest of the page uses `arn:aws:bedrock-agentcore:...:runtime/...`; (c) the docs use the npm `@aws/agentcore` CLI while some ecosystem material still refers to the Python starter toolkit.
8. **Product gap for Alexa+**: AgentCore Runtime authenticates callers with SigV4 or an OAuth JWT, so a device platform that sends a static bearer token cannot call a runtime directly. A documented pattern for "third-party voice platform -> AgentCore-hosted MCP tools" (token forwarding, or a Gateway with API-key style inbound auth) would remove a dead end. [confirm what Alexa+ actually sends]
9. App Runner CloudFormation: `InstanceRoleArn` and `RuntimeEnvironmentSecrets` are documented, but the property pages do not state the trust principal for the instance role or which secret permissions are needed; I wrote them from memory and marked TODO-verify.
10. Bedrock and AgentCore docs are large and reorganised often (several pages returned "moved" stubs); a stable "recipes" index (child-safe tutor, MCP tools on AgentCore, EMF metrics from a container) would help hackathon builders.

**Why SageMaker / AgentCore Memory were not used** (a fair-use note for judges): no real training data or model need (SageMaker; serverless inference cold-starts are documented as a trade-off, wrong for a spoken turn), and our relational store already covers what Memory offers with better privacy control. We preferred to say so rather than force-fit.

## B. Your live experience (fill in; keep it specific and honest)
**Alexa+ / MCP**
- How was the endpoint registered? Auth options offered (bearer / OAuth / other)? [ ]
- Does a household/profile/child identifier reach the tool? What did you actually receive? [ ]
- Latency budget for a tool call; how are tool errors voiced? [ ]
- Parental-control guidance (spoken PIN vs out-of-band unlock); child-directed / COPPA-style expectations? [ ]
- Local simulator/test harness for MCP-backed Alexa+ experiences? [ ]

**Bedrock**
- Model access approval time / Region / model id that worked: [ ]
- Measured latency p50/p95 of `judge` and `feedback` on the model you chose: [ ]
- Guardrail: false positives on kids' vocabulary? Blocked-message behaviour? [ ]
- Quality: how often did `judge` wrongly accept? [ ]
- Cost observed for N sessions: [ ]

**Strands**: did the planner beat "least recently practised" in your judgement? [ ]
**AgentCore**: deployment friction, invocation from your MCP client, header forwarding: [ ]
**App Runner / CloudFormation / Secrets Manager / CloudWatch**: stack create errors, EMF metrics visible?: [ ]

## What we would ask Amazon for
1. One end-to-end sample: voice platform bearer token -> tenant-aware MCP server on AgentCore (or explicit statement that it is unsupported).
2. Copy-paste least-privilege IAM for Converse with inference profiles and guardrails.
3. Numbered guardrail versions from CloudFormation.
4. A child-safety guardrail template/preset for education apps.
5. Alexa+: household/profile identity in MCP tool calls; a device-less simulator.

**Contact:** [your name/email]
