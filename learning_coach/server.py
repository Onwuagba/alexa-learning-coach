"""Alexa+ learning coach: MCP over streamable HTTP + parent dashboard + TV REST API on one port.

Run:  python -m learning_coach.server
  MCP           http://localhost:8000/mcp/          (Authorization: Bearer <family link token>)
  Parent page   http://localhost:8000/parent/
  TV REST API   http://localhost:8000/api/progress/{child}   (Bearer <family TV token>)
  Health        http://localhost:8000/healthz
  Metrics       http://localhost:8000/metrics        (Bearer METRICS_TOKEN if set)
Env: PORT, DATABASE_URL (or PROGRESS_DB), PARENT_PIN (default family, 4-8 digits), MCP_AUTH_TOKEN (single-family
fallback bearer for /mcp; maps to the `default` family), TV_API_TOKEN (same for /api), REQUIRE_MCP_AUTH=1,
TV_CORS_ORIGINS, SESSION_SECRET, COOKIE_SECURE=1, ALLOW_SIGNUP=0/1, RATE_LIMIT_PER_MIN, AUTH_RATE_LIMIT_PER_MIN,
TRUST_PROXY=1, METRICS_TOKEN, LOG_LEVEL, AWS_SECRETS_ID (optional Secrets Manager adapter, unverified).
AWS extras (all optional, see docs/AWS_STACK.md): BEDROCK_* (Converse features), TUTOR_MODE=strands, EMF_NAMESPACE
(CloudWatch metrics), MCP_STATELESS=1, MCP_ONLY=1, MCP_LINK_TOKEN_HEADER (AgentCore Runtime).

Tenancy on /mcp: the bearer token identifies the family; the tools never take a family argument, so one family
cannot address another's children.
"""
from __future__ import annotations

import hmac
import os

from mcp.server.fastmcp import Context, FastMCP
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route

from .ops import METRICS, OpsMiddleware, get_secret, log_event
from .service import Coach, InvalidInput
from .store import DEFAULT_FAMILY, make_store
from .tvapi import make_tv_app
from .web import make_app

# MCP_STATELESS=1: stateless streamable HTTP, what Amazon Bedrock AgentCore Runtime recommends for MCP servers.
mcp = FastMCP("learning-coach", host="0.0.0.0", port=int(os.getenv("PORT", "8000")),
              stateless_http=os.getenv("MCP_STATELESS") == "1")
store = make_store(get_secret("DATABASE_URL") or os.getenv("PROGRESS_DB", "progress.db"))
coach = Coach(store, get_secret("PARENT_PIN"))
MCP_ENV_TOKEN = get_secret("MCP_AUTH_TOKEN")


def _bearer(headers) -> str:
    """The family/link token. Default: `Authorization: Bearer <token>`. MCP_LINK_TOKEN_HEADER=x-lc-link-token reads
    the raw token from that header instead: behind AgentCore Runtime with a JWT authorizer, `Authorization` carries
    the OAuth JWT, so the family token must travel in an allow-listed custom header (see docs/AWS_STACK.md)."""
    alt = os.getenv("MCP_LINK_TOKEN_HEADER", "").strip().lower()
    if alt:
        return headers.get(alt, "").strip()
    v = headers.get("authorization", "")
    return v[7:].strip() if v.lower().startswith("bearer ") else ""


def family_of_headers(headers) -> str | None:
    """Family for an /mcp request: env fallback token -> default family; else a family link token."""
    tok = _bearer(headers)
    if tok and MCP_ENV_TOKEN and hmac.compare_digest(tok, MCP_ENV_TOKEN):
        return DEFAULT_FAMILY
    if tok:
        return coach.family_for_link_token(tok)
    if MCP_ENV_TOKEN or os.getenv("REQUIRE_MCP_AUTH") == "1":
        return None
    return DEFAULT_FAMILY  # unauthenticated local/dev mode


def _family(ctx: Context | None) -> str:
    req = ctx.request_context.request if ctx is not None else None
    if req is None:  # e.g. stdio transport: single local family
        return DEFAULT_FAMILY
    return family_of_headers(req.headers) or DEFAULT_FAMILY  # the ASGI middleware already 401s bad tokens


def _safe(fn, *a, **k):
    try:
        return fn(*a, **k)
    except InvalidInput as e:
        return {"error": str(e), "say": str(e)}


