"""Read-only evidence tools over the running simulation (no tool fabricates data).

Sources: the service's /metrics/summary, its JSON log file, the launcher's deployments.jsonl,
and the demo repository's git history.
"""

from __future__ import annotations

import collections
import json
import subprocess
from pathlib import Path
from typing import Any

import httpx

from app.agents.common import get_agent_settings


def metrics_summary() -> dict[str, Any]:
    cfg = get_agent_settings()
    return httpx.get(f"{cfg.service_url}/metrics/summary", timeout=5).json()


def recent_log_signatures(max_lines: int = 5000) -> dict[str, Any]:
    """Group recent ERROR log lines by (event, message) and count them."""
    path = get_agent_settings().runtime / "logs" / "app.jsonl"
    if not path.exists():
        return {"available": False, "signatures": []}
    lines = path.read_text(errors="ignore").splitlines()[-max_lines:]
    counts: collections.Counter[tuple[str, str]] = collections.Counter()
    first_seen: dict[tuple[str, str], str] = {}
    deploys: collections.Counter[str] = collections.Counter()
    for line in lines:
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("level") != "ERROR":
            continue
        key = (rec.get("event", ""), rec.get("message", rec.get("error", "")))
        counts[key] += 1
        first_seen.setdefault(key, rec.get("ts", ""))
        deploys[str(rec.get("deploy_id"))] += 1
    return {
        "available": True,
        "lines_scanned": len(lines),
        "signatures": [
            {"event": e, "message": m, "count": c, "first_seen": first_seen[(e, m)]}
            for (e, m), c in counts.most_common(5)
        ],
        "errors_by_deploy": dict(deploys),
    }


def deployments() -> list[dict[str, Any]]:
    path = get_agent_settings().runtime / "deployments.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout


def deploy_diff(base_sha: str, head_sha: str) -> dict[str, Any]:
    repo = get_agent_settings().demo_repo
    return {
        "base": base_sha,
        "head": head_sha,
        "files": git(repo, "diff", "--name-only", base_sha, head_sha).split(),
        "stat": git(repo, "diff", "--shortstat", base_sha, head_sha).strip(),
        "patch": git(repo, "diff", "--no-color", base_sha, head_sha),
        "message": git(repo, "log", "-1", "--format=%s%n%n%b", head_sha).strip(),
        "author": git(repo, "log", "-1", "--format=%an", head_sha).strip(),
    }
