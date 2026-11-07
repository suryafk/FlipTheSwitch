"""Simulation control: reset to HEALTHY, trigger the faulty deploy, show status.

python -m simulator.ctl reset     # rebuild repo, seed deploy history, mark C4 (#841) deployed
python -m simulator.ctl trigger [scenario]   # default: pool-exhaustion (C5, the original)
python -m simulator.ctl surge on|off         # the traffic-surge scenario (no deploy)
python -m simulator.ctl scenarios            # list the fault scenarios
python -m simulator.ctl status
"""

import json
import os
import shutil
import sys
from datetime import UTC, datetime

import httpx

from simulator import scenarios
from simulator.build_repo import build
from simulator.paths import (
    APP_LOG,
    DEPLOYMENTS,
    LAUNCHER_PORT,
    LOAD_CONTROL,
    PAYMENTS_DB,
    RELEASES,
    RUNTIME,
    SERVICE_HOST,
    SERVICE_PORT,
    SHAS,
    STATE,
)
from simulator.scenarios import POOL_EXHAUSTION

LAUNCHER = f"http://127.0.0.1:{LAUNCHER_PORT}"


def _token() -> dict:
    token = os.environ.get("SIM_LAUNCHER_TOKEN", "")
    if not token:
        raise SystemExit("SIM_LAUNCHER_TOKEN must be set")
    return {"Authorization": f"Bearer {token}"}


def reset() -> None:
    """Restore the HEALTHY baseline on disk. Start (or restart) the launcher afterwards."""
    for path in (RELEASES, RUNTIME / "worktrees", RUNTIME / "approved"):
        shutil.rmtree(path, ignore_errors=True)
    for path in (APP_LOG, PAYMENTS_DB, DEPLOYMENTS, LOAD_CONTROL):
        path.unlink(missing_ok=True)
    shas = build()
    seed = {
        "deploy_id": "841",
        "git_sha": shas["841"],
        "service": "payment-service",
        "environment": "staging-sandbox",
        "started_at": "2026-11-05T15:05:00Z",
        "finished_at": "2026-11-05T15:05:09Z",
        "status": "succeeded",
        "source": "seed",
        "actor": "ci-bot (simulated)",
        "commit_message": "feat: structured JSON logging and pool metrics",
        "changed_files": [],
        "approval_id": None,
    }
    DEPLOYMENTS.write_text(json.dumps(seed) + "\n")
    STATE.write_text(json.dumps({"sha": shas["841"], "deploy_id": "841"}))
    print(json.dumps({"reset": "ok", "deployed": shas["841"], "shas": shas}, indent=2))


def _next_deploy_id() -> str:
    ids = [
        int(json.loads(line)["deploy_id"])
        for line in DEPLOYMENTS.read_text().splitlines()
        if line.strip() and str(json.loads(line).get("deploy_id", "")).isdigit()
    ]
    return str(max(ids, default=841) + 1)


def trigger(scenario_id: str = POOL_EXHAUSTION) -> None:
    sc = scenarios.by_id(scenario_id)
    if sc is None:
        raise SystemExit(f"unknown scenario {scenario_id!r}; see `ctl scenarios`")
    shas = json.loads(SHAS.read_text())
    summary = httpx.get(f"http://{SERVICE_HOST}:{SERVICE_PORT}/metrics/summary", timeout=5).json()
    if summary.get("git_sha") != shas["841"]:
        raise SystemExit(f"refusing: deployed sha is {summary.get('git_sha')}, expected C4 (#841)")
    if sc["kind"] == "load":
        surge(True, sc.get("multiplier", 5.0))
        return
    sha = shas["scenarios"][scenario_id]
    r = httpx.post(
        f"{LAUNCHER}/deploy",
        headers=_token(),
        timeout=60,
        json={
            "sha": sha,
            "deploy_id": _next_deploy_id(),
            "source": "fault-injection",
        },
    )
    print(json.dumps(r.json(), indent=2))
    r.raise_for_status()


def surge(on: bool, multiplier: float = 5.0) -> None:
    """Traffic-surge scenario: the load generator picks this up within ~2 s (no deploy)."""
    if on:
        LOAD_CONTROL.write_text(
            json.dumps(
                {
                    "multiplier": multiplier,
                    "scenario": "traffic-surge",
                    "since": datetime.now(UTC).isoformat(),
                }
            )
        )
    else:
        LOAD_CONTROL.unlink(missing_ok=True)
    print(json.dumps({"surge": on, "multiplier": multiplier if on else 1.0}))


def status() -> None:
    out = {"at": datetime.now(UTC).isoformat()}
    try:
        out["launcher"] = httpx.get(f"{LAUNCHER}/status", timeout=3).json()
        out["metrics"] = httpx.get(
            f"http://{SERVICE_HOST}:{SERVICE_PORT}/metrics/summary", timeout=3
        ).json()
    except httpx.HTTPError as exc:
        out["error"] = repr(exc)
    print(json.dumps(out, indent=2))


def list_scenarios() -> None:
    for sc in scenarios.SCENARIOS:
        print(f"{sc['id']:<20} {sc['kind']:<7} {sc['expected']:<9} {sc['title']}")


if __name__ == "__main__":
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "trigger":
        trigger(*args[:1])
    elif cmd == "surge":
        surge(args[:1] == ["on"])
    else:
        {"reset": reset, "status": status, "scenarios": list_scenarios}[cmd]()
