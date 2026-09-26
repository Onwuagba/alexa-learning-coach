"""Production hardening: structured JSON logs, /metrics-style counters, rate limiting, secrets adapter.

Logs never contain child names, answers or PINs: only family ids (opaque), route, status and timings."""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import uuid
from collections import defaultdict


# -- structured logging -----------------------------------------------------------------------------
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = {"ts": round(record.created, 3), "level": record.levelname, "msg": record.getMessage(),
             "logger": record.name}
        d.update(getattr(record, "fields", {}))
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)
        return json.dumps(d, default=str)


def get_logger(name: str = "learning_coach") -> logging.Logger:
    log = logging.getLogger(name)
    if not log.handlers:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(JsonFormatter())
        log.addHandler(h)
        log.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
        log.propagate = False
    return log


def log_event(msg: str, **fields) -> None:
    get_logger().info(msg, extra={"fields": fields})


# -- metrics ------------------------------------------------------------------------------------------
class Metrics:
    """Tiny Prometheus-text counters. Labels must be low-cardinality (never child names)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.c: dict[tuple, float] = defaultdict(float)
        self.started = time.time()

    def inc(self, name: str, n: float = 1, **labels) -> None:
        with self.lock:
            self.c[(name, tuple(sorted(labels.items())))] += n

    def get(self, name: str, **labels) -> float:
        with self.lock:
            return self.c.get((name, tuple(sorted(labels.items()))), 0.0)

    def render(self) -> str:
        with self.lock:
            items = sorted(self.c.items())
        lines = [f"# TYPE learning_coach_{n} counter" for n in sorted({k[0] for k, _ in items})]
        for (name, labels), v in items:
            lab = ",".join(f'{k}="{str(x).replace(chr(34), "")}"' for k, x in labels)
            lines.append(f"learning_coach_{name}{{{lab}}} {v:g}" if lab else f"learning_coach_{name} {v:g}")
        lines.append("# TYPE learning_coach_uptime_seconds gauge")
        lines.append(f"learning_coach_uptime_seconds {time.time() - self.started:.0f}")
        return "\n".join(lines) + "\n"


METRICS = Metrics()


def emit_emf(metrics: dict[str, tuple[float, str]], dimensions: dict[str, str] | None = None,
             namespace: str | None = None, **props) -> bool:
    """Write one CloudWatch Embedded Metric Format log line (JSON on stderr), so CloudWatch Logs extracts metrics
    with no extra API calls or IAM permissions. Enabled only when EMF_NAMESPACE (or `namespace`) is set.

    Structure follows the EMF specification (_aws.Timestamp in ms, _aws.CloudWatchMetrics[Namespace, Dimensions,
    Metrics[Name, Unit]], target members at the root). Each metric also gets an un-dimensioned series (for account-wide alarms). Dimensions must be LOW
    cardinality (each combination is a billed custom metric): never child names or family ids. `props` are extra searchable fields, not metrics.
    Source: docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Embedded_Metric_Format_Specification.html
    NOT verified against a live CloudWatch (TODO-verify that the metrics appear in the console)."""
    ns = namespace or os.getenv("EMF_NAMESPACE")
    if not ns or not metrics:
        return False
    dims = {k: str(v) for k, v in (dimensions or {}).items()}
    doc = {"_aws": {"Timestamp": int(time.time() * 1000),
                    "CloudWatchMetrics": [{"Namespace": ns, "Dimensions": [sorted(dims), []] if dims else [[]],
                                           "Metrics": [{"Name": n, "Unit": u} for n, (_, u) in metrics.items()]}]},
           **dims, **{n: v for n, (v, _) in metrics.items()}, **props}
    get_logger().info("emf", extra={"fields": doc})
    return True


# -- rate limiting ------------------------------------------------------------------------------------
class RateLimiter:
    """In-memory token bucket per key. Per-process: behind several instances the effective limit is
    multiplied (use an edge/WAF limit or Redis for a hard global limit)."""

    def __init__(self, per_minute: float, burst: float | None = None, clock=time.monotonic):
        self.rate, self.burst, self.clock = per_minute / 60.0, burst or per_minute, clock
        self.b: dict[str, tuple[float, float]] = {}
        self.lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, float]:
        """(allowed, seconds_to_wait)."""
        now = self.clock()
        with self.lock:
            if len(self.b) > 50_000:  # crude memory bound
                self.b.clear()
            tokens, last = self.b.get(key, (self.burst, now))
            tokens = min(self.burst, tokens + (now - last) * self.rate)
            if tokens >= 1:
                self.b[key] = (tokens - 1, now)
                return True, 0.0
            self.b[key] = (tokens, now)
            return False, (1 - tokens) / self.rate


class OpsMiddleware:
    """ASGI: request id, JSON access log, metrics, rate limits. Strict bucket for sign-in / sign-up."""

    STRICT = ("/parent/login", "/parent/signup")

    def __init__(self, app, general_per_min: float | None = None, strict_per_min: float | None = None):
        self.app = app
        self.general = RateLimiter(general_per_min or float(os.getenv("RATE_LIMIT_PER_MIN", "240")))
        self.strict = RateLimiter(strict_per_min or float(os.getenv("AUTH_RATE_LIMIT_PER_MIN", "10")))
        self.trust_proxy = os.getenv("TRUST_PROXY") == "1"

    def _ip(self, scope) -> str:
        h = dict(scope["headers"])
        if self.trust_proxy and b"x-forwarded-for" in h:
            return h[b"x-forwarded-for"].decode().split(",")[-1].strip()  # last hop = the proxy's view of the client
        return (scope.get("client") or ("?", 0))[0]

    @staticmethod
    def _route(path: str) -> str:
        """Low-cardinality route label from a whitelist: child names sit in /api/progress/{child}, so they are
        never part of a label or log line."""
        parts = [p for p in path.split("/") if p]
        if not parts:
            return "/"
        if parts[0] == "api":
            return "/api/progress" if len(parts) > 1 and parts[1] == "progress" else "/api"
        if parts[0] == "parent":
            return "/parent/" + parts[1] if len(parts) > 1 and parts[1] in {
                "login", "signup", "unlock", "report", "export", "child", "settings", "tokens", "family", "logout"} else "/parent"
        return "/" + parts[0] if parts[0] in {"mcp", "healthz", "metrics"} else "/other"

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path, method, t0 = scope["path"], scope["method"], time.time()
        rid = uuid.uuid4().hex[:12]
        status = {"v": 500}
        ip = self._ip(scope)
        limiter = self.strict if (method == "POST" and path.rstrip("/") in self.STRICT) else self.general
        ok, wait = limiter.allow(f"{ip}:{'s' if limiter is self.strict else 'g'}")

        async def send2(msg):
            if msg["type"] == "http.response.start":
                status["v"] = msg["status"]
                msg = {**msg, "headers": [*msg.get("headers", []), (b"x-request-id", rid.encode())]}
            await send(msg)

        if path == "/healthz":
            return await self.app(scope, receive, send2)  # never rate-limit health checks
        if not ok:
            METRICS.inc("rate_limited_total", route=self._route(path))
            status["v"] = 429
            body = b'{"error":"rate limited"}'
            await send2({"type": "http.response.start", "status": 429, "headers": [
                (b"content-type", b"application/json"), (b"retry-after", str(int(wait) + 1).encode()),
                (b"content-length", str(len(body)).encode())]})
            await send2({"type": "http.response.body", "body": body})
        else:
            try:
                await self.app(scope, receive, send2)
            finally:
                pass
        dur = time.time() - t0
        route = self._route(path)
        METRICS.inc("http_requests_total", route=route, method=method, status=str(status["v"]))
        log_event("request", rid=rid, method=method, route=route, status=status["v"], ms=int(dur * 1000))


# -- secrets ---------------------------------------------------------------------------------------------
def get_secret(name: str, default: str | None = None) -> str | None:
    """Environment first. If AWS_SECRETS_ID is set, fall back to that Secrets Manager JSON secret's `name` key.

    The request shape (`get_secret_value(SecretId=...)`, JSON `SecretString`) is checked offline against the real
    service model with botocore's Stubber (tests/test_aws_stack.py). It was never run against live AWS
    (TODO-verify with a real secret; the role needs `secretsmanager:GetSecretValue` on that secret)."""
    v = os.getenv(name)
    if v:
        return v
    sid = os.getenv("AWS_SECRETS_ID")
    if sid:
        cache = get_secret.__dict__.setdefault("_cache", {})
        if sid not in cache:
            try:
                import boto3  # optional dependency
                raw = boto3.client("secretsmanager").get_secret_value(SecretId=sid)["SecretString"]
                cache[sid] = json.loads(raw)
            except Exception as e:  # missing boto3, no IAM, bad JSON...
                log_event("secrets_manager_unavailable", error=type(e).__name__)
                cache[sid] = {}
        if name in cache[sid]:
            return str(cache[sid][name])
    return default
