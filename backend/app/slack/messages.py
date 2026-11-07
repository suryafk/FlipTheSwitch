"""Turn verified BLACKBOX audit events into human-readable incident activity.

The source is the append-only audit log, which agents and the executor write as they act
through Switch. Nothing here talks to the agents or to Switch; it only describes what already
happened. Output is sanitized: secrets are redacted, Slack control syntax is escaped (so text
cannot ping @channel or forge links), raw logs and code are never included, length is capped.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from app.db.session import get_sessionmaker

AGENTS = {
    "sentinel": "Sentinel",
    "rootcause": "RootCause",
    "recovery": "Recovery",
    "executor": "Executor",
    "blackbox-control": "Control",
}

SECRET_PATTERNS = [
    re.compile(r"xox[abposr]-[A-Za-z0-9-]+"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_-]+"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]+-----.*?-----END [A-Z ]+-----", re.DOTALL),
    re.compile(r"(?i)(password|secret|token|api[_-]?key)\s*[=:]\s*\S+"),
]


def clean(value: Any, limit: int = 280) -> str:
    """Sanitize free text for Slack mrkdwn."""
    s = str(value or "")
    s = re.sub(r"```.*?```", "[code omitted]", s, flags=re.DOTALL)
    for pattern in SECRET_PATTERNS:
        s = pattern.sub("[redacted]", s)
    s = " ".join(s.split())
    # Escape Slack control characters so text can't become <!channel>, <@U…> or links.
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return s if len(s) <= limit else s[: limit - 1] + "…"


def short_hash(value: str | None) -> str:
    if not value:
        return "?"
    v = value.removeprefix("sha256:")
    return f"sha256:{v[:12]}…"


def short_identity(participant: str | None) -> str:
    """'@switch-mattermost-<bridge>-user:localhost' -> 'user' (display only)."""
    if not participant:
        return "unknown"
    local = participant.lstrip("@").split(":")[0]
    return local.rsplit("-", 1)[-1] if "-" in local else local


@dataclass(frozen=True)
class Activity:
    event_seq: int
    incident_id: str
    agent: str
    summary: str
    at: str  # RFC 3339
    level: str = "info"  # info | success | warning
    kind: str = ""


def _row(sql: str, **params: Any) -> dict[str, Any] | None:
    with get_sessionmaker()() as s:
        r = s.execute(text(sql), params).mappings().first()
        return dict(r) if r else None


def _incident_for(kind: str, payload: dict[str, Any]) -> str | None:
    if payload.get("incident_id"):
        return str(payload["incident_id"])
    if kind.startswith("approval.") and payload.get("approval_id"):
        r = _row("SELECT incident_id FROM approvals WHERE approval_id=:a", a=payload["approval_id"])
        return r["incident_id"] if r else None
    if kind.startswith("execution.") and payload.get("approval_id"):
        r = _row("SELECT incident_id FROM approvals WHERE approval_id=:a", a=payload["approval_id"])
        return r["incident_id"] if r else None
    if kind.startswith("remediation.") and payload.get("remediation_id"):
        r = _row(
            "SELECT incident_id FROM remediations WHERE remediation_id=:r",
            r=payload["remediation_id"],
        )
        return r["incident_id"] if r else None
    if kind.startswith("handoff.") and payload.get("handoff_id"):
        r = _row("SELECT incident_id FROM handoffs WHERE handoff_id=:h", h=payload["handoff_id"])
        return r["incident_id"] if r else None
    return None


def _at(created_at: Any) -> str:
    s = str(created_at)
    return s.replace(" ", "T") + ("" if s.endswith("Z") or "+" in s[10:] else "Z")


TRANSITIONS: dict[str, tuple[str, str, str]] = {
    # to_state: (agent, summary template, level); "" summary = update root only
    "INVESTIGATING": ("sentinel", "Investigation started", "info"),
    "ROOT_CAUSE_IDENTIFIED": ("rootcause", "Root cause identified: {reason}", "info"),
    "VALIDATING": ("recovery", "Validating the proposed fix", "info"),
    "AWAITING_APPROVAL": (
        "recovery",
        "Approval requested from the incident commander "
        "in the Switch incident room ({reason}). Slack cannot approve.",
        "warning",
    ),
    "EXECUTING": ("executor", "Sandbox deployment started ({reason})", "info"),
    "VERIFYING": ("executor", "Deployment finished ({reason}); verifying recovery", "info"),
    "ROLLING_BACK": ("executor", "Recovery not verified; rolling back ({reason})", "warning"),
    "ROLLED_BACK": ("executor", "Rolled back ({reason})", "warning"),
    "FAILED": ("executor", "Execution failed ({reason}); needs attention", "warning"),
    "NEEDS_HUMAN": ("", "Needs a human: {reason}", "warning"),
    "NEEDS_REVISION": ("recovery", "Fix sent back for revision: {reason}", "warning"),
    "PREVENTIVE_TASKS_CREATED": (
        "recovery",
        "Incident resolved. Incident report and preventive engineering tasks created.",
        "success",
    ),
}

PROGRESS = {
    "REPO_INSPECTED": "Repository inspected: {text}",
    "PATCH_GENERATING": "Generating a patch: {text}",
    "TESTS_RUNNING": "Regression tests started: {text}",
    "TESTS_RESULT": "Tests completed: {text}",
    "NEEDS_HUMAN": "Needs a human: {text}",
    "ERROR": "Remediation attempt hit an error: {text}",
}


def describe(
    seq: int, kind: str, actor: str, payload: dict[str, Any], created_at: Any
) -> Activity | None:
    """Map one audit event to an Activity, or None if it is not worth publishing."""
    iid = _incident_for(kind, payload)
    if not iid:
        return None
    at = _at(created_at)

    def act(agent: str, summary: str, level: str = "info") -> Activity:
        return Activity(seq, iid, AGENTS.get(agent, agent.title()), summary, at, level, kind)

    if kind == "incident.created":
        return act("sentinel", "Incident detected from a verified monitoring alert", "warning")
    if kind == "incident.transition":
        spec = TRANSITIONS.get(str(payload.get("to")))
        if not spec:
            return None
        agent, template, level = spec
        return act(agent or actor, template.format(reason=clean(payload.get("reason"), 220)), level)
    if kind == "evidence.collected":
        evidence = payload.get("evidence") or []
        kinds = sorted({e.get("kind", "?") for e in evidence})
        hyps = (payload.get("analysis") or {}).get("hypotheses") or []
        top = max(hyps, key=lambda h: h.get("confidence") or 0) if hyps else None
        s = (
            f"Logs and metrics inspected; evidence collected ({len(evidence)} items: "
            f"{', '.join(kinds)})"
        )
        if top:
            s += f". Top hypothesis ({top.get('confidence')}): {clean(top.get('statement'), 200)}"
        return act("sentinel", s)
    if kind == "handoff.sent":
        h = _row(
            "SELECT from_agent, to_agent, handoff_type FROM handoffs WHERE handoff_id=:h",
            h=payload.get("handoff_id"),
        )
        if not h:
            return None
        return act(
            h["from_agent"],
            f"Handed {h['handoff_type'].replace('_', ' ')} to "
            f"{AGENTS.get(h['to_agent'], h['to_agent'])} through Switch "
            f"({clean(payload.get('handoff_id'), 40)})",
        )
    if kind == "agent.progress":
        template = PROGRESS.get(str(payload.get("milestone")))
        if not template:
            return None
        level = "warning" if payload.get("milestone") in ("NEEDS_HUMAN", "ERROR") else "info"
        return act(actor, template.format(text=clean(payload.get("text"), 220)), level)
    if kind == "remediation.staged":
        mode = (
            "Claude Code"
            if payload.get("coding_mode") == "claude_code"
            else "deterministic fallback [SIMULATED coding agent]"
        )
        return act(
            "rootcause",
            f"Patch generated by {mode} and committed "
            f"({clean(payload.get('remediation_id'), 40)}, "
            f"{short_hash(payload.get('patch_hash'))})",
            "info",
        )
    if kind == "approval.approved":
        return act(
            "recovery",
            "Approval accepted in Switch by "
            f"{clean(short_identity(payload.get('by')), 60)}; the executor re-verifies it "
            "with Switch before deploying",
            "success",
        )
    if kind == "approval.rejected":
        return act(
            "recovery",
            "Approval rejected in Switch by "
            f"{clean(short_identity(payload.get('by')), 60)}; nothing will be deployed",
            "warning",
        )
    if kind == "approval.expired":
        return act(
            "recovery", "Approval expired without an answer; nothing was deployed", "warning"
        )
    if kind == "execution.refused":
        return act("executor", f"Execution refused: {clean(payload.get('reason'), 200)}", "warning")
    if kind == "execution.succeeded":
        b, a = payload.get("before") or {}, payload.get("after") or {}
        return act(
            "executor",
            "Recovery verification completed "
            f"({len(payload.get('samples') or [])} samples): 5xx "
            f"{b.get('error_rate_pct')}% → {a.get('error_rate_pct')}%, p50 "
            f"{b.get('latency_ms_p50')} ms → {a.get('latency_ms_p50')} ms, DB pool "
            f"{b.get('pool_utilization_pct')}% → {a.get('pool_utilization_pct')}%",
            "success",
        )
    return None


def describe_row(row: dict[str, Any]) -> Activity | None:
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except json.JSONDecodeError:
        return None
    return describe(int(row["seq"]), row["kind"], row["actor"], payload, row["created_at"])


# ---------- Slack Block Kit rendering ----------

LEVEL_MARK = {"info": "•", "success": "✓", "warning": "!"}


def incident_link(public_url: str, incident_id: str) -> str:
    return f"{public_url.rstrip('/')}/incidents/{incident_id}"


def activity_message(a: Activity, state: str, public_url: str) -> tuple[str, list[dict]]:
    link = incident_link(public_url, a.incident_id)
    text_fallback = f"[{a.incident_id}] {a.agent}: {a.summary}"
    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"{LEVEL_MARK[a.level]} *{a.agent}* · {a.summary}"},
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"{a.incident_id} · status `{state}` · {a.at} · <{link}|View in BLACKBOX>",
                }
            ],
        },
    ]
    return text_fallback, blocks


def root_message(incident: dict[str, Any], public_url: str) -> tuple[str, list[dict]]:
    iid = incident["incident_id"]
    link = incident_link(public_url, iid)
    sev = incident.get("severity") or "SEV-?"
    title = clean(incident.get("title"), 150)
    text_fallback = f"{iid} {sev} {incident.get('service')}: {incident.get('state')}"
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{iid} · {incident.get('service')} · {sev}"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Status:* `{incident.get('state')}`\n{title}\n"
                "Agents collaborate in the Switch incident room; updates follow in this thread.",
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Opened {_at(incident.get('created_at'))} · <{link}|View in BLACKBOX>",
                }
            ],
        },
    ]
    return text_fallback, blocks
