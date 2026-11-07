"""Pre-flight parsing and the reset's administrative close."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.demo import preflight
from app.incidents import store

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=UTC)
UP = "{} INFO  [slack_sdk.socket_mode.aiohttp] A new session (s_1) has been established"
DOWN = "{} INFO  [slack_sdk.socket_mode.aiohttp] The session (s_1) seems to be stale. Reconnecting"


def test_fresh_slack_session_passes() -> None:
    c = preflight.parse_slack_socket([UP.format("2026-11-07T11:55:00")], NOW)
    assert c["ok"] is True and "5 min" in c["detail"]


def test_old_slack_session_is_a_warning_not_a_failure() -> None:
    c = preflight.parse_slack_socket([UP.format("2026-11-07T11:00:00")], NOW)
    assert c["ok"] is False and c["critical"] is False and "docker restart" in c["fix"]


def test_dropped_slack_session_fails() -> None:
    lines = [UP.format("2026-11-07T11:50:00"), DOWN.format("2026-11-07T11:58:00")]
    c = preflight.parse_slack_socket(lines, NOW)
    assert c["ok"] is False and c["critical"] is True


def test_no_slack_session_fails() -> None:
    assert preflight.parse_slack_socket(["unrelated"], NOW)["ok"] is False


def test_processes_check_reads_pid_files(tmp_path: Path) -> None:
    import os

    for name in preflight.PROCESSES:
        (tmp_path / f"{name}.pid").write_text(str(os.getpid()))
    assert preflight.check_processes(tmp_path)["ok"] is True
    (tmp_path / "sentinel.pid").write_text("999999")
    c = preflight.check_processes(tmp_path)
    assert c["ok"] is False and "sentinel" in c["detail"]


def test_close_incident_is_audited_and_frees_the_lock(db_url: str) -> None:
    from app.db.migrate import upgrade_to_head

    upgrade_to_head(db_url)
    inc = store.create_incident("payment-service", "t", "fp", {}, "sentinel")
    iid = inc["incident_id"]
    store.transition(iid, "INVESTIGATING", "sentinel", "start")
    assert [i["incident_id"] for i in store.open_incidents()] == [iid]
    assert store.close_incident(iid, "demo-reset", "reset") is True
    assert store.get_incident(iid)["state"] == "CLOSED"
    assert store.open_incidents() == []
    assert store.close_incident(iid, "demo-reset", "again") is False  # terminal already
    # The single-active-incident lock is released: a new incident can open.
    assert store.create_incident("payment-service", "t2", "fp2", {}, "sentinel")["incident_id"]
