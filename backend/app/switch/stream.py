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
from collections.abc import Callable
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


class AgentStream:
    def __init__(self, base_url: str, agent_id: str, api_key: str, client_name: str) -> None:
        self.ws_url = base_url.replace("http://", "ws://").replace("https://", "wss://").rstrip("/")
        self.agent_id = agent_id
        self.api_key = api_key
        self.client_name = client_name
        self.connection_id = str(uuid.uuid4())
        self.frames: list[Frame] = []
        self.cursor = 0
        self._task: asyncio.Task[None] | None = None
        self.closed_reason: str | None = None

    def _url(self) -> str:
        query = (
            f"connection_id={self.connection_id}&scope=all&filter=all&start_from=head"
            f"&protocol=8&protocol_accepts=1&client={self.client_name}&client_version=0.1.0"
        )
        return f"{self.ws_url}/agents/{self.agent_id}/connection/ws?{query}"

    async def _run(self) -> None:
        try:
            async with websockets.connect(
                self._url(),
                additional_headers={"Authorization": f"Bearer {self.api_key}"},
                ping_interval=None,  # Switch's own ping/pong is the heartbeat.
            ) as sock:
                async for raw in sock:
                    msg = json.loads(raw)
                    event, data = msg.get("event"), msg.get("data") or {}
                    if event == "ping":
                        await sock.send(json.dumps({"type": "pong", "cursor": self.cursor}))
                        continue
                    frame_id = msg.get("id")
                    if isinstance(frame_id, int):
                        self.cursor = max(self.cursor, frame_id)
                    self.frames.append(Frame(event=event, data=data, id=frame_id))
                    if event in ("evicted", "refused"):
                        self.closed_reason = f"{event}: {data}"
        except websockets.ConnectionClosed as exc:
            self.closed_reason = self.closed_reason or f"closed: {exc.code} {exc.reason}"
        except Exception as exc:  # noqa: BLE001 - surfaced through closed_reason and wait_for
            self.closed_reason = f"error: {exc!r}"
            logger.warning("switch stream failed", extra={"error": repr(exc)})

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

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
