"""An agent's live Switch connection over the WebSocket transport.

Verified behavior (docs/01 §19-§20): GET /agents/{id}/connection/ws; first frame is
connection_state; the server sends `ping` every heartbeat interval and the client must answer
{"type": "pong", "cursor": n}; domain events carry an integer `id` (the sequence).
Declaring protocol >= 4 on a scope=all connection enables `approval_outcome` frames.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import websockets

logger = logging.getLogger("app.switch.stream")


@dataclass
class Frame:
    event: str
    data: dict[str, Any]
    id: int | None
    received_at: float = field(default_factory=time.time)


# Close codes are 4000 + HTTP status (docs/01 §14). Auth and not-found refusals are permanent.
TERMINAL_CLOSE_CODES = {4401, 4403, 4404}
TERMINAL_EVICTIONS = {"taken_over"}  # another process now owns this connection id: never reopen


class AgentStream:
    """One agent's WebSocket connection, with optional automatic reconnect and resume.

    Reconnect (verified in S6, docs/01 §20): reopen with the *same* connection_id and
    `start_from=<last sequence>`; Switch delivers what was missed without replaying what was
    already seen. After a drop longer than the 6 s heartbeat TTL the room claims are gone, so
    `on_reconnect` lets the owner re-claim its rooms. A `gap` frame means resume was impossible:
    the owner must re-read rooms with read_context (`on_gap`).
    """

    def __init__(
        self,
        base_url: str,
        agent_id: str,
        api_key: str,
        client_name: str,
        *,
        reconnect: bool = False,
        max_backoff: float = 30.0,
        on_reconnect: Callable[[], Awaitable[None]] | None = None,
        on_gap: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        self.ws_url = base_url.replace("http://", "ws://").replace("https://", "wss://").rstrip("/")
        self.agent_id = agent_id
        self.api_key = api_key
        self.client_name = client_name
        self.connection_id = str(uuid.uuid4())
        self.frames: list[Frame] = []
        self.cursor = 0
        self._task: asyncio.Task[None] | None = None
        self._sock: Any = None
        self.closed_reason: str | None = None
        self.terminal = False
        self.reconnect = reconnect
        self.max_backoff = max_backoff
        self.on_reconnect = on_reconnect
        self.on_gap = on_gap
        self.connects = 0  # successful opens (1 = first connect)
        self.history: list[dict[str, Any]] = []  # connect/disconnect log with timestamps

    def _url(self) -> str:
        start = str(self.cursor) if self.cursor else "head"
        query = (
            f"connection_id={self.connection_id}&scope=all&filter=all&start_from={start}"
            f"&protocol=8&protocol_accepts=1&client={self.client_name}&client_version=0.1.0"
        )
        return f"{self.ws_url}/agents/{self.agent_id}/connection/ws?{query}"

    def _log(self, what: str, **detail: Any) -> None:
        self.history.append({"at": time.time(), "what": what, **detail})

    async def _session(self) -> None:
        """One WebSocket session; returns when it closes."""
        async with websockets.connect(
            self._url(),
            additional_headers={"Authorization": f"Bearer {self.api_key}"},
            ping_interval=None,  # Switch's own ping/pong is the heartbeat.
            open_timeout=15,
        ) as sock:
            self._sock = sock
            first = self.connects == 0
            self.connects += 1
            self._log("connected", start_from=self.cursor or "head", resumed=not first)
            if not first and self.on_reconnect:
                asyncio.create_task(self._call(self.on_reconnect))
            async for raw in sock:
                msg = json.loads(raw)
                event, data = msg.get("event"), msg.get("data") or {}
                if event == "ping":
                    await sock.send(json.dumps({"type": "pong", "cursor": self.cursor}))
                    continue
                frame_id = msg.get("id")
                if event == "gap":
                    # The server reset our position; resume from what it says, then re-read.
                    self.cursor = 0
                    if self.on_gap:
                        asyncio.create_task(self._call(self.on_gap, data))
                elif isinstance(frame_id, int):
                    self.cursor = max(self.cursor, frame_id)
                self.frames.append(Frame(event=event, data=data, id=frame_id))
                if event in ("evicted", "refused"):
                    self.closed_reason = f"{event}: {data}"
                    if event == "refused" or data.get("code") in TERMINAL_EVICTIONS:
                        self.terminal = True

    async def _call(self, fn: Callable[..., Awaitable[None]], *args: Any) -> None:
        try:
            await fn(*args)
        except Exception:  # noqa: BLE001 - a callback failure must not kill the stream
            logger.exception("switch stream callback failed")

    async def _run(self) -> None:
        backoff = 1.0
        while True:
            opened_before = self.connects
            try:
                await self._session()
                self.closed_reason = self.closed_reason or "closed by server"
            except websockets.ConnectionClosed as exc:
                self.closed_reason = self.closed_reason or f"closed: {exc.code} {exc.reason}"
                if exc.code in TERMINAL_CLOSE_CODES:
                    self.terminal = True
            except websockets.InvalidStatus as exc:
                status = exc.response.status_code
                self.closed_reason = f"refused: HTTP {status}"
                if status in (401, 403, 404):
                    self.terminal = True
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - surfaced through closed_reason
                self.closed_reason = f"error: {exc!r}"
            self._sock = None
            self._log("disconnected", reason=self.closed_reason, terminal=self.terminal)
            logger.warning(
                "switch stream closed",
                extra={"reason": self.closed_reason, "terminal": self.terminal},
            )
            if self.terminal or not self.reconnect:
                return
            if self.connects > opened_before:
                backoff = 1.0  # the last attempt connected: start the backoff over
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self.max_backoff)
            self.closed_reason = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    @property
    def alive(self) -> bool:
        return bool(self._task and not self._task.done())

    async def drop(self) -> None:
        """Close the socket from our side (tests/demos use it to exercise reconnect)."""
        if self._sock is not None:
            await self._sock.close()

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task

    async def wait_for(self, predicate: Callable[[Frame], bool], timeout: float) -> Frame | None:
        """Return the first received frame matching predicate, or None on timeout."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for frame in self.frames:
                if predicate(frame):
                    return frame
            if self._task and self._task.done():
                return None
            await asyncio.sleep(0.2)
        return None
