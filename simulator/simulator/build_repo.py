"""Build the demo repository with a deterministic history.

C4 (deploy #841): healthy baseline, pool_size 50.
C5 (deploy #842): "chore(db): reduce pool to cut idle connections on staging" -> pool_size 10,
                  plus an unrelated log-level change (a realistic red herring).

Fixed authors and dates make the commit SHAs identical on every machine and every reset.
`main` points at C5 (the bad change is merged); what is *deployed* is tracked by the launcher.
"""

import json
import os
import shutil
import subprocess

from simulator.paths import DEMO_REPO, RUNTIME, SEED_DIR, SHAS
from simulator.scenarios import POOL_EXHAUSTION, SCENARIOS

SCENARIO_CATALOG = RUNTIME / "scenarios.json"

COMMITS = [
    {
        "deploy_id": "841",
        "date": "2026-11-05T15:00:00Z",
        "author": "Priya Nair <priya@payments.example>",
        "message": "feat: structured JSON logging and pool metrics",
        "edits": {},
    },
    {
        "deploy_id": "842",
        "date": "2026-11-06T10:26:00Z",
        "author": "ci-bot <ci-bot@payments.example>",
        "message": "chore(db): reduce pool to cut idle connections on staging",
        "edits": {
            "config/database.yml": [
                (
                    "# Sized for peak concurrency; see config/capacity.yml and RUNBOOK.md#capacity.",
                    "# Reduced to cut idle connections on staging.",
                ),
                ("pool_size: 50", "pool_size: 10"),
            ],
            "config/app.yml": [("log_level: INFO", "log_level: WARNING")],
        },
    },
]


def git(*args: str, env: dict | None = None) -> str:
    return subprocess.run(
        ["git", "-C", str(DEMO_REPO), *args],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    ).stdout.strip()


def _commit(edits: dict, author: str, date: str, message: str) -> None:
    for rel, replacements in edits.items():
        path = DEMO_REPO / rel
        content = path.read_text()
        for old, new in replacements:
            if old not in content:
                raise RuntimeError(f"seed drift: {old!r} not found in {rel}")
            content = content.replace(old, new)
        path.write_text(content)
    name, email = author.rstrip(">").split(" <")
    env = {
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_AUTHOR_DATE": date,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
        "GIT_COMMITTER_DATE": date,
    }
    git("add", "-A")
    git("commit", "-q", "-m", message, env=env)


def build() -> dict:
    if DEMO_REPO.exists():
        shutil.rmtree(DEMO_REPO)
    shutil.copytree(
        SEED_DIR, DEMO_REPO, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache")
    )
    git("init", "-q", "-b", "main")
    git("config", "user.name", "payments-team")
    git("config", "user.email", "payments@example")
    shas: dict[str, str] = {}
    for c in COMMITS:
        _commit(c["edits"], c["author"], c["date"], f"{c['message']}\n\nDeploy: #{c['deploy_id']}")
        shas[c["deploy_id"]] = git("rev-parse", "HEAD")
    # Scenario commits: each on its own branch from the healthy release C4 (#841), so `main`
    # and the pinned C4/C5 SHAs are unchanged. They become deploys only when triggered.
    catalog = []
    for sc in SCENARIOS:
        entry = {k: v for k, v in sc.items() if k != "commit"}
        if sc["id"] == POOL_EXHAUSTION:
            entry["sha"] = shas["842"]
        elif sc.get("commit"):
            c = sc["commit"]
            git("checkout", "-q", "-b", f"scenario/{sc['id']}", shas["841"])
            _commit(c["edits"], c["author"], c["date"], c["message"])
            entry["sha"] = git("rev-parse", "HEAD")
            git("checkout", "-q", "main")
        catalog.append(entry)
    shas["scenarios"] = {e["id"]: e.get("sha") for e in catalog}
    SCENARIO_CATALOG.parent.mkdir(parents=True, exist_ok=True)
    SCENARIO_CATALOG.write_text(
        json.dumps({"baseline": shas["841"], "scenarios": catalog}, indent=2)
    )
    SHAS.parent.mkdir(parents=True, exist_ok=True)
    SHAS.write_text(json.dumps(shas, indent=2))
    return shas


if __name__ == "__main__":
    print(json.dumps(build(), indent=2))
