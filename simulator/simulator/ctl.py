"""Simulation control: reset to HEALTHY, trigger the faulty deploy, show status.

python -m simulator.ctl reset     # rebuild repo, seed deploy history, mark C4 (#841) deployed
python -m simulator.ctl trigger   # deploy C5 (#842) through the launcher (the "faulty deploy")
python -m simulator.ctl status
"""

import json
import os
import shutil
import sys
from datetime import UTC, datetime

import httpx

from simulator.build_repo import build
from simulator.paths import (
    APP_LOG,
    DEPLOYMENTS,
    LAUNCHER_PORT,
    PAYMENTS_DB,
    RELEASES,
    RUNTIME,
    SERVICE_HOST,
    SERVICE_PORT,
    SHAS,
    STATE,
)

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
    for path in (APP_LOG, PAYMENTS_DB, DEPLOYMENTS):
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


def trigger() -> None:
    shas = json.loads(SHAS.read_text())
    summary = httpx.get(f"http://{SERVICE_HOST}:{SERVICE_PORT}/metrics/summary", timeout=5).json()
    if summary.get("git_sha") != shas["841"]:
        raise SystemExit(f"refusing: deployed sha is {summary.get('git_sha')}, expected C4 (#841)")
    r = httpx.post(
        f"{LAUNCHER}/deploy",
        headers=_token(),
        timeout=60,
        json={"sha": shas["842"], "deploy_id": "842", "source": "fault-injection"},
    )
    print(json.dumps(r.json(), indent=2))
    r.raise_for_status()


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


if __name__ == "__main__":
    {"reset": reset, "trigger": trigger, "status": status}[sys.argv[1]]()
