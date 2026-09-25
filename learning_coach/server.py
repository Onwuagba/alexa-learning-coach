"""Alexa+ learning coach: MCP over streamable HTTP + parent web page on one port.

Run:  python -m learning_coach.server
  MCP           http://localhost:8000/mcp
  Parent page   http://localhost:8000/parent/
  Health        http://localhost:8000/healthz
Env: PORT, DATABASE_URL (or PROGRESS_DB), PARENT_PIN (4-8 digits), MCP_AUTH_TOKEN (optional
bearer token required on /mcp), SESSION_SECRET, COOKIE_SECURE=1.
"""
from __future__ import annotations

import hmac
import os

from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route

from .service import Coach, InvalidInput
from .store import make_store
from .web import make_app

mcp = FastMCP("learning-coach", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
store = make_store(os.getenv("DATABASE_URL") or os.getenv("PROGRESS_DB", "progress.db"))
coach = Coach(store, os.getenv("PARENT_PIN"))


def _safe(fn, *a):
    try:
        return fn(*a)
    except InvalidInput as e:
        return {"error": str(e)}


@mcp.tool()
def start_session(child: str) -> dict:
    """Start a practice session for a child. Returns the topics they can practise."""
    return _safe(coach.start_session, child)


@mcp.tool()
def next_question(child: str, topic: str) -> dict:
    """Ask the next question for a topic at the child's current level. Read `prompt` aloud."""
    return _safe(coach.next_question, child, topic)


@mcp.tool()
def check_answer(child: str, answer: str) -> dict:
    """Check the child's spoken answer to the last question and adapt difficulty."""
    return _safe(coach.check_answer, child, answer)


@mcp.tool()
def get_progress(child: str) -> dict:
    """Summarise a child's progress for a parent."""
    return _safe(coach.get_progress, child)


@mcp.tool()
def unlock_topic(child: str, topic: str, parent_pin: str) -> dict:
    """Parent-only: unlock a new topic for a child. Needs the parent PIN (locks out after 5 misses)."""
    return _safe(coach.unlock_topic, child, topic, parent_pin)


class BearerAuth:
    """Optional ASGI middleware: require `Authorization: Bearer <token>` on /mcp."""

    def __init__(self, app, token: str):
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/mcp"):
            got = dict(scope["headers"]).get(b"authorization", b"").decode()
            if not hmac.compare_digest(got, f"Bearer {self.token}"):
                return await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
        await self.app(scope, receive, send)


def build_app():
    app = mcp.streamable_http_app()  # Starlette; keeps the MCP session-manager lifespan
    app.router.routes.append(Route("/healthz", lambda r: PlainTextResponse("ok")))
    app.router.routes.append(Mount("/parent", make_app(coach)))
    token = os.getenv("MCP_AUTH_TOKEN")
    return BearerAuth(app, token) if token else app


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(build_app(), host="0.0.0.0", port=mcp.settings.port)
