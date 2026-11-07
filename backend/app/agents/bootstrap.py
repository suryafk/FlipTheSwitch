"""One-time Switch bootstrap for the BLACKBOX agents.

    uv run python -m app.agents.bootstrap [--as-ic]

1. Mint a personal registration key (owner = the Gateway user, who is the incident commander).
2. Register four identities: blackbox-control (system voice, no LLM), blackbox-sentinel,
   blackbox-rootcause, blackbox-recovery. Credentials are saved per role (mode 0600).
3. Create the standing intake room (Control + Sentinel + the IC).
4. Capture the IC's Switch participant id (the exact `answered_by` Switch will report) from the
   IC's own message in the intake room. Without --as-ic a person posts it; with --as-ic the
   TEST HARNESS posts it through Mattermost as that person.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid

import httpx

from app.agents.common import (
    AGENT_NAMES,
    DISPLAY_NAMES,
    ROLES,
    Topology,
    save_credentials,
    save_topology,
)
from app.switch.client import (
    SwitchAgentClient,
    disable_agent_greetings,
    gateway_login,
    mint_registration_key,
    register_agent,
)
from app.switch.config import get_switch_settings
from app.switch.mattermost_harness import MattermostHarness
from app.switch.stream import AgentStream

DESCRIPTIONS = {
    "control": "BLACKBOX system voice: posts verified alerts and state notices. No AI.",
    "sentinel": "Detects incidents, gathers evidence, hands investigations to RootCause.",
    "rootcause": "Diagnoses the faulty change, writes and tests a fix, proposes remediation.",
    "recovery": "Validates remediations, requests human approval, verifies recovery.",
}


async def main(as_ic: bool, wait: int) -> None:
    cfg = get_switch_settings()
    if not (cfg.gateway_admin_email and cfg.gateway_admin_password) and not cfg.registration_key:
        raise SystemExit("Set SWITCH_GATEWAY_ADMIN_EMAIL/PASSWORD or SWITCH_REGISTRATION_KEY.")
    async with httpx.AsyncClient(base_url=cfg.api_url, timeout=60) as http:
        key = cfg.registration_key or await mint_registration_key(
            http, cfg.gateway_admin_email or "", cfg.gateway_admin_password or "", "blackbox-agents"
        )
        creds = {}
        for role in ROLES:
            creds[role] = await register_agent(
                http,
                key,
                name=AGENT_NAMES[role],
                display_name=DISPLAY_NAMES[role],
                description=DESCRIPTIONS[role],
                overwrite=True,
            )
            save_credentials(role, creds[role])
            print(f"registered {AGENT_NAMES[role]} id={creds[role].agent_id}")

    control = SwitchAgentClient(cfg.api_url, creds["control"])
    if cfg.gateway_admin_email and cfg.gateway_admin_password:
        bridges = await control.op("list_bridges", {})
        async with httpx.AsyncClient(base_url=cfg.api_url, timeout=60) as http:
            cookie = await gateway_login(http, cfg.gateway_admin_email, cfg.gateway_admin_password)
            for bridge in bridges or []:
                bid = bridge.get("id") or bridge.get("bridge_id")
                if bid:
                    await disable_agent_greetings(http, cookie, bid)
                    print(f"agent greetings disabled on bridge {bid}")
    stream = AgentStream(
        cfg.api_url, creds["control"].agent_id, creds["control"].api_key, "blackbox-bootstrap"
    )
    stream.start()
    await stream.wait_for(lambda f: f.event == "connection_state", 15)
    suffix = uuid.uuid4().hex[:4]
    room = await control.create_room(
        name=f"blackbox-intake-{suffix}",
        description="BLACKBOX intake: verified monitoring alerts and human incident reports.",
        agent_names=[AGENT_NAMES["control"], AGENT_NAMES["sentinel"]],
        user_names=[cfg.ic_chat_username],
        instructions="Alerts are posted here by BLACKBOX Control. Sentinel triages them.",
    )
    room_id, room_name = room["id"], room["name"]
    await control.op(
        "connect_to_room",
        {"room_id": room_id, "include_general_instructions": False},
        connection_id=stream.connection_id,
    )
    print(f"intake room {room_name} id={room_id}")

    token = f"blackbox-ic-check-{suffix}"
    if as_ic:
        if not cfg.mattermost_ic_password:
            raise SystemExit("--as-ic needs MATTERMOST_IC_PASSWORD (test harness only).")
        ic = MattermostHarness(cfg.mattermost_url, cfg.ic_chat_username, cfg.mattermost_ic_password)
        await ic.login()
        channel = await ic.find_channel(room_name)
        await ic.post(channel["id"], f"{token}: incident commander present.")
        await ic.aclose()
        print(f"[HARNESS acting as IC] posted {token} in #{room_name}")
    else:
        print(f"ACTION: post a message containing '{token}' in #{room_name} (waiting {wait}s)")
    frame = await stream.wait_for(
        lambda f: f.event == "message" and token in f.data.get("payload", {}).get("body", ""), wait
    )
    await stream.stop()
    await control.aclose()
    if frame is None:
        raise SystemExit("IC message not received; cannot capture the approver identity.")
    ic_id = frame.data["payload"]["sender"]
    agent_ids = await capture_agent_identities(cfg.api_url, creds)
    save_topology(
        Topology(
            intake_room_id=room_id,
            ic_participant_id=ic_id,
            ic_chat_username=cfg.ic_chat_username,
            agent_participants=agent_ids,
        )
    )
    print(f"incident commander participant id: {ic_id}")
    print("bootstrap complete")


async def capture_agent_identities(api_url: str, creds: dict) -> dict[str, str]:
    """Pin each agent's Switch sender id (the `sender` Switch stamps on its messages).

    Message sender ids (`@switch-agent-…`) are not exposed by the roster APIs (docs/10 F10),
    so each agent posts once in a short-lived identity room and Control reads the senders back.
    Switch derives the sender from the API key, so receivers can then require an exact match.
    """
    control = SwitchAgentClient(api_url, creds["control"])
    room = await control.create_room(
        name=f"blackbox-identity-{uuid.uuid4().hex[:4]}",
        description="BLACKBOX bootstrap: agents announce themselves once to pin identities.",
        agent_names=[AGENT_NAMES[r] for r in ROLES],
        user_names=[],
        instructions="Bootstrap only.",
    )
    tokens = {}
    for role in ROLES:
        client = SwitchAgentClient(api_url, creds[role])
        tokens[role] = f"identity-check-{role}-{uuid.uuid4().hex[:6]}"
        await client.post_message(room["id"], tokens[role])
        await client.aclose()
    ctx = await control.read_context(room["id"], limit=50)
    ids: dict[str, str] = {}
    for thread in ctx.get("threads", []):
        for e in [thread["root"], *thread.get("replies", [])]:
            for role, tok in tokens.items():
                if tok in (e.get("body") or "") and e.get("sender_name") == AGENT_NAMES[role]:
                    ids[role] = e["sender"]
    await control.op("archive_room", {"room_id": room["id"]})
    await control.aclose()
    missing = set(ROLES) - set(ids)
    if missing:
        raise SystemExit(f"could not pin Switch identities for {sorted(missing)}")
    for role, sid in ids.items():
        print(f"pinned {AGENT_NAMES[role]} sender id {sid}")
    return ids


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--as-ic", action="store_true")
    p.add_argument("--wait", type=int, default=300)
    a = p.parse_args()
    asyncio.run(main(a.as_ic, a.wait))
