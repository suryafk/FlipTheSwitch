"""Incident store: the authoritative BLACKBOX record that agents share.

Agents never trigger each other through this module: it only holds state. Every write is also
appended to the audit log. Incident state changes go through `transition`, which enforces the
lifecycle table (docs/03-AGENT-ARCHITECTURE.md §3) and the actor allowed to make each change.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.models import AuditEvent
from app.db.session import get_sessionmaker
from app.switch.approvals import canonical_json

# (from_state, to_state) -> agent (or agents) allowed to make that change
TRANSITIONS: dict[tuple[str, str], str | tuple[str, ...]] = {
    ("DETECTED", "INVESTIGATING"): "sentinel",
    # Sentinel when evidence is missing; RootCause when no deploy or change explains the fault.
    ("INVESTIGATING", "NEEDS_HUMAN"): ("sentinel", "rootcause"),
    ("NEEDS_HUMAN", "INVESTIGATING"): "sentinel",
    ("INVESTIGATING", "ROOT_CAUSE_IDENTIFIED"): "rootcause",
    ("NEEDS_REVISION", "ROOT_CAUSE_IDENTIFIED"): "rootcause",
    ("ROOT_CAUSE_IDENTIFIED", "REMEDIATION_STAGED"): "rootcause",
    ("ROOT_CAUSE_IDENTIFIED", "NEEDS_HUMAN"): "rootcause",
    ("REMEDIATION_STAGED", "VALIDATING"): "recovery",
    ("VALIDATING", "NEEDS_REVISION"): "recovery",
    ("VALIDATING", "AWAITING_APPROVAL"): "recovery",
    ("AWAITING_APPROVAL", "APPROVED"): "recovery",
    ("AWAITING_APPROVAL", "REJECTED"): "recovery",
    ("AWAITING_APPROVAL", "APPROVAL_EXPIRED"): "recovery",
    ("APPROVAL_EXPIRED", "NEEDS_HUMAN"): "recovery",
    ("REJECTED", "NEEDS_REVISION"): "recovery",
    ("APPROVED", "EXECUTING"): "executor",
    ("APPROVED", "NEEDS_HUMAN"): "executor",  # execution refused
    ("EXECUTING", "VERIFYING"): "executor",
    ("EXECUTING", "ROLLED_BACK"): "executor",  # deploy failed, launcher reverted
    ("VERIFYING", "RESOLVED"): "executor",
    ("VERIFYING", "ROLLING_BACK"): "executor",
    ("ROLLING_BACK", "ROLLED_BACK"): "executor",
    ("ROLLING_BACK", "FAILED"): "executor",
    ("ROLLED_BACK", "NEEDS_REVISION"): "recovery",
    ("RESOLVED", "POSTMORTEM_GENERATED"): "recovery",
    ("POSTMORTEM_GENERATED", "PREVENTIVE_TASKS_CREATED"): "recovery",
}
TERMINAL = {"PREVENTIVE_TASKS_CREATED", "FAILED", "CLOSED"}


class TransitionError(Exception):
    pass


def _session():
    return get_sessionmaker()()


def audit(kind: str, actor: str, payload: dict[str, Any]) -> int:
    with _session() as s:
        event = AuditEvent(kind=kind, actor=actor, payload_json=canonical_json(payload))
        s.add(event)
        s.commit()
        return event.seq


def _row(sql: str, **params: Any) -> dict[str, Any] | None:
    with _session() as s:
        r = s.execute(text(sql), params).mappings().first()
        return dict(r) if r else None


def _rows(sql: str, **params: Any) -> list[dict[str, Any]]:
    with _session() as s:
        return [dict(r) for r in s.execute(text(sql), params).mappings().all()]


# ---------- incidents ----------


def open_incident_for(fingerprint: str) -> dict[str, Any] | None:
    terminal = ",".join(f"'{t}'" for t in TERMINAL)
    return _row(
        f"SELECT * FROM incidents WHERE fingerprint=:f AND state NOT IN ({terminal}) "
        "ORDER BY created_at DESC LIMIT 1",
        f=fingerprint,
    )


def active_incident() -> dict[str, Any] | None:
    terminal = ",".join(f"'{t}'" for t in TERMINAL)
    return _row(
        f"SELECT * FROM incidents WHERE state NOT IN ({terminal}) ORDER BY created_at LIMIT 1"
    )


def create_incident(service: str, title: str, fingerprint: str, alert: dict, actor: str) -> dict:
    """Idempotent by fingerprint; one active incident at a time (docs/08 review)."""
    existing = open_incident_for(fingerprint)
    if existing:
        return existing | {"_existing": True}
    busy = active_incident()
    if busy:
        raise TransitionError(f"another incident is active: {busy['incident_id']}")
    with _session() as s:
        n = s.execute(text("SELECT COUNT(*) FROM incidents")).scalar_one()
        incident_id = f"INC-{1042 + n}"
        s.execute(
            text(
                "INSERT INTO incidents (incident_id, service, state, title, fingerprint, alert_json) "
                "VALUES (:i, :s, 'DETECTED', :t, :f, :a)"
            ),
            {
                "i": incident_id,
                "s": service,
                "t": title,
                "f": fingerprint,
                "a": canonical_json(alert),
            },
        )
        s.commit()
    audit("incident.created", actor, {"incident_id": incident_id, "fingerprint": fingerprint})
    return get_incident(incident_id) or {}


def latest_incident() -> dict[str, Any] | None:
    return _row("SELECT * FROM incidents ORDER BY created_at DESC LIMIT 1")


def list_incidents(limit: int = 50) -> list[dict[str, Any]]:
    return _rows("SELECT * FROM incidents ORDER BY created_at DESC LIMIT :n", n=limit)


def get_incident(incident_id: str) -> dict[str, Any] | None:
    return _row("SELECT * FROM incidents WHERE incident_id=:i", i=incident_id)


def incident_by_room(room_id: str) -> dict[str, Any] | None:
    return _row("SELECT * FROM incidents WHERE room_id=:r", r=room_id)


def update_incident(incident_id: str, actor: str, **fields: Any) -> None:
    allowed = {"severity", "room_id", "room_name", "title"}
    if not fields or set(fields) - allowed:
        raise ValueError(f"cannot update {set(fields) - allowed}")
    sets = ", ".join(f"{k}=:{k}" for k in fields)
    with _session() as s:
        s.execute(
            text(f"UPDATE incidents SET {sets}, updated_at=CURRENT_TIMESTAMP WHERE incident_id=:i"),
            {**fields, "i": incident_id},
        )
        s.commit()
    audit("incident.updated", actor, {"incident_id": incident_id, **fields})


def transition(incident_id: str, to_state: str, actor: str, reason: str) -> str:
    """Validated, audited state change. Idempotent when already in `to_state`."""
    with _session() as s:
        current = s.execute(
            text("SELECT state FROM incidents WHERE incident_id=:i"), {"i": incident_id}
        ).scalar_one()
        if current == to_state:
            return current
        allowed = TRANSITIONS.get((current, to_state))
        if actor not in ((allowed,) if isinstance(allowed, str) else (allowed or ())):
            raise TransitionError(f"{actor} may not move {incident_id} {current} -> {to_state}")
        s.execute(
            text(
                "UPDATE incidents SET state=:t, updated_at=CURRENT_TIMESTAMP WHERE incident_id=:i "
                "AND state=:c"
            ),
            {"t": to_state, "i": incident_id, "c": current},
        )
        s.commit()
    audit(
        "incident.transition",
        actor,
        {"incident_id": incident_id, "from": current, "to": to_state, "reason": reason},
    )
    return to_state


def close_incident(incident_id: str, actor: str, reason: str) -> bool:
    """Administrative close (demo reset): any non-terminal incident -> CLOSED, audited.

    Not an agent transition: agents never call this. It exists so a reset can clear an
    incident left open (e.g. NEEDS_HUMAN after an expired approval) without deleting history.
    """
    with _session() as s:
        current = s.execute(
            text("SELECT state FROM incidents WHERE incident_id=:i"), {"i": incident_id}
        ).scalar_one_or_none()
        if current is None or current in TERMINAL:
            return False
        s.execute(
            text(
                "UPDATE incidents SET state='CLOSED', updated_at=CURRENT_TIMESTAMP "
                "WHERE incident_id=:i AND state=:c"
            ),
            {"i": incident_id, "c": current},
        )
        s.commit()
    audit(
        "incident.transition",
        actor,
        {"incident_id": incident_id, "from": current, "to": "CLOSED", "reason": reason},
    )
    return True


def open_incidents() -> list[dict[str, Any]]:
    terminal = ",".join(f"'{t}'" for t in TERMINAL)
    return _rows(f"SELECT * FROM incidents WHERE state NOT IN ({terminal}) ORDER BY created_at")


def open_approvals() -> list[dict[str, Any]]:
    return _rows(
        "SELECT approval_id, incident_id, status FROM approvals WHERE status IN ('bound', 'open')"
    )


# ---------- handoffs ----------


def record_handoff(payload: dict[str, Any], payload_sha256: str) -> None:
    with _session() as s:
        s.execute(
            text(
                "INSERT INTO handoffs (handoff_id, incident_id, from_agent, to_agent, handoff_type, "
                "payload_json, payload_sha256, status) VALUES (:h, :i, :f, :t, :ty, :p, :sha, 'recorded')"
            ),
            {
                "h": payload["handoff_id"],
                "i": payload["incident_id"],
                "f": payload["from_agent"],
                "t": payload["to_agent"],
                "ty": payload["handoff_type"],
                "p": canonical_json(payload),
                "sha": payload_sha256,
            },
        )
        s.commit()
    audit(
        "handoff.recorded",
        payload["from_agent"],
        {"handoff_id": payload["handoff_id"], "sha256": payload_sha256},
    )


def get_handoff(handoff_id: str) -> dict[str, Any] | None:
    return _row("SELECT * FROM handoffs WHERE handoff_id=:h", h=handoff_id)


def set_handoff_status(
    handoff_id: str, status: str, actor: str, event_id: str | None = None
) -> None:
    with _session() as s:
        s.execute(
            text(
                "UPDATE handoffs SET status=:s, switch_event_id=COALESCE(:e, switch_event_id) "
                "WHERE handoff_id=:h"
            ),
            {"s": status, "e": event_id, "h": handoff_id},
        )
        s.commit()
    audit("handoff." + status, actor, {"handoff_id": handoff_id, "switch_event_id": event_id})


def next_handoff_id(incident_id: str) -> str:
    n = _row("SELECT COUNT(*) AS n FROM handoffs WHERE incident_id=:i", i=incident_id)["n"]
    return f"HND-{incident_id.removeprefix('INC-')}-{n + 1:03d}"


# ---------- remediations ----------


def create_remediation(incident_id: str, base_commit: str, worktree: str, actor: str) -> dict:
    n = _row("SELECT COUNT(*) AS n FROM remediations WHERE incident_id=:i", i=incident_id)["n"]
    rid = f"REM-{incident_id.removeprefix('INC-')}-{n + 1}"
    with _session() as s:
        s.execute(
            text(
                "INSERT INTO remediations (remediation_id, incident_id, attempt, base_commit, "
                "worktree, coding_mode, details_json, status) VALUES "
                "(:r, :i, :a, :b, :w, 'pending', '{}', 'in_progress')"
            ),
            {"r": rid, "i": incident_id, "a": n + 1, "b": base_commit, "w": worktree},
        )
        s.commit()
    audit("remediation.created", actor, {"remediation_id": rid, "base_commit": base_commit})
    return get_remediation(rid) or {}


def finish_remediation(
    rid: str,
    actor: str,
    *,
    status: str,
    commit_sha: str | None,
    patch_hash: str | None,
    coding_mode: str,
    details: dict,
) -> None:
    with _session() as s:
        s.execute(
            text(
                "UPDATE remediations SET status=:st, commit_sha=:c, patch_hash=:p, "
                "coding_mode=:m, details_json=:d WHERE remediation_id=:r"
            ),
            {
                "st": status,
                "c": commit_sha,
                "p": patch_hash,
                "m": coding_mode,
                "d": canonical_json(details),
                "r": rid,
            },
        )
        s.commit()
    audit(
        "remediation." + status,
        actor,
        {
            "remediation_id": rid,
            "commit": commit_sha,
            "patch_hash": patch_hash,
            "coding_mode": coding_mode,
        },
    )


def get_remediation(rid: str) -> dict[str, Any] | None:
    row = _row("SELECT * FROM remediations WHERE remediation_id=:r", r=rid)
    if row:
        row["details"] = json.loads(row.pop("details_json") or "{}")
    return row


# ---------- approvals and executions ----------


def create_approval(
    approval_id: str,
    incident_id: str,
    remediation_id: str,
    request_id: str,
    binding: dict,
    actor: str,
) -> None:
    with _session() as s:
        s.execute(
            text(
                "INSERT INTO approvals (approval_id, incident_id, remediation_id, request_id, "
                "binding_json, status) VALUES (:a, :i, :r, :q, :b, 'bound')"
            ),
            {
                "a": approval_id,
                "i": incident_id,
                "r": remediation_id,
                "q": request_id,
                "b": canonical_json(binding),
            },
        )
        s.commit()
    audit("approval.bound", actor, binding)


def next_approval_id(incident_id: str) -> str:
    n = _row("SELECT COUNT(*) AS n FROM approvals WHERE incident_id=:i", i=incident_id)["n"]
    return f"APR-{incident_id.removeprefix('INC-')}-{n + 1}"


def get_approval(approval_id: str) -> dict[str, Any] | None:
    row = _row("SELECT * FROM approvals WHERE approval_id=:a", a=approval_id)
    if row:
        row["binding"] = json.loads(row.pop("binding_json"))
    return row


def approval_by_request(request_id: str) -> dict[str, Any] | None:
    row = _row("SELECT approval_id FROM approvals WHERE request_id=:q", q=request_id)
    return get_approval(row["approval_id"]) if row else None


def set_approval_status(approval_id: str, status: str, actor: str, detail: dict) -> None:
    with _session() as s:
        s.execute(
            # A BLACKBOX revocation is final: a later Switch answer cannot undo it.
            text("UPDATE approvals SET status=:s WHERE approval_id=:a AND status != 'revoked'"),
            {"s": status, "a": approval_id},
        )
        s.commit()
    audit("approval." + status, actor, {"approval_id": approval_id, **detail})


def claim_execution(approval_id: str, incident_id: str) -> str:
    """Atomically reserve the single execution allowed for an approval."""
    execution_id = "EXR-" + approval_id.removeprefix("APR-")
    try:
        with _session() as s:
            s.execute(
                text(
                    "INSERT INTO executions (execution_id, approval_id, incident_id, status) "
                    "VALUES (:e, :a, :i, 'started')"
                ),
                {"e": execution_id, "a": approval_id, "i": incident_id},
            )
            s.commit()
    except IntegrityError as exc:
        raise TransitionError(f"approval {approval_id} has already been used") from exc
    audit(
        "execution.started", "executor", {"execution_id": execution_id, "approval_id": approval_id}
    )
    return execution_id


def execution_exists(approval_id: str) -> bool:
    return _row("SELECT 1 AS x FROM executions WHERE approval_id=:a", a=approval_id) is not None


def finish_execution(execution_id: str, status: str, result: dict) -> None:
    with _session() as s:
        s.execute(
            text("UPDATE executions SET status=:s, result_json=:r WHERE execution_id=:e"),
            {"s": status, "r": canonical_json(result), "e": execution_id},
        )
        s.commit()
    audit("execution." + status, "executor", {"execution_id": execution_id, **result})


def revoke_approval(approval_id: str, actor: str, reason: str) -> bool:
    """BLACKBOX-side revocation (Switch has no revoke after an answer, docs/01).

    A revoked approval can never be executed: the executor checks this before and after
    verifying with Switch. Returns False if it was already executed or does not exist.
    """
    with _session() as s:
        n = s.execute(
            text(
                "UPDATE approvals SET status='revoked' WHERE approval_id=:a AND status != 'revoked' "
                "AND approval_id NOT IN (SELECT approval_id FROM executions)"
            ),
            {"a": approval_id},
        ).rowcount
        s.commit()
    if n:
        audit("approval.revoked", actor, {"approval_id": approval_id, "reason": reason})
    return bool(n)


def is_revoked(approval_id: str) -> bool:
    row = _row("SELECT status FROM approvals WHERE approval_id=:a", a=approval_id)
    return bool(row and row["status"] == "revoked")


# ---------- Switch event ledger ----------


def claim_switch_event(
    agent: str,
    event_id: str,
    room_id: str,
    *,
    sequence: int | None,
    sender: str | None,
    sender_name: str | None,
    addressed: bool | None,
    timestamp_ms: int | None,
    via: str,
) -> bool:
    """Record that `agent` is processing Switch event `event_id`. False if it already did.

    Durable across restarts and reconnects, so a resumed stream or a read_context catch-up can
    never make an agent act on the same Switch message twice.
    """
    try:
        with _session() as s:
            s.execute(
                text(
                    "INSERT INTO switch_events (agent, event_id, room_id, sequence, sender, "
                    "sender_name, addressed, switch_timestamp_ms, via) VALUES "
                    "(:a, :e, :r, :q, :s, :n, :ad, :t, :v)"
                ),
                {
                    "a": agent,
                    "e": event_id,
                    "r": room_id,
                    "q": sequence,
                    "s": sender,
                    "n": sender_name,
                    "ad": addressed,
                    "t": timestamp_ms,
                    "v": via,
                },
            )
            s.commit()
        return True
    except IntegrityError:
        return False


def switch_event(agent: str, event_id: str) -> dict[str, Any] | None:
    return _row("SELECT * FROM switch_events WHERE agent=:a AND event_id=:e", a=agent, e=event_id)


def get_execution_for(approval_id: str) -> dict[str, Any] | None:
    row = _row("SELECT * FROM executions WHERE approval_id=:a", a=approval_id)
    if row:
        row["result"] = json.loads(row.pop("result_json"))
    return row
