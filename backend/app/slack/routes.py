"""Slack HTTP surface: OAuth install, admin configuration, status, and slash commands.

Nothing here can approve, deploy, or reach an agent. Slash commands are read-only lookups of
the incident store; every Slack request is verified with the signing secret first.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import time
from typing import Annotated, Any
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.core.errors import ApiError
from app.incidents import store as incident_store
from app.slack import bridge, store
from app.slack.api import SlackApiError, SlackClient
from app.slack.config import BOT_SCOPES, get_slack_settings
from app.slack.crypto import TokenCryptoError
from app.slack.messages import clean, incident_link

log = logging.getLogger("app.slack")
router = APIRouter(prefix="/api/slack", tags=["slack"])

SIGNATURE_WINDOW_SECONDS = 300
MAX_COMMAND_BODY = 8 * 1024
AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
INCIDENT_ID = re.compile(r"^INC-\d{1,8}$")

# Overridable in tests (httpx.MockTransport) without touching the network.
client_factory = lambda: SlackClient(get_slack_settings().api_base)  # noqa: E731


# ---------- helpers ----------


def require_admin(authorization: str | None) -> None:
    expected = get_slack_settings().admin_token
    if not expected:
        raise ApiError(503, "admin_disabled", "Set BLACKBOX_ADMIN_TOKEN to enable Slack admin.")
    supplied = (authorization or "").removeprefix("Bearer ").strip()
    if not hmac.compare_digest(supplied.encode(), expected.encode()):
        raise ApiError(401, "unauthorized", "Admin token required.")


def team_allowed(team_id: str) -> bool:
    allowed = get_slack_settings().allowed_teams
    return not allowed or team_id in allowed


def verify_slack_signature(
    body: bytes, timestamp: str | None, signature: str | None, now: float | None = None
) -> None:
    """Slack request signing: v0=HMAC_SHA256(signing_secret, 'v0:{ts}:{body}')."""
    secret = get_slack_settings().signing_secret
    if not secret:
        raise ApiError(503, "slack_commands_disabled", "SLACK_SIGNING_SECRET is not set.")
    if not timestamp or not signature:
        raise ApiError(401, "bad_signature", "Missing Slack signature headers.")
    try:
        ts = int(timestamp)
    except ValueError:
        raise ApiError(401, "bad_signature", "Bad Slack timestamp.") from None
    if abs((now or time.time()) - ts) > SIGNATURE_WINDOW_SECONDS:
        raise ApiError(401, "stale_request", "Slack request timestamp outside the window.")
    base = b"v0:" + timestamp.encode() + b":" + body
    expected = "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise ApiError(401, "bad_signature", "Slack signature mismatch.")


async def check_channel(client: SlackClient, token: str, channel: str) -> dict[str, Any]:
    try:
        info = await client.conversations_info(token, channel)
    except SlackApiError as exc:
        raise ApiError(400, "channel_unusable", f"Slack: {exc.error}") from None
    ch = info.get("channel") or {}
    if ch.get("is_archived"):
        raise ApiError(400, "channel_unusable", "The channel is archived.")
    if not ch.get("is_member"):
        raise ApiError(
            400,
            "channel_unusable",
            "Invite the BLACKBOX bot to the channel first (/invite @BLACKBOX).",
        )
    return {"id": ch.get("id", channel), "name": ch.get("name")}


# ---------- status ----------


@router.get("/status")
def status() -> dict[str, Any]:
    """Connection and delivery health for the settings page. Contains no secrets."""
    return bridge.health()


# ---------- OAuth installation ----------


@router.get("/install")
def install() -> RedirectResponse:
    s = get_slack_settings()
    if not s.oauth_configured:
        raise ApiError(
            503,
            "oauth_not_configured",
            "Set SLACK_CLIENT_ID, SLACK_CLIENT_SECRET and SLACK_REDIRECT_URI.",
        )
    if not s.secret_key:
        raise ApiError(503, "secret_key_missing", "Set BLACKBOX_SECRET_KEY to store tokens.")
    query = {
        "client_id": s.client_id,
        "scope": ",".join(BOT_SCOPES),
        "state": store.new_oauth_state(),
        "redirect_uri": s.redirect_uri,
    }
    if len(s.allowed_teams) == 1:
        query["team"] = next(iter(s.allowed_teams))
    return RedirectResponse(f"{AUTHORIZE_URL}?{urlencode(query)}", status_code=302)


@router.get("/oauth/callback")
async def oauth_callback(state: str = "", code: str = "", error: str = "") -> RedirectResponse:
    s = get_slack_settings()
    back = f"{s.public_url.rstrip('/')}/settings/slack"
    if error:
        return RedirectResponse(f"{back}?{urlencode({'error': error[:60]})}", status_code=302)
    if not state or not store.consume_oauth_state(state):
        raise ApiError(400, "bad_oauth_state", "Unknown, used or expired OAuth state.")
    if not code:
        raise ApiError(400, "missing_code", "OAuth code missing.")
    client = client_factory()
    try:
        resp = await client.oauth_access(s.client_id, s.client_secret, code, s.redirect_uri)
    except SlackApiError as exc:
        return RedirectResponse(f"{back}?{urlencode({'error': exc.error})}", status_code=302)
    finally:
        await client.aclose()
    team = resp.get("team") or {}
    granted = set(str(resp.get("scope", "")).split(","))
    if resp.get("token_type") != "bot" or not resp.get("access_token"):
        raise ApiError(400, "not_a_bot_token", "Slack did not return a bot token.")
    if not team_allowed(team.get("id", "")):
        return RedirectResponse(f"{back}?error=workspace_not_allowed", status_code=302)
    extra = granted - set(BOT_SCOPES) - {""}
    if extra:
        # Least privilege: refuse a token that carries more than the app needs.
        return RedirectResponse(f"{back}?error=excess_scopes", status_code=302)
    try:
        store.save_installation(
            team["id"],
            team.get("name", ""),
            resp.get("bot_user_id"),
            ",".join(sorted(granted - {""})),
            resp["access_token"],
            actor="slack-oauth",
        )
    except TokenCryptoError as exc:
        raise ApiError(503, "secret_key_missing", str(exc)) from None
    bridge.record_health("idle")
    return RedirectResponse(f"{back}?connected=1", status_code=302)


# ---------- admin configuration ----------


class ChannelBody(BaseModel):
    channel_id: str = Field(pattern=r"^[CG][A-Z0-9]{6,20}$")


class TokenBody(BaseModel):
    token: str = Field(pattern=r"^xoxb-[A-Za-z0-9-]{10,}$")


AdminHeader = Annotated[str | None, Header(alias="Authorization")]


@router.post("/admin/channel")
async def set_channel(body: ChannelBody, authorization: AdminHeader = None) -> dict[str, Any]:
    require_admin(authorization)
    inst = store.active_installation()
    if not inst:
        raise ApiError(409, "not_connected", "Connect a Slack workspace first.")
    client = client_factory()
    try:
        ch = await check_channel(client, store.bot_token(inst["team_id"]), body.channel_id)
    finally:
        await client.aclose()
    store.set_setting("channel_id", ch["id"])
    store.set_setting("channel_name", ch.get("name"))
    incident_store.audit("slack.channel_set", "slack-admin", {"channel_id": ch["id"]})
    bridge.record_health("idle")
    return {"channel_id": ch["id"], "channel_name": ch.get("name")}


@router.post("/admin/token")
async def import_token(body: TokenBody, authorization: AdminHeader = None) -> dict[str, Any]:
    """Manual fallback when OAuth (which needs a public HTTPS redirect) is not available."""
    require_admin(authorization)
    client = client_factory()
    try:
        who = await client.auth_test(body.token)
    except SlackApiError as exc:
        raise ApiError(400, "token_rejected", f"Slack: {exc.error}") from None
    finally:
        await client.aclose()
    if not team_allowed(who.get("team_id", "")):
        raise ApiError(403, "workspace_not_allowed", "Workspace is not in SLACK_ALLOWED_TEAM_IDS.")
    if not who.get("bot_id"):
        raise ApiError(400, "not_a_bot_token", "Use the app's bot token (xoxb-).")
    try:
        store.save_installation(
            who["team_id"],
            who.get("team", ""),
            who.get("user_id"),
            "manual-import",
            body.token,
            actor="slack-admin",
        )
    except TokenCryptoError as exc:
        raise ApiError(503, "secret_key_missing", str(exc)) from None
    bridge.record_health("idle")
    return {"team_id": who["team_id"], "team_name": who.get("team")}


@router.post("/admin/test")
async def send_test(authorization: AdminHeader = None) -> dict[str, Any]:
    require_admin(authorization)
    try:
        target = bridge.resolve_target(get_slack_settings())
    except bridge.BridgePaused as exc:
        raise ApiError(409, "not_ready", str(exc)) from None
    client = client_factory()
    try:
        resp = await client.post_message(
            target.token,
            target.channel_id,
            "BLACKBOX is connected. Incident updates will appear in threads here.",
        )
    except SlackApiError as exc:
        bridge.record_health("degraded", exc.error)
        raise ApiError(502, "slack_error", f"Slack: {exc.error}") from None
    finally:
        await client.aclose()
    bridge.record_health("healthy")
    return {"ok": True, "ts": resp.get("ts")}


@router.post("/admin/disconnect")
def disconnect(authorization: AdminHeader = None) -> dict[str, Any]:
    require_admin(authorization)
    inst = store.active_installation()
    if not inst:
        return {"disconnected": False}
    store.deactivate_installation(inst["team_id"], "disconnected by an admin", "slack-admin")
    bridge.record_health("disconnected")
    return {"disconnected": True}


# ---------- slash commands ----------

HELP = (
    "*BLACKBOX for Slack* (read-only)\n"
    "• `/blackbox status`: the active incident, or the latest one\n"
    "• `/blackbox status INC-1042`: one incident\n"
    "• `/blackbox help`: this message\n"
    "Agents work in the Switch incident room. Remediation approvals happen only on the Switch "
    "approval card in that room (press Approve, or reply with the card's handle, e.g. "
    "`A1 approve`). "
    "This command cannot approve or deploy anything."
)


def ephemeral(text: str) -> JSONResponse:
    return JSONResponse({"response_type": "ephemeral", "text": text})


def incident_summary(inc: dict[str, Any]) -> str:
    link = incident_link(get_slack_settings().public_url, inc["incident_id"])
    return (
        f"*{inc['incident_id']}* · {clean(inc.get('service'), 60)} · "
        f"{inc.get('severity') or 'SEV-?'}\n"
        f"Status: `{inc.get('state')}` (updated {inc.get('updated_at')})\n"
        f"{clean(inc.get('title'), 200)}\n<{link}|View in BLACKBOX>"
    )


@router.post("/commands")
async def slash_command(
    request: Request,
    x_slack_request_timestamp: Annotated[str | None, Header()] = None,
    x_slack_signature: Annotated[str | None, Header()] = None,
) -> JSONResponse:
    body = await request.body()
    if len(body) > MAX_COMMAND_BODY:
        raise ApiError(413, "too_large", "Request too large.")
    verify_slack_signature(body, x_slack_request_timestamp, x_slack_signature)
    form = {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace")).items()}

    team_id = form.get("team_id", "")
    inst = store.active_installation()
    if not team_allowed(team_id) or not inst or inst["team_id"] != team_id:
        return ephemeral("This workspace is not connected to BLACKBOX.")
    s = get_slack_settings()
    allowed_channels = s.command_channels or {bridge.channel_id(s)} - {""}
    if form.get("channel_id") not in allowed_channels:
        return ephemeral("`/blackbox` is not enabled in this channel. Use the incident channel.")

    words = form.get("text", "").strip().split()
    sub = words[0].lower() if words else "help"
    incident_store.audit(
        "slack.command",
        "slack",
        {
            "team_id": team_id,
            "channel_id": form.get("channel_id"),
            "user_id": form.get("user_id"),
            "subcommand": sub[:20],
        },
    )
    if sub in ("approve", "reject", "deploy", "rollback", "run", "exec"):
        return ephemeral(
            "/blackbox cannot approve, deploy or run anything. Approvals happen only on the "
            "Switch approval card in the incident room: press Approve, or reply with the "
            "card's handle, e.g. `A1 approve`."
        )
    if sub != "status":
        return ephemeral(HELP)
    if len(words) > 1:
        iid = words[1].upper()
        if not INCIDENT_ID.match(iid):
            return ephemeral("Usage: `/blackbox status [INC-1234]`")
        inc = incident_store.get_incident(iid)
        return ephemeral(incident_summary(inc) if inc else f"No incident {iid}.")
    inc = incident_store.active_incident() or incident_store.latest_incident()
    return ephemeral(incident_summary(inc) if inc else "No incidents recorded. All quiet.")
