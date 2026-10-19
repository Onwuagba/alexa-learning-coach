# Friction log

Entries come from reading the documentation and offline work (botocore Stubber, cfn-lint, a local Strands loop).
No live AWS account or Alexa+ device was used, so there are no claims about live service behaviour.

## F1. Choosing a Bedrock model id
- **Task:** call Converse on a small model.
- **Steps:** opened the Converse page, followed it to "models at a glance", then to per-model cards; the old "supported models" page just said "moved".
- **Expected:** one table of model id / inference-profile id / Regions.
- **Actual:** several hops; the Nova Micro card lists "EOL no sooner than Dec 05, 2025" beside "Active".
- **Severity:** medium.
- **Workaround:** read the model card and set `BEDROCK_MODEL_ID` by hand.
- **Suggestion:** a single table plus a clear EOL date or "none announced".

## F2. strands-agents pulled mcp 2.x
- **Task:** install the Strands extra next to an MCP server.
- **Steps:** `pip install strands-agents` resolved `mcp 2.1.1`, which dropped `streamablehttp_client`.
- **Expected:** a working install.
- **Actual:** an import error in the MCP client code.
- **Severity:** high for newcomers.
- **Workaround:** pin `mcp<2` (verified with mcp 1.30.0 and strands 1.57.1).
- **Suggestion:** a version note in the Strands and AgentCore docs.

## F3. Least-privilege IAM for Converse via a geo inference profile
- **Task:** write a minimal policy.
- **Steps:** read the Bedrock authorization reference.
- **Expected:** a copy-paste policy.
- **Actual:** resource types are listed but there is no example; I used a wildcard Region scoped to one model id (not confirmed on a live account).
- **Severity:** medium.
- **Workaround:** the scoped wildcard.
- **Suggestion:** a published example policy.

## F4. AgentCore Runtime auth vs static bearer tokens
- **Task:** host the Alexa-facing MCP server on AgentCore.
- **Steps:** read the auth section: SigV4 or OAuth JWT only.
- **Expected:** a path for a third-party voice platform.
- **Actual:** a dead end for static bearer tokens (what Alexa+ sends is unverified).
- **Severity:** high for this use case.
- **Workaround:** App Runner is the primary deployment path; AgentCore is only packaged.
- **Suggestion:** a documented forwarding pattern or Gateway API-key inbound auth.

## F5. Guardrail behaviour that is easy to miss
- **Task:** use guardrails with Converse.
- **Expected:** all content is evaluated.
- **Actual:** once any `guardContent` block exists only those blocks are evaluated; tool results never are; the trace `match` field returns the original PII.
- **Severity:** medium (safety relevant).
- **Workaround:** validate output in code as well.
- **Suggestion:** prominent warning boxes.

## Feature requests
1. **Household/profile identity in MCP tool calls for Alexa+** (critical): a kids' coach must know who is speaking.
2. **A device-less simulator or test harness for MCP-backed Alexa+ experiences** (important).
3. **A documented pattern from a voice platform's static bearer token to AgentCore-hosted MCP tools**, or an explicit statement that it is unsupported (important).
4. **Copy-paste least-privilege IAM for Bedrock Converse with inference profiles and guardrails** (important).
5. **Numbered guardrail versions from CloudFormation** (nice-to-have).
6. **A child-safety guardrail preset for education apps** (nice-to-have).
