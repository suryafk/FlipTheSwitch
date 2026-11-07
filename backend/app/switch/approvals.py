"""Approval binding and independent decision verification.

Implements docs/04-APPROVAL-SECURITY.md §2 and §5 (steps that apply before any execution
exists). The verifier trusts nothing it was handed in memory: it re-derives the request id,
re-hashes the stored open body, and re-reads the decision from Switch itself.

Switch facts this relies on (verified live, docs/01 §19):
- GET /agent-sessions/approvals/outcomes lists answered/expired requests not yet acknowledged.
- Re-POSTing the identical open body returns the current row; after expiry it returns 422.
- Re-POSTing different content for the same request_id returns 409 REQUEST_CONFLICT.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

APPROVE_OPTION = "approve"
REJECT_OPTION = "reject"


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_request_id(
    incident_id: str, remediation_id: str, patch_hash: str, env: str, nonce: str
) -> str:
    """`{incident}:{remediation}:{hash first 16 hex}:{env}` (docs/04-APPROVAL-SECURITY.md §2)."""
    digest = patch_hash.removeprefix("sha256:")
    # The nonce makes every binding's request id unique even when ids and hashes repeat
    # (e.g. after a demo reset Switch still remembers earlier requests: REQUEST_CONFLICT).
    return f"{incident_id}:{remediation_id}:{digest[:16]}:{env}:{nonce}"


@dataclass(frozen=True)
class ApprovalBinding:
    """What a human is asked to authorize. Persisted before the request is opened."""

    incident_id: str
    remediation_id: str
    patch_hash: str
    action: str
    environment: str
    nonce: str
    room_id: str
    session_id: str
    request_id: str
    expires_at: str  # RFC 3339 UTC
    approver_allowlist: tuple[str, ...]
    open_body: dict[str, Any]
    open_body_sha256: str

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["approver_allowlist"] = list(self.approver_allowlist)
        return d

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> ApprovalBinding:
        return cls(**{**d, "approver_allowlist": tuple(d["approver_allowlist"])})


def create_binding(
    *,
    incident_id: str,
    remediation_id: str,
    patch_hash: str,
    action: str,
    environment: str,
    room_id: str,
    expires_at: datetime,
    approver_allowlist: list[str],
    title: str,
    detail_lines: list[str],
) -> ApprovalBinding:
    nonce = secrets.token_hex(4)
    request_id = build_request_id(incident_id, remediation_id, patch_hash, environment, nonce)
    expires = expires_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    detail = "\n".join(
        [
            f"Action: {action}",
            f"Target: {environment}",
            f"Patch: {patch_hash}",
            *detail_lines,
            f"Expires: {expires}",
        ]
    )
    open_body: dict[str, Any] = {
        "request_id": request_id,
        "turn_id": remediation_id,
        "kind": "approval",
        "title": title,
        "detail": detail,
        "options": [
            {"id": APPROVE_OPTION, "label": "Approve", "decision": "accept"},
            {"id": REJECT_OPTION, "label": "Reject", "decision": "decline"},
        ],
        "questions": [],
        "room_id": room_id,
        "thread_id": None,
        "expires_at": expires,
    }
    return ApprovalBinding(
        incident_id=incident_id,
        remediation_id=remediation_id,
        patch_hash=patch_hash,
        action=action,
        environment=environment,
        room_id=room_id,
        # One Switch session per approval: on Slack each session's cards share a thread, and a
        # bare "approved" reply is matched by thread. A session per approval keeps every card in
        # its own thread so a reply can only ever answer that card (docs/12 §2.7, live finding).
        session_id=f"{incident_id}-{remediation_id}",
        request_id=request_id,
        nonce=nonce,
        expires_at=expires,
        approver_allowlist=tuple(approver_allowlist),
        open_body=open_body,
        open_body_sha256=sha256_hex(canonical_json(open_body)),
    )


class ApprovalReader(Protocol):
    async def undelivered_outcomes(self) -> list[dict[str, Any]]: ...

    async def open_approval(self, session_id: str, body: dict[str, Any]) -> dict[str, Any]: ...


@dataclass
class Verdict:
    allowed: bool
    request_id: str
    checks: list[dict[str, Any]] = field(default_factory=list)
    observed: dict[str, Any] | None = None
    source: str | None = None  # where the decision was read from

    def add(self, name: str, ok: bool, detail: str) -> bool:
        self.checks.append({"check": name, "ok": ok, "detail": detail})
        return ok

    @property
    def failed(self) -> list[dict[str, Any]]:
        return [c for c in self.checks if not c["ok"]]


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _field(row: dict[str, Any], camel: str, snake: str) -> Any:
    return row.get(camel, row.get(snake))


async def verify_decision(
    reader: ApprovalReader,
    binding: ApprovalBinding,
    *,
    now: datetime | None = None,
    already_consumed: bool = False,
    revoked: bool = False,
) -> Verdict:
    """Fail closed: `allowed` is True only if every check passes."""
    now = now or datetime.now(UTC)
    v = Verdict(allowed=False, request_id=binding.request_id)

    # 1. The binding is internally consistent (no tampering between bind and verify).
    expected_id = build_request_id(
        binding.incident_id,
        binding.remediation_id,
        binding.patch_hash,
        binding.environment,
        binding.nonce,
    )
    ok = v.add(
        "request_id_matches_binding",
        expected_id == binding.request_id == binding.open_body.get("request_id"),
        f"expected {expected_id}",
    )
    ok &= v.add(
        "open_body_unchanged",
        sha256_hex(canonical_json(binding.open_body)) == binding.open_body_sha256,
        "sha256 of stored open body",
    )
    ok &= v.add(
        "card_shows_patch_hash",
        binding.patch_hash in str(binding.open_body.get("detail", "")),
        "the human saw the hash being approved",
    )
    if not ok:
        return v

    # 2. Read the decision from Switch (never from memory or chat).
    row: dict[str, Any] | None = None
    try:
        for outcome in await reader.undelivered_outcomes():
            if _field(outcome, "requestId", "request_id") == binding.request_id:
                row, v.source = outcome, "GET /agent-sessions/approvals/outcomes"
                break
        if row is None:
            row = await reader.open_approval(binding.session_id, binding.open_body)
            v.source = "identical re-open (POST /agent-sessions/{session}/approvals)"
    except Exception as exc:  # noqa: BLE001 - unverifiable means refused
        v.add("decision_readable_from_switch", False, f"{type(exc).__name__}: {exc}")
        return v
    v.observed = row
    v.add("decision_readable_from_switch", True, v.source or "")

    state = _field(row, "state", "state")
    answer = _field(row, "answer", "answer")
    answered_by = _field(row, "answeredBy", "answered_by")
    answered_at = _parse_ts(_field(row, "answeredAt", "answered_at"))
    expires_at = _parse_ts(binding.expires_at)

    checks = [
        v.add("state_answered", state == "answered", f"state={state}"),
        v.add("answer_is_approve", answer == APPROVE_OPTION, f"answer={answer}"),
        v.add(
            "approver_allowlisted",
            answered_by in binding.approver_allowlist,
            f"answered_by={answered_by}",
        ),
        v.add(
            "answered_before_expiry",
            answered_at is not None and expires_at is not None and answered_at <= expires_at,
            f"answered_at={answered_at} expires_at={expires_at}",
        ),
        v.add(
            "not_expired_now",
            expires_at is not None and now < expires_at,
            f"now={now.isoformat()}",
        ),
        v.add("first_use", not already_consumed, "no prior consumption recorded"),
        # Switch cannot revoke an answered request; BLACKBOX's revocation is checked here.
        v.add("not_revoked", not revoked, "no BLACKBOX revocation recorded"),
    ]
    v.allowed = all(checks)
    return v
