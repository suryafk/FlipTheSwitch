"""Settings for the Switch integration (env prefix SWITCH_*).

Only what has been verified against Switch 0.31.0 is configurable here (docs/01 §19-§20).
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import BACKEND_DIR, REPO_ROOT


class SwitchSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", BACKEND_DIR / ".env"), extra="ignore"
    )

    # Agent bridge base URL (switch-core). Not the Gateway UI port.
    api_url: str = Field(default="http://127.0.0.1:8100", alias="SWITCH_API_URL")

    # Used once, to mint a personal registration key, so the agents are owned by this user
    # (who must be the incident commander to answer their approval cards). See docs/07 §1.
    gateway_admin_email: str | None = Field(default=None, alias="SWITCH_GATEWAY_ADMIN_EMAIL")
    gateway_admin_password: str | None = Field(default=None, alias="SWITCH_GATEWAY_ADMIN_PASSWORD")
    # Alternatively supply an already-minted personal registration key.
    registration_key: str | None = Field(default=None, alias="SWITCH_REGISTRATION_KEY")

    # The incident commander's username on the bridged chat platform.
    ic_chat_username: str = Field(default="user", alias="SWITCH_IC_CHAT_USERNAME")
    # Which Switch chat connection ("bridge") incident rooms live on. Empty = Switch's default
    # bridge (Mattermost in the standalone stack). Set it to the Slack connection's id to run
    # incident rooms, and approval cards, in Slack. List ids with `make switch-bridges`.
    incident_bridge_id: str | None = Field(default=None, alias="SWITCH_INCIDENT_BRIDGE_ID")

    # TEST HARNESS ONLY: lets the proof script act as the IC through Mattermost's own API
    # (pressing the real approval button / posting a real message). Never used by product code.
    mattermost_url: str = Field(default="http://127.0.0.1:8065", alias="MATTERMOST_URL")
    mattermost_ic_password: str | None = Field(default=None, alias="MATTERMOST_IC_PASSWORD")

    agent_name: str = Field(default="blackbox-recovery", alias="SWITCH_PROOF_AGENT_NAME")


@lru_cache
def get_switch_settings() -> SwitchSettings:
    return SwitchSettings()
