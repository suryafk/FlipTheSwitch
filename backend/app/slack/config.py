"""Slack integration settings (env vars). Secrets are read from the environment only."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import BACKEND_DIR, REPO_ROOT

# Least-privilege bot scopes: post/update its own messages, receive slash commands,
# read public channel metadata (to verify the configured channel and membership).
BOT_SCOPES = ("chat:write", "commands", "channels:read")


class SlackSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", BACKEND_DIR / ".env"), extra="ignore"
    )

    client_id: str = Field(default="", alias="SLACK_CLIENT_ID")
    client_secret: str = Field(default="", alias="SLACK_CLIENT_SECRET")
    signing_secret: str = Field(default="", alias="SLACK_SIGNING_SECRET")
    # Must exactly match a Redirect URL configured in the Slack app (HTTPS, e.g. a tunnel).
    redirect_uri: str = Field(default="", alias="SLACK_REDIRECT_URI")
    # Comma-separated workspace (team) ids allowed to install and use the app. Empty = any.
    allowed_team_ids: str = Field(default="", alias="SLACK_ALLOWED_TEAM_IDS")
    # Default incident channel (can also be set through the admin API).
    incident_channel_id: str = Field(default="", alias="SLACK_INCIDENT_CHANNEL_ID")
    # Channels where /blackbox works. Empty = only the incident channel.
    command_channel_ids: str = Field(default="", alias="SLACK_COMMAND_CHANNEL_IDS")
    api_base: str = Field(default="https://slack.com/api", alias="SLACK_API_BASE")
    # Encrypts bot tokens at rest (Fernet key). Required to store a token.
    secret_key: str = Field(default="", alias="BLACKBOX_SECRET_KEY")
    # Protects configuration endpoints (set channel, import token, disconnect).
    admin_token: str = Field(default="", alias="BLACKBOX_ADMIN_TOKEN")
    # Where links in Slack messages point (the BLACKBOX UI).
    public_url: str = Field(default="http://localhost:5180", alias="BLACKBOX_PUBLIC_URL")
    bridge_poll_seconds: float = Field(default=2.0, alias="SLACK_BRIDGE_POLL_SECONDS")
    # Run the event bridge inside the API process (single API worker). Set false to run it as
    # its own process instead: `make slack-bridge`.
    bridge_in_api: bool = Field(default=True, alias="SLACK_BRIDGE_IN_API")

    @property
    def allowed_teams(self) -> set[str]:
        return {t.strip() for t in self.allowed_team_ids.split(",") if t.strip()}

    @property
    def command_channels(self) -> set[str]:
        return {c.strip() for c in self.command_channel_ids.split(",") if c.strip()}

    @property
    def oauth_configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.redirect_uri)


@lru_cache
def get_slack_settings() -> SlackSettings:
    return SlackSettings()
