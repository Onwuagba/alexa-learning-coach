"""Small authenticated REST API for the Fire TV app (separate from MCP and the parent page).

  GET  /api/progress/{child}          -> {child, progress: {topic: {level, correct, attempts, streak,
                                          updatedAt}}, available: [...], promoteAfter}
  POST /api/progress/{child}/events   body {"events": [{"id", "topic", "correct", "at"}, ...]}
                                       -> same as GET plus {applied, duplicates, rejected: [{id, reason}]}
Auth: `Authorization: Bearer $TV_API_TOKEN`. If TV_API_TOKEN is unset the API answers 503 (disabled).
It cannot unlock topics (that needs the parent PIN via MCP/parent page); events for locked topics are
rejected. Levels are decided by the coach, so a TV cannot claim a level. CORS origins: TV_CORS_ORIGINS
(comma separated; default = local Expo web/preview ports).
"""
from __future__ import annotations

import hmac
import json
import os
import re

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import curriculum
from .service import Coach, InvalidInput

MAX_BODY = 64 * 1024
MAX_EVENTS = 100
ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
DEFAULT_ORIGINS = "http://localhost:8082,http://127.0.0.1:8082,http://localhost:5173,http://127.0.0.1:5173"


def _err(status: int, msg: str) -> JSONResponse:
    return JSONResponse({"error": msg}, status_code=status)


def validate_events(body: object) -> list[dict]:
    """Strict validation; raises InvalidInput."""
    ev = body.get("events") if isinstance(body, dict) else None
    if not isinstance(ev, list) or not ev:
        raise InvalidInput("Body must be {\"events\": [ ... ]} with at least one event.")
    if len(ev) > MAX_EVENTS:
        raise InvalidInput(f"At most {MAX_EVENTS} events per request.")
    out, ids = [], set()
    for e in ev:
        if not isinstance(e, dict):
            raise InvalidInput("Each event must be an object.")
        eid, topic, correct, at = e.get("id"), e.get("topic"), e.get("correct"), e.get("at")
        if not isinstance(eid, str) or not ID_RE.match(eid):
            raise InvalidInput("event.id must be 1-64 chars of A-Z a-z 0-9 _ -.")
        if eid in ids:
            raise InvalidInput("Duplicate event.id in one request.")
        ids.add(eid)
        if not isinstance(topic, str) or topic not in curriculum.TOPICS:
            raise InvalidInput("event.topic must be a known topic.")
        if not isinstance(correct, bool):
            raise InvalidInput("event.correct must be true or false.")
        if isinstance(at, bool) or not isinstance(at, (int, float)) or not 0 <= at < 2**53:
            raise InvalidInput("event.at must be a non-negative number (ms since epoch).")
        out.append({"id": eid, "topic": topic, "correct": correct, "at": at})
    return out


def make_tv_app(coach: Coach, token: str | None = None) -> Starlette:
    token = token if token is not None else os.getenv("TV_API_TOKEN")

    def guard(request: Request) -> JSONResponse | None:
        if not token:
            return _err(503, "TV API disabled: set TV_API_TOKEN.")
        got = request.headers.get("authorization", "")
        if not hmac.compare_digest(got.encode(), f"Bearer {token}".encode()):
            return _err(401, "unauthorized")
        return None

    async def get_progress(request: Request):
        if (r := guard(request)):
            return r
        try:
            return JSONResponse(coach.tv_progress(request.path_params["child"]))
        except InvalidInput as e:
            return _err(400, str(e))

    async def post_events(request: Request):
        if (r := guard(request)):
            return r
        if int(request.headers.get("content-length") or 0) > MAX_BODY:
            return _err(413, "Body too large.")
        raw = await request.body()
        if len(raw) > MAX_BODY:
            return _err(413, "Body too large.")
        try:
            events = validate_events(json.loads(raw))
            return JSONResponse(coach.tv_events(request.path_params["child"], events))
        except (ValueError, InvalidInput) as e:  # json errors are ValueError too
            return _err(400, str(e) if isinstance(e, InvalidInput) else "Body must be valid JSON.")

    origins = [o.strip() for o in os.getenv("TV_CORS_ORIGINS", DEFAULT_ORIGINS).split(",") if o.strip()]
    return Starlette(
        routes=[Route("/progress/{child}", get_progress, methods=["GET"]),
                Route("/progress/{child}/events", post_events, methods=["POST"])],
        middleware=[Middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"],
                               allow_headers=["authorization", "content-type"], max_age=600)])
