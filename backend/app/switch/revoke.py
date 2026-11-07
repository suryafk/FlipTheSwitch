"""Revoke a BLACKBOX approval: `python -m app.switch.revoke APR-1042-1 "reason"`.

Switch 0.31.0 lets the requesting agent close an approval request only while it is still open
(POST /agent-sessions/{session}/approvals/{request}/close); there is no revoke after a human
has answered (docs/01 capability matrix). So revocation is recorded in BLACKBOX, which the
executor checks before and after verifying the decision with Switch, and the Switch request is
also closed when it is still open so the card can no longer be answered.
"""

from __future__ import annotations

import asyncio
import json
import sys

from app.agents.common import load_credentials
from app.core.config import get_settings
from app.db.migrate import upgrade_to_head
from app.incidents import store
from app.switch.client import SwitchAgentClient, SwitchError
from app.switch.config import get_switch_settings


async def revoke(approval_id: str, reason: str, actor: str = "incident-commander") -> dict:
    approval = store.get_approval(approval_id)
    if approval is None:
        return {"revoked": False, "reason": "unknown approval"}
    if not store.revoke_approval(approval_id, actor, reason):
        return {"revoked": False, "reason": "already revoked or already executed"}
    binding = approval["binding"]
    result: dict = {"revoked": True, "approval_id": approval_id, "switch_close": None}
    client = SwitchAgentClient(get_switch_settings().api_url, load_credentials("recovery"))
    try:
        closed = await client.close_approval(binding["session_id"], binding["request_id"])
        result["switch_close"] = {"ok": True, "state": closed.get("state")}
    except SwitchError as exc:
        # Expected once the request was answered or expired: Switch only closes open requests.
        result["switch_close"] = {"ok": False, "status": exc.status, "body": exc.body[:200]}
    finally:
        await client.aclose()
    store.audit("approval.switch_close", actor, {"approval_id": approval_id, **result})
    return result


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit('usage: python -m app.switch.revoke APR-… ["reason"]')
    upgrade_to_head(get_settings().database_url)
    reason = sys.argv[2] if len(sys.argv) > 2 else "revoked by the incident commander"
    print(json.dumps(asyncio.run(revoke(sys.argv[1], reason)), indent=1))


if __name__ == "__main__":
    main()
