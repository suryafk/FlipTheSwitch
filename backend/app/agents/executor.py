"""BLACKBOX executor: the only component that can change the sandbox service.

    uv run python -m app.agents.executor APR-1042-1

Recovery may only *ask* it to act on an approval id. It trusts nothing in that request and,
fail-closed, refuses unless every check passes (docs/04-APPROVAL-SECURITY.md §5):
  1. the decision, re-read from Switch, is answered/approve by an allowlisted approver, unexpired,
     bound to this exact request (verify_decision, proven in Task 10);
  2. the approved commit, frozen into the demo repository, hashes to the approved patch hash,
     its parent is the approved base, and the base is what is deployed right now;
  3. this approval has never been used (unique execution row, claimed atomically).
Then it deploys the exact commit through the launcher, verifies recovery with measured samples,
rolls back automatically if verification fails, records everything, and only then acknowledges
the Switch outcome (/delivered).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import time
from typing import Any

import httpx

from app.agents import patch, tools
from app.agents.common import get_agent_settings, load_credentials
from app.core.config import get_settings
from app.db.migrate import upgrade_to_head
from app.incidents import store
from app.incidents.store import TransitionError
from app.switch.approvals import ApprovalBinding, verify_decision
from app.switch.client import SwitchAgentClient
from app.switch.config import get_switch_settings

PASS = {"error_rate_pct": 1.0, "latency_ms_p50": 300.0, "pool_utilization_pct": 80.0}
FLUSH_SECONDS = 15
VERIFY_SECONDS = 60
NEEDED = 3


def ok_sample(s: dict[str, Any], sha: str) -> list[str]:
    fails = []
    if s.get("git_sha") != sha:
        fails.append("git_sha")
    if (s.get("requests") or 0) < 1000:
        fails.append("requests<1000")
    for k, limit in PASS.items():
        if (s.get(k) or 0) >= limit:
            fails.append(f"{k}>={limit}")
    return fails


def deploy(sha: str, deploy_id: str, source: str, approval_id: str) -> dict[str, Any]:
    cfg = get_agent_settings()
    r = httpx.post(
        f"{cfg.launcher_url}/deploy",
        timeout=90,
        headers={"Authorization": f"Bearer {cfg.launcher_token}"},
        json={"sha": sha, "deploy_id": deploy_id, "source": source, "approval_id": approval_id},
    )
    r.raise_for_status()
    return r.json()


def next_deploy_id() -> str:
    ids = [int(d["deploy_id"]) for d in tools.deployments() if str(d.get("deploy_id")).isdigit()]
    return str(max(ids, default=841) + 1)


async def run(approval_id: str) -> dict[str, Any]:
    cfg = get_agent_settings()
    approval = store.get_approval(approval_id)
    if approval is None:
        return {"status": "refused", "reason": "unknown approval"}
    b = approval["binding"]
    binding = ApprovalBinding.from_json(b)
    iid = binding.incident_id
    rem = store.get_remediation(binding.remediation_id) or {}
    reader = SwitchAgentClient(get_switch_settings().api_url, load_credentials("recovery"))

    def refuse(reason: str, checks: list | None = None) -> dict[str, Any]:
        store.audit(
            "execution.refused",
            "executor",
            {"approval_id": approval_id, "reason": reason, "checks": checks},
        )
        with contextlib.suppress(TransitionError):
            store.transition(iid, "NEEDS_HUMAN", "executor", f"execution refused: {reason}")
        return {"status": "refused", "reason": reason, "checks": checks}

    try:
        # 1. The decision, read back from Switch (never from Recovery or chat).
        verdict = await verify_decision(
            reader,
            binding,
            already_consumed=store.execution_exists(approval_id),
            revoked=store.is_revoked(approval_id),
        )
        if verdict.allowed:
            store.audit(
                "approval.verified",
                "executor",
                {
                    "approval_id": approval_id,
                    "request_id": binding.request_id,
                    "source": verdict.source,
                    "checks": verdict.checks,
                },
            )
        if not verdict.allowed:
            return refuse(
                "decision not verified: " + ", ".join(c["check"] for c in verdict.failed),
                verdict.checks,
            )

        # 2. Freeze the approved commit into the demo repository and re-derive its hash there.
        commit, base = rem.get("commit_sha"), rem.get("base_commit")
        worktree = (rem.get("details") or {}).get("worktree")
        if not (commit and base and worktree):
            return refuse("remediation record incomplete")
        repo = cfg.demo_repo
        patch.git(repo, "fetch", "-q", worktree, commit)
        patch.git(repo, "update-ref", f"refs/blackbox/approved/{approval_id}", commit)
        frozen_hash = patch.patch_hash(repo, base, commit)
        parent = patch.git(repo, "rev-parse", f"{commit}^").strip()
        deployed = tools.metrics_summary().get("git_sha")
        artifact_checks = [
            {
                "check": "frozen_hash_matches_approval",
                "ok": frozen_hash == binding.patch_hash,
                "detail": frozen_hash,
            },
            {"check": "commit_parent_is_base", "ok": parent == base, "detail": parent},
            {"check": "base_is_currently_deployed", "ok": deployed == base, "detail": deployed},
        ]
        if not all(c["ok"] for c in artifact_checks):
            return refuse("artifact checks failed", artifact_checks)

        state = (store.get_incident(iid) or {}).get("state")
        if state != "APPROVED":
            return refuse(f"incident is {state}, not APPROVED")

        # 3. Single use, claimed atomically.
        try:
            execution_id = store.claim_execution(approval_id, iid)
        except TransitionError as exc:
            return refuse(str(exc))
        # A revocation that landed between verification and the claim still wins.
        if store.is_revoked(approval_id):
            store.finish_execution(execution_id, "refused", {"reason": "approval revoked"})
            return refuse("approval revoked")
        store.transition(iid, "EXECUTING", "executor", f"{execution_id}: deploying {commit[:12]}")
        before = tools.metrics_summary()
        deploy_id = next_deploy_id()
        result = deploy(commit, deploy_id, "blackbox-executor", approval_id)
        outcome: dict[str, Any] = {
            "execution_id": execution_id,
            "approval_id": approval_id,
            "commit": commit,
            "deploy_id": deploy_id,
            "deploy": result,
            "before": before,
            "decision": {
                "answered_by": (verdict.observed or {}).get("answeredBy"),
                "answered_at": (verdict.observed or {}).get("answeredAt"),
                "source": verdict.source,
            },
            "checks": verdict.checks + artifact_checks,
        }
        if result.get("status") != "succeeded":
            store.transition(
                iid, "ROLLED_BACK", "executor", f"deploy failed: {result.get('error')}"
            )
            store.finish_execution(execution_id, "failed", outcome)
            return {"status": "deploy_failed", **outcome}

        # 4. Verify recovery with measured samples.
        store.transition(iid, "VERIFYING", "executor", f"deploy #{deploy_id} succeeded")
        await asyncio.sleep(FLUSH_SECONDS)
        samples, streak, deadline = [], 0, time.monotonic() + VERIFY_SECONDS
        while time.monotonic() < deadline and streak < NEEDED:
            s = tools.metrics_summary()
            fails = ok_sample(s, commit)
            samples.append({**s, "failed_criteria": fails})
            streak = streak + 1 if not fails else 0
            if streak < NEEDED:
                await asyncio.sleep(5)
        outcome["samples"] = samples
        outcome["after"] = samples[-1] if samples else None
        if streak >= NEEDED:
            store.transition(iid, "RESOLVED", "executor", f"{NEEDED} consecutive passing samples")
            store.finish_execution(execution_id, "succeeded", outcome)
            status = "resolved"
        else:
            store.transition(iid, "ROLLING_BACK", "executor", "verification failed")
            rb = deploy(base, next_deploy_id(), "rollback", approval_id)
            outcome["rollback"] = rb
            store.transition(
                iid,
                "ROLLED_BACK" if rb.get("status") == "succeeded" else "FAILED",
                "executor",
                "pre-approved rollback",
            )
            store.finish_execution(execution_id, "rolled_back", outcome)
            status = "rolled_back"
        # 5. Only now acknowledge the Switch outcome.
        await reader.mark_delivered(binding.session_id, binding.request_id)
        return {"status": status, **outcome}
    finally:
        await reader.aclose()


def main() -> None:
    upgrade_to_head(get_settings().database_url)
    result = asyncio.run(run(sys.argv[1]))
    print(json.dumps(result, default=str))
    sys.exit(0 if result["status"] == "resolved" else 3)


if __name__ == "__main__":
    main()
