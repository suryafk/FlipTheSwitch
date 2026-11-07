"""HTTP client for the Switch agent bridge.

Every call here maps to an endpoint verified live against Switch 0.31.0
(docs/01-SWITCH-RECONNAISSANCE.md §19-§20). Nothing is invented: unknown fields are passed
through untouched, and every non-2xx response raises SwitchError with Switch's own body.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger("app.switch.client")

# Operations that are safe to retry: reads, and calls Switch treats idempotently. Writes that
# would duplicate a message or a room (post_message, send_targeted_message, create_room) are
# never retried automatically: a timeout there is reported and the caller decides.
IDEMPOTENT_OPS = {
    "connect_to_room",
    "read_context",
    "list_rooms",
    "list_participants",
    "get_room_detail",
    "get_my_context",
    "list_agents",
    "list_roles",
    "get_agent_detail",
    "list_operations",
}
RETRY_STATUSES = {502, 503, 504}

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
    """Calls made with one agent's API key.

    Timeouts: 10 s to connect, `timeout` overall (create_room waits on the chat bridge).
    Retries: idempotent calls only, on network errors and 502/503/504, with backoff.
    """

    def __init__(
        self,
        base_url: str,
        creds: AgentCredentials,
        timeout: float = 90.0,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        max_attempts: int = 3,
        backoff_seconds: float = 0.5,
    ) -> None:
        self.creds = creds
        self.http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {creds.api_key}"},
            timeout=httpx.Timeout(timeout, connect=10.0),
            transport=transport,
        )
        self.max_attempts = max_attempts
        self.backoff = backoff_seconds

    async def _send(
        self, op: str, method: str, path: str, *, idempotent: bool, **kwargs: Any
    ) -> httpx.Response:
        attempts = self.max_attempts if idempotent else 1
        for attempt in range(1, attempts + 1):
            try:
                r = await self.http.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                if attempt == attempts:
                    raise SwitchError(op, 0, f"network: {type(exc).__name__}: {exc}") from exc
                logger.warning("switch call retry", extra={"op": op, "error": type(exc).__name__})
            else:
                if r.status_code not in RETRY_STATUSES or attempt == attempts:
                    return _check(op, r)
                logger.warning("switch call retry", extra={"op": op, "status": r.status_code})
            await asyncio.sleep(self.backoff * attempt)
        raise AssertionError("unreachable")

    async def aclose(self) -> None:
        await self.http.aclose()

    async def list_operations(self) -> list[str]:
        r = await self._send(
            "list ops", "GET", f"/agents/{self.creds.agent_id}/ops", idempotent=True
        )
        return sorted(r.json()["operations"].keys())

    async def op(self, name: str, args: dict[str, Any], connection_id: str | None = None) -> Any:
        """POST /agents/{id}/ops/{name}; arguments unwrapped in the body. [V] S3"""
        headers = {"X-Switch-Connection-Id": connection_id} if connection_id else {}
        r = await self._send(
            f"op {name}",
            "POST",
            f"/agents/{self.creds.agent_id}/ops/{name}",
            idempotent=name in IDEMPOTENT_OPS,
            json=args,
            headers=headers,
        )
        return r.json().get("result")

    async def create_room(
        self,
        name: str,
        description: str,
        agent_names: list[str],
        user_names: list[str],
        instructions: str,
        bridge_id: str | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {
            "name": name,
            "description": description,
            "agent_names": agent_names,
            "user_names": user_names,
            "channel_type": "channel_public",
            "instructions": instructions,
        }
        if bridge_id:
            args["bridge_id"] = bridge_id  # otherwise Switch uses its default bridge
        return await self.op("create_room", args)

    async def post_message(self, room_id: str, content: str) -> dict[str, Any]:
        """POST /agents/{id}/message: needs no connection. [V] S6"""
        r = await self._send(
            "post message",
            "POST",
            f"/agents/{self.creds.agent_id}/message",
            idempotent=False,
            json={"room_id": room_id, "content": content},
        )
        return r.json()

    async def read_context(self, room_id: str, limit: int = 50) -> dict[str, Any]:
        return await self.op("read_context", {"limit": limit, "room_id": room_id})

    # ---- approval requests (Switch's only human-decision primitive) [V] S5 ----

    async def open_approval(self, session_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Open, or (identical body) re-read, an approval request. Different content → 409."""
        # Idempotent by construction: Switch returns the same row for an identical body.
        r = await self._send(
            "open approval",
            "POST",
            f"/agent-sessions/{session_id}/approvals",
            idempotent=True,
            json=body,
        )
        return r.json()

    async def undelivered_outcomes(self) -> list[dict[str, Any]]:
        r = await self._send(
            "list outcomes", "GET", "/agent-sessions/approvals/outcomes", idempotent=True
        )
        return list(r.json())

    async def mark_delivered(self, session_id: str, request_id: str) -> dict[str, Any]:
        """Acknowledge an outcome. Only after the executor has recorded its result (docs/04)."""
        r = await self._send(
            "mark delivered",
            "POST",
            f"/agent-sessions/{session_id}/approvals/{request_id}/delivered",
            idempotent=True,
        )
        return r.json()

    async def close_approval(self, session_id: str, request_id: str) -> dict[str, Any]:
        r = await self._send(
            "close approval",
            "POST",
            f"/agent-sessions/{session_id}/approvals/{request_id}/close",
            idempotent=False,
        )
        return r.json()
