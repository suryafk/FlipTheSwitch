from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.session import get_engine, get_sessionmaker
from app.slack.config import get_slack_settings


def _clear_caches() -> None:
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    get_slack_settings.cache_clear()


@pytest.fixture
def db_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """Point the app at a fresh SQLite file for each test."""
    url = f"sqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("BLACKBOX_DATABASE_URL", url)
    monkeypatch.setenv("BLACKBOX_ENV", "test")
    monkeypatch.setenv("BLACKBOX_LOG_FORMAT", "console")
    monkeypatch.setenv("BLACKBOX_AUTO_MIGRATE", "true")
    monkeypatch.setenv("SLACK_BRIDGE_IN_API", "false")
    for var in (
        "SLACK_CLIENT_ID",
        "SLACK_CLIENT_SECRET",
        "SLACK_SIGNING_SECRET",
        "SLACK_REDIRECT_URI",
        "SLACK_ALLOWED_TEAM_IDS",
        "SLACK_INCIDENT_CHANNEL_ID",
        "SLACK_COMMAND_CHANNEL_IDS",
        "BLACKBOX_SECRET_KEY",
        "BLACKBOX_ADMIN_TOKEN",
    ):
        monkeypatch.setenv(var, "")
    _clear_caches()
    yield url
    get_engine().dispose()
    _clear_caches()


@pytest.fixture
def app(db_url: str) -> FastAPI:
    from app.main import create_app

    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # Entering the context runs the lifespan: migrations, then the startup audit event.
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
