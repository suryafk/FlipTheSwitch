"""Slack Event Bridge: publishes verified BLACKBOX incident activity to a Slack channel.

One direction only: audit log -> Slack. The bridge never writes to Switch, never messages an
agent, never changes incident state. Agents keep collaborating through Switch; Slack is the
human-facing notification layer.

Delivery model
- Source: the append-only `audit_events` table, read in `seq` order after a stored cursor.
- One thread per incident: a root message (kept current with `chat.update`) plus one reply per
  published event.
- Exactly-once intent: each event is claimed in `slack_deliveries` (unique on the event seq)
  before posting, so restarts, retries and concurrent bridges do not post twice.
- Failures: transient errors are retried by the API client, then the event stays `failed` and
  is retried on the next poll (in order: the cursor does not move past it). Auth errors
  deactivate the installation; channel errors put the bridge in `degraded` until an admin fixes
  the channel. A non-transient error that keeps failing is skipped after MAX_ATTEMPTS so one
  bad event cannot block the stream.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import Any

from app.incidents import store as incident_store
from app.slack import messages, store
from app.slack.api import SlackApiError, SlackClient
from app.slack.config import SlackSettings, get_slack_settings
from app.slack.crypto import TokenCryptoError

log = logging.getLogger("app.slack.bridge")

MAX_ATTEMPTS = 5
CURSOR_KEY = "bridge_cursor"
ROOT_UPDATE_KINDS = {"incident.created", "incident.transition", "incident.updated"}


@dataclass
class Target:
    team_id: str
    channel_id: str
    token: str


class BridgePaused(Exception):
    """The bridge cannot publish right now; the cursor stays where it is."""


def record_health(status: str, error: str | None = None) -> None:
    store.set_setting("health", status)
    if status == "healthy":
        store.set_setting("last_success_at", store.now_iso())
        store.set_setting("last_error", None)
    elif error:
        store.set_setting("last_error", error)
        store.set_setting("last_error_at", store.now_iso())


def channel_id(settings: SlackSettings) -> str:
    return store.get_setting("channel_id") or settings.incident_channel_id


def resolve_target(settings: SlackSettings) -> Target:
    inst = store.active_installation()
    if not inst:
        raise BridgePaused("disconnected: no active Slack installation")
    if settings.allowed_teams and inst["team_id"] not in settings.allowed_teams:
        raise BridgePaused(f"blocked: workspace {inst['team_id']} is not in SLACK_ALLOWED_TEAM_IDS")
    channel = channel_id(settings)
    if not channel:
        raise BridgePaused("not_configured: no incident channel set")
    try:
        token = store.bot_token(inst["team_id"])
    except TokenCryptoError as exc:
        raise BridgePaused(f"error: {exc}") from exc
    return Target(inst["team_id"], channel, token)


class SlackBridge:
    def __init__(self, client: SlackClient, settings: SlackSettings | None = None) -> None:
        self.client = client
        self.settings = settings or get_slack_settings()

    # ---------- cursor ----------

    def cursor(self) -> int:
        value = store.get_setting(CURSOR_KEY)
        if value is None:
            # First start: publish from now on, do not replay history into Slack.
            start = store.max_audit_seq()
            store.set_setting(CURSOR_KEY, str(start))
            return start
        return int(value)

    def advance(self, seq: int) -> None:
        store.set_setting(CURSOR_KEY, str(seq))

    # ---------- publishing ----------

    async def run_once(self, batch: int = 100) -> int:
        """Publish pending events. Returns how many events were consumed (published or skipped)."""
        cursor = self.cursor()
        rows = store.audit_after(cursor, batch)
        if not rows:
            return 0
        try:
            target = resolve_target(self.settings)
        except BridgePaused as paused:
            status, _, detail = str(paused).partition(": ")
            if store.get_setting("health") != status:
                log.warning("slack bridge paused", extra={"reason": str(paused)})
            record_health(status, detail)
            return 0

        consumed = 0
        for row in rows:
            try:
                await self.publish(row, target)
            except BridgePaused as paused:
                status, _, detail = str(paused).partition(": ")
                record_health(status, detail)
                break
            self.advance(int(row["seq"]))
            consumed += 1
        return consumed

    async def publish(self, row: dict[str, Any], target: Target) -> None:
        activity = messages.describe_row(row)
        if activity is None:
            return  # not a human-facing event
        seq = activity.event_seq
        if not store.claim_delivery(seq, activity.incident_id, activity.kind):
            return  # already delivered or skipped (or held by another publisher)

        incident = incident_store.get_incident(activity.incident_id) or {
            "incident_id": activity.incident_id,
            "state": "UNKNOWN",
        }
        try:
            thread = await self.ensure_thread(incident, target)
            text_, blocks = messages.activity_message(
                activity, incident.get("state", "?"), self.settings.public_url
            )
            resp = await self.client.post_message(
                target.token, thread["channel_id"], text_, blocks, thread_ts=thread["thread_ts"]
            )
        except SlackApiError as exc:
            self.fail(seq, exc, target)
            return  # poison event skipped by fail(); otherwise fail() raised BridgePaused
        store.finish_delivery(seq, "delivered", slack_ts=resp.get("ts"))
        record_health("healthy")
        if activity.kind in ROOT_UPDATE_KINDS:
            await self.refresh_root(incident, thread, target)

    async def ensure_thread(self, incident: dict[str, Any], target: Target) -> dict[str, Any]:
        thread = store.thread_for(incident["incident_id"])
        if thread:
            return thread
        text_, blocks = messages.root_message(incident, self.settings.public_url)
        resp = await self.client.post_message(target.token, target.channel_id, text_, blocks)
        store.save_thread(incident["incident_id"], target.team_id, target.channel_id, resp["ts"])
        return store.thread_for(incident["incident_id"]) or {}

    async def refresh_root(
        self, incident: dict[str, Any], thread: dict[str, Any], target: Target
    ) -> None:
        """Keep the root message's status current. Best effort: the thread reply already went."""
        text_, blocks = messages.root_message(incident, self.settings.public_url)
        try:
            await self.client.update_message(
                target.token, thread["channel_id"], thread["thread_ts"], text_, blocks
            )
        except SlackApiError as exc:
            log.warning("slack root update failed", extra={"error": exc.error})

    def fail(self, seq: int, exc: SlackApiError, target: Target) -> None:
        row = store.delivery(seq) or {}
        attempts = int(row.get("attempts") or 0) + 1
        if exc.auth_problem:
            store.finish_delivery(seq, "failed", error=exc.error)
            with contextlib.suppress(Exception):
                store.deactivate_installation(
                    target.team_id, f"Slack said {exc.error}", "slack-bridge"
                )
            raise BridgePaused(f"disconnected: {exc.error}")
        if exc.channel_problem:
            store.finish_delivery(seq, "failed", error=exc.error)
            raise BridgePaused(f"degraded: {exc.error} for channel {target.channel_id}")
        if exc.transient or attempts < MAX_ATTEMPTS:
            store.finish_delivery(seq, "failed", error=exc.error)
            raise BridgePaused(f"degraded: {exc.error} (attempt {attempts}, will retry)")
        store.finish_delivery(seq, "skipped", error=f"{exc.error} after {attempts} attempts")
        log.error("slack event skipped", extra={"seq": seq, "error": exc.error})

    async def run_forever(self, stop: asyncio.Event | None = None) -> None:
        stop = stop or asyncio.Event()
        while not stop.is_set():
            try:
                await self.run_once()
            except Exception:  # noqa: BLE001 - keep the bridge alive, surface in health
                log.exception("slack bridge iteration failed")
                record_health("error", "unexpected bridge error; see backend logs")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), self.settings.bridge_poll_seconds)


