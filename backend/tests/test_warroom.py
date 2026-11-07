"""War-room API: everything comes from the store and audit log; read-only; text is sanitized."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.incidents import store


def test_warroom_collects_incident_state(client: TestClient) -> None:
    inc = store.create_incident("payment-service", "5xx <spike>", "fp", {}, "sentinel")
    iid = inc["incident_id"]
    store.transition(iid, "INVESTIGATING", "sentinel", "started")
    store.audit(
        "evidence.collected",
        "sentinel",
        {
            "incident_id": iid,
            "evidence": [
                {"kind": "metric", "label": "5xx", "value": "token=abc123", "simulated": False}
            ],
            "analysis": {"hypotheses": [{"statement": "pool cut", "confidence": 0.9}]},
        },
    )
    store.transition(iid, "ROOT_CAUSE_IDENTIFIED", "rootcause", "pool_size 50 -> 10")
    store.audit(
        "task.preventive",
        "recovery",
        {"incident_id": iid, "slug": "t", "text": "keep a < b", "owner": "o"},
    )

    d = client.get(f"/api/incidents/{iid}/warroom").json()
    assert d["incident"]["incident_id"] == iid
    assert d["incident"]["title"] == "5xx <spike>"  # unescaped for React, which escapes itself
    assert [s["status"] for s in d["stages"]][:3] == ["done", "done", "current"]
    assert d["root_cause"] == "pool_size 50 -> 10"
    assert d["evidence"][0]["value"] == "[redacted]"  # secrets never reach the UI
    assert d["hypotheses"][0]["confidence"] == 0.9
    assert d["preventive_tasks"][0]["text"] == "keep a < b"
    assert d["remediation"] is None and d["approval"] is None and d["execution"] is None
    assert any(t["summary"] == "Investigation started" for t in d["timeline"])


@pytest.mark.parametrize("bad", ["INC-9999", "nope", "INC-1;drop"])
def test_warroom_unknown_or_malformed_ids(client: TestClient, bad: str) -> None:
    assert client.get(f"/api/incidents/{bad}/warroom").status_code == 404


def test_live_metrics_reports_unreachable_service(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.agents.common import get_agent_settings

    monkeypatch.setenv("BLACKBOX_SIM_SERVICE_URL", "http://127.0.0.1:9")
    get_agent_settings.cache_clear()
    r = client.get("/api/metrics/live")
    get_agent_settings.cache_clear()
    assert r.status_code == 503 and r.json()["error"]["code"] == "service_unreachable"
