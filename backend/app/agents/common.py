"""Shared agent configuration: identities, Switch topology, simulator locations."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import BACKEND_DIR, REPO_ROOT
from app.switch.client import AgentCredentials

SWITCH_STATE = BACKEND_DIR / "data" / "switch"
AGENTS_DIR = SWITCH_STATE / "agents"
TOPOLOGY_FILE = SWITCH_STATE / "topology.json"

ROLES = ("control", "sentinel", "rootcause", "recovery")
AGENT_NAMES = {role: f"blackbox-{role}" for role in ROLES}
DISPLAY_NAMES = {
    "control": "BLACKBOX Control",
    "sentinel": "BLACKBOX Sentinel",
    "rootcause": "BLACKBOX RootCause",
    "recovery": "BLACKBOX Recovery",
}


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", BACKEND_DIR / ".env"), extra="ignore"
    )
    sim_dir: Path = Field(default=REPO_ROOT / "simulator", alias="BLACKBOX_SIM_DIR")
    service_url: str = Field(default="http://127.0.0.1:8090", alias="BLACKBOX_SIM_SERVICE_URL")
    launcher_url: str = Field(default="http://127.0.0.1:9001", alias="BLACKBOX_SIM_LAUNCHER_URL")
    launcher_token: str = Field(default="", alias="SIM_LAUNCHER_TOKEN")
    claude_bin: str = Field(default="claude", alias="BLACKBOX_CLAUDE_BIN")
    claude_model: str | None = Field(default=None, alias="BLACKBOX_CLAUDE_MODEL")
    coding_budget_usd: float = Field(default=0.50, alias="BLACKBOX_CODING_BUDGET_USD")
    approval_ttl_minutes: int = Field(default=15, alias="BLACKBOX_APPROVAL_TTL_MINUTES")

    @property
    def runtime(self) -> Path:
        return self.sim_dir / "runtime"

    @property
    def demo_repo(self) -> Path:
        return self.runtime / "demo-repo"


@lru_cache
def get_agent_settings() -> AgentSettings:
    return AgentSettings()


def save_credentials(role: str, creds: AgentCredentials) -> None:
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = AGENTS_DIR / f"{role}.json"
    path.write_text(json.dumps(creds.__dict__))
    os.chmod(path, 0o600)


def load_credentials(role: str) -> AgentCredentials:
    path = AGENTS_DIR / f"{role}.json"
    if not path.exists():
        raise SystemExit(f"no credentials for {role}: run `make agents-bootstrap` first")
    d = json.loads(path.read_text())
    return AgentCredentials(agent_id=d["agent_id"], name=d["name"], api_key=d["api_key"])


@dataclass(frozen=True)
class Topology:
    intake_room_id: str
    ic_participant_id: str
    ic_chat_username: str
    # role -> the exact `sender` id Switch stamps on that agent's messages (pinned at bootstrap)
    agent_participants: dict[str, str]
    # The Switch chat connection incident rooms are created on (None = Switch's default).
    # The IC participant id above belongs to that connection.
    bridge_id: str | None = None


def save_topology(t: Topology) -> None:
    SWITCH_STATE.mkdir(parents=True, exist_ok=True)
    TOPOLOGY_FILE.write_text(json.dumps(t.__dict__, indent=2))


def load_topology() -> Topology:
    if not TOPOLOGY_FILE.exists():
        raise SystemExit("no Switch topology: run `make agents-bootstrap` first")
    return Topology(**json.loads(TOPOLOGY_FILE.read_text()))
