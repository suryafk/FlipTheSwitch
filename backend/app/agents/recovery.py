"""BLACKBOX Recovery: validation, human approval, controlled execution, verification.

Triggered only by Switch events: an addressed remediation handoff from RootCause, and the
`approval_outcome` Switch pushes when a human answers the approval card. Recovery cannot deploy:
on a verified "approve" it starts the separate executor process with the approval id only.
Chat text is never treated as approval.

    uv run python -m app.agents.recovery
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.agents import patch, tools
from app.agents.common import get_agent_settings, load_topology
from app.agents.handoff import ack, extract_block, send_handoff, verify_incoming
from app.agents.runtime import AgentRuntime
from app.core.config import BACKEND_DIR
from app.incidents import store
from app.switch.approvals import create_binding

ENVIRONMENT = "staging-sandbox"
ACTION = "apply_remediation_commit"
PREVENTIVE_TASKS = [
    (
        "capacity-test-in-ci",
        "Keep test_pool_capacity_meets_peak_concurrency in CI so pool "
        "changes below config/capacity.yml fail the build",
        "service owner",
    ),
    (
        "startup-capacity-check",
        "Refuse to start when pool_size + max_overflow < peak_concurrent_db_sessions",
        "service owner",
    ),
    ("config-review-rule", "Require a reviewer for changes to config/database.yml", "eng manager"),
    ("pool-utilization-alert", "Alert at DB pool utilization > 80% for 1 min, before 5xx", "SRE"),
    ("runbook-update", "Add 'pool exhaustion after deploy' diagnosis steps to RUNBOOK.md", "SRE"),
]


class Recovery:
    def __init__(self) -> None:
        self.rt = AgentRuntime("recovery")
        self.rt.on_message = self.on_message
        self.rt.on_outcome = self.on_outcome
        self.cfg = get_agent_settings()
        self.topo = load_topology()
        self.handled: set[str] = set()

    # ---------- remediation proposal → validation → approval request ----------

    async def on_message(self, room_id: str, msg: dict[str, Any]) -> None:
        if not msg.get("addressed"):
            return
        block = extract_block(msg.get("body", ""))
        if not block or block.get("handoff_type") != "remediation_proposal":
            return
        if block.get("handoff_id") in self.handled:
            return
        record = store.get_handoff(block.get("handoff_id", ""))
        if record and record["status"] == "acked":
            return
        payload, reason = await verify_incoming(
            self.rt, room_id, msg, "rootcause", "remediation_proposal"
        )
        if payload is None:
            store.audit(
                "handoff.rejected",
                "recovery",
                {"handoff_id": block.get("handoff_id"), "reason": reason},
            )
            await self.rt.post(room_id, f"Rejected handoff {block.get('handoff_id')}: {reason}")
            return
        self.handled.add(payload["handoff_id"])
        await ack(self.rt, room_id, payload, "accepted")
        try:
            await self.validate_and_request(room_id, payload)
        except Exception as exc:  # noqa: BLE001
            self.rt.log.exception("validation failed")
            await self.rt.post(room_id, f"ERROR: validation failed: {type(exc).__name__}: {exc}")

    def validate(self, ref: dict[str, Any]) -> tuple[list[dict], dict]:
        rem = store.get_remediation(ref["remediation_id"]) or {}
        details = rem.get("details") or {}
        wt = details.get("worktree")
        checks: list[dict] = []

        def check(name: str, ok: bool, detail: str) -> None:
            checks.append({"check": name, "ok": bool(ok), "detail": detail})

        check("remediation_staged", rem.get("status") == "staged", str(rem.get("status")))
        check("commit_matches", rem.get("commit_sha") == ref["commit"], ref["commit"][:12])
        recomputed = patch.patch_hash(Path(wt), ref["base_commit"], ref["commit"]) if wt else None
        check(
            "patch_hash_recomputed",
            recomputed == ref["patch_hash"] == rem.get("patch_hash"),
            str(recomputed),
        )
        files = ref["changed_files"]
        check(
            "files_allowlisted",
            all(f.startswith(patch.ALLOWED_PREFIXES) for f in files),
            ", ".join(files),
        )
        t = details.get("tests") or {}
        check(
            "tests_green",
            t.get("failed") == 0 and t.get("errors") == 0 and t.get("total", 0) > 0,
            f"{t.get('passed')}/{t.get('total')}",
        )
        check(
            "regression_test_proves_fix",
            bool(details.get("new_tests")) and details.get("new_tests_fail_on_base") is True,
            str(details.get("new_tests")),
        )
        deployed = tools.metrics_summary().get("git_sha")
        check("base_is_deployed", deployed == ref["base_commit"], str(deployed)[:12])
        diff_lines = (
            sum(
                1
                for line in patch.git(
                    Path(wt), "diff", ref["base_commit"], ref["commit"]
                ).splitlines()
                if line[:1] in "+-" and not line.startswith(("+++", "---"))
            )
            if wt
            else 999
        )
        config_only = all(f.startswith(("config/", "tests/")) for f in files)
        risk = "LOW" if config_only and diff_lines <= 40 else "MEDIUM"
        return checks, {
            "risk": risk,
            "diff_lines": diff_lines,
            "worktree": wt,
            "tests": t,
            "new_tests": details.get("new_tests"),
        }

    async def validate_and_request(self, room_id: str, handoff: dict[str, Any]) -> None:
        ref = handoff["remediation_ref"]
        iid = handoff["incident_id"]
        store.transition(iid, "VALIDATING", "recovery", f"validating {ref['remediation_id']}")
        checks, info = await asyncio.to_thread(self.validate, ref)
        failed = [c for c in checks if not c["ok"]]
        store.audit(
            "validation.result",
            "recovery",
            {"remediation_id": ref["remediation_id"], "checks": checks, **info},
        )
        await self.rt.post(
            room_id,
            "VALIDATION "
            + ("PASSED" if not failed else "FAILED")
            + ": "
            + "; ".join(f"{'✓' if c['ok'] else '✗'} {c['check']} ({c['detail']})" for c in checks),
        )
        if failed:
            store.transition(iid, "NEEDS_REVISION", "recovery", "validation failed")
            await self.request_revision(
                iid,
                ref["remediation_id"],
                ref["patch_hash"],
                "validation_failed",
                None,
                [c["check"] for c in failed],
            )
            return

        approval_id = store.next_approval_id(iid)
        excerpt = patch.git(
            Path(info["worktree"]), "diff", "--no-color", ref["base_commit"], ref["commit"]
        )[:1400]
        binding = create_binding(
            incident_id=iid,
            remediation_id=ref["remediation_id"],
            patch_hash=ref["patch_hash"],
            action=ACTION,
            environment=ENVIRONMENT,
            room_id=room_id,
            expires_at=datetime.now(UTC) + timedelta(minutes=self.cfg.approval_ttl_minutes),
            approver_allowlist=[self.topo.ic_participant_id],
            title=f"{iid}: apply {ref['remediation_id']} to payment-service ({ENVIRONMENT})",
            detail_lines=[
                f"Commit: {ref['commit']} (base {ref['base_commit'][:12]})",
                f"Files: {', '.join(ref['changed_files'])}",
                f"Tests: {info['tests'].get('passed')}/{info['tests'].get('total')} passed; "
                f"new test {', '.join(info['new_tests'] or [])} fails on the faulty commit",
                f"Risk: {info['risk']} ({info['diff_lines']} changed lines)",
                f"Rollback: redeploy {ref['base_commit'][:12]} (pre-approved; automatic if "
                "verification fails)",
                "Diff:",
                excerpt,
            ],
        )
        store.create_approval(
            approval_id,
            iid,
            ref["remediation_id"],
            binding.request_id,
            binding.to_json(),
            "recovery",
        )
        store.transition(iid, "AWAITING_APPROVAL", "recovery", f"{approval_id} opened")
        row = await self.rt.client.open_approval(binding.session_id, binding.open_body)
        store.set_approval_status(
            approval_id, "open", "recovery", {"switch_state": row.get("state")}
        )
        await self.rt.send_to(
            room_id,
            [self.topo.ic_chat_username],
            f"Approval requested for {iid}: apply {ref['remediation_id']} "
            f"(patch {ref['patch_hash'][:19]}…) to payment-service "
            f"({ENVIRONMENT}). Risk {info['risk']}. Press Approve or Reject on "
            f"the approval card in this channel; it expires {binding.expires_at}. "
            "Typing 'approve' does not authorize anything.",
        )

    async def request_revision(
        self,
        iid: str,
        rid: str,
        phash: str,
        kind: str,
        feedback: str | None,
        failed: list[str],
        by: str | None = None,
    ) -> None:
        await send_handoff(
            self.rt,
            incident=store.get_incident(iid) or {},
            to_role="rootcause",
            handoff_type="revision_request",
            summary=f"Revision requested for {iid}: {rid} {kind.replace('_', ' ')}",
            body={
                "revision": {
                    "reason_kind": kind,
                    "rejected_remediation_id": rid,
                    "rejected_patch_hash": phash,
                    "human_feedback": feedback,
                    "failed_checks": failed,
                    "decided_by": by,
                }
            },
        )

    # ---------- approval outcome (pushed by Switch) ----------

    async def on_outcome(self, outcome: dict[str, Any]) -> None:
        approval = store.approval_by_request(outcome.get("request_id", ""))
        if approval is None or approval["approval_id"] in self.handled:
            return
        self.handled.add(approval["approval_id"])
        iid, room = approval["incident_id"], approval["binding"]["room_id"]
        state, answer, by = outcome.get("state"), outcome.get("answer"), outcome.get("answered_by")
        store.audit(
            "approval.outcome_received",
            "recovery",
            {"approval_id": approval["approval_id"], **outcome},
        )
        if state == "answered" and answer == "approve":
            store.set_approval_status(approval["approval_id"], "approved", "recovery", {"by": by})
            store.transition(iid, "APPROVED", "recovery", f"approved by {by}")
            await self.rt.post(
                room,
                f"Switch decision received for {approval['approval_id']}: "
                f"APPROVED by {by}. Handing to the executor, which re-verifies "
                "the decision with Switch before deploying anything.",
            )
            await self.execute(iid, room, approval)
        elif state == "answered":
            store.set_approval_status(approval["approval_id"], "rejected", "recovery", {"by": by})
            store.transition(iid, "REJECTED", "recovery", f"rejected by {by}")
            store.transition(iid, "NEEDS_REVISION", "recovery", "human rejected the remediation")
            await self.rt.post(
                room,
                f"{approval['approval_id']} REJECTED by {by}. Nothing will be "
                "deployed. Asking RootCause for a revised fix.",
            )
            b = approval["binding"]
            await self.request_revision(
                iid, b["remediation_id"], b["patch_hash"], "rejected_by_human", None, [], by
            )
        elif state == "expired":
            store.set_approval_status(approval["approval_id"], "expired", "recovery", {})
            store.transition(iid, "APPROVAL_EXPIRED", "recovery", "approval expired")
            store.transition(iid, "NEEDS_HUMAN", "recovery", "approval expired")
            await self.rt.post(
                room, f"{approval['approval_id']} EXPIRED without an answer. Nothing was deployed."
            )

    async def execute(self, iid: str, room: str, approval: dict[str, Any]) -> None:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "app.agents.executor",
            approval["approval_id"],
            cwd=str(BACKEND_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        try:
            result = json.loads(out.decode().strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError):
            await self.rt.post(room, f"EXECUTOR ERROR: no result ({err.decode()[-400:]})")
            return
        checks = result.get("checks") or []
        verification = "; ".join(f"{'✓' if c.get('ok') else '✗'} {c.get('check')}" for c in checks)
        if result["status"] == "refused":
            await self.rt.post(
                room,
                f"EXECUTION REFUSED by the executor: {result.get('reason')}. "
                f"Nothing was deployed. Checks: {verification}",
            )
            return
        b, a = result.get("before") or {}, result.get("after") or {}
        await self.rt.post(
            room,
            f"EXECUTED deploy #{result.get('deploy_id')} "
            f"({result.get('commit', '')[:12]}). Executor checks: {verification}",
        )
        if result["status"] != "resolved":
            await self.rt.post(
                room,
                f"Recovery NOT verified ({result['status']}); the pre-approved "
                "rollback ran. Samples: " + json.dumps(result.get("samples"))[:1500],
            )
            return
        await self.rt.post(
            room,
            f"RECOVERED (verified, {len(result.get('samples', []))} samples): 5xx "
            f"{b.get('error_rate_pct')}% → {a.get('error_rate_pct')}%, p50 "
            f"{b.get('latency_ms_p50')} ms → {a.get('latency_ms_p50')} ms, DB pool "
            f"{b.get('pool_utilization_pct')}% → {a.get('pool_utilization_pct')}% "
            f"(measured at {b.get('sampled_at')} and {a.get('sampled_at')}).",
        )
        await self.report(iid, room, result)

    async def report(self, iid: str, room: str, result: dict[str, Any]) -> None:
        inc = store.get_incident(iid) or {}
        rem_id = (store.get_approval(result["approval_id"]) or {}).get("remediation_id")
        rem = store.get_remediation(rem_id) or {}
        rc = (rem.get("details") or {}).get("root_cause", {})
        dec = result.get("decision") or {}
        report = {
            "incident_id": iid,
            "service": inc.get("service"),
            "severity": inc.get("severity"),
            "root_cause": rc.get("statement"),
            "remediation_id": rem_id,
            "patch_hash": rem.get("patch_hash"),
            "coding_agent": rem.get("coding_mode"),
            "approval": {"approval_id": result["approval_id"], **dec},
            "deploy_id": result.get("deploy_id"),
            "before": result.get("before"),
            "after": result.get("after"),
            "preventive_tasks": [t[0] for t in PREVENTIVE_TASKS],
        }
        store.audit("report.generated", "recovery", report)
        store.transition(iid, "POSTMORTEM_GENERATED", "recovery", "report generated")
        tasks = "\n".join(
            f"• [{slug}] {text} (owner: {owner})" for slug, text, owner in PREVENTIVE_TASKS
        )
        for slug, text, owner in PREVENTIVE_TASKS:
            store.audit(
                "task.preventive",
                "recovery",
                {"incident_id": iid, "slug": slug, "text": text, "owner": owner},
            )
        store.transition(iid, "PREVENTIVE_TASKS_CREATED", "recovery", "preventive tasks created")
        await self.rt.post(
            room,
            f"INCIDENT REPORT {iid} ({inc.get('severity')}, {inc.get('service')})\n"
            f"Root cause: {rc.get('statement')}\n"
            f"Fix: {rem_id} {rem.get('patch_hash', '')[:19]}… "
            f"(coding agent: {rem.get('coding_mode')})\n"
            f"Approved by {dec.get('answered_by')} at {dec.get('answered_at')} "
            f"(read back from Switch via {dec.get('source')})\n"
            f"Deployed as #{result.get('deploy_id')}; recovery verified.\n"
            f"Preventive tasks:\n{tasks}",
        )


async def main() -> None:
    await Recovery().rt.run()


if __name__ == "__main__":
    asyncio.run(main())
