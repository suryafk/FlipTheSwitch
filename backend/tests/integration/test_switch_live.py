"""Integration tests against the real Switch platform (no mocks).

Skipped unless SWITCH_LIVE_TEST=1. Needs the Switch stack running and `make agents-bootstrap`
done. The approval tests use the TEST HARNESS (MATTERMOST_IC_PASSWORD) to press the IC's buttons
in Mattermost; Switch still authorizes the press. Slack is checked when the running BLACKBOX has
a Slack installation (or SLACK_LIVE_BOT_TOKEN/SLACK_LIVE_CHANNEL_ID are set), otherwise skipped.

    SWITCH_LIVE_TEST=1 make switch-live-test

The tests run in order on one event loop and share one live context, because each Switch step
builds on the previous one (the room, the subscriptions, the approved decision).
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("SWITCH_LIVE_TEST") != "1",
    reason="set SWITCH_LIVE_TEST=1 with a running Switch stack",
)


class Live:
    def __init__(self) -> None:
        from app.core.config import get_settings
        from app.switch.livecheck import LIVE_DIR, LiveCheck, use_isolated_db

        self.main_db_url = get_settings().database_url
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        use_isolated_db(LIVE_DIR / f"pytest-{stamp}.db")
        self.loop = asyncio.new_event_loop()
        self.lc = LiveCheck(harness=True, include_expiry=True)
        self.lc.main_db_url = self.main_db_url
        self.failed: str | None = None

    def step(self, name: str) -> dict[str, Any]:
        if self.failed:
            pytest.skip(f"depends on failed step {self.failed}")
        try:
            self.loop.run_until_complete(getattr(self.lc, f"step_{name}")())
        except BaseException:
            if name in ("connect", "room", "approval"):
                self.failed = name
            raise
        return self.lc.evidence["steps"].get(name, {})


@pytest.fixture(scope="module")
def live() -> Iterator[Live]:
    saved = os.environ.get("BLACKBOX_DATABASE_URL")
    lv = Live()
    yield lv
    lv.loop.run_until_complete(lv.lc.close())
    lv.loop.close()
    if saved is None:
        os.environ.pop("BLACKBOX_DATABASE_URL", None)
    else:
        os.environ["BLACKBOX_DATABASE_URL"] = saved


def test_01_agent_registration_and_auth(live: Live) -> None:
    ev = live.step("register")
    assert ev["operations"] > 0 and ev["invalid_key_status"] == 401


def test_02_event_subscription(live: Live) -> None:
    ev = live.step("connect")
    assert {c["scope"] for c in ev["connections"].values()} == {"all"}


def test_03_room_creation_membership_roles(live: Live) -> None:
    ev = live.step("room")
    assert ev["room_id"] and ev["incident_id"]
    assert {"blackbox-sentinel", "blackbox-rootcause", "blackbox-recovery"} <= set(
        ev["participants"]
    )
    assert "blackbox-rootcause" in ev["roles"]["investigator"]


def test_04_message_publish_and_receive(live: Live) -> None:
    ev = live.step("messaging")
    assert ev["broadcast"]["sequence"] and ev["broadcast"]["switch_timestamp_ms"]
    assert ev["addressed"]["rootcause_addressed"] is True
    assert ev["addressed"]["recovery_addressed"] is False
    assert ev["role_addressed"]["rootcause_addressed"] is True


def test_05_duplicate_events_processed_once(live: Live) -> None:
    ev = live.step("dedup")
    assert ev["replayed_by_read_context"] > 0 and ev["reprocessed"] == 0


def test_06_agent_handoff(live: Live) -> None:
    ev = live.step("handoff")
    assert ev["status"] == "acked" and ev["switch_event_id"].startswith("sw_")
    assert "sender" in ev["forged_refused"] and "hash" in ev["tampered_refused"]


def test_07_tasks_unsupported(live: Live) -> None:
    ev = live.step("tasks")
    assert ev["supported"] is False and ev["task_or_decision_operations"] == []


def test_08_approval_creation_and_independent_verification(live: Live) -> None:
    ev = live.step("approval")
    assert ev["before_answer"]  # refused before any human answer
    assert ev["verified_from"] == "GET /agent-sessions/approvals/outcomes"
    assert all(c["ok"] for c in ev["checks"])
    assert ev["replay_refused"] == ["first_use"]


def test_09_invalid_and_duplicate_approvals(live: Live) -> None:
    ev = live.step("invalid_approvals")
    assert ev["content_change"].startswith("HTTP 409")
    assert "request_id_matches_binding" in ev["mismatched_binding"]
    assert "not_revoked" in ev["revoked"]["failed_checks"]
    assert "answer_is_approve" in ev["rejected"]
    assert ev["expired"]


def test_10_reconnection(live: Live) -> None:
    ev = live.step("reconnect")
    assert ev["delivered_copies"] == 1 and ev["reconnect_seconds"] < 20


def test_11_slack_notification_delivery(live: Live) -> None:
    ev = live.step("slack")
    if ev.get("skipped"):
        pytest.skip(ev["skipped"])
    assert ev["deliveries"].get("delivered", 0) > 0
    assert ev["replay_deliveries"] == ev["deliveries"]
