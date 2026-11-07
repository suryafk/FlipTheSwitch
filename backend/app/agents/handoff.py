"""Switch-visible handoffs (docs/03-HANDOFF-SCHEMAS.md §1-§2).

Sender: record the handoff in the store first, then post it to Switch as an addressed message:
a one-line human summary plus one fenced JSON block.
Receiver: accept it only if (a) it arrived from the expected agent's Switch identity,
(b) a store record with the same handoff id exists, and (c) the payload hash matches the record.
A human pasting JSON into the room fails (a); a tampered message fails (c).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from app.agents.common import AGENT_NAMES, load_topology
from app.agents.runtime import AgentRuntime
from app.incidents import store
from app.switch.approvals import canonical_json, sha256_hex

FENCE = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)
HANDOFF_SCHEMA = "blackbox.handoff/v1"
ACK_SCHEMA = "blackbox.ack/v1"


def now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def payload_hash(payload: dict[str, Any]) -> str:
    return sha256_hex(canonical_json({k: v for k, v in payload.items() if k != "integrity"}))


def extract_block(body: str) -> dict[str, Any] | None:
    for match in FENCE.finditer(body or ""):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("schema"), str):
            return data
    return None


def render(summary: str, block: dict[str, Any]) -> str:
    return f"{summary}\n\n```json\n{json.dumps(block, indent=1)}\n```"


async def send_handoff(
    rt: AgentRuntime,
    *,
    incident: dict,
    to_role: str,
    handoff_type: str,
    summary: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "schema": HANDOFF_SCHEMA,
        "handoff_id": store.next_handoff_id(incident["incident_id"]),
        "incident_id": incident["incident_id"],
        "from_agent": rt.role,
        "to_agent": to_role,
        "handoff_type": handoff_type,
        "summary": summary[:500],
        "room_id": incident["room_id"],
        "created_at": now(),
        **body,
    }
    digest = payload_hash(payload)
    payload["integrity"] = {
        "payload_sha256": digest,
        "store_record": f"handoffs/{payload['handoff_id']}",
    }
    store.record_handoff(payload, digest)  # store first ...
    result = await rt.send_to(
        incident["room_id"], [AGENT_NAMES[to_role]], render(summary, payload)
    )  # ... then Switch
    store.set_handoff_status(payload["handoff_id"], "sent", rt.role, result.get("event_id"))
    return payload


async def verify_incoming(
    rt: AgentRuntime, room_id: str, message: dict[str, Any], expected_from: str, expected_type: str
) -> tuple[dict | None, str]:
    """Return (payload, "") if the handoff is genuine, else (None, reason)."""
    block = extract_block(message.get("body", ""))
    if not block or block.get("schema") != HANDOFF_SCHEMA:
        return None, "not a handoff"
    if block.get("to_agent") != rt.role or block.get("handoff_type") != expected_type:
        return None, "not addressed to this agent / unexpected type"
    expected_name = AGENT_NAMES[expected_from]
    if message.get("sender_name") != expected_name:
        return None, f"sender {message.get('sender_name')!r} is not {expected_name}"
    # Exact match against the sender id pinned at bootstrap (Switch derives it from the API key).
    pinned = load_topology().agent_participants.get(expected_from)
    if not pinned or message.get("sender") != pinned:
        return None, "sender id does not match the expected agent's pinned Switch identity"
    record = store.get_handoff(block.get("handoff_id", ""))
    if record is None:
        return None, "no store record for this handoff id"
    if (
        payload_hash(block) != record["payload_sha256"]
        or block.get("integrity", {}).get("payload_sha256") != record["payload_sha256"]
    ):
        return None, "payload hash does not match the store record"
    return block, ""


async def ack(
    rt: AgentRuntime, room_id: str, payload: dict[str, Any], status: str, reason: str | None = None
) -> None:
    block = {
        "schema": ACK_SCHEMA,
        "handoff_id": payload.get("handoff_id"),
        "incident_id": payload.get("incident_id"),
        "by_agent": rt.role,
        "status": status,
        "reason": reason,
        "at": now(),
    }
    line = f"ACK {payload.get('handoff_id')}: {status}" + (f" ({reason})" if reason else "")
    await rt.send_to(
        room_id, [AGENT_NAMES[payload.get("from_agent", "sentinel")]], render(line, block)
    )
    if status == "accepted":
        store.set_handoff_status(payload["handoff_id"], "acked", rt.role)
