"""War-room data for the dashboard: one incident, everything the room knows, read-only.

Every value comes from the incident store, the append-only audit log, the remediation's git
worktree or the simulated service's measured metrics. Free text goes through the same
sanitizer as Slack (secrets redacted, length capped). Nothing here can change state.
"""

from __future__ import annotations

import html
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter
from sqlalchemy import text

from app.agents.common import get_agent_settings
from app.api.incidents import INCIDENT_ID, public
from app.core.errors import ApiError
from app.db.session import get_sessionmaker
from app.incidents import store
from app.slack.messages import clean as slack_clean
from app.slack.messages import describe_row, short_identity

router = APIRouter(tags=["war-room"])


def clean(value: Any, limit: int = 280) -> str:
    """Slack's sanitizer (secrets redacted, code dropped, capped) without Slack's escaping:
    React renders text, so `<`, `>` and `&` must reach the UI as themselves."""
    return html.unescape(slack_clean(value, limit))


SHA = re.compile(r"^[0-9a-f]{7,40}$")
MAX_DIFF = 8000
STAGES = [
    ("DETECTED", "Detected"),
    ("INVESTIGATING", "Investigating"),
    ("ROOT_CAUSE_IDENTIFIED", "Root cause"),
    ("REMEDIATION_STAGED", "Patched"),
    ("AWAITING_APPROVAL", "Approval"),
    ("EXECUTING", "Deploying"),
    ("VERIFYING", "Verifying"),
    ("RESOLVED", "Resolved"),
]
STAGE_INDEX = {
    "DETECTED": 0,
    "INVESTIGATING": 1,
    "NEEDS_HUMAN": 1,
    "ROOT_CAUSE_IDENTIFIED": 2,
    "NEEDS_REVISION": 2,
    "REMEDIATION_STAGED": 3,
    "VALIDATING": 3,
    "AWAITING_APPROVAL": 4,
    "APPROVED": 4,
    "REJECTED": 4,
    "APPROVAL_EXPIRED": 4,
    "EXECUTING": 5,
    "VERIFYING": 6,
    "ROLLING_BACK": 6,
    "ROLLED_BACK": 6,
    "FAILED": 6,
    "RESOLVED": 7,
    "POSTMORTEM_GENERATED": 7,
    "PREVENTIVE_TASKS_CREATED": 7,
}


def _rows(sql: str, **params: Any) -> list[dict[str, Any]]:
    with get_sessionmaker()() as s:
        return [dict(r) for r in s.execute(text(sql), params).mappings().all()]


def _events(incident_id: str) -> list[dict[str, Any]]:
    """Audit events from this incident's creation onward."""
    return _rows(
        "SELECT seq, kind, actor, payload_json, created_at FROM audit_events WHERE seq >= "
        "(SELECT COALESCE(MIN(seq), 0) FROM audit_events WHERE kind='incident.created' AND "
        "payload_json LIKE :p) ORDER BY seq LIMIT 3000",
        p=f'%"incident_id":"{incident_id}"%',
    )


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(row["payload_json"] or "{}")
    except json.JSONDecodeError:
        return {}


def _last(events: list[dict[str, Any]], kind: str, pred=lambda p: True) -> dict[str, Any] | None:
    for row in reversed(events):
        if row["kind"] == kind:
            p = _payload(row)
            if pred(p):
                return {**p, "_at": row["created_at"]}
    return None


