"""Health endpoints.

- GET /api/health/live: the process is up (no dependencies checked).
- GET /api/health: readiness. Checks the database and migration state; returns 503 when not ready.

Only real checks are reported. External integrations (Switch, simulator) are added here when
they are implemented, not before.
"""

import logging
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel
from sqlalchemy import text

from app import __version__
from app.core.config import get_settings
from app.db.migrate import migration_status
from app.db.session import get_engine

logger = logging.getLogger("app.health")
router = APIRouter(prefix="/api/health", tags=["health"])


class CheckResult(BaseModel):
    status: Literal["ok", "error"]
    detail: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    service: str
    version: str
    environment: str
    checks: dict[str, CheckResult]


class LiveResponse(BaseModel):
    status: Literal["ok"]


@router.get("/live", response_model=LiveResponse)
def live() -> LiveResponse:
    return LiveResponse(status="ok")


@router.get("", response_model=HealthResponse)
def health(response: Response) -> HealthResponse:
    settings = get_settings()
    checks: dict[str, CheckResult] = {}

    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = CheckResult(status="ok")
    except Exception as exc:  # noqa: BLE001 - reported as a failed check, not raised
        logger.warning("database check failed", extra={"error": str(exc)})
        checks["database"] = CheckResult(status="error", detail="database unreachable")

    if checks["database"].status == "ok":
        try:
            mig = migration_status(get_engine(), settings.database_url)
            checks["migrations"] = (
                CheckResult(status="ok", detail=f"at {mig['current']}")
                if mig["up_to_date"]
                else CheckResult(
                    status="error", detail=f"at {mig['current']}, head is {mig['head']}"
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("migration check failed", extra={"error": str(exc)})
            checks["migrations"] = CheckResult(status="error", detail="migration state unknown")

    healthy = all(c.status == "ok" for c in checks.values())
    if not healthy:
        response.status_code = 503
    return HealthResponse(
        status="ok" if healthy else "degraded",
        service=settings.app_name,
        version=__version__,
        environment=settings.app_env,
        checks=checks,
    )
