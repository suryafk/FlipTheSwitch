"""Persistence for the Slack integration (tables from migration 0003_slack)."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.session import get_sessionmaker
from app.incidents import store as incident_store
from app.slack.crypto import decrypt_token, encrypt_token

OAUTH_STATE_TTL = timedelta(minutes=10)
STALE_PENDING = timedelta(minutes=5)


def _session():
    return get_sessionmaker()()


def _row(sql: str, **params: Any) -> dict[str, Any] | None:
    with _session() as s:
        r = s.execute(text(sql), params).mappings().first()
        return dict(r) if r else None


def _exec(sql: str, **params: Any) -> None:
    with _session() as s:
        s.execute(text(sql), params)
        s.commit()


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


# ---------- settings (key/value) ----------


def get_setting(key: str) -> str | None:
    row = _row("SELECT value FROM slack_settings WHERE key=:k", k=key)
    return row["value"] if row else None


def set_setting(key: str, value: str | None) -> None:
    _exec(
        "INSERT INTO slack_settings (key, value) VALUES (:k, :v) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        k=key,
        v=value,
    )


# ---------- installation ----------


def save_installation(
    team_id: str, team_name: str, bot_user_id: str | None, scopes: str, token: str, actor: str
) -> None:
    """Store (or replace) the workspace installation. Only one workspace is active at a time."""
    ciphertext = encrypt_token(token)
    with _session() as s:
        s.execute(
            text(
                "UPDATE slack_installations SET active=0, "
                "deactivated_reason='replaced by a new installation' WHERE team_id != :t"
            ),
            {"t": team_id},
        )
        s.execute(
            text(
                "INSERT INTO slack_installations (team_id, team_name, bot_user_id, scopes, "
                "token_encrypted, active, deactivated_reason) VALUES (:t, :n, :b, :s, :c, 1, NULL) "
                "ON CONFLICT(team_id) DO UPDATE SET team_name=excluded.team_name, "
                "bot_user_id=excluded.bot_user_id, scopes=excluded.scopes, "
                "token_encrypted=excluded.token_encrypted, active=1, deactivated_reason=NULL"
            ),
            {"t": team_id, "n": team_name, "b": bot_user_id, "s": scopes, "c": ciphertext},
        )
        s.commit()
    incident_store.audit(
        "slack.installed", actor, {"team_id": team_id, "team_name": team_name, "scopes": scopes}
    )


def active_installation() -> dict[str, Any] | None:
    return _row(
        "SELECT team_id, team_name, bot_user_id, scopes, active, deactivated_reason, "
        "created_at FROM slack_installations WHERE active=1 LIMIT 1"
    )


def latest_installation() -> dict[str, Any] | None:
    return _row(
        "SELECT team_id, team_name, bot_user_id, scopes, active, deactivated_reason, "
        "created_at FROM slack_installations ORDER BY created_at DESC LIMIT 1"
    )


def bot_token(team_id: str) -> str:
    row = _row(
        "SELECT token_encrypted FROM slack_installations WHERE team_id=:t AND active=1", t=team_id
    )
    if not row:
        raise LookupError(f"no active Slack installation for team {team_id}")
    return decrypt_token(row["token_encrypted"])


def deactivate_installation(team_id: str, reason: str, actor: str) -> None:
    _exec(
        "UPDATE slack_installations SET active=0, deactivated_reason=:r WHERE team_id=:t",
        r=reason,
        t=team_id,
    )
    incident_store.audit("slack.disconnected", actor, {"team_id": team_id, "reason": reason})


# ---------- OAuth state (CSRF protection, single use) ----------


def new_oauth_state() -> str:
    state = secrets.token_urlsafe(32)
    _exec("INSERT INTO slack_oauth_states (state) VALUES (:s)", s=state)
    return state


def consume_oauth_state(state: str) -> bool:
    row = _row("SELECT used, created_at FROM slack_oauth_states WHERE state=:s", s=state)
    if not row or row["used"]:
        return False
    created = row["created_at"]
    if isinstance(created, str):
        created = datetime.fromisoformat(created.replace("Z", "+00:00"))
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    _exec("UPDATE slack_oauth_states SET used=1 WHERE state=:s", s=state)
    return datetime.now(UTC) - created <= OAUTH_STATE_TTL


# ---------- threads and deliveries ----------


def thread_for(incident_id: str) -> dict[str, Any] | None:
    return _row("SELECT * FROM slack_threads WHERE incident_id=:i", i=incident_id)


def save_thread(incident_id: str, team_id: str, channel_id: str, thread_ts: str) -> None:
    _exec(
        "INSERT INTO slack_threads (incident_id, team_id, channel_id, thread_ts) "
        "VALUES (:i, :t, :c, :ts)",
        i=incident_id,
        t=team_id,
        c=channel_id,
        ts=thread_ts,
    )


def delivery(event_seq: int) -> dict[str, Any] | None:
    return _row("SELECT * FROM slack_deliveries WHERE event_seq=:s", s=event_seq)


def claim_delivery(event_seq: int, incident_id: str | None, kind: str) -> bool:
    """Atomically reserve an event for publishing; the duplicate-publishing guard.

    True for a new event, a previously failed one, or one left pending by a crashed publisher
    for longer than STALE_PENDING. False if it was delivered, skipped, or another publisher
    holds it right now.
    """
    try:
        _exec(
            "INSERT INTO slack_deliveries (event_seq, incident_id, kind, status, attempts) "
            "VALUES (:s, :i, :k, 'pending', 0)",
            s=event_seq,
            i=incident_id,
            k=kind,
        )
        return True
    except IntegrityError:
        pass
    stale = (datetime.now(UTC) - STALE_PENDING).strftime("%Y-%m-%d %H:%M:%S")
    with _session() as s:
        n = s.execute(
            text(
                "UPDATE slack_deliveries SET status='pending', updated_at=CURRENT_TIMESTAMP "
                "WHERE event_seq=:s AND (status='failed' OR (status='pending' AND updated_at < :st))"
            ),
            {"s": event_seq, "st": stale},
        ).rowcount
        s.commit()
    return n == 1


def finish_delivery(
    event_seq: int, status: str, slack_ts: str | None = None, error: str | None = None
) -> None:
    _exec(
        "UPDATE slack_deliveries SET status=:st, slack_ts=COALESCE(:ts, slack_ts), "
        "last_error=:e, attempts=attempts+1, updated_at=CURRENT_TIMESTAMP WHERE event_seq=:s",
        st=status,
        ts=slack_ts,
        e=error,
        s=event_seq,
    )


def max_audit_seq() -> int:
    row = _row("SELECT COALESCE(MAX(seq), 0) AS n FROM audit_events")
    return int(row["n"]) if row else 0


def audit_after(seq: int, limit: int = 100) -> list[dict[str, Any]]:
    with _session() as s:
        return [
            dict(r)
            for r in s.execute(
                text(
                    "SELECT seq, kind, actor, payload_json, created_at FROM audit_events WHERE seq > :s "
                    "ORDER BY seq LIMIT :n"
                ),
                {"s": seq, "n": limit},
            )
            .mappings()
            .all()
        ]


def delivery_counts() -> dict[str, int]:
    with _session() as s:
        rows = s.execute(text("SELECT status, COUNT(*) AS n FROM slack_deliveries GROUP BY status"))
        return {r[0]: int(r[1]) for r in rows}


def pending_count(after_seq: int) -> int:
    row = _row("SELECT COUNT(*) AS n FROM audit_events WHERE seq > :s", s=after_seq)
    return int(row["n"]) if row else 0
