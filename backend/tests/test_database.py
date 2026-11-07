import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.db.migrate import upgrade_to_head
from app.db.models import AuditEvent
from app.db.session import get_engine, get_sessionmaker


def test_startup_runs_migrations_and_records_event(client: TestClient) -> None:
    with get_sessionmaker()() as s:
        events = s.scalars(select(AuditEvent)).all()
    assert [e.kind for e in events] == ["system.started"]
    assert json.loads(events[0].payload_json)["env"] == "test"


def test_audit_events_are_append_only(client: TestClient) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text("INSERT INTO audit_events (kind, actor, payload_json) VALUES ('t', 'test', '{}')")
        )
    for statement in (
        "UPDATE audit_events SET kind = 'tampered'",
        "DELETE FROM audit_events",
    ):
        with (
            pytest.raises((IntegrityError, OperationalError), match="append-only"),
            get_engine().begin() as conn,
        ):
            conn.execute(text(statement))
    with get_engine().connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM audit_events")).scalar_one()
    assert count == 2


def test_startup_creates_missing_database_directory(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression: a fresh checkout has no backend/data/ directory; startup must create it.
    from fastapi.testclient import TestClient as _TestClient

    from app.core.config import get_settings
    from app.main import create_app

    nested = tmp_path / "does" / "not" / "exist" / "blackbox.db"
    monkeypatch.setenv("BLACKBOX_DATABASE_URL", f"sqlite:///{nested}")
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
    try:
        with _TestClient(create_app()) as c:
            assert c.get("/api/health").status_code == 200
        assert nested.exists()
    finally:
        get_engine().dispose()
        get_settings.cache_clear()
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()


def test_upgrade_is_idempotent(db_url: str) -> None:
    upgrade_to_head(db_url)
    upgrade_to_head(db_url)
    with get_engine().connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert version == "0002_incident_store"
