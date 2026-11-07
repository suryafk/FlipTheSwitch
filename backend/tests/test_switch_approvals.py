"""Unit tests for approval binding and independent verification (fail-closed logic).

The reader below returns rows in the exact shapes observed from Switch 0.31.0
(docs/01 §19: camelCase from GET /agent-sessions/approvals/outcomes and from re-open).
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.switch.approvals import (
    ApprovalBinding,
    build_request_id,
    create_binding,
    verify_decision,
)

IC = "@switch-mattermost-039eb334-user:localhost"
NOW = datetime(2026, 11, 7, 10, 40, tzinfo=UTC)


def make_binding(**over: Any) -> ApprovalBinding:
    args: dict[str, Any] = dict(
        incident_id="INC-1042",
        remediation_id="REM-1",
        patch_hash="sha256:" + "3f9a0c1d2e3b4a59" + "a" * 48,
        action="apply_remediation_commit",
        environment="staging-sandbox",
        room_id="room-1",
        expires_at=NOW + timedelta(minutes=15),
        approver_allowlist=[IC],
        title="INC-1042: apply fix",
        detail_lines=["Tests 27/27"],
    )
    args.update(over)
    return create_binding(**args)


class FakeReader:
    def __init__(
        self,
        outcomes: list[dict[str, Any]] | None = None,
        reopen_row: dict[str, Any] | None = None,
        fail: bool = False,
    ) -> None:
        self.outcomes = outcomes or []
        self.reopen_row = reopen_row
        self.fail = fail
        self.reopened_with: dict[str, Any] | None = None

    async def undelivered_outcomes(self) -> list[dict[str, Any]]:
        if self.fail:
            raise ConnectionError("switch down")
        return self.outcomes

    async def open_approval(self, session_id: str, body: dict[str, Any]) -> dict[str, Any]:
        self.reopened_with = body
        assert self.reopen_row is not None
        return self.reopen_row


def answered(b: ApprovalBinding, **over: Any) -> dict[str, Any]:
    row = {
        "sessionId": b.session_id,
        "requestId": b.request_id,
        "kind": "approval",
        "state": "answered",
        "answer": "approve",
        "answeredBy": IC,
        "answeredAt": (NOW - timedelta(minutes=1)).isoformat(),
    }
    row.update(over)
    return row


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_request_id_format() -> None:
    assert build_request_id("INC-1", "REM-2", "sha256:" + "ab" * 32, "env", "n1") == (
        "INC-1:REM-2:abababababababab:env:n1"
    )


def test_each_binding_gets_a_unique_request_id() -> None:
    assert make_binding().request_id != make_binding().request_id


def test_allowed_when_switch_shows_approved_by_allowlisted_ic() -> None:
    b = make_binding()
    v = run(verify_decision(FakeReader(outcomes=[answered(b)]), b, now=NOW))
    assert v.allowed, v.failed
    assert v.source == "GET /agent-sessions/approvals/outcomes"


def test_falls_back_to_identical_reopen_when_not_in_outcomes() -> None:
    b = make_binding()
    reader = FakeReader(outcomes=[], reopen_row=answered(b))
    v = run(verify_decision(reader, b, now=NOW))
    assert v.allowed
    assert reader.reopened_with == b.open_body  # exactly the stored body, nothing else
    assert v.source and v.source.startswith("identical re-open")


@pytest.mark.parametrize(
    ("row_over", "failed_check"),
    [
        (
            {"state": "open", "answer": None, "answeredBy": None, "answeredAt": None},
            "state_answered",
        ),
        ({"answer": "reject"}, "answer_is_approve"),
        ({"answeredBy": "@switch-mattermost-039eb334-stranger:localhost"}, "approver_allowlisted"),
        ({"state": "expired", "answer": None}, "state_answered"),
    ],
)
def test_refused_for_wrong_decision(row_over: dict[str, Any], failed_check: str) -> None:
    b = make_binding()
    v = run(verify_decision(FakeReader(outcomes=[answered(b, **row_over)]), b, now=NOW))
    assert not v.allowed
    assert failed_check in [c["check"] for c in v.failed]


def test_refused_after_expiry_even_if_approved() -> None:
    b = make_binding()
    v = run(verify_decision(FakeReader(outcomes=[answered(b)]), b, now=NOW + timedelta(minutes=16)))
    assert not v.allowed
    assert [c["check"] for c in v.failed] == ["not_expired_now"]


def test_refused_when_answered_after_expiry() -> None:
    b = make_binding()
    late = (NOW + timedelta(minutes=20)).isoformat()
    v = run(verify_decision(FakeReader(outcomes=[answered(b, answeredAt=late)]), b, now=NOW))
    assert "answered_before_expiry" in [c["check"] for c in v.failed]


def test_refused_on_second_use() -> None:
    b = make_binding()
    v = run(verify_decision(FakeReader(outcomes=[answered(b)]), b, now=NOW, already_consumed=True))
    assert [c["check"] for c in v.failed] == ["first_use"]


def test_refused_when_revoked_in_blackbox() -> None:
    """Switch cannot revoke an answered request; a BLACKBOX revocation still refuses it."""
    b = make_binding()
    v = run(verify_decision(FakeReader(outcomes=[answered(b)]), b, now=NOW, revoked=True))
    assert not v.allowed
    assert [c["check"] for c in v.failed] == ["not_revoked"]


def test_tampered_patch_hash_is_refused_before_contacting_switch() -> None:
    b = make_binding()
    tampered = ApprovalBinding.from_json({**b.to_json(), "patch_hash": "sha256:" + "0" * 64})
    reader = FakeReader(fail=True)  # would raise if contacted
    v = run(verify_decision(reader, tampered, now=NOW))
    assert not v.allowed
    assert {"request_id_matches_binding", "card_shows_patch_hash"} <= {c["check"] for c in v.failed}


def test_modified_open_body_is_refused() -> None:
    b = make_binding()
    body = {**b.open_body, "detail": b.open_body["detail"] + "\nextra"}
    v = run(
        verify_decision(
            FakeReader(fail=True),
            ApprovalBinding.from_json({**b.to_json(), "open_body": body}),
            now=NOW,
        )
    )
    assert "open_body_unchanged" in [c["check"] for c in v.failed]


def test_switch_unreachable_fails_closed() -> None:
    b = make_binding()
    v = run(verify_decision(FakeReader(fail=True), b, now=NOW))
    assert not v.allowed
    assert v.failed[-1]["check"] == "decision_readable_from_switch"


def test_other_requests_in_outcomes_are_ignored() -> None:
    b = make_binding()
    other = answered(b, requestId="INC-9:REM-1:ffff:staging-sandbox")
    reader = FakeReader(outcomes=[other], reopen_row=answered(b, state="open", answer=None))
    v = run(verify_decision(reader, b, now=NOW))
    assert not v.allowed
    assert v.observed and v.observed["state"] == "open"
