"""Demo pre-flight: is everything the live demo depends on actually ready?

    make preflight            # print the checklist; exit 1 if not ready
    GET /api/preflight        # the same checks, for the dashboard

Every check reads the real system (HTTP health, Switch's own bridge list, the switch-core log,
the running service's measurements, process ids, the incident store). Nothing is assumed.
A check is `critical` (the demo will fail without it) or a `warning` (it degrades the demo).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text

from app.agents.common import get_agent_settings, load_credentials, load_topology
from app.core.config import REPO_ROOT
from app.db.session import get_sessionmaker
from app.incidents import store
from app.switch.client import SwitchAgentClient
from app.switch.config import get_switch_settings

SWITCH_CONTAINER = os.environ.get("SWITCH_CONTAINER", "blackbox-switch-switch-1")
SLACK_SESSION_MAX_AGE_MIN = 20
PROCESSES = ("launcher", "backend", "loadgen", "monitor", "sentinel", "rootcause", "recovery")
LOG_TS = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)")


def _check(name: str, ok: bool | None, detail: str, fix: str = "", critical: bool = True) -> dict:
    """ok=True pass, ok=False fail, ok=None unknown (treated as a warning)."""
    return {
        "name": name,
        "ok": ok,
        "critical": critical,
        "detail": detail,
        "fix": "" if ok else fix,
    }


def _get(url: str, timeout: float = 3) -> httpx.Response | None:
    try:
        return httpx.get(url, timeout=timeout)
    except httpx.HTTPError:
        return None


def check_database() -> dict:
    try:
        with get_sessionmaker()() as s:
            s.execute(text("SELECT 1"))
        return _check("BLACKBOX database", True, "reachable")
    except Exception as exc:  # noqa: BLE001
        return _check("BLACKBOX database", False, type(exc).__name__, "Check BLACKBOX_DATABASE_URL")


def check_switch() -> list[dict]:
    cfg = get_switch_settings()
    r = _get(f"{cfg.api_url}/health")
    if r is None or r.status_code != 200:
        return [
            _check(
                "Switch server",
                False,
                f"{cfg.api_url} not answering",
                "Start the Switch stack (docker compose) and wait for /health",
            )
        ]
    out = [_check("Switch server", True, f"{cfg.api_url} healthy")]
    try:
        topo = load_topology()
    except SystemExit as exc:
        return [*out, _check("Switch topology", False, str(exc), "make agents-bootstrap")]
    try:

        async def bridges() -> list[dict]:
            c = SwitchAgentClient(cfg.api_url, load_credentials("control"), timeout=10)
            try:
                return await c.op("list_bridges", {}) or []
            finally:
                await c.aclose()

        rows = _run_async(bridges)
    except Exception as exc:  # noqa: BLE001
        return [
            *out,
            _check(
                "Incident chat connection",
                False,
                f"list_bridges failed: {exc}",
                "Check the agents' Switch credentials (make agents-bootstrap)",
            ),
        ]
    wanted = topo.bridge_id
    bridge = next(
        (b for b in rows if (b.get("id") == wanted) or (not wanted and b.get("is_default"))), None
    )
    if bridge is None:
        out.append(
            _check(
                "Incident chat connection",
                False,
                f"bridge {wanted or 'default'} not found in Switch",
                "Reconnect it in the Switch Gateway, or fix SWITCH_INCIDENT_BRIDGE_ID "
                "and re-run make agents-bootstrap",
            )
        )
        return out
    active = bridge.get("status") == "active"
    out.append(
        _check(
            "Incident chat connection",
            active,
            f"{bridge.get('display_name')} ({bridge.get('type')}): {bridge.get('status')}",
            "Reconnect the messaging app in the Switch Gateway",
        )
    )
    configured = get_switch_settings().incident_bridge_id
    if (configured or None) != (topo.bridge_id or None):
        out.append(
            _check(
                "Bootstrap matches config",
                False,
                "SWITCH_INCIDENT_BRIDGE_ID differs from the bootstrapped topology",
                "make agents-bootstrap",
            )
        )
    if bridge.get("type") == "slack":
        out.append(check_slack_socket())
    elif bridge.get("type") == "mattermost":
        r = _get(f"{cfg.mattermost_url}/api/v4/system/ping", timeout=5)
        out.append(
            _check(
                "Mattermost",
                r is not None and r.status_code == 200,
                "ping ok" if r is not None and r.status_code == 200 else "not answering",
                "docker restart blackbox-switch-mattermost-1, wait ~1 min",
            )
        )
    return out


def _run_async(coro_fn: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
    """Run a coroutine from sync code whether or not the caller is inside an event loop."""
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        return pool.submit(lambda: asyncio.run(coro_fn())).result(timeout=30)


def check_slack_socket(now: datetime | None = None) -> dict:
    """Switch's Slack Socket Mode session can die silently (seen live on 2026-11-07): it looks
    connected but delivers nothing. A fresh session is the reliable state, so report its age."""
    name = "Switch Slack connection"
    fix = f"docker restart {SWITCH_CONTAINER} (make demo-reset does this)"
    if not shutil.which("docker"):
        return _check(name, None, "docker CLI not found; cannot read switch-core logs", fix, False)
    try:
        logs = subprocess.run(
            ["docker", "logs", "--since", "6h", SWITCH_CONTAINER],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return _check(name, None, f"cannot read logs: {exc}", fix, False)
    return parse_slack_socket((logs.stdout + logs.stderr).splitlines(), now)


def parse_slack_socket(lines: list[str], now: datetime | None = None) -> dict:
    name = "Switch Slack connection"
    fix = f"docker restart {SWITCH_CONTAINER} (make demo-reset does this)"
    events = []
    for line in lines:
        if "socket_mode" not in line:
            continue
        m = LOG_TS.match(line)
        if not m:
            continue
        at = datetime.fromisoformat(m.group(1)).replace(tzinfo=UTC)
        if "has been established" in line:
            events.append((at, "up"))
        elif "stale" in line or "abandoned" in line or "already closed" in line:
            events.append((at, "down"))
    if not events:
        return _check(name, False, "no Slack session seen in the last 6 h", fix)
    at, state = max(events)
    if state == "down":
        return _check(
            name, False, f"session dropped at {at:%H:%M:%S} UTC and not re-established", fix
        )
    age = ((now or datetime.now(UTC)) - at).total_seconds() / 60
    if age > SLACK_SESSION_MAX_AGE_MIN:
        return _check(
            name,
            False,
            f"session established {age:.0f} min ago; long-lived sessions have died silently before",
            fix,
            critical=False,
        )
    return _check(name, True, f"fresh session established {age:.0f} min ago")


def check_service() -> list[dict]:
    cfg = get_agent_settings()
    r = _get(f"{cfg.service_url}/metrics/summary")
    if r is None:
        return [_check("payment-service", False, "not running", "make stack-up")]
    m = r.json()
    try:
        baseline = json.loads((cfg.runtime / "scenarios.json").read_text())["baseline"]
    except (OSError, ValueError, KeyError):
        baseline = None
    out = [
        _check(
            "Healthy baseline deployed",
            baseline is not None and m.get("git_sha") == baseline,
            f"deployed {str(m.get('git_sha'))[:7]} (baseline {str(baseline)[:7]})",
            "make demo-reset",
        ),
        _check(
            "Service healthy",
            (m.get("error_rate_pct") or 0) < 1.0 and (m.get("latency_ms_p50") or 0) < 300,
            f"5xx {m.get('error_rate_pct')}% · p50 {m.get('latency_ms_p50')} ms · pool "
            f"{m.get('pool_utilization_pct')}%",
            "Wait ~30 s after start, or make demo-reset",
        ),
        _check(
            "Customer traffic flowing",
            (m.get("requests") or 0) >= 1000,
            f"{m.get('requests')} requests in the window",
            "The load generator is not running at full rate: make stack-up",
        ),
    ]
    surge = cfg.runtime / "load-control.json"
    out.append(
        _check(
            "No traffic surge running",
            not surge.exists(),
            "surge active" if surge.exists() else "normal traffic",
            "make sim-surge-off",
        )
    )
    return out


def check_processes(pids_dir: Path | None = None) -> dict:
    pids_dir = pids_dir or (get_agent_settings().runtime / "pids")
    down = []
    for name in PROCESSES:
        try:
            os.kill(int((pids_dir / f"{name}.pid").read_text().strip()), 0)
        except (OSError, ValueError):
            down.append(name)
    return _check(
        "Stack processes (agents, monitor, load)",
        not down,
        "all running" if not down else f"not running: {', '.join(down)}",
        "make stack-up",
    )


def check_leftovers() -> list[dict]:
    incidents = store.open_incidents()
    approvals = store.open_approvals()
    return [
        _check(
            "No incident open",
            not incidents,
            "none"
            if not incidents
            else ", ".join(f"{i['incident_id']} ({i['state']})" for i in incidents),
            "make demo-reset (closes it; only one incident can be open at a time)",
        ),
        _check(
            "No approval left open",
            not approvals,
            "none" if not approvals else ", ".join(a["approval_id"] for a in approvals),
            "make demo-reset (revokes and closes them)",
        ),
    ]


def check_slack_notifications() -> dict:
    from app.slack import bridge as slack_bridge

    h = slack_bridge.health()
    ok = h["status"] in ("healthy", "idle")
    return _check(
        "Slack notification bot",
        ok if h["connected"] else None,
        f"{h['status']}" + (f" · {h['last_error']}" if h.get("last_error") else ""),
        "See /settings/slack",
        critical=False,
    )


def check_claude() -> dict:
    binary = get_agent_settings().claude_bin
    found = shutil.which(binary)
    return _check(
        "Claude Code CLI",
        bool(found),
        found or f"{binary} not on PATH",
        "Install Claude Code; otherwise RootCause uses the labelled fallback patch",
        critical=False,
    )


def run_checks() -> dict[str, Any]:
    checks: list[dict] = [check_database()]
    for group in (check_switch, check_service, check_leftovers):
        try:
            checks.extend(group())
        except Exception as exc:  # noqa: BLE001 - a broken check is a failed check
            checks.append(_check(group.__name__, False, f"{type(exc).__name__}: {exc}"))
    for one in (check_processes, check_slack_notifications, check_claude):
        try:
            checks.append(one())
        except Exception as exc:  # noqa: BLE001
            checks.append(
                _check(one.__name__, None, f"{type(exc).__name__}: {exc}", critical=False)
            )
    ready = all(c["ok"] for c in checks if c["critical"])
    return {"ready": ready, "checked_at": datetime.now(UTC).isoformat(), "checks": checks}


def print_report(report: dict[str, Any]) -> None:
    for c in report["checks"]:
        mark = "PASS" if c["ok"] else ("FAIL" if c["ok"] is False and c["critical"] else "WARN")
        print(f"  {mark:<4}  {c['name']:<42} {c['detail']}")
        if c["fix"] and not c["ok"]:
            print(f"        -> {c['fix']}")
    print("\nREADY FOR THE DEMO" if report["ready"] else "\nNOT READY (fix the FAIL lines)")


def main() -> None:
    os.chdir(REPO_ROOT / "backend")
    report = run_checks()
    print_report(report)
    sys.exit(0 if report["ready"] else 1)


if __name__ == "__main__":
    main()
