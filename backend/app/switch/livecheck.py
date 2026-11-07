"""Milestone 12 live check: BLACKBOX's Switch integration exercised against the real platform.

    make switch-livecheck            # unattended: the TEST HARNESS presses the IC's buttons
    make switch-livecheck ARGS=--no-harness   # a human answers the approval cards in Mattermost

Everything here talks to the running Switch server (SWITCH_API_URL) with the agents
registered by `make agents-bootstrap`. Nothing is mocked. The only non-BLACKBOX actor is the
TEST HARNESS (app/switch/mattermost_harness.py), which logs in to Mattermost *as the human
incident commander* and presses the approval card's buttons exactly as the person would;
Switch still authorizes that press. Every harness action is printed with "[HARNESS]".

State goes to an isolated SQLite database under backend/data/livecheck/, so the live check
never touches the real incident store. Evidence is written next to it as JSON.

This is not an autonomous agent run: Sentinel, RootCause and Recovery are driven step by step
through their real runtimes (the same Switch connections, handoff code and ledger they use).
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import hashlib
import json
import os
import secrets
import sys
import time
import traceback
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.core.config import BACKEND_DIR

LIVE_DIR = BACKEND_DIR / "data" / "livecheck"
ROLES = ("sentinel", "rootcause", "recovery")
TASK_WORDS = ("task", "delegat", "decision")


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class StepFailed(AssertionError):
    pass


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise StepFailed(msg)


def use_isolated_db(path: Path) -> str:
    """Point BLACKBOX at a fresh SQLite file (before any store access) and migrate it."""
    from app.core.config import get_settings
    from app.db.migrate import upgrade_to_head
    from app.db.session import get_engine, get_sessionmaker

    path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite:///{path}"
    os.environ["BLACKBOX_DATABASE_URL"] = url
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    upgrade_to_head(url)
    return url


def slack_target_from(main_db_url: str) -> tuple[str, str] | None:
    """(bot token, channel) for the real Slack step: env vars first, else the installation the
    running BLACKBOX already has (token decrypted with BLACKBOX_SECRET_KEY)."""
    token = os.environ.get("SLACK_LIVE_BOT_TOKEN", "")
    channel = os.environ.get("SLACK_LIVE_CHANNEL_ID", "")
    if token and channel:
        return token, channel
    from sqlalchemy import create_engine, text

    from app.slack.config import get_slack_settings
    from app.slack.crypto import TokenCryptoError, decrypt_token

    engine = create_engine(main_db_url)
    try:
        with engine.connect() as c:
            row = c.execute(
                text("SELECT token_encrypted FROM slack_installations WHERE active=1 LIMIT 1")
            ).first()
            ch = c.execute(text("SELECT value FROM slack_settings WHERE key='channel_id'")).first()
    except Exception:  # noqa: BLE001 - no Slack tables or no database: not configured
        return None
    finally:
        engine.dispose()
    channel = (ch[0] if ch else "") or get_slack_settings().incident_channel_id
    if not row or not channel:
        return None
    try:
        return decrypt_token(row[0]), channel
    except TokenCryptoError:
        return None


class LiveCheck:
    def __init__(
        self,
        *,
        harness: bool = True,
        include_expiry: bool = True,
        log: Callable[[str], None] = print,
    ) -> None:
        from app.agents.common import load_topology
        from app.switch.config import get_switch_settings

        self.cfg = get_switch_settings()
        self.topo = load_topology()
        self.run_id = secrets.token_hex(3)
        # The Mattermost harness cannot act in Slack: on another bridge a human answers.
        self.use_harness = harness and not self.topo.bridge_id
        self.include_expiry = include_expiry
        self.log = log
        self.evidence: dict[str, Any] = {
            "run_id": self.run_id,
            "started_at": now_iso(),
            "switch_api_url": self.cfg.api_url,
            "bridge_id": self.topo.bridge_id or "default",
            "answered_by": "harness" if self.use_harness else "human",
            "steps": {},
        }
        self.rts: dict[str, Any] = {}
        self.inbox: dict[str, list[dict[str, Any]]] = {r: [] for r in ROLES}
        self.outcomes: list[dict[str, Any]] = []
        self.pumps: list[asyncio.Task[None]] = []
        self.ic: Any = None
        self.ic_channel: str | None = None
        self.incident_id = ""
        self.room_id = ""
        self.room_name = ""
        self.approved: Any = None  # the binding that was approved (step approval)
        self.main_db_url = ""

    # ---------- plumbing ----------

    def say(self, step: str, msg: str) -> None:
        self.log(f"[{step}] {msg}")

    def harness(self, msg: str) -> None:
        self.log(f"[HARNESS acting as IC] {msg}")

    def record(self, step: str, data: dict[str, Any]) -> None:
        self.evidence["steps"][step] = {"ok": True, "at": now_iso(), **data}

    async def wait_inbox(
        self, role: str, pred: Callable[[dict[str, Any]], bool], timeout: float = 20
    ) -> dict[str, Any] | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for m in self.inbox[role]:
                if pred(m):
                    return m
            await asyncio.sleep(0.2)
        return None

    async def room_op(self, role: str, name: str, args: dict[str, Any]) -> Any:
        """Run a room-bound operation on `role`'s connection (connect_to_room first)."""
        rt = self.rts[role]
        async with rt._lock:
            await rt.client.op(
                "connect_to_room",
                {"room_id": self.room_id, "include_general_instructions": False},
                connection_id=rt.stream.connection_id,
            )
            return await rt.client.op(name, args, connection_id=rt.stream.connection_id)

    # ---------- 1. registration and authentication ----------

    async def step_register(self) -> None:
        from app.switch.client import (
            AgentCredentials,
            SwitchAgentClient,
            SwitchError,
            mint_registration_key,
            register_agent,
        )

        async with httpx.AsyncClient(base_url=self.cfg.api_url, timeout=60) as http:
            key = self.cfg.registration_key or await mint_registration_key(
                http,
                self.cfg.gateway_admin_email or "",
                self.cfg.gateway_admin_password or "",
                f"blackbox-livecheck-{self.run_id}",
            )
            creds = await register_agent(
                http,
                key,
                name="blackbox-livecheck",
                display_name="BLACKBOX Live Check",
                description="Milestone 12 live check identity (no autonomy)",
                overwrite=True,
            )
        client = SwitchAgentClient(self.cfg.api_url, creds)
        try:
            ops = await client.list_operations()
        finally:
            await client.aclose()
        check(len(ops) > 0, "freshly registered agent could not list operations")
        bad = SwitchAgentClient(
            self.cfg.api_url,
            AgentCredentials(creds.agent_id, creds.name, "invalid-" + secrets.token_hex(8)),
        )
        try:
            await bad.list_operations()
            raise StepFailed("Switch accepted an invalid API key")
        except SwitchError as exc:
            rejected = exc.status
        finally:
            await bad.aclose()
        check(rejected == 401, f"invalid key gave HTTP {rejected}, expected 401")
        self.say(
            "1 register",
            f"registered blackbox-livecheck id={creds.agent_id}; "
            f"{len(ops)} operations; invalid key -> HTTP {rejected}",
        )
        self.record(
            "register",
            {
                "agent_id": creds.agent_id,
                "operations": len(ops),
                "invalid_key_status": rejected,
                "api_key_stored": "in memory only, never written or printed",
            },
        )

    # ---------- 2. connections (event subscription) ----------

    async def step_connect(self) -> None:
        from app.agents.runtime import AgentRuntime

        conns = {}
        for role in ROLES:
            rt = AgentRuntime(role)
            rt.on_message = self._collector(role)
            if role == "recovery":
                rt.on_outcome = self._outcome_collector
            rt.stream.start()
            state = await rt.stream.wait_for(lambda f: f.event == "connection_state", 20)
            check(state is not None, f"{role}: WebSocket did not open ({rt.stream.closed_reason})")
            rt._cursor = len(rt.stream.frames)
            self.rts[role] = rt
            self.pumps.append(asyncio.create_task(self._pump(rt)))
            conns[role] = {
                "connection_id": rt.stream.connection_id,
                "scope": state.data.get("scope"),
                "filter": state.data.get("filter"),
            }
        self.say(
            "2 connect",
            "Sentinel, RootCause and Recovery hold live WebSocket "
            "subscriptions (scope=all, filter=all, reconnect on)",
        )
        self.record("connect", {"connections": conns})

    def _collector(self, role: str) -> Callable[[str, dict[str, Any]], Awaitable[None]]:
        async def collect(room_id: str, payload: dict[str, Any]) -> None:
            self.inbox[role].append({**payload, "room_id": room_id, "seen_at": time.time()})

        return collect

    async def _outcome_collector(self, data: dict[str, Any]) -> None:
        self.outcomes.append(data)

    async def _pump(self, rt: Any) -> None:
        try:
            await rt._pump()
        except Exception as exc:  # noqa: BLE001 - reported by the steps that need the stream
            self.log(f"[pump] {rt.role} stopped: {exc}")

    # ---------- 3. incident room, membership, roles ----------

    async def step_room(self) -> None:
        from app.incidents import store

        inc = store.create_incident(
            "payment-service",
            f"[live check {self.run_id}] Switch integration check",
            f"livecheck-{self.run_id}",
            {"source": "livecheck"},
            "sentinel",
        )
        self.incident_id = inc["incident_id"]
        name = f"livecheck-{self.incident_id.lower()}-{self.run_id}"
        room = await self.rts["sentinel"].client.create_room(
            name,
            f"BLACKBOX live check for {self.incident_id}",
            agent_names=[f"blackbox-{r}" for r in ROLES],
            user_names=[self.topo.ic_chat_username],
            instructions="BLACKBOX live check room. No action is taken on this incident.",
            bridge_id=self.topo.bridge_id,
        )
        self.room_id, self.room_name = room["id"], room["name"]
        store.update_incident(
            self.incident_id, "sentinel", room_id=self.room_id, room_name=self.room_name
        )
        store.transition(self.incident_id, "INVESTIGATING", "sentinel", "live check started")
        for role in ROLES:
            await self.rts[role]._claim_and_catch_up(self.room_id)
        people = await self.rts["rootcause"].participants(self.room_id)
        names = sorted(p.get("name") for p in people)
        for r in ROLES:
            check(f"blackbox-{r}" in names, f"blackbox-{r} is not a room member: {names}")
        check(self.topo.ic_chat_username in names, f"IC not in room: {names}")
        await self.room_op(
            "sentinel",
            "define_role",
            {
                "name": "investigator",
                "exclusive": True,
                "instructions": "Investigate the incident's root cause and report evidence.",
            },
        )
        await self.room_op("rootcause", "release_role", {})
        await self.room_op("rootcause", "assume_role", {"role": "investigator"})
        roles = await self.room_op("sentinel", "list_roles", {})
        held = {r["name"]: [h.get("name") for h in r.get("held_by") or []] for r in roles or []}
        check(
            "blackbox-rootcause" in held.get("investigator", []),
            f"role not held as expected: {held}",
        )
        linked = store.get_incident(self.incident_id) or {}
        check(linked.get("room_id") == self.room_id, "incident not linked to the room")
        self.say(
            "3 room",
            f"{self.incident_id} <-> Switch room {self.room_name} "
            f"({self.room_id}); members {names}; role investigator held by RootCause",
        )
        self.record(
            "room",
            {
                "incident_id": self.incident_id,
                "room_id": self.room_id,
                "room_name": self.room_name,
                "participants": names,
                "roles": held,
            },
        )

    # ---------- 4. messages: broadcast, addressed, role-addressed ----------

    async def step_messaging(self) -> None:
        from app.incidents import store

        sent_at = int(time.time() * 1000)
        broadcast = await self.rts["sentinel"].post(
            self.room_id, f"[live check {self.run_id}] broadcast: incident room open"
        )
        got_rc = await self.wait_inbox("rootcause", lambda m: m.get("message_id") == broadcast)
        got_rv = await self.wait_inbox("recovery", lambda m: m.get("message_id") == broadcast)
        check(bool(got_rc and got_rv), "broadcast not received by both agents")
        check(got_rc["addressed"] is False, "broadcast should not be addressed")

        targeted = (
            await self.rts["sentinel"].send_to(
                self.room_id,
                ["blackbox-rootcause"],
                f"[live check {self.run_id}] addressed to RootCause",
            )
        )["event_id"]
        t_rc = await self.wait_inbox("rootcause", lambda m: m.get("message_id") == targeted)
        t_rv = await self.wait_inbox("recovery", lambda m: m.get("message_id") == targeted)
        check(bool(t_rc and t_rc["addressed"] is True), "RootCause did not get it as addressed")
        check(
            bool(t_rv and t_rv["addressed"] is False), "Recovery should overhear, not be addressed"
        )

        by_role = (
            await self.room_op(
                "sentinel",
                "send_targeted_message",
                {
                    "body": f"[live check {self.run_id}] addressed to the investigator role",
                    "target_roles": ["investigator"],
                },
            )
        )["event_id"]
        r_rc = await self.wait_inbox("rootcause", lambda m: m.get("message_id") == by_role)
        check(bool(r_rc and r_rc["addressed"] is True), "role-addressed message not addressed")

        row = store.switch_event("blackbox-rootcause", broadcast)
        check(row is not None, "broadcast not in the switch_events ledger")
        check(
            row["sequence"] is not None and row["switch_timestamp_ms"] >= sent_at - 5000,
            f"ledger lost the sequence or timestamp: {row}",
        )
        self.say(
            "4 messaging",
            f"broadcast {broadcast} (overheard by both), addressed "
            f"{targeted} (RootCause addressed, Recovery overheard), role-addressed {by_role}",
        )
        self.record(
            "messaging",
            {
                "broadcast": {
                    "event_id": broadcast,
                    "sequence": row["sequence"],
                    "switch_timestamp_ms": row["switch_timestamp_ms"],
                },
                "addressed": {
                    "event_id": targeted,
                    "rootcause_addressed": t_rc["addressed"],
                    "recovery_addressed": t_rv["addressed"],
                },
                "role_addressed": {"event_id": by_role, "rootcause_addressed": r_rc["addressed"]},
            },
        )

    # ---------- 5. duplicate processing ----------

    async def step_dedup(self) -> None:
        from app.incidents import store

        before = len(self.inbox["rootcause"])
        # A full read_context catch-up re-reads every message above: none may be processed again.
        await self.rts["rootcause"]._claim_and_catch_up(self.room_id)
        ctx = await self.rts["rootcause"].client.read_context(self.room_id, limit=50)
        ids = {
            e.get("id") for t in ctx.get("threads", []) for e in [t["root"], *t.get("replies", [])]
        }
        broadcast = self.evidence["steps"]["messaging"]["broadcast"]["event_id"]
        check(broadcast in ids, "read_context ids do not match stream message ids")
        check(
            len(self.inbox["rootcause"]) == before,
            f"catch-up re-delivered {len(self.inbox['rootcause']) - before} messages",
        )
        count = store._row(
            "SELECT COUNT(*) AS n FROM switch_events WHERE agent=:a AND event_id=:e",
            a="blackbox-rootcause",
            e=broadcast,
        )["n"]
        check(count == 1, f"ledger has {count} rows for one event")
        self.say(
            "5 dedup",
            f"read_context replayed {len(ids)} messages; 0 re-processed "
            "(ledger keyed by Switch event id)",
        )
        self.record("dedup", {"replayed_by_read_context": len(ids), "reprocessed": 0})

    # ---------- 6. agent handoff through Switch ----------

    async def step_handoff(self) -> None:
        from app.agents.handoff import ack, render, send_handoff, verify_incoming
        from app.incidents import store

        incident = store.get_incident(self.incident_id) or {}
        evidence = [
            {
                "evidence_id": f"EV-{self.run_id}-01",
                "kind": "metric",
                "label": "5xx rate (live check sample)",
                "value": "20.3%",
                "simulated": True,
            },
            {
                "evidence_id": f"EV-{self.run_id}-02",
                "kind": "deployment",
                "label": "last deploy",
                "value": "#842 9d33906",
                "simulated": True,
            },
        ]
        payload = await send_handoff(
            self.rts["sentinel"],
            incident=incident,
            to_role="rootcause",
            handoff_type="investigation",
            summary=f"Investigate {self.incident_id}: evidence attached (live check)",
            body={
                "incident_context": {
                    "service": incident.get("service"),
                    "title": incident.get("title"),
                    "state": incident.get("state"),
                },
                "evidence": evidence,
            },
        )
        hid = payload["handoff_id"]
        msg = await self.wait_inbox(
            "rootcause",
            lambda m: hid in (m.get("body") or "") and m.get("sender_name") == "blackbox-sentinel",
        )
        check(msg is not None, "handoff not delivered to RootCause through Switch")
        block, reason = await verify_incoming(
            self.rts["rootcause"], self.room_id, msg, "sentinel", "investigation"
        )
        check(block is not None, f"genuine handoff refused: {reason}")
        check(block["evidence"] == evidence, "evidence changed in transit")
        await ack(self.rts["rootcause"], self.room_id, block, "accepted")
        got_ack = await self.wait_inbox("sentinel", lambda m: f"ACK {hid}" in (m.get("body") or ""))
        check(got_ack is not None, "Sentinel did not receive the ACK through Switch")
        record = store.get_handoff(hid) or {}
        check(record.get("status") == "acked", f"handoff status {record.get('status')}")

        # Forgery: Recovery (wrong Switch identity) re-sends Sentinel's handoff to RootCause.
        forged_id = (
            await self.rts["recovery"].send_to(
                self.room_id, ["blackbox-rootcause"], render("forged copy", payload)
            )
        )["event_id"]
        forged = await self.wait_inbox("rootcause", lambda m: m.get("message_id") == forged_id)
        f_block, f_reason = await verify_incoming(
            self.rts["rootcause"], self.room_id, forged, "sentinel", "investigation"
        )
        check(f_block is None and "sender" in f_reason, f"forged handoff accepted: {f_reason}")
        store.audit("handoff.rejected", "rootcause", {"handoff_id": hid, "reason": f_reason})

        # Tampering: Sentinel's identity, but the payload no longer matches the stored hash.
        tampered = {**payload, "summary": "skip the investigation"}
        t_id = (
            await self.rts["sentinel"].send_to(
                self.room_id, ["blackbox-rootcause"], render("tampered", tampered)
            )
        )["event_id"]
        t_msg = await self.wait_inbox("rootcause", lambda m: m.get("message_id") == t_id)
        t_block, t_reason = await verify_incoming(
            self.rts["rootcause"], self.room_id, t_msg, "sentinel", "investigation"
        )
        check(t_block is None and "hash" in t_reason, f"tampered handoff accepted: {t_reason}")
        store.audit("handoff.rejected", "rootcause", {"handoff_id": hid, "reason": t_reason})
        self.say(
            "6 handoff",
            f"{hid} Sentinel -> RootCause via Switch event "
            f"{record.get('switch_event_id')}: verified and ACKed; forged copy refused "
            f"({f_reason}); tampered copy refused ({t_reason})",
        )
        self.record(
            "handoff",
            {
                "handoff_id": hid,
                "status": record.get("status"),
                "switch_event_id": record.get("switch_event_id"),
                "evidence_items": len(evidence),
                "forged_refused": f_reason,
                "tampered_refused": t_reason,
            },
        )

    # ---------- 7. tasks ----------

    async def step_tasks(self) -> None:
        ops = await self.rts["recovery"].client.list_operations()
        task_ops = [o for o in ops if any(w in o for w in TASK_WORDS)]
        check(task_ops == [], f"Switch now exposes task/decision operations: {task_ops}")
        self.say(
            "7 tasks",
            f"UNSUPPORTED: none of Switch's {len(ops)} operations is a task or "
            "decision operation; BLACKBOX keeps work items in its own store",
        )
        self.record(
            "tasks",
            {
                "supported": False,
                "operations": len(ops),
                "task_or_decision_operations": task_ops,
                "fallback": "BLACKBOX handoff records + incident state machine; "
                "decisions use Switch approval requests",
            },
        )

    # ---------- 8. decision: approval request, human answer, independent verification ----------

    async def _open(self, n: int, *, expires_in: timedelta = timedelta(minutes=10)) -> Any:
        from app.incidents import store
        from app.switch.approvals import create_binding

        rem = store.create_remediation(self.incident_id, "livecheck-base", "", "rootcause")
        patch_text = f"live check {self.run_id} proposed change {n}"
        patch_hash = "sha256:" + hashlib.sha256(patch_text.encode()).hexdigest()
        aid = f"APR-{self.incident_id.removeprefix('INC-')}-{n}"
        binding = create_binding(
            incident_id=self.incident_id,
            remediation_id=rem["remediation_id"],
            patch_hash=patch_hash,
            action="apply_remediation_commit",
            environment="staging-sandbox",
            room_id=self.room_id,
            expires_at=datetime.now(UTC) + expires_in,
            approver_allowlist=[self.topo.ic_participant_id],
            title=f"{self.incident_id} live check {aid}: approve the test action",
            detail_lines=["LIVE CHECK: approving deploys nothing."],
        )
        store.create_approval(
            aid,
            self.incident_id,
            rem["remediation_id"],
            binding.request_id,
            binding.to_json(),
            "recovery",
        )
        row = await self.rts["recovery"].client.open_approval(binding.session_id, binding.open_body)
        store.set_approval_status(aid, "open", "recovery", {"switch_state": row.get("state")})
        return aid, binding

    async def _ic(self) -> Any:
        if self.ic is None:
            from app.switch.mattermost_harness import MattermostHarness

            pw = self.cfg.mattermost_ic_password
            check(bool(pw), "MATTERMOST_IC_PASSWORD is required for the harness")
            self.ic = MattermostHarness(self.cfg.mattermost_url, self.topo.ic_chat_username, pw)
            await self.ic.login()
            self.ic_channel = (await self.ic.find_channel(self.room_name))["id"]
        return self.ic

    async def _answer(self, binding: Any, button: str) -> None:
        title = binding.open_body["title"]
        if self.use_harness:
            ic = await self._ic()
            card = await ic.find_card(self.ic_channel, title)
            pressed = await ic.press(card, button)
            self.harness(f"pressed '{pressed}' on the Switch card '{title}'")
        else:
            word = "approve" if button == "Approve" else "reject"
            self.log(
                f"\n>>> In channel #{self.room_name}, find the card '{title}'. Reply in that "
                f"card's thread with its handle and `{word}` (e.g. `A1 {word}`; the handle is "
                f"printed on the card), or press {button}. Waiting up to 5 minutes...\n"
            )

    async def _wait_state(self, binding: Any, states: set[str], timeout: float) -> dict:
        reader = self.rts["recovery"].client
        deadline = time.monotonic() + timeout
        row: dict[str, Any] = {}
        while time.monotonic() < deadline:
            for o in await reader.undelivered_outcomes():
                if (o.get("requestId") or o.get("request_id")) == binding.request_id:
                    row = o
                    if o.get("state") in states:
                        return o
            await asyncio.sleep(1)
        raise StepFailed(f"approval {binding.request_id} never reached {states}: {row}")

    async def step_approval(self) -> None:
        from app.incidents import store
        from app.switch.approvals import verify_decision

        aid, binding = await self._open(1)
        reader = self.rts["recovery"].client
        early = await verify_decision(reader, binding, revoked=store.is_revoked(aid))
        check(not early.allowed, "verification passed before any human answer")
        await self._answer(binding, "Approve")
        await self._wait_state(binding, {"answered"}, 30 if self.use_harness else 300)
        verdict = await verify_decision(
            reader,
            binding,
            already_consumed=store.execution_exists(aid),
            revoked=store.is_revoked(aid),
        )
        check(verdict.allowed, f"approved decision refused: {verdict.failed}")
        store.audit(
            "approval.verified",
            "executor",
            {
                "approval_id": aid,
                "request_id": binding.request_id,
                "allowed": True,
                "source": verdict.source,
                "checks": verdict.checks,
            },
        )
        execution_id = store.claim_execution(aid, self.incident_id)  # single use, no deploy
        replay = await verify_decision(
            reader,
            binding,
            already_consumed=store.execution_exists(aid),
            revoked=store.is_revoked(aid),
        )
        check(
            not replay.allowed and [c["check"] for c in replay.failed] == ["first_use"],
            f"replay not refused correctly: {replay.failed}",
        )
        await reader.mark_delivered(binding.session_id, binding.request_id)
        self.approved = binding
        observed = verdict.observed or {}
        self.say(
            "8 decision",
            f"{aid} opened in Switch, refused before the answer, approved by "
            f"{observed.get('answeredBy')}, verified from {verdict.source}; replay refused",
        )
        self.record(
            "approval",
            {
                "approval_id": aid,
                "request_id": binding.request_id,
                "bound_to": {
                    "incident": self.incident_id,
                    "action": binding.action,
                    "patch_hash": binding.patch_hash,
                    "environment": binding.environment,
                },
                "before_answer": [c["check"] for c in early.failed],
                "answered_by": observed.get("answeredBy"),
                "answered_at": observed.get("answeredAt"),
                "verified_from": verdict.source,
                "checks": verdict.checks,
                "execution_claimed": execution_id,
                "replay_refused": [c["check"] for c in replay.failed],
            },
        )

    # ---------- 9. invalid approvals ----------

    async def step_invalid_approvals(self) -> None:
        from app.incidents import store
        from app.switch.approvals import verify_decision
        from app.switch.client import SwitchError
        from app.switch.revoke import revoke

        reader = self.rts["recovery"].client
        results: dict[str, Any] = {}
        b = self.approved
        check(b is not None, "run the approval step first")

        # Content change under the same request id: Switch itself refuses (REQUEST_CONFLICT).
        changed = {**b.open_body, "detail": b.open_body["detail"] + "\nPatch: sha256:evil"}
        try:
            await reader.open_approval(b.session_id, changed)
            raise StepFailed("Switch accepted different content for an existing request id")
        except SwitchError as exc:
            check(exc.status == 409, f"expected 409, got {exc.status}")
            results["content_change"] = f"HTTP {exc.status} {exc.body[:80]}"

        # Mismatched binding (patch hash swapped after approval): refused before reading Switch.
        swapped = dataclasses.replace(b, patch_hash="sha256:" + "0" * 64)
        v = await verify_decision(reader, swapped)
        check(not v.allowed, "mismatched binding accepted")
        results["mismatched_binding"] = [c["check"] for c in v.failed]

        # Revoked while open: BLACKBOX revocation + Switch close.
        aid2, b2 = await self._open(2)
        rv = await revoke(aid2, "live check: revoked before answer", "livecheck")
        check(rv["revoked"] and rv["switch_close"]["ok"], f"revoke failed: {rv}")
        v2 = await verify_decision(reader, b2, revoked=store.is_revoked(aid2))
        check(
            not v2.allowed and "not_revoked" in [c["check"] for c in v2.failed],
            f"revoked approval not refused: {v2.failed}",
        )
        results["revoked"] = {
            "switch_close": rv["switch_close"],
            "failed_checks": [c["check"] for c in v2.failed],
        }

        # Rejected by the human.
        aid3, b3 = await self._open(3)
        await self._answer(b3, "Reject")
        await self._wait_state(b3, {"answered"}, 30 if self.use_harness else 300)
        v3 = await verify_decision(reader, b3, revoked=store.is_revoked(aid3))
        check(
            not v3.allowed and "answer_is_approve" in [c["check"] for c in v3.failed],
            f"rejected approval not refused: {v3.failed}",
        )
        await reader.mark_delivered(b3.session_id, b3.request_id)
        results["rejected"] = [c["check"] for c in v3.failed]

        # Expired with no answer.
        if self.include_expiry:
            aid4, b4 = await self._open(4, expires_in=timedelta(seconds=15))
            await self._wait_state(b4, {"expired"}, 45)
            v4 = await verify_decision(reader, b4, revoked=store.is_revoked(aid4))
            check(not v4.allowed, "expired approval accepted")
            await reader.mark_delivered(b4.session_id, b4.request_id)
            results["expired"] = [c["check"] for c in v4.failed]
        self.say(
            "9 invalid",
            "content change -> 409 REQUEST_CONFLICT; mismatched binding, "
            "revoked, rejected"
            + (", expired" if self.include_expiry else "")
            + " approvals all refused (fail closed)",
        )
        self.record("invalid_approvals", results)

    # ---------- 10. reconnection ----------

    async def step_reconnect(self) -> None:
        from app.incidents import store

        rt = self.rts["rootcause"]
        cid, before = rt.stream.connection_id, rt.stream.connects
        t0 = time.monotonic()
        await rt.stream.drop()
        missed = await self.rts["sentinel"].post(
            self.room_id, f"[live check {self.run_id}] posted while RootCause was disconnected"
        )
        deadline = time.monotonic() + 20
        while rt.stream.connects <= before and time.monotonic() < deadline:
            await asyncio.sleep(0.2)
        check(rt.stream.connects > before, f"no reconnect: {rt.stream.closed_reason}")
        reconnect_s = round(time.monotonic() - t0, 2)
        got = await self.wait_inbox("rootcause", lambda m: m.get("message_id") == missed, 20)
        check(got is not None, "message posted during the drop was lost")
        await asyncio.sleep(2)  # let the post-reconnect room re-claim and catch-up finish
        copies = sum(1 for m in self.inbox["rootcause"] if m.get("message_id") == missed)
        check(copies == 1, f"message processed {copies} times")
        check(rt.stream.connection_id == cid, "connection id changed")
        last = [h for h in rt.stream.history if h["what"] == "connected"][-1]
        reclaimed = store._row(
            "SELECT COUNT(*) AS n FROM audit_events WHERE kind='switch.reconnected'"
        )["n"]
        self.say(
            "10 reconnect",
            f"socket dropped; reconnected in {reconnect_s}s with the same "
            f"connection id, start_from={last['start_from']}; the message sent during the "
            f"drop arrived once ({got.get('via', 'stream')})",
        )
        self.record(
            "reconnect",
            {
                "connection_id": cid,
                "reconnect_seconds": reconnect_s,
                "resumed_from": last["start_from"],
                "missed_event": missed,
                "delivered_copies": copies,
                "reclaim_audit_events": reclaimed,
            },
        )

    # ---------- 11. Slack ----------

    async def step_slack(self) -> None:
        from app.slack import bridge as bridge_mod
        from app.slack import store as slack_store
        from app.slack.api import SlackClient
        from app.slack.config import get_slack_settings

        target = slack_target_from(self.main_db_url) if self.main_db_url else None
        if target is None:
            self.say("11 slack", "SKIPPED: no Slack installation or SLACK_LIVE_* variables")
            self.evidence["steps"]["slack"] = {"ok": None, "skipped": "Slack not configured"}
            return
        token, channel = target
        client = SlackClient(get_slack_settings().api_base)
        try:
            who = await client.auth_test(token)
            slack_store.save_installation(
                who["team_id"],
                who.get("team", ""),
                who.get("user_id"),
                "livecheck",
                token,
                actor="livecheck",
            )
            slack_store.set_setting("channel_id", channel)
            slack_store.set_setting(bridge_mod.CURSOR_KEY, "0")
            bridge = bridge_mod.SlackBridge(client, get_slack_settings())
            total = 0
            while (n := await bridge.run_once()) > 0:
                total += n
            counts = slack_store.delivery_counts()
            thread = slack_store.thread_for(self.incident_id)
            check(thread is not None, "no Slack thread for the incident")
            check(
                counts.get("delivered", 0) > 0 and not counts.get("failed"),
                f"deliveries: {counts} health={bridge_mod.health()}",
            )
            slack_store.set_setting(bridge_mod.CURSOR_KEY, "0")  # replay: must post nothing new
            await bridge.run_once()
            again = slack_store.delivery_counts()
            check(again == counts, f"replay changed deliveries: {counts} -> {again}")
        finally:
            await client.aclose()
        self.say(
            "11 slack",
            f"{counts.get('delivered')} messages in one thread "
            f"(ts {thread['thread_ts']}) in channel {channel}; replay posted nothing",
        )
        self.record(
            "slack",
            {
                "workspace": who.get("team"),
                "channel": channel,
                "thread_ts": thread["thread_ts"],
                "deliveries": counts,
                "replay_deliveries": again,
            },
        )

    # ---------- teardown ----------

    async def close(self) -> None:
        if "rootcause" in self.rts and self.room_id:
            with contextlib.suppress(Exception):
                await self.room_op("rootcause", "release_role", {})
        for t in self.pumps:
            t.cancel()
        for rt in self.rts.values():
            await rt.stream.stop()
            await rt.client.aclose()
        if self.ic:
            await self.ic.aclose()
        self.evidence["finished_at"] = now_iso()


