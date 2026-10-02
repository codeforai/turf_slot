"""Structured logging, request IDs, access logs and Prometheus metrics."""

import json
import logging
import time
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime

from prometheus_client import Counter, Gauge, Histogram
from starlette.routing import Match
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import Settings

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

# --- Metrics -----------------------------------------------------------------

HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests handled", ["method", "route", "status"])
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
HTTP_IN_PROGRESS = Gauge("http_requests_in_progress", "HTTP requests currently being handled")
BOOKINGS_CREATED = Counter("turfslot_bookings_created_total", "Bookings created")
BOOKING_CONFLICTS = Counter("turfslot_booking_conflicts_total", "Booking attempts rejected because the slot was taken")
PAYMENTS_CONFIRMED = Counter("turfslot_payments_confirmed_total", "Payments confirmed by owners/admins")
BOOKINGS_CANCELLED = Counter("turfslot_bookings_cancelled_total", "Bookings cancelled", ["by"])
HOLDS_EXPIRED = Counter("turfslot_booking_holds_expired_total", "Unpaid booking holds released")

# --- Logging -----------------------------------------------------------------


class JsonFormatter(logging.Formatter):
    _RESERVED = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        for key, value in vars(record).items():
            if key not in self._RESERVED and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


class PlainFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.request_id = request_id_var.get()
        return super().format(record)


def configure_logging(settings: Settings) -> None:
    handler = logging.StreamHandler()
    if settings.log_json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(PlainFormatter("%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level.upper())
    # Our middleware writes access logs; silence uvicorn's duplicate ones.
    logging.getLogger("uvicorn.access").disabled = True


access_logger = logging.getLogger("turfslot.access")
error_logger = logging.getLogger("turfslot.error")

# --- Middleware --------------------------------------------------------------


def _route_template(scope: Scope) -> str:
    """Return the matched route path (e.g. /bookings/{booking_id}) to keep metric labels bounded.

    FastAPI records the route it matched in scope["route"]; before routing (or for paths that
    match nothing) we fall back to matching the app's routes ourselves.
    """
    matched = scope.get("route")
    if matched is not None and getattr(matched, "path", None):
        return matched.path
    app = scope.get("app")
    for route in getattr(app, "routes", []):
        match, _ = route.matches(scope)
        if match == Match.FULL:
            return getattr(route, "path", "unmatched")
    return "unmatched"


class ObservabilityMiddleware:
    """Pure ASGI middleware: request ID, JSON access log, latency and status metrics."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        incoming = headers.get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if 0 < len(incoming) <= 64 else uuid.uuid4().hex
        token = request_id_var.set(request_id)

        route = "unmatched"
        method = scope["method"]
        status_code = 500
        response_started = False
        start = time.perf_counter()
        finished: float | None = None  # when the last body chunk went out (before background tasks)

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code, response_started, finished
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message["status"]
                message["headers"] = [*message.get("headers", []), (b"x-request-id", request_id.encode("latin-1"))]
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                finished = time.perf_counter()
            await send(message)

        HTTP_IN_PROGRESS.inc()
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # Unhandled error: log it with the request ID and return a generic 500
            # so internals (stack traces, SQL) never leak to clients.
            status_code = 500
            error_logger.exception("unhandled error", extra={"path": scope.get("path")})
            if not response_started:
                body = json.dumps({"detail": "Internal server error", "request_id": request_id}).encode()
                await send_wrapper(
                    {
                        "type": "http.response.start",
                        "status": 500,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode()),
                        ],
                    }
                )
                await send_wrapper({"type": "http.response.body", "body": body})
        finally:
            duration = (finished or time.perf_counter()) - start
            HTTP_IN_PROGRESS.dec()
            route = _route_template(scope)  # routing has run now, so this is the exact template
            quiet = route in ("/metrics", "/health", "/health/ready")
            if not quiet:
                HTTP_REQUESTS.labels(method, route, str(status_code)).inc()
                HTTP_LATENCY.labels(method, route).observe(duration)
            client = scope.get("client")
            log = access_logger.debug if quiet and status_code < 400 else access_logger.info
            log(
                "request",
                extra={
                    "method": method,
                    "path": scope.get("path"),
                    "route": route,
                    "status": status_code,
                    "duration_ms": round(duration * 1000, 2),
                    "client_ip": client[0] if client else None,
                },
            )
            request_id_var.reset(token)
