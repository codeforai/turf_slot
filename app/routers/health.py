"""Liveness, readiness and Prometheus metrics endpoints."""

import logging
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Header, Response
from fastapi.responses import JSONResponse, RedirectResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.config import get_settings
from app.db import engine

router = APIRouter(tags=["health"])
logger = logging.getLogger("turfslot.health")
ALEMBIC_INI = Path(__file__).resolve().parents[2] / "alembic.ini"


@lru_cache
def expected_migration_head() -> str | None:
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        return ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_current_head()
    except Exception:
        logger.exception("could not read alembic head revision")
        return None


@router.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    """Send visitors of the bare URL to the interactive API docs."""
    return RedirectResponse(url="/docs")


@router.get("/health")
def liveness() -> dict[str, str]:
    """Is the process up? Cheap, no dependencies. Used by the platform to restart a stuck instance."""
    return {"status": "ok"}


@router.get("/health/ready")
def readiness() -> JSONResponse:
    """Can this instance serve traffic? Checks the database connection and that migrations are applied."""
    checks: dict[str, str] = {}
    healthy = True
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        checks["database"] = "ok"
        head = expected_migration_head()
        if head is not None and current != head:
            checks["migrations"] = f"behind (db={current}, code={head})"
            healthy = False
        else:
            checks["migrations"] = "ok"
    except Exception as exc:
        logger.warning("readiness check failed", extra={"error": str(exc)})
        checks["database"] = "unreachable"
        healthy = False
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ready" if healthy else "not_ready",
            "checks": checks,
            "version": get_settings().app_version,
        },
    )


@router.get("/metrics", include_in_schema=False)
def metrics(authorization: Annotated[str | None, Header()] = None) -> Response:
    """Prometheus scrape endpoint. When METRICS_TOKEN is set, scrapers must send it as a bearer token."""
    settings = get_settings()
    if not settings.metrics_enabled:
        return Response(status_code=404)
    if settings.metrics_token:
        expected = f"Bearer {settings.metrics_token}".encode()
        if authorization is None or not secrets.compare_digest(authorization.encode(), expected):
            return Response(status_code=401, headers={"WWW-Authenticate": "Bearer"})
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
