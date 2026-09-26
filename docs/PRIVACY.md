# Privacy notes (children's data)

**Not legal advice.** This project handles data about children. Before real launch, get counsel to review COPPA
(US), GDPR / UK GDPR (Article 8 child consent, DPIA), and any Alexa/Amazon skill or MCP policy that applies
**[TODO-verify: Amazon's requirements for child-directed skills and third-party MCP servers]**.

## What is stored (per family)
- Family name, optional parent email (for reports), family code, salted scrypt hash of the parent PIN, SHA-256 hashes of the TV and Alexa tokens.
- Child **first names or nicknames only** (typed by the parent or spoken), no surnames, birthdates, photos, voice recordings or device ids.
- Practice results: topic, skill, item, right/wrong, hinted, timestamp; per-topic level; badges; session counts.
- Not stored: audio, full transcripts (only the graded answer is compared, never saved), IP addresses (they are used in memory for rate limiting and never logged).

## Design choices that help
- Data minimisation: nicknames are fine; the form says so.
- Parent-controlled: children cannot unlock topics; parents set daily time limits and goals; the parent PIN is required for access changes and deletion.
- Tenant isolation: every query is filtered by `family_id`; tokens identify the family, tools never take a family id.
- Export and deletion: `/parent/export` (JSON, no secrets) and `/parent/family/delete` (typed family name + PIN) remove the family and all child rows; `Coach.delete_child`, `Coach.purge_old(days)` support retention. Backups are not covered by these calls (delete or expire them per your retention policy).
- Logs contain no child names, answers or PINs (route labels are whitelisted).
- Transport: run only behind HTTPS; set `COOKIE_SECURE=1`.

## Gaps you must close before a public launch
1. Verifiable parental consent (COPPA) or equivalent: the sign-up form is a parent self-declaration only.
2. Privacy policy, data-processing terms with your host (AWS/Neon) and email provider, breach process.
3. Retention schedule: nothing purges automatically; run `Coach.purge_old` on a schedule.
4. Alexa-side data flow: what Alexa+ sends to an MCP server (utterance text? account/profile ids?) is unverified.
5. Email: reports go to the parent address only; confirm the address (double opt-in) before enabling `REPORT_MODE=smtp`.
6. Access logging for staff, encryption at rest (database/host setting), and a DPIA if operating in the EU/UK.

## Optional Amazon Bedrock / Strands processing (off by default)
When `BEDROCK_ENABLED=1` the server sends short prompts to Amazon Bedrock in your AWS account/Region: curriculum question text,
the expected answer, and (only for the free-text "second opinion") the child's spoken answer, cleaned of emails, phone numbers and
URLs and cut to 100 characters. Weekly report narratives use aggregate numbers only. Child names, family ids and tokens are never
included. A guardrail with PII masking is recommended (created by `infra/aws-stack.yaml`). Do **not** enable Bedrock model
invocation logging (it would store prompts). The Strands planner sends topic ids, levels and 0/1 results only. Switch the answer
check off with `BEDROCK_FEATURES=feedback,report`. Disclose the processing in your own privacy notice; this is not legal advice.
See [AWS_STACK.md](AWS_STACK.md).
