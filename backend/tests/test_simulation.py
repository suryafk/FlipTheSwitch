"""Simulation API guards and the general (code-change) root-cause localization."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import simulation
from app.incidents import store

BASE = "a" * 40


@pytest.fixture
def sim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "scenarios.json").write_text(
        json.dumps(
            {
                "baseline": BASE,
                "scenarios": [
                    {
                        "id": "processor-timeout",
                        "title": "P",
                        "kind": "deploy",
                        "sha": "b" * 40,
                        "category": "Code",
                        "summary": "s",
                        "breaks": "b",
                        "expected": "fix",
                        "agents": "a",
                    },
                    {
                        "id": "traffic-surge",
                        "title": "T",
                        "kind": "load",
                        "multiplier": 5.0,
                        "category": "Load",
                        "summary": "s",
                        "breaks": "b",
                        "expected": "escalate",
                        "agents": "a",
                    },
                ],
            }
        )
    )
    (tmp_path / "deployments.jsonl").write_text(json.dumps({"deploy_id": "841"}) + "\n")
    monkeypatch.setattr(simulation, "_runtime", lambda: tmp_path)
    monkeypatch.setattr(simulation, "_metrics", lambda: {"git_sha": BASE, "error_rate_pct": 0.2})
    return tmp_path


def test_lists_scenarios_and_is_ready_on_healthy_baseline(sim: Path, client: TestClient) -> None:
    d = client.get("/api/simulation").json()
    assert d["blocked_reason"] is None and d["deployed"]["on_baseline"] is True
    assert [s["id"] for s in d["scenarios"]] == ["processor-timeout", "traffic-surge"]
    assert d["scenarios"][0]["commit"] == "b" * 12


def test_surge_trigger_writes_load_control_and_stop_clears_it(
    sim: Path, client: TestClient
) -> None:
    r = client.post("/api/simulation/scenarios/traffic-surge/trigger")
    assert r.status_code == 200 and r.json()["multiplier"] == 5.0
    assert json.loads((sim / "load-control.json").read_text())["multiplier"] == 5.0
    # A surge blocks further triggers until it is ended.
    assert client.post("/api/simulation/scenarios/traffic-surge/trigger").status_code == 409
    assert client.post("/api/simulation/surge/stop").json() == {"surge": False, "was_running": True}
    assert not (sim / "load-control.json").exists()


def test_refuses_while_an_incident_is_open(sim: Path, client: TestClient) -> None:
    store.create_incident("payment-service", "t", "fp", {}, "sentinel")
    r = client.post("/api/simulation/scenarios/traffic-surge/trigger")
    assert r.status_code == 409 and "still open" in r.json()["error"]["message"]


def test_refuses_off_baseline_or_unhealthy(
    sim: Path, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(simulation, "_metrics", lambda: {"git_sha": "c" * 40, "error_rate_pct": 0})
    assert client.post("/api/simulation/scenarios/traffic-surge/trigger").status_code == 409
    monkeypatch.setattr(simulation, "_metrics", lambda: {"git_sha": BASE, "error_rate_pct": 20})
    assert client.post("/api/simulation/scenarios/traffic-surge/trigger").status_code == 409
    monkeypatch.setattr(simulation, "_metrics", lambda: None)
    assert "not running" in client.get("/api/simulation").json()["blocked_reason"]


def test_unknown_scenario_and_production_guard(
    sim: Path, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert client.post("/api/simulation/scenarios/nope/trigger").status_code == 404
    from app.core.config import get_settings

    monkeypatch.setenv("BLACKBOX_ENV", "production")
    get_settings.cache_clear()
    assert client.post("/api/simulation/scenarios/traffic-surge/trigger").status_code == 403
    get_settings.cache_clear()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def test_localize_change_names_files_and_constants(tmp_path: Path) -> None:
    from app.agents.rootcause import localize

    repo = tmp_path / "repo"
    (repo / "config").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "config" / "database.yml").write_text("pool_size: 50\nmax_overflow: 0\n")
    (repo / "config" / "capacity.yml").write_text("peak_concurrent_db_sessions: 30\n")
    (repo / "config" / "app.yml").write_text("log_level: INFO\n")
    (repo / "src" / "main.py").write_text("X = 1\n")
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    (repo / "src" / "main.py").write_text("X = 1\nTIMEOUT_MS = 40\n")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "bad")
    head = _git(repo, "rev-parse", "HEAD")

    rc = localize(repo, base, head)
    assert rc["found"] and rc["kind"] == "code_change"
    assert rc["files"] == ["src/main.py"]
    assert "TIMEOUT_MS: (new) -> 40" in rc["statement"]
    # No change at all -> nothing to localize (the surge case never gets here, but be safe).
    assert localize(repo, head, head)["found"] is False
