"""Genuine Slack integration test: real Web API calls against a real workspace.

Skipped unless explicitly enabled. Run it with a test workspace and channel:

    SLACK_LIVE_TEST=1 \
    SLACK_LIVE_BOT_TOKEN=xoxb-... \
    SLACK_LIVE_CHANNEL_ID=C0123456789 \
    make slack-live-test

The bot must be invited to the channel. The test posts one incident thread (root, two replies,
one root update) to that channel through the real bridge, then checks Slack's responses and
that a second bridge run publishes nothing (no duplicates). It also checks that a revoked-looking
token is classified as an auth problem. It does not test OAuth (that needs a browser).
"""

from __future__ import annotations

import asyncio
import os

import pytest
from cryptography.fernet import Fernet

LIVE = os.environ.get("SLACK_LIVE_TEST") == "1"
TOKEN = os.environ.get("SLACK_LIVE_BOT_TOKEN", "")
CHANNEL = os.environ.get("SLACK_LIVE_CHANNEL_ID", "")

pytestmark = pytest.mark.skipif(
    not (LIVE and TOKEN and CHANNEL),
    reason="set SLACK_LIVE_TEST=1, SLACK_LIVE_BOT_TOKEN and SLACK_LIVE_CHANNEL_ID",
)


def test_live_bridge_posts_thread_once(db_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BLACKBOX_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("SLACK_ALLOWED_TEAM_IDS", "")
    monkeypatch.setenv("BLACKBOX_PUBLIC_URL", "http://localhost:5180")
    from app.db.migrate import upgrade_to_head
    from app.incidents import store as incidents
    from app.slack import bridge as bridge_mod
    from app.slack import store as slack_store
    from app.slack.api import SlackApiError, SlackClient
    from app.slack.config import get_slack_settings

    get_slack_settings.cache_clear()
    upgrade_to_head(db_url)

    async def scenario() -> None:
        client = SlackClient(get_slack_settings().api_base)
        try:
            who = await client.auth_test(TOKEN)
            assert who["ok"] and who.get("bot_id"), "SLACK_LIVE_BOT_TOKEN must be a bot token"
            info = await client.conversations_info(TOKEN, CHANNEL)
            assert info["channel"].get("is_member"), "invite the bot to the channel first"

            slack_store.save_installation(
                who["team_id"],
                who.get("team", ""),
                who.get("user_id"),
                "live-test",
                TOKEN,
                actor="live-test",
            )
            slack_store.set_setting("channel_id", CHANNEL)
            slack_store.set_setting(bridge_mod.CURSOR_KEY, str(slack_store.max_audit_seq()))

            iid = incidents.create_incident(
                "payment-service", "[live test] 5xx spike", "live-test", {}, "sentinel"
            )["incident_id"]
            incidents.transition(iid, "INVESTIGATING", "sentinel", "live integration test")

            bridge = bridge_mod.SlackBridge(client, get_slack_settings())
            consumed = await bridge.run_once()
            assert consumed >= 2
            thread = slack_store.thread_for(iid)
            assert thread and thread["channel_id"] == CHANNEL
            with_ts = [d for d in (slack_store.delivery(s) for s in range(1, 100)) if d]
            assert [d["status"] for d in with_ts] == ["delivered", "delivered"]
            assert all(d["slack_ts"] for d in with_ts)
            assert bridge_mod.health()["status"] == "healthy"

            # No duplicates: a lost cursor replays, the delivery ledger refuses.
            slack_store.set_setting(bridge_mod.CURSOR_KEY, "0")
            await bridge.run_once()
            assert [d["status"] for d in (slack_store.delivery(s) for s in range(1, 100)) if d] == [
                "delivered",
                "delivered",
            ]

            with pytest.raises(SlackApiError) as bad:
                await client.auth_test("xoxb-0000-0000-invalidinvalidinvalid")
            assert bad.value.auth_problem
        finally:
            await client.aclose()
            get_slack_settings.cache_clear()

    asyncio.run(scenario())
