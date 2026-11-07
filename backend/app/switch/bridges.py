"""List Switch's chat connections ("bridges"): `make switch-bridges`.

Uses an agent's own `list_bridges` operation, so no admin login is needed and no secrets are
shown. Put the Slack connection's id in SWITCH_INCIDENT_BRIDGE_ID to run incident rooms there.
"""

from __future__ import annotations

import asyncio
import json

from app.agents.common import load_credentials
from app.switch.client import SwitchAgentClient
from app.switch.config import get_switch_settings


async def main() -> None:
    client = SwitchAgentClient(get_switch_settings().api_url, load_credentials("control"))
    try:
        bridges = await client.op("list_bridges", {})
    finally:
        await client.aclose()
    for b in bridges or []:
        keep = {
            k: b.get(k)
            for k in (
                "id",
                "type",
                "display_name",
                "is_default",
                "status",
                "channel_creation_enabled",
            )
            if k in b
        }
        print(json.dumps(keep or b))


if __name__ == "__main__":
    asyncio.run(main())