STEPS = [
    "register",
    "connect",
    "room",
    "messaging",
    "dedup",
    "handoff",
    "tasks",
    "approval",
    "invalid_approvals",
    "reconnect",
    "slack",
]


async def run_all(lc: LiveCheck, steps: list[str]) -> int:
    failed = 0
    for name in steps:
        try:
            await getattr(lc, f"step_{name}")()
        except Exception as exc:  # noqa: BLE001 - report every step's outcome
            failed += 1
            lc.evidence["steps"][name] = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "trace": traceback.format_exc(),
            }
            lc.log(f"[{name}] FAILED: {type(exc).__name__}: {exc}")
            if name in ("connect", "room"):
                break  # later steps depend on these
    return failed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--no-harness",
        action="store_true",
        help="a human answers the cards in Mattermost instead of the harness",
    )
    parser.add_argument("--skip-expiry", action="store_true", help="skip the 15 s expiry case")
    parser.add_argument("--steps", default=",".join(STEPS))
    args = parser.parse_args()

    from app.core.config import get_settings

    main_url = get_settings().database_url
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    db_path = LIVE_DIR / f"livecheck-{stamp}.db"
    use_isolated_db(db_path)

    async def go() -> int:
        lc = LiveCheck(harness=not args.no_harness, include_expiry=not args.skip_expiry)
        lc.main_db_url = main_url
        lc.evidence["database"] = str(db_path)
        try:
            failed = await run_all(lc, [s for s in args.steps.split(",") if s])
        finally:
            await lc.close()
        out = LIVE_DIR / f"livecheck-{stamp}.json"
        out.write_text(json.dumps(lc.evidence, indent=1, default=str))
        ok = sum(1 for s in lc.evidence["steps"].values() if s.get("ok") is True)
        skipped = sum(1 for s in lc.evidence["steps"].values() if s.get("ok") is None)
        print(
            f"\nLIVE CHECK {'PASSED' if not failed else 'FAILED'}: {ok} passed, {failed} "
            f"failed, {skipped} skipped. Evidence: {out}"
        )
        return 1 if failed else 0

    sys.exit(asyncio.run(go()))


if __name__ == "__main__":
    main()
