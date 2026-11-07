"""Switch integration proof (Task 10).

    uv run python -m app.switch.proof run [--as-ic]   # steps 1-6, then verify in a new process
    uv run python -m app.switch.proof verify [REQUEST_ID]

`run` performs, against a real Switch server:
  1. register one BLACKBOX agent (or reuse its saved credentials)
  2. create an incident room (bridged to Mattermost)
  3. send a message from the agent
  4. receive an event from Switch (a human message in that room, over the agent's WebSocket)
  5. create a test approval decision (a real Switch approval card)
  6. read and independently verify that decision (`verify`, in a separate process, from the
     binding stored in the append-only audit log and a fresh read from Switch)

Without --as-ic, a person performs the human steps in Mattermost (the script waits).
With --as-ic, the TEST HARNESS logs in to Mattermost as that person and does them; this is
announced on every use. The approval itself authorizes nothing: no execution exists yet.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import select

from app.core.config import BACKEND_DIR, get_settings
from app.db.migrate import upgrade_to_head
from app.db.models import AuditEvent
from app.db.session import get_sessionmaker
from app.switch.approvals import (
    ApprovalBinding,
    canonical_json,
    create_binding,
    sha256_hex,
    verify_decision,
)
from app.switch.client import (
    AgentCredentials,
    SwitchAgentClient,
    SwitchError,
    mint_registration_key,
    register_agent,
)
from app.switch.config import SwitchSettings, get_switch_settings
from app.switch.mattermost_harness import MattermostHarness
from app.switch.stream import AgentStream, Frame

STATE_DIR = BACKEND_DIR / "data" / "switch"
CREDS_FILE = STATE_DIR / "agent.json"
EVIDENCE_DIR = STATE_DIR / "evidence"
ACTOR = "switch-proof"


def say(step: str, msg: str) -> None:
    print(f"[{step}] {msg}", flush=True)


def harness(msg: str) -> None:
    print(f"    [HARNESS acting as IC] {msg}", flush=True)


# ---------- persistence (append-only audit log) ----------


def record(kind: str, payload: dict[str, Any]) -> int:
    with get_sessionmaker()() as s:
        event = AuditEvent(kind=kind, actor=ACTOR, payload_json=canonical_json(payload))
        s.add(event)
        s.commit()
        return event.seq


def find_events(kind: str) -> list[dict[str, Any]]:
    with get_sessionmaker()() as s:
        rows = s.scalars(
            select(AuditEvent).where(AuditEvent.kind == kind).order_by(AuditEvent.seq)
        ).all()
        return [json.loads(r.payload_json) | {"_seq": r.seq} for r in rows]


# ---------- credentials ----------


def load_creds() -> AgentCredentials | None:
    if not CREDS_FILE.exists():
        return None
    d = json.loads(CREDS_FILE.read_text())
    return AgentCredentials(agent_id=d["agent_id"], name=d["name"], api_key=d["api_key"])


def save_creds(creds: AgentCredentials) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CREDS_FILE.write_text(json.dumps(creds.__dict__))
    os.chmod(CREDS_FILE, 0o600)


async def ensure_agent(cfg: SwitchSettings) -> tuple[AgentCredentials, bool]:
    creds = load_creds()
    if creds:
        client = SwitchAgentClient(cfg.api_url, creds)
        try:
            await client.list_operations()
            return creds, False
        except SwitchError:
            pass  # key revoked or server reset: register again
        finally:
            await client.aclose()
    async with httpx.AsyncClient(base_url=cfg.api_url, timeout=60) as http:
        key = cfg.registration_key
        if not key:
            if not (cfg.gateway_admin_email and cfg.gateway_admin_password):
                raise SystemExit(
                    "Set SWITCH_REGISTRATION_KEY, or SWITCH_GATEWAY_ADMIN_EMAIL and "
                    "SWITCH_GATEWAY_ADMIN_PASSWORD, to register the agent."
                )
            key = await mint_registration_key(
                http, cfg.gateway_admin_email, cfg.gateway_admin_password, "blackbox-proof"
            )
        creds = await register_agent(
            http,
            key,
            name=cfg.agent_name,
            display_name="BLACKBOX Recovery",
            description="BLACKBOX Recovery agent (Switch integration proof)",
            overwrite=True,
        )
    save_creds(creds)
    return creds, True


# ---------- run ----------


def _is_message(frame: Frame, room_id: str) -> bool:
    return frame.event == "message" and frame.data.get("room_id") == room_id


async def run(as_ic: bool, wait_seconds: int) -> int:
    cfg = get_switch_settings()
    run_id = datetime.now(UTC).strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:4]
    incident_id = f"INC-PROOF-{run_id}"
    evidence: dict[str, Any] = {"run_id": run_id, "switch_api_url": cfg.api_url, "steps": {}}

    ic: MattermostHarness | None = None
    if as_ic:
        if not cfg.mattermost_ic_password:
            raise SystemExit("--as-ic needs MATTERMOST_IC_PASSWORD (test harness only).")
        ic = MattermostHarness(cfg.mattermost_url, cfg.ic_chat_username, cfg.mattermost_ic_password)
        await ic.login()

    # 1. Register one agent
    creds, registered = await ensure_agent(cfg)
    client = SwitchAgentClient(cfg.api_url, creds)
    ops = await client.list_operations()
    say(
        "1/6 register",
        f"agent {creds.name} id={creds.agent_id} "
        f"({'registered now' if registered else 'reused saved credentials'}); "
        f"{len(ops)} operations available",
    )
    record(
        "switch.agent_ready",
        {
            "agent_id": creds.agent_id,
            "name": creds.name,
            "registered_now": registered,
            "operations": len(ops),
        },
    )
    evidence["steps"]["register"] = {
        "agent_id": creds.agent_id,
        "name": creds.name,
        "registered_now": registered,
        "operation_count": len(ops),
    }

    stream = AgentStream(cfg.api_url, creds.agent_id, creds.api_key, "blackbox-proof")
    stream.start()
    state = await stream.wait_for(lambda f: f.event == "connection_state", 15)
    if state is None:
        raise SystemExit(f"WebSocket did not open: {stream.closed_reason}")
    say(
        "   stream",
        f"WebSocket open, protocol={state.data.get('protocol')}, "
        f"heartbeat={state.data.get('heartbeat_interval_seconds')}s",
    )

    try:
        # 2. Create an incident room
        room_name = f"incident-proof-{run_id}"
        room = await client.create_room(
            name=room_name,
            description=f"{incident_id}: BLACKBOX Switch integration proof (no real incident)",
            agent_names=[creds.name],
            user_names=[cfg.ic_chat_username],
            instructions="Integration proof room. Nothing here triggers any action.",
        )
        room_id = room["id"]
        say("2/6 room", f"created {room_name} id={room_id}")
        record(
            "switch.room_created",
            {"room_id": room_id, "name": room_name, "incident_id": incident_id},
        )
        evidence["steps"]["room"] = {"room_id": room_id, "name": room_name}

        # A scope=all connection opened before the agent joined this room does not start
        # delivering its events on its own (observed in the first live run, 2026-11-07: the
        # human's message reached Switch but not the stream). Claim the room on this
        # connection, which is the documented way to cover a room after open.
        connected = await client.op(
            "connect_to_room",
            {"room_id": room_id, "include_general_instructions": False},
            connection_id=stream.connection_id,
        )
        say("   stream", f"claimed room on this connection (warning={connected.get('warning')})")

        # 3. Send a message from the agent
        sent = await client.post_message(
            room_id, f"BLACKBOX Recovery online for {incident_id} (Switch integration proof)."
        )
        say("3/6 message", f"agent posted, event_id={sent.get('event_id')}")
        record("switch.message_sent", {"room_id": room_id, "event_id": sent.get("event_id")})
        evidence["steps"]["message"] = {"event_id": sent.get("event_id")}

        # 4. Receive an event from Switch: a human message in the room
        channel_id: str | None = None
        hello = f"proof-hello-{run_id}"
        if ic:
            channel_id = (await ic.find_channel(room_name))["id"]
            await ic.post(channel_id, f"{hello}: IC here, acknowledging the room.")
            harness(f"posted '{hello}…' in #{room_name}")
        else:
            say(
                "4/6 event",
                f"ACTION: in Mattermost, post any message containing "
                f"'{hello}' in #{room_name} (waiting {wait_seconds}s)",
            )
        got = await stream.wait_for(
            lambda f: (
                _is_message(f, room_id) and hello in f.data.get("payload", {}).get("body", "")
            ),
            wait_seconds,
        )
        if got is None:
            raise SystemExit(f"No human message received ({stream.closed_reason or 'timeout'}).")
        payload = got.data["payload"]
        ic_participant = payload["sender"]
        say(
            "4/6 event",
            f"received '{got.event}' seq={got.id} from {payload.get('sender_name')} "
            f"({ic_participant}) addressed={payload.get('addressed')}",
        )
        record(
            "switch.event_received",
            {
                "room_id": room_id,
                "sequence": got.id,
                "sender": ic_participant,
                "addressed": payload.get("addressed"),
            },
        )
        evidence["steps"]["event"] = {
            "sequence": got.id,
            "sender": ic_participant,
            "sender_name": payload.get("sender_name"),
            "addressed": payload.get("addressed"),
        }

        # 5. Create a test approval decision, bound to a test artifact
        artifact = {"proof_run": run_id, "kind": "no-op test artifact", "executes": "nothing"}
        binding = create_binding(
            incident_id=incident_id,
            remediation_id="REM-1",
            patch_hash="sha256:" + sha256_hex(canonical_json(artifact)),
            action="proof_noop",
            environment="proof-sandbox",
            room_id=room_id,
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
            approver_allowlist=[ic_participant],
            title=f"{incident_id}: TEST approval (authorizes nothing)",
            detail_lines=["This is an integration test. Approving executes nothing."],
        )
        bound_seq = record("approval.bound", binding.to_json())  # persisted BEFORE opening
        row = await client.open_approval(binding.session_id, binding.open_body)
        say(
            "5/6 decision",
            f"approval opened request_id={binding.request_id} "
            f"state={row.get('state')} expires={binding.expires_at}",
        )
        record("approval.opened", {"request_id": binding.request_id, "state": row.get("state")})
        evidence["steps"]["approval_opened"] = {
            "request_id": binding.request_id,
            "binding_audit_seq": bound_seq,
            "state": row.get("state"),
        }

        # Negative control A: verify before any human decision -> must refuse.
        early = await verify_decision(client, binding)
        say(
            "   control A",
            f"verify before decision: allowed={early.allowed} "
            f"(failed: {[c['check'] for c in early.failed]})",
        )
        evidence["negative_controls"] = {"before_decision": _verdict_json(early)}

        # Negative control B: chat text is not a decision.
        if ic and channel_id:
            await ic.post(channel_id, f"APPROVE {incident_id}")
            harness(f"typed 'APPROVE {incident_id}' as plain chat")
            await asyncio.sleep(3)
            after_text = await verify_decision(client, binding)
            say(
                "   control B",
                f"verify after typed APPROVE text: allowed={after_text.allowed} "
                f"state={(after_text.observed or {}).get('state')}",
            )
            evidence["negative_controls"]["after_chat_text"] = _verdict_json(after_text)

        # Negative control C: a tampered binding (different artifact) -> must refuse.
        tampered = ApprovalBinding.from_json(
            {**binding.to_json(), "patch_hash": "sha256:" + "0" * 64}
        )
        tv = await verify_decision(client, tampered)
        say(
            "   control C",
            f"verify with tampered patch hash: allowed={tv.allowed} "
            f"(failed: {[c['check'] for c in tv.failed]})",
        )
        evidence["negative_controls"]["tampered_binding"] = _verdict_json(tv)

        # The human decides in Mattermost.
        if ic and channel_id:
            card = await ic.find_card(channel_id, binding.open_body["title"])
            pressed = await ic.press(card, "Approve")
            harness(f"pressed '{pressed}' on the approval card")
        else:
            say(
                "5/6 decision",
                f"ACTION: press Approve on the approval card in #{room_name} "
                f"(waiting {wait_seconds}s)",
            )
        outcome = await stream.wait_for(
            lambda f: (
                f.event == "approval_outcome" and f.data.get("request_id") == binding.request_id
            ),
            wait_seconds,
        )
        if outcome is None:
            raise SystemExit(f"No approval_outcome received ({stream.closed_reason or 'timeout'}).")
        say(
            "5/6 decision",
            f"approval_outcome pushed: state={outcome.data.get('state')} "
            f"answer={outcome.data.get('answer')} "
            f"by={outcome.data.get('answered_by')}",
        )
        record("approval.outcome_received", {"request_id": binding.request_id, **outcome.data})
        evidence["steps"]["approval_outcome_push"] = outcome.data
    finally:
        await stream.stop()
        await client.aclose()
        if ic:
            await ic.aclose()

    # 6. Independent verification: a separate process, binding from the audit log only.
    say("6/6 verify", "starting a separate process: python -m app.switch.proof verify")
    proc = subprocess.run(
        [sys.executable, "-m", "app.switch.proof", "verify", binding.request_id],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        check=False,
    )
    print(proc.stdout, end="")
    if proc.stderr.strip():
        print(proc.stderr, file=sys.stderr, end="")
    evidence["steps"]["independent_verify_exit_code"] = proc.returncode

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE_DIR / f"{run_id}.json"
    path.write_text(json.dumps(evidence, indent=2, default=str))
    say("done", f"evidence written to {path.relative_to(BACKEND_DIR)}")
    return proc.returncode


def _verdict_json(v: Any) -> dict[str, Any]:
    return {
        "allowed": v.allowed,
        "source": v.source,
        "checks": v.checks,
        "observed_state": (v.observed or {}).get("state"),
    }


# ---------- verify (independent) ----------


async def verify(request_id: str | None) -> int:
    cfg = get_switch_settings()
    bound = find_events("approval.bound")
    if request_id:
        bound = [b for b in bound if b["request_id"] == request_id]
    if not bound:
        print("verify: no approval.bound event found in the audit log", file=sys.stderr)
        return 2
    stored = bound[-1]
    binding = ApprovalBinding.from_json({k: v for k, v in stored.items() if k != "_seq"})
    creds = load_creds()
    if creds is None:
        print("verify: no agent credentials", file=sys.stderr)
        return 2
    consumed = any(
        e.get("request_id") == binding.request_id for e in find_events("approval.consumed")
    )
    client = SwitchAgentClient(cfg.api_url, creds)
    try:
        verdict = await verify_decision(client, binding, already_consumed=consumed)
    finally:
        await client.aclose()

    print(f"    independent verification of {binding.request_id}")
    print(f"    binding: audit_events seq={stored['_seq']}; decision source: {verdict.source}")
    for c in verdict.checks:
        print(f"      {'PASS' if c['ok'] else 'FAIL'}  {c['check']:<30} {c['detail']}")
    print(
        f"    VERDICT: {'ALLOWED' if verdict.allowed else 'REFUSED'} "
        f"(nothing is executed in this phase)"
    )
    record(
        "approval.verified",
        {
            "request_id": binding.request_id,
            "allowed": verdict.allowed,
            "checks": verdict.checks,
            "source": verdict.source,
        },
    )
    return 0 if verdict.allowed else 3


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.switch.proof")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run", help="run the Switch integration proof (steps 1-6)")
    p_run.add_argument(
        "--as-ic",
        action="store_true",
        help="TEST HARNESS: act as the human IC via Mattermost's API",
    )
    p_run.add_argument("--wait", type=int, default=300, help="seconds to wait for human steps")
    p_ver = sub.add_parser("verify", help="independently verify a stored approval")
    p_ver.add_argument("request_id", nargs="?")
    args = parser.parse_args()

    upgrade_to_head(get_settings().database_url)
    if args.cmd == "run":
        raise SystemExit(asyncio.run(run(args.as_ic, args.wait)))
    raise SystemExit(asyncio.run(verify(args.request_id)))


if __name__ == "__main__":
    main()