def _diff(worktree: str, base: str, commit: str) -> str | None:
    """`git diff base..commit` in the remediation's own worktree (read-only, capped)."""
    if not (SHA.match(base or "") and SHA.match(commit or "")):
        return None
    root = (get_agent_settings().runtime / "worktrees").resolve()
    wt = Path(worktree).resolve()
    if root not in wt.parents or not (wt / ".git").exists():
        return None
    try:
        out = subprocess.run(
            ["git", "-C", str(wt), "diff", "--no-color", "--no-ext-diff", base, commit],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout
    except (subprocess.SubprocessError, OSError):
        return None
    return out if len(out) <= MAX_DIFF else out[:MAX_DIFF] + "\n… (diff truncated)"


def _metrics_view(m: dict[str, Any] | None) -> dict[str, Any] | None:
    if not m:
        return None
    keys = (
        "error_rate_pct",
        "latency_ms_p50",
        "latency_ms_p95",
        "pool_utilization_pct",
        "requests",
        "deploy_id",
        "git_sha",
        "sampled_at",
    )
    return {k: m.get(k) for k in keys}


@router.get("/api/incidents/{incident_id}/warroom")
def warroom(incident_id: str) -> dict[str, Any]:
    if not INCIDENT_ID.match(incident_id):
        raise ApiError(404, "not_found", "No such incident.")
    inc = store.get_incident(incident_id)
    if not inc:
        raise ApiError(404, "not_found", "No such incident.")
    events = _events(incident_id)

    timeline = []
    for row in events:
        a = describe_row(row)
        if a and a.incident_id == incident_id:
            timeline.append(
                {
                    "seq": a.event_seq,
                    "at": a.at,
                    "agent": a.agent,
                    "summary": html.unescape(a.summary),
                    "level": a.level,
                    "kind": a.kind,
                }
            )

    ev = _last(events, "evidence.collected", lambda p: p.get("incident_id") == incident_id) or {}
    hyps = (ev.get("analysis") or {}).get("hypotheses") or []
    rc = _last(
        events,
        "incident.transition",
        lambda p: p.get("incident_id") == incident_id and p.get("to") == "ROOT_CAUSE_IDENTIFIED",
    )

    handoffs = [
        {
            "handoff_id": h["handoff_id"],
            "from": h["from_agent"],
            "to": h["to_agent"],
            "type": h["handoff_type"],
            "status": h["status"],
            "switch_event_id": h["switch_event_id"],
            "sha256": h["payload_sha256"],
            "created_at": h["created_at"],
        }
        for h in _rows(
            "SELECT * FROM handoffs WHERE incident_id=:i ORDER BY created_at", i=incident_id
        )
    ]

    remediation = None
    rems = _rows(
        "SELECT * FROM remediations WHERE incident_id=:i ORDER BY created_at", i=incident_id
    )
    staged = [r for r in rems if r["status"] == "staged"] or rems
    if staged:
        r = staged[-1]
        d = json.loads(r["details_json"] or "{}")
        tests = d.get("tests") or {}
        agent = d.get("agent") or {}
        remediation = {
            "remediation_id": r["remediation_id"],
            "status": r["status"],
            "attempts": len(rems),
            "commit": r["commit_sha"],
            "base": r["base_commit"],
            "patch_hash": r["patch_hash"],
            "coding_mode": r["coding_mode"],
            "files": d.get("files") or [],
            "new_tests": d.get("new_tests") or [],
            "new_tests_fail_on_base": d.get("new_tests_fail_on_base"),
            "tests": {k: tests.get(k) for k in ("passed", "total", "failed", "errors")},
            "post_check_problems": d.get("post_check_problems") or [],
            "agent": {
                "cost_usd": agent.get("cost_usd"),
                "duration_s": agent.get("duration_s"),
                "summary": clean(agent.get("summary"), 600),
            },
            "diff": _diff(
                d.get("worktree") or r["worktree"], r["base_commit"], r["commit_sha"] or ""
            ),
        }

    approval = None
    aps = _rows("SELECT * FROM approvals WHERE incident_id=:i ORDER BY created_at", i=incident_id)
    if aps:
        a = aps[-1]
        b = json.loads(a["binding_json"] or "{}")
        decided = _last(
            events, "approval.approved", lambda p: p.get("approval_id") == a["approval_id"]
        ) or _last(events, "approval.rejected", lambda p: p.get("approval_id") == a["approval_id"])
        verified = _last(
            events, "approval.verified", lambda p: p.get("approval_id") == a["approval_id"]
        )
        approval = {
            "approval_id": a["approval_id"],
            "status": a["status"],
            "request_id": a["request_id"],
            "action": b.get("action"),
            "environment": b.get("environment"),
            "patch_hash": b.get("patch_hash"),
            "expires_at": b.get("expires_at"),
            "answered_by": short_identity(decided.get("by")) if decided else None,
            "answered_at": decided.get("_at") if decided else None,
            "verification": {"source": verified.get("source"), "checks": verified.get("checks")}
            if verified
            else None,
        }

    execution = None
    exs = _rows("SELECT * FROM executions WHERE incident_id=:i ORDER BY created_at", i=incident_id)
    if exs:
        x = exs[-1]
        res = json.loads(x["result_json"] or "{}")
        execution = {
            "execution_id": x["execution_id"],
            "status": x["status"],
            "deploy_id": res.get("deploy_id"),
            "commit": res.get("commit"),
            "before": _metrics_view(res.get("before")),
            "after": _metrics_view(res.get("after")),
            "samples": [_metrics_view(s) for s in (res.get("samples") or [])],
        }

    report = _last(events, "report.generated", lambda p: p.get("incident_id") == incident_id)
    tasks = [
        {"slug": p.get("slug"), "text": clean(p.get("text"), 240), "owner": p.get("owner")}
        for p in (_payload(r) for r in events if r["kind"] == "task.preventive")
        if p.get("incident_id") == incident_id
    ]

    stage = STAGE_INDEX.get(inc["state"], 0)
    return {
        "incident": public(inc),
        "room": {"name": inc.get("room_name"), "id": inc.get("room_id")},
        "stages": [
            {
                "state": s,
                "label": label,
                "status": "done" if i < stage else "current" if i == stage else "todo",
            }
            for i, (s, label) in enumerate(STAGES)
        ],
        "resolved": inc["state"]
        in ("RESOLVED", "POSTMORTEM_GENERATED", "PREVENTIVE_TASKS_CREATED"),
        "timeline": timeline,
        "evidence": [
            {
                "kind": e.get("kind"),
                "label": clean(e.get("label"), 160),
                "value": clean(e.get("value"), 60),
                "simulated": bool(e.get("simulated")),
            }
            for e in (ev.get("evidence") or [])
        ],
        "hypotheses": [
            {"statement": clean(h.get("statement"), 300), "confidence": h.get("confidence")}
            for h in sorted(hyps, key=lambda h: -(h.get("confidence") or 0))
        ],
        "root_cause": clean(rc.get("reason"), 500) if rc else None,
        "handoffs": handoffs,
        "remediation": remediation,
        "approval": approval,
        "execution": execution,
        "report": {"generated_at": report["_at"]} if report else None,
        "preventive_tasks": tasks,
    }


@router.get("/api/metrics/live")
def live_metrics() -> dict[str, Any]:
    """The simulated payment-service's measured window (never synthesized here)."""
    url = get_agent_settings().service_url
    try:
        m = httpx.get(f"{url}/metrics/summary", timeout=3).json()
    except (httpx.HTTPError, ValueError):
        raise ApiError(503, "service_unreachable", "payment-service metrics unavailable") from None
    return {"service": "payment-service", **(_metrics_view(m) or {})}
