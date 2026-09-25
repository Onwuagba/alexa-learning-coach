# Feedback note for Amazon (DRAFT, edit with your real experience)

Written before hands-on Alexa+ integration; replace bracketed items with what actually happened.

**What we built:** an MCP server (streamable HTTP) exposing five tools for a kids' practice coach, plus a parent web page.

**What worked well:** [fill in] MCP tool schemas map naturally to voice turns (start_session / next_question / check_answer).

**Friction / requests (hypotheses to confirm or delete):**
- [TODO-verify] Clear docs on how a third-party MCP endpoint is registered with Alexa+, including required auth (bearer token vs OAuth) and TLS/hostname requirements.
- [TODO-verify] How the child's identity is passed to tools. We take a `child` name argument; a household/profile id from Alexa would be safer and remove name-guessing.
- [TODO-verify] Guidance for parental-control flows: is a PIN spoken aloud acceptable, or should unlock happen out of band (our web page)? Guidance on child-directed/COPPA-style expectations.
- [TODO-verify] Latency budget for tool calls, and how errors in tool results should be voiced.
- A local test harness or simulator for MCP-backed Alexa+ experiences without a device.

**Contact:** [your name/email]
