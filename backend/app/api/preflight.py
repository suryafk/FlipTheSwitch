"""GET /api/preflight: the demo readiness checklist (app/demo/preflight.py), read-only."""

from typing import Any

from fastapi import APIRouter

from app.demo.preflight import run_checks

router = APIRouter(tags=["demo"])


@router.get("/api/preflight")
def preflight() -> dict[str, Any]:
    return run_checks()
