"""Minimal client for the official Slack Web API (https://api.slack.com/web).

Only documented methods are used: oauth.v2.access, auth.test, conversations.info,
chat.postMessage, chat.update. Retries follow Slack's guidance: HTTP 429 carries Retry-After;
5xx and network errors are retried with backoff. `ok: false` errors are classified so callers
can tell "fix the configuration" apart from "try again later".
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

# Errors that mean the token/workspace is no longer usable.
AUTH_ERRORS = {
    "invalid_auth",
    "not_authed",
    "token_revoked",
    "token_expired",
    "account_inactive",
    "team_disabled",
    "app_uninstalled",
    "org_login_required",
}
# Errors that mean the channel/permissions are wrong (fixable by an admin).
CHANNEL_ERRORS = {
    "channel_not_found",
    "not_in_channel",
    "is_archived",
    "missing_scope",
    "restricted_action",
    "no_permission",
    "ekm_access_denied",
}


class SlackApiError(Exception):
    def __init__(self, method: str, error: str, transient: bool = False) -> None:
        super().__init__(f"Slack {method} failed: {error}")
        self.method = method
        self.error = error
        self.transient = transient

    @property
    def auth_problem(self) -> bool:
        return self.error in AUTH_ERRORS

    @property
    def channel_problem(self) -> bool:
        return self.error in CHANNEL_ERRORS


class SlackClient:
    def __init__(
        self,
        base_url: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        max_attempts: int = 4,
        backoff_seconds: float = 0.5,
        max_retry_after: float = 30.0,
    ) -> None:
        self.http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=15, transport=transport
        )
        self.max_attempts = max_attempts
        self.backoff = backoff_seconds
        self.max_retry_after = max_retry_after

    async def aclose(self) -> None:
        await self.http.aclose()

    async def call(
        self,
        method: str,
        *,
        token: str | None = None,
        json: dict | None = None,
        form: dict | None = None,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        last: SlackApiError | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                if form is not None:
                    r = await self.http.post(f"/{method}", data=form, headers=headers)
                else:
                    r = await self.http.post(f"/{method}", json=json or {}, headers=headers)
            except httpx.HTTPError as exc:
                last = SlackApiError(method, f"network: {type(exc).__name__}", transient=True)
                await asyncio.sleep(self.backoff * attempt)
                continue
            if r.status_code == 429:
                wait = min(float(r.headers.get("Retry-After", "1") or 1), self.max_retry_after)
                last = SlackApiError(method, "ratelimited", transient=True)
                await asyncio.sleep(wait)
                continue
            if r.status_code >= 500:
                last = SlackApiError(method, f"http_{r.status_code}", transient=True)
                await asyncio.sleep(self.backoff * attempt)
                continue
            try:
                body = r.json()
            except ValueError:
                raise SlackApiError(method, f"http_{r.status_code}_not_json") from None
            if body.get("ok"):
                return body
            error = str(body.get("error", "unknown_error"))
            if error in ("ratelimited", "internal_error", "fatal_error", "service_unavailable"):
                last = SlackApiError(method, error, transient=True)
                await asyncio.sleep(self.backoff * attempt)
                continue
            raise SlackApiError(method, error)
        assert last is not None
        raise last

    # ---- documented methods ----

    async def oauth_access(
        self, client_id: str, client_secret: str, code: str, redirect_uri: str
    ) -> dict[str, Any]:
        return await self.call(
            "oauth.v2.access",
            form={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )

    async def auth_test(self, token: str) -> dict[str, Any]:
        return await self.call("auth.test", token=token)

    async def conversations_info(self, token: str, channel: str) -> dict[str, Any]:
        return await self.call("conversations.info", token=token, form={"channel": channel})

    async def post_message(
        self,
        token: str,
        channel: str,
        text: str,
        blocks: list | None = None,
        thread_ts: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "channel": channel,
            "text": text,
            "unfurl_links": False,
            "unfurl_media": False,
        }
        if blocks:
            payload["blocks"] = blocks
        if thread_ts:
            payload["thread_ts"] = thread_ts
        return await self.call("chat.postMessage", token=token, json=payload)

    async def update_message(
        self, token: str, channel: str, ts: str, text: str, blocks: list | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"channel": channel, "ts": ts, "text": text}
        if blocks:
            payload["blocks"] = blocks
        return await self.call("chat.update", token=token, json=payload)
