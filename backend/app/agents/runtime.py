"""Agent runtime: one Switch connection per agent process, and a dispatcher.

The *only* way an agent learns about work is a Switch event on its own WebSocket.
Rules learned live (docs/10 F2): a scope=all connection does not start delivering a room the
agent joins after the connection opened, so the runtime polls the agent's room list and claims
each new room on its connection (connect_to_room), then catches up on that room's history with
read_context so nothing posted before the claim is lost.

Reliability (docs/12): the stream reconnects with backoff and resumes from its last sequence;
after a reconnect every known room is claimed again (claims lapse after 6 s), and after a `gap`
every room is re-read. Each Switch message is recorded in the `switch_events` ledger with its
event id, sequence and timestamp before the handler runs, so a resume or a catch-up can never
make the agent act on the same message twice, even across a process restart.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.agents.common import AGENT_NAMES, load_credentials
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.incidents import store
from app.switch.client import SwitchAgentClient
from app.switch.config import get_switch_settings
from app.switch.stream import AgentStream, Frame

# Catch-up only covers messages posted after this process started (see _claim_and_catch_up).
CATCH_UP_MARGIN_SECONDS = 0

MessageHandler = Callable[[str, dict[str, Any]], Awaitable[None]]
OutcomeHandler = Callable[[dict[str, Any]], Awaitable[None]]


class AgentRuntime:
    def __init__(self, role: str) -> None:
        settings = get_settings()
        configure_logging(settings.log_level, settings.log_format)
        self.role = role
        self.name = AGENT_NAMES[role]
        self.log = logging.getLogger(f"agent.{role}")
        self.creds = load_credentials(role)
        self.api_url = get_switch_settings().api_url
        self.client = SwitchAgentClient(self.api_url, self.creds)
        self.stream = AgentStream(
            self.api_url,
            self.creds.agent_id,
            self.creds.api_key,
            f"blackbox-{role}",
            reconnect=True,
            on_reconnect=self._reclaim_rooms,
            on_gap=self._on_gap,
        )
        self.on_message: MessageHandler | None = None
        self.on_outcome: OutcomeHandler | None = None
        self._rooms: set[str] = set()
        self._cursor = 0
        self._lock = asyncio.Lock()
        self._mention = re.compile(rf"(^|\s)@{re.escape(self.name)}\b")
        self._catch_up_floor_ms = int((time.time() - CATCH_UP_MARGIN_SECONDS) * 1000)

    # ---------- Switch I/O helpers ----------

    async def post(self, room_id: str, text: str) -> str | None:
        """Broadcast a message in a room (no addressing)."""
        sent = await self.client.post_message(room_id, text)
        return sent.get("event_id")

    async def send_to(self, room_id: str, target_names: list[str], body: str) -> dict:
        """Addressed message: Switch prefixes @target and marks it addressed for the target."""
        async with self._lock:  # connect_to_room sets the room the op is bound to
            await self.client.op(
                "connect_to_room",
                {"room_id": room_id, "include_general_instructions": False},
                connection_id=self.stream.connection_id,
            )
            return await self.client.op(
                "send_targeted_message",
                {"body": body, "target_names": target_names},
                connection_id=self.stream.connection_id,
            )

    async def participants(self, room_id: str) -> list[dict[str, Any]]:
        async with self._lock:
            await self.client.op(
                "connect_to_room",
                {"room_id": room_id, "include_general_instructions": False},
                connection_id=self.stream.connection_id,
            )
            return await self.client.op(
                "list_participants", {}, connection_id=self.stream.connection_id
            )

    # ---------- event intake ----------

    async def _deliver(
        self, room_id: str, payload: dict[str, Any], sequence: int | None = None
    ) -> None:
        mid = payload.get("message_id") or payload.get("id")
        if not mid:
            self.log.warning("switch message without an id ignored", extra={"room_id": room_id})
            return
        if not isinstance(payload.get("body", ""), str):
            self.log.warning("malformed switch message ignored", extra={"event_id": mid})
            return
        if not store.claim_switch_event(
            self.name,
            str(mid),
            room_id,
            sequence=sequence,
            sender=payload.get("sender"),
            sender_name=payload.get("sender_name"),
            addressed=payload.get("addressed"),
            timestamp_ms=payload.get("timestamp"),
            via=payload.get("via", "stream"),
        ):
            return  # already processed (resume, catch-up or restart)
        if payload.get("sender_name") == self.name:
            return  # our own message
        if self.on_message:
            try:
                await self.on_message(room_id, payload)
            except Exception:  # noqa: BLE001 - one bad event must not stop the agent
                self.log.exception("message handler failed", extra={"room_id": room_id})

    async def _claim_and_catch_up(self, room_id: str) -> None:
        async with self._lock:
            await self.client.op(
                "connect_to_room",
                {"room_id": room_id, "include_general_instructions": False},
                connection_id=self.stream.connection_id,
            )
        self._rooms.add(room_id)
        ctx = await self.client.read_context(room_id, limit=50)
        entries = []
        for thread in ctx.get("threads", []):
            entries.extend([thread["root"], *thread.get("replies", [])])
        entries.sort(key=lambda e: e.get("timestamp") or 0)
        for e in entries:
            # Catch-up covers the seconds between a room's creation and our claim. History from
            # before this process started (minus a margin) is context, never new work: replaying
            # it would re-trigger old alerts and handoffs (seen in the first full run).
            if (e.get("timestamp") or 0) < self._catch_up_floor_ms:
                continue
            if e.get("kind") != "message":
                continue
            body = e.get("body") or ""
            await self._deliver(
                room_id,
                {
                    "message_id": e.get("id"),
                    "sender": e.get("sender"),
                    "sender_name": e.get("sender_name"),
                    "body": body,
                    "timestamp": e.get("timestamp"),
                    "addressed": bool(self._mention.search(body)),
                    "via": "read_context",
                },
            )
        self.log.info("claimed room", extra={"room_id": room_id, "history": len(entries)})

    async def _reclaim_rooms(self) -> None:
        """After a reconnect: room claims lapse after 6 s (S6), so claim every room again."""
        rooms = sorted(self._rooms)
        self._rooms.clear()
        for room_id in rooms:
            await self._claim_and_catch_up(room_id)
        store.audit(
            "switch.reconnected",
            self.role,
            {
                "agent": self.name,
                "connection_id": self.stream.connection_id,
                "rooms_reclaimed": len(rooms),
                "resumed_from": self.stream.cursor,
            },
        )

    async def _on_gap(self, data: dict[str, Any]) -> None:
        """Resume was impossible: re-read every room; the ledger drops what was already seen."""
        store.audit("switch.gap", self.role, {"agent": self.name, "gap": data})
        await self._reclaim_rooms()

    async def _watch_rooms(self) -> None:
        while True:
            try:
                rooms = await self.client.op("list_rooms", {})
                for room in rooms or []:
                    rid = room.get("id") or room.get("room_id")
                    if rid and rid not in self._rooms:
                        await self._claim_and_catch_up(rid)
            except Exception:  # noqa: BLE001
                self.log.exception("room watch failed")
            await asyncio.sleep(2.0)

    async def _pump(self) -> None:
        while True:
            frames = self.stream.frames[self._cursor :]
            self._cursor += len(frames)
            for f in frames:
                await self._handle_frame(f)
            if not self.stream.alive:  # only after a terminal close (auth, taken_over)
                raise RuntimeError(f"Switch stream ended: {self.stream.closed_reason}")
            await asyncio.sleep(0.1)

    async def _handle_frame(self, f: Frame) -> None:
        if f.event == "message":
            await self._deliver(f.data.get("room_id", ""), f.data.get("payload", {}), f.id)
        elif f.event == "approval_outcome" and self.on_outcome:
            try:
                await self.on_outcome(f.data)
            except Exception:  # noqa: BLE001
                self.log.exception("outcome handler failed")
        elif f.event in ("evicted", "gap"):
            self.log.warning("stream control frame", extra={"event": f.event, "data": f.data})

    async def run(self) -> None:
        self.stream.start()
        state = await self.stream.wait_for(lambda f: f.event == "connection_state", 20)
        if state is None:
            raise SystemExit(
                f"{self.name}: Switch WebSocket did not open ({self.stream.closed_reason})"
            )
        self._cursor = len(self.stream.frames)
        self.log.info(
            "connected to Switch",
            extra={"agent": self.name, "protocol": state.data.get("protocol")},
        )
        await asyncio.gather(self._watch_rooms(), self._pump())
