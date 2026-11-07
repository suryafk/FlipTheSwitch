import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

import app.api.health as health_module


def test_live(client: TestClient) -> None:
    r = client.get("/api/health/live")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_health_ok_with_real_checks(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "blackbox-backend"
    assert body["environment"] == "test"
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["migrations"] == {"status": "ok", "detail": "at 0003_slack"}


def test_health_degraded_when_database_unreachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = create_engine("sqlite:////nonexistent-dir/blackbox/unreachable.db")
    monkeypatch.setattr(health_module, "get_engine", lambda: broken)
    r = client.get("/api/health")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "degraded"
    assert body["checks"]["database"] == {"status": "error", "detail": "database unreachable"}
    assert "migrations" not in body["checks"]
