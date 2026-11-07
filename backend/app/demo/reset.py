"""One-command demo reset: `make demo-reset` (add ARGS=--keep-switch to skip the Switch restart).

Brings the whole sandbox back to the same READY state every time:

1. stop the BLACKBOX stack (agents, monitor, load, backend, launcher);
2. close what the last run left open: incidents (-> CLOSED, audited), BLACKBOX approvals
   (revoked; the Switch request is closed when still open), and acknowledge undelivered Switch
   approval outcomes so a restarted Recovery does not act on them;
3. reset the simulation (rebuild the demo repo, healthy release #841, no surge);
4. restart switch-core, so Switch's Slack Socket Mode session is fresh (it has died silently
   before), and wait for Switch and the Slack session;
5. start the stack and wait until the pre-flight checklist is green.

History is kept: closing is an audited state change, nothing is deleted.
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import time
from datetime import UTC, datetime

import httpx

from app.agents.common import load_credentials
from app.core.config import REPO_ROOT
from app.demo import preflight
from app.incidents import store
from app.switch.client import SwitchAgentClient, SwitchError
from app.switch.config import get_switch_settings

STACK = REPO_ROOT / "scripts" / "stack.sh"
ACTOR = "demo-reset"


def step(msg: str) -> None:
    print(f"\n== {msg}", flush=True)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:  # noqa: ANN003
    return subprocess.run(cmd, check=True, text=True, **kw)


async def _clear_leftovers() -> None:
    from app.switch.revoke import revoke

    for a in store.open_approvals():
        result = await revoke(a["approval_id"], "closed by demo reset", ACTOR)
        print(
            f"   approval {a['approval_id']}: revoked={result['revoked']} "
            f"switch_close={result.get('switch_close')}"
        )
    for inc in store.open_incidents():
        store.close_incident(inc["incident_id"], ACTOR, f"demo reset (was {inc['state']})")
        print(f"   incident {inc['incident_id']}: {inc['state']} -> CLOSED")
    client = SwitchAgentClient(get_switch_settings().api_url, load_credentials("recovery"))
    try:
        outcomes = await client.undelivered_outcomes()
        for o in outcomes:
            sid, rid = (
                o.get("sessionId") or o.get("session_id"),
                o.get("requestId") or o.get("request_id"),
            )
            try:
                await client.mark_delivered(sid, rid)
            except SwitchError as exc:
                print(f"   outcome {rid}: could not acknowledge ({exc.status})")
        print(f"   acknowledged {len(outcomes)} stale Switch approval outcome(s)")
    finally:
        await client.aclose()


def restart_switch(timeout: float = 120) -> None:
    container = preflight.SWITCH_CONTAINER
    since = datetime.now(UTC).replace(microsecond=0)
    run(["docker", "restart", container], capture_output=True)
    api = get_switch_settings().api_url
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{api}/health", timeout=3).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(2)
    else:
        raise SystemExit(f"Switch did not come back within {timeout:.0f} s")
    print(f"   {container} restarted and healthy")
    # Wait for the Slack session (only matters when incident rooms are on Slack).
    while time.monotonic() < deadline:
        out = subprocess.run(
            ["docker", "logs", "--since", since.isoformat(), container],
            capture_output=True,
            text=True,
        )
        if "socket_mode" in (logs := out.stdout + out.stderr) and "has been established" in logs:
            print("   Slack Socket Mode session established")
            return
        time.sleep(2)
    print("   (no Slack session seen; fine if incidents run on Mattermost)")


def wait_ready(timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    report = preflight.run_checks()
    while not report["ready"] and time.monotonic() < deadline:
        time.sleep(5)
        report = preflight.run_checks()
    return report


def main() -> None:
    p = argparse.ArgumentParser(description="Reset the BLACKBOX demo to a READY state.")
    p.add_argument("--keep-switch", action="store_true", help="do not restart switch-core")
    p.add_argument("--wait", type=float, default=150, help="seconds to wait for READY")
    a = p.parse_args()
    started = time.monotonic()

    step("1/5 stopping the stack")
    run([str(STACK), "down"])

    step("2/5 closing what the last run left open")
    asyncio.run(_clear_leftovers())

    step("3/5 resetting the simulation (healthy release #841, no surge)")
    run(
        [
            "bash",
            "-c",
            "set -a && . ./.env && set +a && cd simulator && uv run python -m simulator.ctl reset",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
    )
    print("   demo repo rebuilt, deploy history reset, release #841 marked deployed")

    if a.keep_switch:
        step("4/5 keeping switch-core running (--keep-switch)")
    else:
        step("4/5 restarting switch-core for a fresh Slack session")
        restart_switch()

    step("5/5 starting the stack and waiting for the pre-flight checklist")
    run([str(STACK), "up"])
    report = wait_ready(a.wait)
    print()
    preflight.print_report(report)
    print(f"\n(reset took {time.monotonic() - started:.0f} s)")
    sys.exit(0 if report["ready"] else 1)


if __name__ == "__main__":
    main()
