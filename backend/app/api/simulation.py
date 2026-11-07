"""Error simulation for demos: list the fault scenarios and trigger one in the sandbox.

Triggering a deploy scenario deploys a real commit through the simulator's launcher, exactly
like `make sim-trigger`; the load scenario only raises the simulated customer traffic. The
agents then react on their own; nothing here talks to an agent, Switch or the incident store
beyond an audit record.

Guard rails: requests must come from this machine, never in production, and only from the
healthy baseline with no incident open (one incident at a time, matching the store's lock).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Request

from app.agents.common import get_agent_settings
from app.core.config import get_settings
from app.core.errors import ApiError
from app.incidents import store

router = APIRouter(prefix="/api/simulation", tags=["simulation"])
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost", "testclient"}
HEALTHY_MAX_5XX = 1.0


def _runtime() -> Path:
    return get_agent_settings().runtime


def _catalog() -> dict[str, Any] | None:
    path = _runtime() / "scenarios.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _surge() -> dict[str, Any] | None:
    try:
        return json.loads((_runtime() / "load-control.json").read_text())
    except (OSError, ValueError):
        return None


def _metrics() -> dict[str, Any] | None:
    try:
        return httpx.get(f"{get_agent_settings().service_url}/metrics/summary", timeout=3).json()
    except (httpx.HTTPError, ValueError):
        return None


def _deployments() -> list[dict[str, Any]]:
    path = _runtime() / "deployments.jsonl"
    try:
        return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    except (OSError, ValueError):
        return []


def _state() -> dict[str, Any]:
    catalog = _catalog()
    metrics = _metrics()
    surge = _surge()
    active = store.active_incident()
    deps = _deployments()
    last = deps[-1] if deps else {}
    baseline = (catalog or {}).get("baseline")

    blocked = None
    if get_settings().app_env == "production":
        blocked = "Simulation is disabled in production."
    elif catalog is None:
        blocked = "No scenario catalog: run `make sim-reset` first."
    elif metrics is None:
        blocked = "payment-service is not running: start the stack (`make stack-up`)."
    elif active:
        blocked = f"{active['incident_id']} is still open ({active['state']}). One at a time."
    elif surge:
        blocked = "A traffic surge is running. End it first."
    elif metrics.get("git_sha") != baseline:
        blocked = "The service is not on the healthy baseline (#841): run `make sim-reset`."
    elif (metrics.get("error_rate_pct") or 0) >= HEALTHY_MAX_5XX:
        blocked = "The service is not healthy yet; wait for it to settle."

    return {
        "blocked_reason": blocked,
        "baseline": baseline,
        "deployed": {
            "deploy_id": last.get("deploy_id"),
            "git_sha": (metrics or {}).get("git_sha"),
            "message": last.get("commit_message"),
            "on_baseline": bool(metrics) and metrics.get("git_sha") == baseline,
        },
        "metrics": {
            k: (metrics or {}).get(k)
            for k in ("error_rate_pct", "latency_ms_p50", "pool_utilization_pct", "requests")
        }
        if metrics
        else None,
        "surge": surge,
        "active_incident": {"incident_id": active["incident_id"], "state": active["state"]}
        if active
        else None,
        "latest_incident": (
            lambda i: {"incident_id": i["incident_id"], "state": i["state"]} if i else None
        )(store.latest_incident()),
        "scenarios": [
            {
                k: sc.get(k)
                for k in (
                    "id",
                    "title",
                    "kind",
                    "category",
                    "summary",
                    "breaks",
                    "expected",
                    "agents",
                    "multiplier",
                )
            }
            | {"commit": (sc.get("sha") or "")[:12] or None}
            for sc in (catalog or {}).get("scenarios", [])
        ],
    }


def _require_local(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in LOCAL_HOSTS:
        raise ApiError(403, "local_only", "Simulation controls only work from this machine.")
    if get_settings().app_env == "production":
        raise ApiError(403, "disabled", "Simulation is disabled in production.")


@router.get("")
def simulation_state() -> dict[str, Any]:
    return _state()


@router.post("/scenarios/{scenario_id}/trigger")
def trigger(scenario_id: str, request: Request) -> dict[str, Any]:
    _require_local(request)
    state = _state()
    if state["blocked_reason"]:
        raise ApiError(409, "not_ready", state["blocked_reason"])
    sc = next((s for s in (_catalog() or {}).get("scenarios", []) if s["id"] == scenario_id), None)
    if sc is None:
        raise ApiError(404, "not_found", "No such scenario.")

    if sc["kind"] == "load":
        multiplier = float(sc.get("multiplier") or 5.0)
        (_runtime() / "load-control.json").write_text(
            json.dumps(
                {
                    "multiplier": multiplier,
                    "scenario": sc["id"],
                    "since": datetime.now(UTC).isoformat(),
                }
            )
        )
        result: dict[str, Any] = {"surge": True, "multiplier": multiplier}
    else:
        cfg = get_agent_settings()
        ids = [int(d["deploy_id"]) for d in _deployments() if str(d.get("deploy_id")).isdigit()]
        deploy_id = str(max(ids, default=841) + 1)
        try:
            r = httpx.post(
                f"{cfg.launcher_url}/deploy",
                headers={"Authorization": f"Bearer {cfg.launcher_token}"},
                json={
                    "sha": sc["sha"],
                    "deploy_id": deploy_id,
                    "source": "fault-injection",
                },
                timeout=60,
            )
        except httpx.HTTPError as exc:
            raise ApiError(502, "launcher_unreachable", f"Launcher: {type(exc).__name__}") from None
        if r.status_code >= 400:
            raise ApiError(502, "deploy_failed", f"Launcher said HTTP {r.status_code}")
        body = r.json()
        result = {"deploy_id": deploy_id, "git_sha": sc["sha"], "status": body.get("status")}
    store.audit("simulation.triggered", "demo-operator", {"scenario": sc["id"], **result})
    return {"scenario": sc["id"], **result}


@router.post("/surge/stop")
def stop_surge(request: Request) -> dict[str, Any]:
    _require_local(request)
    path = _runtime() / "load-control.json"
    was = path.exists()
    path.unlink(missing_ok=True)
    if was:
        store.audit("simulation.surge_stopped", "demo-operator", {})
    return {"surge": False, "was_running": was}