def health() -> dict[str, Any]:
    settings = get_slack_settings()
    inst = store.latest_installation()
    cursor = store.get_setting(CURSOR_KEY)
    connected = bool(inst and inst["active"])
    status = store.get_setting("health") or ("idle" if connected else "disconnected")
    if not connected:
        status = "disconnected"
    elif not channel_id(settings):
        status = "not_configured"
    return {
        "status": status,
        "connected": connected,
        "workspace": {"team_id": inst["team_id"], "team_name": inst["team_name"]} if inst else None,
        "deactivated_reason": inst["deactivated_reason"] if inst and not inst["active"] else None,
        "scopes": inst["scopes"].split(",") if inst else [],
        "channel_id": channel_id(settings) or None,
        "last_success_at": store.get_setting("last_success_at"),
        "last_error": store.get_setting("last_error"),
        "last_error_at": store.get_setting("last_error_at"),
        "backlog": store.pending_count(int(cursor)) if cursor is not None else 0,
        "deliveries": store.delivery_counts(),
        "oauth_configured": settings.oauth_configured,
        "commands_enabled": bool(settings.signing_secret),
    }


async def main() -> None:
    from app.core.config import get_settings
    from app.core.logging import configure_logging
    from app.db.migrate import upgrade_to_head

    s = get_settings()
    configure_logging(s.log_level, s.log_format)
    upgrade_to_head(s.database_url)
    configure_logging(s.log_level, s.log_format)
    client = SlackClient(get_slack_settings().api_base)
    log.info("slack bridge started")
    try:
        await SlackBridge(client).run_forever()
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
