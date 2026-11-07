"""Application settings, loaded from environment variables (and an optional .env file).

Every setting has a safe local-development default except where noted. Missing or invalid
values fail at startup rather than being silently replaced.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_DIR.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # The repository-root .env is shared by every component; a backend/.env overrides it.
        env_file=(REPO_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "blackbox-backend"
    app_env: Literal["development", "test", "production"] = Field(
        default="development", alias="BLACKBOX_ENV"
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO", alias="BLACKBOX_LOG_LEVEL"
    )
    log_format: Literal["json", "console"] = Field(default="json", alias="BLACKBOX_LOG_FORMAT")

    database_url: str = Field(
        default=f"sqlite:///{BACKEND_DIR / 'data' / 'blackbox.db'}",
        alias="BLACKBOX_DATABASE_URL",
    )
    auto_migrate: bool = Field(default=True, alias="BLACKBOX_AUTO_MIGRATE")

    webhook_secret: str = Field(default="", alias="BLACKBOX_WEBHOOK_SECRET")

    cors_origins: list[str] = Field(
        default=["http://localhost:5180", "http://127.0.0.1:5180"],
        alias="BLACKBOX_CORS_ORIGINS",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
