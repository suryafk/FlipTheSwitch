"""HTTP client for the Switch agent bridge.

Every call here maps to an endpoint verified live against Switch 0.31.0
(docs/01-SWITCH-RECONNAISSANCE.md §19-§20). Nothing is invented: unknown fields are passed
through untouched, and every non-2xx response raises SwitchError with Switch's own body.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

INTEGRATION_PROFILE = {
    "connection_model": "always_on",
    "message_exchange": True,
    "pre_invocation_mediation": [],
    "post_invocation_mediation": [],
    "event_reporting": [],
}


class SwitchError(Exception):
    def __init__(self, operation: str, status: int, body: str) -> None:
        super().__init__(f"Switch {operation} failed: HTTP {status}: {body[:500]}")
        self.operation = operation
        self.status = status
        self.body = body


@dataclass(frozen=True)
class AgentCredentials:
    agent_id: str
    name: str
    api_key: str


def _check(op: str, r: httpx.Response) -> httpx.Response:
    if r.is_success:
        return r
    raise SwitchError(op, r.status_code, r.text)


async def gateway_login(http: httpx.AsyncClient, admin_email: str, admin_password: str) -> str:
    """Gateway login → the session cookie value (switch_auth)."""
    r = _check(
        "gateway login",
        await http.post(
            "/gateway/auth/login", json={"email": admin_email, "password": admin_password}
        ),
    )
    cookie = r.cookies.get("switch_auth")
    if not cookie:
        raise SwitchError("gateway login", r.status_code, "no switch_auth cookie issued")
    return str(cookie)


async def disable_agent_greetings(http: httpx.AsyncClient, cookie: str, bridge_id: str) -> None:
    """Turn off the automatic "Hi, mention me…" post Switch makes when an agent joins a room
    (Gateway → Messaging Apps → Agent greetings; docs/10 F3)."""
    _check(
        "disable agent greetings",
        await http.patch(
            f"/gateway/collaborations/{bridge_id}",
            json={"agent_greetings_enabled": False},
            headers={"Cookie": f"switch_auth={cookie}"},
        ),
    )


async def mint_registration_key(
    http: httpx.AsyncClient, admin_email: str, admin_password: str, label: str
) -> str:
    """Gateway login (cookie) → personal registration key owned by that user. [V] S2"""
    cookie = await gateway_login(http, admin_email, admin_password)
    r = _check(
        "mint registration key",
        await http.post(
            "/gateway/api-keys", json={"label": label}, headers={"Cookie": f"switch_auth={cookie}"}
        ),
    )
    return str(r.json()["key"])


async def register_agent(
    http: httpx.AsyncClient,
    registration_key: str,
    name: str,
    display_name: str,
    description: str,
    overwrite: bool,
) -> AgentCredentials:
    """POST /agents with a custom always_on profile. [V] S2"""
    r = _check(
        "register agent",
        await http.post(
            "/agents",
            headers={"Authorization": f"Bearer {registration_key}"},
            json={
                "name": name,
                "display_name": display_name,
                "description": description,
                "connector_type": "blackbox",
                "integration_profile": INTEGRATION_PROFILE,
                "overwrite": overwrite,
            },
        ),
    )
    body = r.json()
    return AgentCredentials(agent_id=body["id"], name=name, api_key=body["api_key"])


class SwitchAgentClient:
    """Calls made with one agent's API key."""

    def __init__(self, base_url: str, creds: AgentCredentials, timeout: float = 90.0) -> None:
        self.creds = creds
        self.http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {creds.api_key}"},
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self.http.aclose()

    async def list_operations(self) -> list[str]:
        r = _check("list ops", await self.http.get(f"/agents/{self.creds.agent_id}/ops"))
        return sorted(r.json()["operations"].keys())

    async def op(self, name: str, args: dict[str, Any], connection_id: str | None = None) -> Any:
        """POST /agents/{id}/ops/{name}; arguments unwrapped in the body. [V] S3"""
        headers = {"X-Switch-Connection-Id": connection_id} if connection_id else {}
        r = _check(
            f"op {name}",
            await self.http.post(
                f"/agents/{self.creds.agent_id}/ops/{name}", json=args, headers=headers
            ),
        )
        return r.json().get("result")

    async def create_room(
        self,
        name: str,
        description: str,
        agent_names: list[str],
        user_names: list[str],
        instructions: str,
    ) -> dict[str, Any]:
        return await self.op(
            "create_room",
            {
                "name": name,
                "description": description,
                "agent_names": agent_names,
                "user_names": user_names,
                "channel_type": "channel_public",
                "instructions": instructions,
            },
        )

    async def post_message(self, room_id: str, content: str) -> dict[str, Any]:
        """POST /agents/{id}/message: needs no connection. [V] S6"""
        r = _check(
            "post message",
            await self.http.post(
                f"/agents/{self.creds.agent_id}/message",
                json={"room_id": room_id, "content": content},
            ),
        )
        return r.json()

    async def read_context(self, room_id: str, limit: int = 50) -> dict[str, Any]:
        return await self.op("read_context", {"limit": limit, "room_id": room_id})

    # ---- approval requests (Switch's only human-decision primitive) [V] S5 ----

    async def open_approval(self, session_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Open, or (identical body) re-read, an approval request. Different content → 409."""
        r = _check(
            "open approval",
            await self.http.post(f"/agent-sessions/{session_id}/approvals", json=body),
        )
        return r.json()

    async def undelivered_outcomes(self) -> list[dict[str, Any]]:
        r = _check("list outcomes", await self.http.get("/agent-sessions/approvals/outcomes"))
        return list(r.json())

    async def mark_delivered(self, session_id: str, request_id: str) -> dict[str, Any]:
        """Acknowledge an outcome. Only after the executor has recorded its result (docs/04)."""
        r = _check(
            "mark delivered",
            await self.http.post(f"/agent-sessions/{session_id}/approvals/{request_id}/delivered"),
        )
        return r.json()

    async def close_approval(self, session_id: str, request_id: str) -> dict[str, Any]:
        r = _check(
            "close approval",
            await self.http.post(f"/agent-sessions/{session_id}/approvals/{request_id}/close"),
        )
        return r.json()
