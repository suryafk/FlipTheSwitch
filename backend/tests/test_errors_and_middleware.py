from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import ApiError


def test_unknown_route_uses_error_envelope(client: TestClient) -> None:
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404
    err = r.json()["error"]
    assert err["code"] == "not_found"
    assert err["request_id"] == r.headers["X-Request-ID"]


def test_request_id_is_echoed_when_safe(client: TestClient) -> None:
    r = client.get("/api/health/live", headers={"X-Request-ID": "demo-123"})
    assert r.headers["X-Request-ID"] == "demo-123"


def test_unsafe_request_id_is_replaced(client: TestClient) -> None:
    r = client.get("/api/health/live", headers={"X-Request-ID": "bad id with spaces <script>"})
    assert r.headers["X-Request-ID"] != "bad id with spaces <script>"
    assert len(r.headers["X-Request-ID"]) == 32


def test_validation_and_api_and_unhandled_errors(app: FastAPI) -> None:
    @app.get("/api/_test/square")
    def square(n: int) -> dict[str, int]:
        return {"result": n * n}

    @app.get("/api/_test/conflict")
    def conflict() -> None:
        raise ApiError(409, "incident_locked", "Another incident is active.", {"active": "INC-1"})

    @app.get("/api/_test/boom")
    def boom() -> None:
        raise RuntimeError("secret internal detail")

    with TestClient(app, raise_server_exceptions=False) as c:
        r = c.get("/api/_test/square", params={"n": "not-a-number"})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "validation_error"
        assert r.json()["error"]["details"][0]["loc"] == ["query", "n"]

        r = c.get("/api/_test/conflict")
        assert r.status_code == 409
        assert r.json()["error"] == {
            "code": "incident_locked",
            "message": "Another incident is active.",
            "request_id": r.headers["X-Request-ID"],
            "details": {"active": "INC-1"},
        }

        r = c.get("/api/_test/boom")
        assert r.status_code == 500
        assert r.json()["error"]["code"] == "internal_error"
        assert "secret internal detail" not in r.text
