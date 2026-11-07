"""TEST HARNESS ONLY — acts as the human incident commander through Mattermost's own API.

This is not a Switch integration and is never used by BLACKBOX product code. It exists so the
Switch proof can run unattended: it logs in to Mattermost *as the human* (with that human's
password) and does exactly what the person would do in the UI — post a message, press the
approval card's button. Switch still applies its own authorization to that press.

Every use is announced on stdout as "[HARNESS acting as IC]".
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx


class MattermostHarness:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.http = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=30)
        self.username = username
        self.password = password
        self.user_id: str | None = None

    async def login(self) -> None:
        r = await self.http.post(
            "/api/v4/users/login", json={"login_id": self.username, "password": self.password}
        )
        r.raise_for_status()
        self.http.headers["Authorization"] = f"Bearer {r.headers['Token']}"
        self.user_id = r.json()["id"]

    async def find_channel(self, display_name: str, timeout: float = 30) -> dict[str, Any]:
        for _ in range(int(timeout)):
            for team in (await self.http.get(f"/api/v4/users/{self.user_id}/teams")).json():
                channels = await self.http.get(
                    f"/api/v4/users/{self.user_id}/teams/{team['id']}/channels"
                )
                for ch in channels.json():
                    if ch.get("display_name") == display_name or ch.get("name") == display_name:
                        return ch
            await asyncio.sleep(1)
        raise TimeoutError(f"Mattermost channel {display_name!r} not visible to {self.username}")

    async def post(self, channel_id: str, message: str, root_id: str | None = None) -> None:
        body: dict[str, Any] = {"channel_id": channel_id, "message": message}
        if root_id:
            body["root_id"] = root_id
        (await self.http.post("/api/v4/posts", json=body)).raise_for_status()

    async def find_card(self, channel_id: str, title: str, timeout: float = 30) -> dict[str, Any]:
        """The approval card Switch posted: a post with this title and action buttons."""
        for _ in range(int(timeout * 2)):
            posts = (
                await self.http.get(f"/api/v4/channels/{channel_id}/posts", params={"per_page": 50})
            ).json()
            for post in posts.get("posts", {}).values():
                attachments = (post.get("props") or {}).get("attachments") or []
                if title in post.get("message", "") and any(a.get("actions") for a in attachments):
                    return post
            await asyncio.sleep(0.5)
        raise TimeoutError(f"approval card {title!r} not found in channel")

    async def press(self, post: dict[str, Any], label_contains: str) -> str:
        actions = [a for att in post["props"]["attachments"] for a in (att.get("actions") or [])]
        action = next((a for a in actions if label_contains in (a.get("name") or "")), None)
        if action is None:
            raise LookupError(f"no button containing {label_contains!r}: {actions}")
        r = await self.http.post(f"/api/v4/posts/{post['id']}/actions/{action['id']}", json={})
        r.raise_for_status()
        return str(action.get("name"))

    async def aclose(self) -> None:
        await self.http.aclose()
