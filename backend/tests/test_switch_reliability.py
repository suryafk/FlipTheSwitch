"""Isolated unit tests for the Switch client's reliability layer, the event ledger and
BLACKBOX-side revocation. Switch is replaced by mocks here (httpx.MockTransport and a local
WebSocket server that speaks the frame shapes recorded from Switch 0.31.0). The real-platform
tests are in tests/integration/test_switch_live.py.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import websockets

from app.incidents import store
from app.switch.client import AgentCredentials, SwitchAgentClient, SwitchError
from app.switch.stream import AgentStream

CREDS = AgentCredentials(agent_id="agent-1", name="blackbox-test", api_key="k")


def run(coro: Any) -> Any:
    return asyncio.run(coro)


# ---------------- HTTP client: retries and timeouts ----------------


def scripted(*responses: Any) -> tuple[httpx.MockTransport, list[str]]:
    calls: list[str] = []
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        item = queue.pop(0) if queue else httpx.Response(200, json={"result": {}})
        if isinstance(item, Exception):
            raise item
        return item

    return httpx.MockTransport(handler), calls


def client(transport: httpx.MockTransport) -> SwitchAgentClient:
    return SwitchAgentClient("http://switch.test", CREDS, transport=transport, backoff_seconds=0)


def test_idempotent_op_is_retried_on_503() -> None:
    transport, calls = scripted(
        httpx.Response(503, text="busy"), httpx.Response(200, json={"result": {"threads": []}})
    )
    c = client(transport)
    assert run(c.read_context("room-1")) == {"threads": []}
    assert len(calls) == 2


def test_idempotent_op_is_retried_on_network_error() -> None:
    transport, calls = scripted(
        httpx.ConnectError("refused"), httpx.Response(200, json={"result": []})
    )
    assert run(client(transport).op("list_rooms", {})) == []
    assert len(calls) == 2


def test_message_post_is_never_retried() -> None:
    """A retried post could duplicate a message in the incident room."""
    transport, calls = scripted(httpx.Response(503, text="busy"))
    with pytest.raises(SwitchError) as e:
        run(client(transport).post_message("room-1", "hello"))
    assert e.value.status == 503 and len(calls) == 1


def test_targeted_message_is_never_retried() -> None:
    transport, calls = scripted(httpx.ConnectError("refused"))
    with pytest.raises(SwitchError) as e:
        run(client(transport).op("send_targeted_message", {"body": "x", "target_names": ["a"]}))
    assert e.value.status == 0 and len(calls) == 1


def test_client_errors_are_not_retried() -> None:
    transport, calls = scripted(httpx.Response(403, json={"detail": "not a member"}))
    with pytest.raises(SwitchError) as e:
        run(client(transport).read_context("room-1"))
    assert e.value.status == 403 and len(calls) == 1


def test_retries_give_up_after_max_attempts() -> None:
    transport, calls = scripted(*[httpx.Response(502, text="bad gateway")] * 5)
    with pytest.raises(SwitchError):
        run(client(transport).undelivered_outcomes())
    assert len(calls) == 3


# ---------------- WebSocket stream: reconnect and resume ----------------


class FakeSwitch:
    """Local WebSocket server reproducing Switch's frame shapes (docs/01 §19-§20)."""

    def __init__(self, script: list[list[dict[str, Any]]], close_code: int = 1000) -> None:
        self.script = script  # one list of frames per connection
        self.close_code = close_code
        self.opens: list[dict[str, str]] = []

    async def handler(self, ws: Any) -> None:
        q = {k: v[0] for k, v in parse_qs(urlparse(ws.request.path).query).items()}
        self.opens.append(q)
        frames = self.script[len(self.opens) - 1] if len(self.opens) <= len(self.script) else []
        await ws.send(
            json.dumps(
                {
                    "event": "connection_state",
                    "data": {"connection_id": q["connection_id"], "rooms": []},
                }
            )
        )
        for frame in frames:
            await ws.send(json.dumps(frame))
        if len(self.opens) <= len(self.script):
            await ws.close(self.close_code)
        else:
            await asyncio.sleep(30)


def message(seq: int, mid: str) -> dict[str, Any]:
    return {
        "event": "message",
        "id": seq,
        "data": {
            "room_id": "r1",
            "payload": {
                "message_id": mid,
                "body": "hi",
                "timestamp": 1794038294812,
                "addressed": False,
            },
        },
    }


