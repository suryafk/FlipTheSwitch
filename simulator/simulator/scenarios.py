"""Fault scenarios for the demo: what breaks, how, and what BLACKBOX's agents should do.

Deploy scenarios are real commits branched from the healthy release C4 (#841), deployed through
the launcher exactly like any release. The load scenario changes only the traffic (no deploy).
Everything the agents see (metrics, logs, the git diff) is produced by the running service; the
card processor and the customers are the simulated parts (as everywhere in this simulator).
"""

from __future__ import annotations

# The original pool fault, deploy #842 on `main` (built by build_repo.COMMITS).
POOL_EXHAUSTION = "pool-exhaustion"

SCENARIOS: list[dict] = [
    {
        "id": POOL_EXHAUSTION,
        "title": "Database pool exhaustion",
        "kind": "deploy",
        "category": "Config regression",
        "summary": "A routine-looking deploy cuts the DB connection pool from 50 to 10.",
        "breaks": "Requests queue for a connection and time out: 5xx ~20%, p50 ~4 s, pool 100%.",
        "expected": "fix",
        "agents": "Sentinel correlates the deploy; RootCause finds pool_size in "
        "config/database.yml below the capacity requirement; Claude Code restores capacity "
        "and adds a regression test; you approve; the executor deploys and verifies.",
        # The commit is C5 from build_repo.COMMITS, not defined here.
    },
    {
        "id": "pool-pgbouncer",
        "title": "Pool matched to the wrong limit",
        "kind": "deploy",
        "category": "Config regression",
        "summary": "A deploy sizes the pool to a pgbouncer limit of 12 and cuts the "
        "acquire timeout from 4 s to 1 s.",
        "breaks": "Peak concurrency (~12-30 sessions) exceeds the pool; requests fail fast "
        "with DB_POOL_TIMEOUT.",
        "expected": "fix",
        "agents": "Same diagnosis as pool exhaustion but different keys and numbers: "
        "RootCause reads the real diff and capacity file, not a canned answer.",
        "commit": {
            "date": "2026-11-06T09:12:00Z",
            "author": "Marco Silva <marco@payments.example>",
            "message": "chore(db): match pool to the pgbouncer per-service limit",
            "edits": {
                "config/database.yml": [
                    (
                        "# Sized for peak concurrency; see config/capacity.yml and "
                        "RUNBOOK.md#capacity.",
                        "# Matched to the pgbouncer per-service limit (12); fail fast on acquire.",
                    ),
                    ("pool_size: 50", "pool_size: 12"),
                    ("pool_timeout_seconds: 4", "pool_timeout_seconds: 1"),
                ],
            },
        },
    },
    {
        "id": "processor-timeout",
        "title": "Card processor timeout regression",
        "kind": "deploy",
        "category": "Code regression",
        "summary": "A code deploy adds a 40 ms timeout to card-processor calls to 'fail fast'.",
        "breaks": "About one payment in four now fails with 502 PROCESSOR_UNAVAILABLE "
        "(5xx ~25%); latency and the pool stay normal.",
        "expected": "fix",
        "agents": "A different failure class: the evidence is a log signature, not the pool. "
        "RootCause localizes the code change in src/payment_service/main.py from the deploy "
        "diff and Claude Code fixes the code and adds a regression test.",
        "commit": {
            "date": "2026-11-06T11:40:00Z",
            "author": "Lena Fischer <lena@payments.example>",
            "message": "perf(processor): fail fast on slow card-processor calls",
            "edits": {
                "src/payment_service/main.py": [
                    (
                        'SCORING_SECONDS = float(os.environ.get("SIM_SCORING_SECONDS", "0.1"))',
                        'SCORING_SECONDS = float(os.environ.get("SIM_SCORING_SECONDS", "0.1"))\n'
                        "\n"
                        "# Card-processor client timeout. The (simulated) processor answers 3 in 4\n"
                        "# calls within 40 ms and all but 1 in 500 within its 250 ms SLA.\n"
                        "PROCESSOR_TIMEOUT_MS = 40\n"
                        "\n"
                        "\n"
                        "def processor_times_out(n: int) -> bool:\n"
                        "    every = 4 if PROCESSOR_TIMEOUT_MS < 250 else 500\n"
                        "    return n % every == 0",
                    ),
                    (
                        "elif n % 500 == 0:  # the (simulated) card processor's periodic timeout",
                        "elif processor_times_out(n):  # processor call exceeded the timeout",
                    ),
                ],
            },
        },
    },
    {
        "id": "traffic-surge",
        "title": "Traffic surge (no bad deploy)",
        "kind": "load",
        "category": "Capacity / load",
        "summary": "Customer traffic jumps about 5x (120 -> 600 requests/s). Nothing is deployed.",
        "breaks": "Demand exceeds the healthy pool: pool saturates, latency rises, 5xx climbs.",
        "expected": "escalate",
        "agents": "Sentinel investigates, but no deploy or change explains the fault. "
        "RootCause refuses to invent a patch and escalates to the incident commander "
        "(NEEDS_HUMAN). End the surge here to recover.",
        "multiplier": 5.0,
    },
]


def by_id(scenario_id: str) -> dict | None:
    return next((s for s in SCENARIOS if s["id"] == scenario_id), None)