@mcp.tool()
def list_children(ctx: Context) -> dict:
    """Who can practise in this family. Ask this if you don't know the child's name."""
    return _safe(coach.list_children, family=_family(ctx))


@mcp.tool()
def start_session(ctx: Context, child: str = "") -> dict:
    """Start a practice session. `child` may be omitted when the family has one child; spoken names are matched
    fuzzily. Returns topics, a streak, and `say`: text to speak."""
    return _safe(coach.start_session, child, family=_family(ctx))


@mcp.tool()
def next_question(ctx: Context, child: str = "", topic: str = "") -> dict:
    """Ask the next question. Leave `topic` empty to let the coach choose (it revisits tricky items using spaced
    repetition). Speak `say` (or `ssml`). If `need_child` is true, ask the listener which child it is."""
    return _safe(coach.next_question, child, topic, family=_family(ctx))


@mcp.tool()
def check_answer(ctx: Context, answer: str, child: str = "") -> dict:
    """Check the child's spoken answer. Understands number words, homophones and fractions. If `reprompt` is
    true, speak `say` and call again with the new answer. Speak `say` otherwise."""
    return _safe(coach.check_answer, child, answer, family=_family(ctx))


@mcp.tool()
def get_hint(ctx: Context, child: str = "") -> dict:
    """Give a progressive hint for the current question (the child says 'hint' or 'help')."""
    return _safe(coach.get_hint, child, family=_family(ctx))


@mcp.tool()
def repeat_question(ctx: Context, child: str = "") -> dict:
    """Repeat the current question (the child says 'again' or 'repeat')."""
    return _safe(coach.repeat_question, child, family=_family(ctx))


@mcp.tool()
def end_session(ctx: Context, child: str = "") -> dict:
    """Finish practising and speak a short recap of the session (questions, accuracy, streak, badges)."""
    return _safe(coach.end_session, child, family=_family(ctx))


@mcp.tool()
def get_progress(ctx: Context, child: str = "") -> dict:
    """Summarise a child's progress for a parent: skills, badges, streak, weekly numbers."""
    return _safe(coach.get_progress, child, family=_family(ctx))


@mcp.tool()
def unlock_topic(ctx: Context, topic: str, parent_pin: str, child: str = "") -> dict:
    """Parent-only: unlock a topic for a child. Needs the parent PIN (locks out after 5 misses)."""
    return _safe(coach.unlock_topic, child, topic, parent_pin, family=_family(ctx))


@mcp.tool()
def lock_topic(ctx: Context, topic: str, parent_pin: str, child: str = "") -> dict:
    """Parent-only: lock a topic again. Needs the parent PIN."""
    return _safe(coach.lock_topic, child, topic, parent_pin, family=_family(ctx))


class BearerAuth:
    """ASGI middleware for /mcp: reject unknown bearer tokens (and missing ones when auth is required)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/mcp"):
            headers = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            if family_of_headers(headers) is None:
                METRICS.inc("mcp_auth_failures_total")
                return await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
        await self.app(scope, receive, send)


def _metrics(request):
    tok = get_secret("METRICS_TOKEN")
    if tok and not hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {tok}"):
        return PlainTextResponse("unauthorized", status_code=401)
    return PlainTextResponse(METRICS.render(), media_type="text/plain; version=0.0.4")


def _healthz(request):
    try:
        store.schema_version()
    except Exception:
        return PlainTextResponse("db unavailable", status_code=503)
    return PlainTextResponse("ok")


def build_app():
    app = mcp.streamable_http_app()  # Starlette; keeps the MCP session-manager lifespan
    app.router.routes.append(Route("/healthz", _healthz))
    app.router.routes.append(Route("/metrics", _metrics))
    if os.getenv("MCP_ONLY") != "1":  # MCP_ONLY=1: AgentCore Runtime container, /mcp (+ /healthz) only
        app.router.routes.append(Mount("/parent", make_app(coach)))
        app.router.routes.append(Mount("/api", make_tv_app(coach, get_secret("TV_API_TOKEN"))))
    log_event("server_built", schema_version=store.schema_version())
    return OpsMiddleware(BearerAuth(app))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(build_app(), host="0.0.0.0", port=mcp.settings.port, log_config=None)