async def with_server(fake: FakeSwitch, body: Any) -> Any:
    async with websockets.serve(fake.handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        return await body(f"http://127.0.0.1:{port}")


def test_stream_reconnects_and_resumes_from_last_sequence() -> None:
    fake = FakeSwitch([[message(5, "sw_a")], [message(6, "sw_b")]])
    reconnected = asyncio.Event()

    async def on_reconnect() -> None:
        reconnected.set()

    async def body(url: str) -> AgentStream:
        s = AgentStream(url, "agent-1", "k", "t", reconnect=True, on_reconnect=on_reconnect)
        s.start()
        got = await s.wait_for(lambda f: f.event == "message" and f.id == 6, 10)
        await asyncio.wait_for(reconnected.wait(), 5)
        await s.stop()
        assert got is not None
        return s

    s = run(with_server(fake, body))
    assert fake.opens[0]["start_from"] == "head"
    assert fake.opens[1]["start_from"] == "5"  # resume, no replay
    assert fake.opens[0]["connection_id"] == fake.opens[1]["connection_id"]
    assert s.connects >= 2


def test_stream_does_not_reconnect_after_taken_over() -> None:
    evicted = {"event": "evicted", "data": {"code": "taken_over"}}
    fake = FakeSwitch([[evicted]])

    async def body(url: str) -> AgentStream:
        s = AgentStream(url, "agent-1", "k", "t", reconnect=True)
        s.start()
        await asyncio.wait_for(s._task, 10)  # type: ignore[arg-type]
        return s

    s = run(with_server(fake, body))
    assert s.terminal and len(fake.opens) == 1


def test_stream_treats_auth_close_code_as_terminal() -> None:
    fake = FakeSwitch([[]], close_code=4401)

    async def body(url: str) -> AgentStream:
        s = AgentStream(url, "agent-1", "k", "t", reconnect=True)
        s.start()
        await asyncio.wait_for(s._task, 10)  # type: ignore[arg-type]
        return s

    s = run(with_server(fake, body))
    assert s.terminal and len(fake.opens) == 1


def test_gap_frame_triggers_reread() -> None:
    gap = {"event": "gap", "data": {"from_sequence": 99, "reason": "re-read room context"}}
    fake = FakeSwitch([[message(3, "sw_a"), gap]])
    gaps: list[dict[str, Any]] = []

    async def on_gap(data: dict[str, Any]) -> None:
        gaps.append(data)

    async def body(url: str) -> AgentStream:
        s = AgentStream(url, "agent-1", "k", "t", reconnect=True, on_gap=on_gap)
        s.start()
        await s.wait_for(lambda f: f.event == "gap", 10)
        await asyncio.sleep(0.2)
        await s.stop()
        return s

    s = run(with_server(fake, body))
    assert gaps and gaps[0]["from_sequence"] == 99
    assert fake.opens[-1]["start_from"] == "head" or s.cursor == 0


# ---------------- event ledger and revocation (database) ----------------


@pytest.fixture
def db(db_url: str) -> str:
    from app.db.migrate import upgrade_to_head

    upgrade_to_head(db_url)
    return db_url


def claim(event_id: str, via: str = "stream") -> bool:
    return store.claim_switch_event(
        "blackbox-rootcause",
        event_id,
        "r1",
        sequence=7,
        sender="@a",
        sender_name="a",
        addressed=True,
        timestamp_ms=1794038294812,
        via=via,
    )


def test_switch_event_processed_once_across_stream_and_catch_up(db: str) -> None:
    assert claim("sw_1") is True
    assert claim("sw_1", via="read_context") is False  # catch-up of the same message
    assert claim("sw_1") is False  # resumed stream replay
    row = store.switch_event("blackbox-rootcause", "sw_1")
    assert row["sequence"] == 7 and row["switch_timestamp_ms"] == 1794038294812
    assert row["via"] == "stream"
    # Another agent may process the same room message independently.
    assert (
        store.claim_switch_event(
            "blackbox-recovery",
            "sw_1",
            "r1",
            sequence=7,
            sender=None,
            sender_name=None,
            addressed=False,
            timestamp_ms=None,
            via="stream",
        )
        is True
    )


def seed_approval() -> str:
    from sqlalchemy import text

    from app.db.session import get_sessionmaker

    inc = store.create_incident("payment-service", "t", "fp", {}, "sentinel")
    rem = store.create_remediation(inc["incident_id"], "base", "", "rootcause")
    with get_sessionmaker()() as s:
        s.execute(
            text(
                "INSERT INTO approvals (approval_id, incident_id, remediation_id, "
                "request_id, binding_json, status) VALUES ('APR-1', :i, :r, 'req-1', "
                "'{}', 'open')"
            ),
            {"i": inc["incident_id"], "r": rem["remediation_id"]},
        )
        s.commit()
    return inc["incident_id"]


def test_revocation_is_final(db: str) -> None:
    seed_approval()
    assert store.revoke_approval("APR-1", "ic", "wrong patch") is True
    assert store.is_revoked("APR-1")
    # A later Switch answer cannot un-revoke it.
    store.set_approval_status("APR-1", "approved", "recovery", {"by": "@ic"})
    assert store.is_revoked("APR-1")
    assert store.revoke_approval("APR-1", "ic", "again") is False


def test_cannot_revoke_after_execution(db: str) -> None:
    iid = seed_approval()
    store.claim_execution("APR-1", iid)
    assert store.revoke_approval("APR-1", "ic", "too late") is False
    assert not store.is_revoked("APR-1")
