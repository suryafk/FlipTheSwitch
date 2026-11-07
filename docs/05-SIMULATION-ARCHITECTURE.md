# 05 — Incident Simulation Architecture

| | |
|---|---|
| **Status** | Design. No application code. |
| **Purpose** | A small, deterministic, production-inspired system whose outage is **caused by a real configuration change** and whose metrics are **measured, not scripted** |
| **Companions** | [`05-DEMO-SCENARIO.md`](05-DEMO-SCENARIO.md) · [`05-TEST-STRATEGY.md`](05-TEST-STRATEGY.md) · [`05-DEMO-ACCEPTANCE-CRITERIA.md`](05-DEMO-ACCEPTANCE-CRITERIA.md) |
| **Constraints inherited** | The executor deploys by exact commit SHA, and `/health` exposes `git_sha` (`04-SECURITY-ARCHITECTURE.md` §9). The coding agent has no shell; BLACKBOX runs tests in a `network=none` container (§5 there). The path allowlist is `config/**`, `src/**`, `tests/**`. |

---

## 1. Design principle: the outage is physics, not theatre

The failure must come from a real mechanism that anyone can inspect:

- **Mechanism.** Each payment request holds one database connection for about 100 ms (a real Postgres query). The service's SQLAlchemy pool size comes from `config/database.yml`.
- **Load.** A load generator sends **open-loop** traffic at a fixed **120 requests/second**. Open-loop means arrivals don't slow down when the service slows down, as in real traffic.
- **Capacity** is `pool_size / hold_time`:

| State | `pool_size` | Capacity | Utilization at 120 rps | Expected outcome |
|---|---|---|---|---|
| **HEALTHY** (deploy #841) | 50 | 500 rps | ≈ 24% (12 connections in use) | No queueing. p50 ≈ 110 ms. 5xx comes only from the simulated processor's baseline (≈ 0.2%). |
| **DEGRADED** (deploy #842) | 10 | 100 rps | 100% (10/10 checked out) | Excess demand of ≈ 20 rps queues and then fails at `pool_timeout = 4 s` with HTTP 503. **5xx ≈ (120 − 100)/120 ≈ 16.7%**, plus the baseline ≈ 17%. Served requests wait in the queue, so **p50 ≈ 3–4 s**. |

These are the scenario's target numbers (18%, 4.2 s, 94%). They are **derived from the mechanism and measured at runtime**; nothing is hardcoded. Calibration tests (`05-TEST-STRATEGY.md` §3) pin them into acceptance ranges.

---

## 2. Components

```mermaid
flowchart LR
  subgraph SIM["Simulation (docker compose profile: sim)"]
    LG[loadgen<br/>open-loop 120 rps<br/>seeded payloads]
    subgraph PSC["payment-service container (sandbox target)"]
      LAU[launcher<br/>control port :9001 (token)]
      APP[uvicorn payment_service<br/>:8080]
      LAU -- spawns/restarts --> APP
    end
    PDB[(payments-db<br/>Postgres 16)]
    MON[monitor<br/>scrapes /metrics/summary every 5s<br/>alert rules]
    REL[(volume: releases/<sha>)]
    LOGV[(volume: logs/app.jsonl)]
    DEPV[(volume: deployments.jsonl)]
    REPO[(volume: demo repo<br/>payment-service.git)]
  end
  LG -->|HTTP| APP
  APP -->|asyncpg pool| PDB
  APP --> LOGV
  MON -->|GET /metrics/summary| APP
  MON -->|HMAC webhook blackbox.alert/v1| API[blackbox-api]
  API -->|read-only| LOGV & DEPV & REPO
  API -->|scrape on demand| APP
  EX[blackbox-executor] -->|git archive sha| REL
  EX -->|POST /deploy {sha} + token| LAU
  LAU -->|reads| REL
  LAU --> DEPV
  RC[RootCause worktrees] -->|clone from| REPO
```

| Component | Tech | Responsibility | Notes |
|---|---|---|---|
| `payment-service` | Python 3.12, FastAPI, SQLAlchemy 2 async engine plus asyncpg | The payment API; the system under test. **Its source is the demo repository.** | One uvicorn worker, so pool behavior is exact |
| `launcher` | A small Python supervisor; part of the simulation, **not** the demo repo | The sandbox deployment runtime. It owns the `current` release symlink and the app process lifecycle, and appends deployment metadata. | Control port 9001 is internal-only and token-authenticated. Only the executor (and the reset script) hold the token. |
| `payments-db` | Postgres 16 | The real database | `max_connections=100` |
| `loadgen` | Python asyncio and httpx, open-loop scheduler | Fixed-rate synthetic customer traffic | Seeded, deterministic payloads; reports its own client-side stats |
| `monitor` | Python | Scrapes service metrics, evaluates alert rules, sends signed webhooks | A stand-in for Prometheus plus Alertmanager. The alert is real (rule on measured data); the *monitoring vendor* is simulated. |
| Demo repo | A bare git repo `payment-service.git` on a volume | Source of truth for code, config and history | Seeded with **fixed commit dates and authors, so commit SHAs are deterministic** (§9) |
| `blackbox-api` observability tools | Part of BLACKBOX | Read logs, deployments, metrics and git for the agents | Read-only mounts |

---

## 3. Repository structures

### 3.1 BLACKBOX project (this repo)

```
FlipTheSwitch/
├── docs/                         # 00–05 design docs
├── blackbox/                     # (later) api, agents, executor, ui — not designed here
├── sim/
│   ├── payment-service-seed/     # files used to build the demo repo history
│   │   ├── commits/              # one directory per commit: the tree delta + commit.yml (message, author, date, deploy_id)
│   │   └── build_repo.py         # materializes payment-service.git deterministically
│   ├── launcher/                 # sandbox runtime (supervisor + control API)
│   ├── loadgen/                  # open-loop generator + profile.yml
│   ├── monitor/                  # scraper + rules.yml + webhook signer
│   ├── fallback/
│   │   └── REM-842.patch         # deterministic fallback remediation (SIMULATED label)
│   ├── seed/
│   │   └── deployments.seed.jsonl# deploys #838–#841 metadata
│   └── reset/                    # reset orchestration (see §10)
└── deploy/
    └── docker-compose.yml        # profiles: switch (external), blackbox, sim
```

### 3.2 Demo repository `payment-service.git` (what the agents inspect)

```
payment-service/
├── README.md
├── RUNBOOK.md                    # on-call notes; states the capacity SLO in prose
├── pyproject.toml
├── config/
│   ├── app.yml                   # service name, port, log level
│   ├── database.yml              # ← pool_size, max_overflow, pool_timeout, statement_timeout
│   └── capacity.yml              # peak_concurrent_db_sessions: 30 (documented capacity requirement)
├── src/payment_service/
│   ├── main.py                   # app factory, lifespan (engine create/dispose)
│   ├── config.py                 # loads YAML → typed settings (pydantic)
│   ├── db.py                     # builds the async engine from database.yml
│   ├── api.py                    # /payments routes
│   ├── processor.py              # simulated card processor (deterministic)
│   ├── health.py                 # /health, /health/live
│   ├── metrics.py                # Prometheus registry + rolling window → /metrics, /metrics/summary
│   └── logging.py                # JSON structured logging
└── tests/
    ├── conftest.py               # app fixture with an in-process fake DB session (no network)
    ├── test_payments.py          # 10 tests: validation, declines, idempotency, amounts
    ├── test_api.py               # 6 tests: status codes, error mapping (pool timeout → 503)
    ├── test_config.py            # 5 tests: YAML parsing, types, defaults
    ├── test_health.py            # 3 tests: status rules
    └── test_metrics.py           # 2 tests: counters and window maths
                                  # = 26 tests, all green on #841 AND on #842 (that is why #842 shipped)
```

**Deliberate realism:** the existing suite does **not** check that `pool_size` meets `capacity.yml`. That gap is the organizational lesson, and it becomes the preventive task.

---

## 4. Payment API behavior

| Endpoint | Behavior |
|---|---|
| `POST /payments` | Body: `{amount_cents:int>0, currency:"USD"\|"EUR", card_token:str, idempotency_key:str}`. Steps: validate → **acquire a DB connection** (pool; `pool_timeout` from config) → in one transaction run the fraud-score query `SELECT pg_sleep(0.1)` (this models the latency of a real scoring query; labelled `simulated_db_work` in code), insert the payment row (unique `idempotency_key`) → call the simulated processor → commit. |
| | Responses: **201** `{payment_id, status:"captured"}`; **402** declined (deterministic: card tokens ending `0002`); **409** duplicate idempotency key; **422** validation; **503** `{"error":"DB_POOL_TIMEOUT"}` when SQLAlchemy raises `TimeoutError` acquiring a connection; **502** `{"error":"PROCESSOR_UNAVAILABLE"}` from the processor's deterministic flake (every 500th call, which gives a ≈ 0.2% healthy baseline; labelled simulated). |
| `GET /payments/{id}` | Reads one row. It uses a pool connection too, so it also degrades. |
| Request accounting | Every response is recorded in the metrics window with route, status and duration. Every 5xx is logged with its exception class and message. |

Only `/payments*` counts toward the SLO metrics. `/health` and `/metrics` don't.

---

## 5. Database connection configuration

`config/database.yml` at deploy #841 (HEALTHY):

```yaml
# Connection pool for the payments database.
# Sized for peak concurrency; see config/capacity.yml and RUNBOOK.md#capacity.
dsn_env: PAYMENTS_DB_DSN
pool_size: 50
max_overflow: 0
pool_timeout_seconds: 4
pool_recycle_seconds: 1800
statement_timeout_ms: 2000
```

Deploy #842 changes **one value**: `pool_size: 50` → `pool_size: 10`. It also edits the comment to "Reduced to cut idle connections on staging". The commit also touches `config/app.yml` (`log_level: INFO` → `log_level: WARNING`), a plausible companion change that is a **red herring** for the investigation.

`db.py` builds `create_async_engine(dsn, pool_size=…, max_overflow=…, pool_timeout=…, pool_recycle=…)`. It publishes pool stats (`pool.size()`, `pool.checkedout()`, the overflow and a wait-time histogram, measured around `connect()`) into the metrics module.

---

## 6. Health-check endpoint

`GET /health` returns status **200 even when degraded** (so the monitor can read it). Shape:

```json
{
  "status": "healthy | degraded | unhealthy",
  "git_sha": "<deployed commit>",
  "deploy_id": "842",
  "started_at": "…",
  "db": {"reachable": true, "pool_size": 10, "checked_out": 10, "utilization_pct": 100.0},
  "window": {"seconds": 15, "requests": 1790, "error_rate_pct": 17.2, "latency_ms_p50": 3920, "latency_ms_p95": 4010},
  "reasons": ["error_rate_pct>2", "latency_ms_p50>1000", "pool_utilization_pct>90"]
}
```

| Status | Rule (evaluated over the rolling 15 s window) |
|---|---|
| `unhealthy` | The DB is unreachable, or the app is not ready |
| `degraded` | Any of: `error_rate_pct > 2`, `latency_ms_p50 > 1000`, `pool_utilization_pct > 90` |
| `healthy` | Otherwise |

`GET /health/live` returns 200 when the process is up. The launcher uses it for readiness.

The rule thresholds live in **the simulation's config**, not the demo repo, so a patch cannot "fix" the incident by loosening the health rules. Health rules are not in the path allowlist either.

---

## 7. Metrics endpoint

`GET /metrics` uses the Prometheus text format:

| Metric | Type | Labels |
|---|---|---|
| `http_requests_total` | counter | `route`, `status` |
| `http_request_duration_seconds` | histogram | `route` |
| `db_pool_size` | gauge | — |
| `db_pool_checked_out` | gauge | — |
| `db_pool_wait_seconds` | histogram | — |
| `db_pool_timeouts_total` | counter | — |
| `payments_processed_total` | counter | `outcome` |
| `build_info` | gauge = 1 | `git_sha`, `deploy_id`, `version` |

`GET /metrics/summary` returns JSON over a rolling 15 s window, computed from per-request records kept in a ring buffer:

```json
{"window_seconds":15,"requests":1802,"rps":120.1,"error_rate_pct":0.22,"latency_ms_p50":108,
 "latency_ms_p95":131,"pool_utilization_pct":24.0,"pool_timeouts":0,"git_sha":"…","deploy_id":"841","sampled_at":"…"}
```

This is the single source for the dashboard's live charts, Sentinel's `get_service_metrics`, the monitor and post-remediation verification. **Before/after numbers in the report are copies of these samples, with their `sampled_at` timestamps.**

---

## 8. Logs and deployment metadata

### 8.1 Application logs

These are real logs from the service, not generated text. JSON lines go to stdout and to `/logs/app.jsonl`:

```json
{"ts":"…","level":"ERROR","logger":"payment_service.api","event":"payment_failed","request_id":"…",
 "route":"POST /payments","status":503,"error":"DB_POOL_TIMEOUT",
 "exception":"sqlalchemy.exc.TimeoutError","message":"QueuePool limit of size 10 overflow 0 reached, connection timed out, timeout 4.00","git_sha":"…","deploy_id":"842"}
```

- The exception text is SQLAlchemy's own message, which contains **"size 10"**. That is primary evidence.
- Other real log events: `payment_captured` (sampled 1 in 100 at INFO), `payment_declined`, `processor_unavailable`, `startup` (pool settings), `shutdown`.
- After #842 sets `log_level: WARNING`, routine INFO lines disappear. That is part of the red herring, and visible in the logs.
- **"Synthetic" applies only to the traffic.** Customers are simulated by `loadgen`; the logs are what the service really emits.

### 8.2 Deployment metadata

The launcher appends to `/deploy/deployments.jsonl`. These are the records Sentinel's `get_recent_deployments` reads:

```json
{"deploy_id":"842","git_sha":"<c5>","parent_sha":"<c4>","service":"payment-service","environment":"staging-sandbox",
 "started_at":"…","finished_at":"…","status":"succeeded","actor":"ci-bot (simulated)",
 "commit_message":"chore(db): reduce pool to cut idle connections on staging","changed_files":["config/app.yml","config/database.yml"],
 "diff_stat":{"added":2,"removed":2},"source":"fault-injection"}
```

- Deploys **#838–#841** are seed records pointing at real historical commits C1–C4.
- **#842** is written by the launcher when the fault is injected.
- Remediation deploys get ids `843+`, with `actor:"blackbox-executor"` and `approval_id`.

---

## 9. Deterministic demo-repo history

`sim/payment-service-seed/build_repo.py` creates `payment-service.git` from `commits/` with fixed `GIT_AUTHOR_DATE`, `GIT_COMMITTER_DATE`, author names and messages. **The commit SHAs are therefore identical on every machine and every reset.**

| Commit | Deploy | Message | State |
|---|---|---|---|
| C1 | #838 | `feat: payment capture API` | healthy |
| C2 | #839 | `feat: idempotency keys` | healthy |
| C3 | #840 | `feat: prometheus metrics and health` | healthy |
| C4 | #841 | `feat: structured JSON logging` | **HEALTHY baseline (deployed at reset)** |
| C5 | #842 | `chore(db): reduce pool to cut idle connections on staging` | **DEGRADED (deployed by fault injection)** |

- Branch `main` points to C5, so the bad change is merged, as in real life.
- The tag `deployed` is maintained by the launcher.
- RootCause's worktree is created from the **currently deployed SHA** (C5), which is also the `base_commit` the executor requires (`04-APPROVAL-SECURITY.md` §5, step 3).

---

## 10. Fault injection

The **`TRIGGER PAYMENT INCIDENT`** button (UI → `blackbox-api` → simulation controller):

1. Precondition: the service is HEALTHY (3 consecutive healthy samples) and the deployed SHA is C4. Otherwise the button is disabled, with the reason shown.
2. Calls the launcher `POST /deploy {sha: C5, deploy_id: "842", source: "fault-injection"}`. This is **the same deployment path** the executor uses. The fault is a genuine deployment of a genuine commit.
3. The launcher extracts C5 into `releases/C5/`, flips `current`, restarts the app, waits for `/health/live`, and appends deployment #842.
4. Nothing else happens. **No alert is sent by the button.** Within about 10–20 s the monitor sees the real degradation and fires the alert (§11).

The button is clearly labelled in the UI: *"Simulates a teammate's faulty deployment (deploy #842)."*

---

## 11. Monitoring alert generation

`sim/monitor/rules.yml`:

```yaml
scrape_interval_seconds: 5
rules:
  - name: PaymentServiceHigh5xx
    expr: error_rate_pct > 5
    for_samples: 2            # ≈10 s
    severity: critical
  - name: PaymentServiceHighLatency
    expr: latency_ms_p50 > 1000
    for_samples: 2
    severity: high
  - name: PaymentDBPoolSaturated
    expr: pool_utilization_pct > 90
    for_samples: 2
    severity: high
grouping: by service        # one alert per service carries all firing signals
```

When the rules fire, the monitor:

1. Builds `blackbox.alert/v1` (`03-HANDOFF-SCHEMAS.md` §2.1) with the **measured** signals, `observed_at` = the first breaching sample, and `fingerprint = sha256("payment-service|PaymentServiceHigh5xx")`. It sets `source:"synthetic-monitor"` and `simulated:false`: the measurement is real, and the vendor is a stand-in.
2. Signs it with HMAC (`04-SECURITY-ARCHITECTURE.md` §6) and POSTs it to `blackbox-api /webhooks/alert`.
3. Re-fires only on state change (firing → resolved), so there is no alert storm.

When the incident recovers, the monitor sends a `resolved` notification, which goes into the incident's event log as evidence.

---

## 12. Root-cause discovery path (the evidence an honest engineer would follow)

| Step | Who | Evidence source (real) | Finding |
|---|---|---|---|
| 1 | Sentinel | Alert: `error_rate_pct=17.x`, `latency_ms_p50≈3900`, pool 100% | Customer-facing failure; SEV-1 |
| 2 | Sentinel | `deployments.jsonl` | #842 finished about 15–30 s before the first breaching sample |
| 3 | Sentinel | `/logs/app.jsonl` signatures | `sqlalchemy.exc.TimeoutError … QueuePool limit of size 10` × N |
| 4 | Sentinel | `get_deployment_diff(841→842)` summary | 2 files changed: `config/app.yml`, `config/database.yml` |
| 5 | RootCause | `get_commit_diff(C4..C5)` | `pool_size: 50 → 10`; `log_level INFO → WARNING` |
| 6 | RootCause | `read_source_file(src/payment_service/db.py)` | The pool is built from `database.yml` |
| 7 | RootCause | `read_source_file(config/capacity.yml)`, `RUNBOOK.md` | Peak concurrency requirement is 30. A pool of 10 is below it, so the change violates the documented capacity. |
| 8 | RootCause | Reasoning check | The log-level change explains missing INFO lines but not 5xx. **Discard it** (the red herring). |

The coding agent is **not told the answer**. Its brief contains RootCause's localized hypothesis, which RootCause derived from the evidence above. The coding agent must open the files itself to change them.

---

## 13. Remediation patch (expected shape; not prescribed)

A correct remediation includes:

1. `config/database.yml`: `pool_size` restored to ≥ 30. 50 is expected, as in the previous known-good configuration.
2. **A new regression test**, for example `tests/test_capacity.py::test_pool_capacity_meets_peak_concurrency`. It loads `database.yml` and `capacity.yml`, and asserts `pool_size + max_overflow ≥ peak_concurrent_db_sessions`. It **fails on C5 and passes on the fix**.
3. *(Optional, desirable)* Startup validation in `src/payment_service/config.py` that refuses to start when the capacity is below the requirement, plus its test.

Expected result: 26 existing tests plus ≥ 1 new test → **27/27 or more passing**. This is what the scenario's "27/27" reflects, and the real count is whatever the runner reports.

**Deterministic fallback.** `sim/fallback/REM-842.patch` is applied with `git apply` only when the coding agent fails twice (`03-AGENT-CONTRACTS.md` B.12). It contains the minimal fix plus the regression test. Every message and the report label it `[SIMULATED coding agent: deterministic fallback]`.

---

## 14. Sandbox deployment mechanism

The launcher is the sandbox runtime. The executor is its only client during incidents.

| Step | Detail |
|---|---|
| 1. Stage | The executor runs `git archive <sha>` from its **frozen** repo copy into `releases/<sha>/` on a shared volume (the executor writes; the launcher reads) |
| 2. Request | `POST launcher:9001/deploy {sha, deploy_id, approval_id}` with `Authorization: Bearer <deploy token>`. The launcher verifies that `releases/<sha>/` exists and that its tree hash matches `git rev-parse <sha>^{tree}`, which is recorded in a `RELEASE` file the executor writes. |
| 3. Switch | Record `previous = readlink(current)`. Atomically repoint `current` to `releases/<sha>`. Send SIGTERM to the app (graceful drain up to 5 s), then start uvicorn from `current`. |
| 4. Ready | Poll `/health/live` for up to 20 s, then check that `/health.git_sha == sha`. |
| 5. Record | Append to `deployments.jsonl` (`status: succeeded \| failed \| rolled_back`). Return `{deploy_id, sha, previous_sha, ready_ms}`. |
| 6. Auto-revert on failure | If step 3 or 4 fails, repoint `current` back to `previous`, restart, verify readiness, and return `{status:"failed", reverted_to: previous}` |

There is no Docker socket, no shell and no free-form command: the launcher accepts **only** "deploy this SHA that is already staged".

---

## 15. Post-remediation health verification

Run by the executor right after the deployment and reported by Recovery:

1. **Flush window.** Wait 15 s, so the rolling window holds only post-deploy requests. This happens while the dashboard shows "verifying".
2. **Sample** `/metrics/summary` every 5 s, for up to 60 s.
3. **Pass** when 3 consecutive samples all have:
   - `git_sha == approved commit`
   - `error_rate_pct < 1.0`
   - `latency_ms_p50 < 300`
   - `pool_utilization_pct < 80`
   - `requests ≥ 1000` in the window (proves the load is present, so "healthy" isn't just idleness)
4. **Record:**
   - `before`: the last degraded sample before execution, or the alert sample;
   - `after`: the last passing sample;
   - `timeline`: every sample.

   These records feed the before/after display and the report verbatim.
5. **Fail** if 60 s pass without 3 consecutive passing samples. Follow the non-recovery handling in §16.

Expected `after`: 5xx ≈ 0.2% (the processor baseline), p50 ≈ 110 ms. The `Blackbox.md` example (0.4%, 120 ms) falls inside the acceptance ranges, but the real numbers are whatever is measured.

---

## 16. Failure handling

| Situation | Detection | Behavior | End state |
|---|---|---|---|
| **The patch is incorrect** (for example `pool_size: 20`, a change to the wrong file, or only raising `pool_timeout`) | The capacity regression test fails (`pool_size + max_overflow < 30`) → treated as **tests fail** below. In this scenario the capacity test means any patch that passes the tests restores at least 300 rps of capacity, so an ineffective-but-green patch is caught **before** approval. If verification fails anyway (an untested cause, such as a concurrent traffic surge, a DB problem, or a wrong environment), **verification** catches it. | Verification failure → the executor rolls back to the pre-execution SHA (C5; still degraded, which is the honest prior state) → Recovery posts the before/during/after samples → `revision_request` to RootCause with the verification evidence. RootCause may propose **"revert deploy #842"** (deploy C4) as an alternative remediation, through its own approval card. | `ROLLED_BACK → NEEDS_REVISION` |
| **Tests fail** | The test-runner JUnit result shows `failed > 0` | Up to 3 patch attempts with the failure output fed back. Never handed to Recovery. If all fail, `NEEDS_HUMAN` with the diagnosis and the best diff marked *NOT VALIDATED* (`03-AGENT-CONTRACTS.md` Failure B). | `NEEDS_HUMAN` |
| **The sandbox deployment fails** (the release fails its tree check, the app crashes on boot, readiness times out) | The launcher's step 2–4 error | The launcher reverts automatically to the previous release (§14.6). The executor records `deploy_failed` with the launcher's error and logs. Recovery posts it. If the auto-revert **also** fails, the executor marks `FAILED`, the service is `unhealthy`, the incident commander is paged in the room, and the operator runs a **demo reset** (§17). | `ROLLED_BACK → NEEDS_REVISION`, or `FAILED` |
| **The health checks don't recover** | §15 fails after 60 s | Pre-approved rollback to the pre-execution SHA (it is on the approval card). The samples are recorded. A revision request goes out with the evidence. | `ROLLED_BACK → NEEDS_REVISION` |
| **Load generator stopped** (false "healthy") | `requests < 1000` in the window | Verification is inconclusive, so the incident is **not** resolved. The dashboard shows "no traffic". | Stays in `VERIFYING` → after the timeout, rollback |

---

## 17. Reset mechanism

`demo-reset` is an operator-only UI action and also a CLI. It restores the HEALTHY state, so every run starts identically.

| # | Step | Detail |
|---|---|---|
| 1 | Quiesce | Stop `loadgen` and `monitor`. Agents enter `paused` (ignore new events). |
| 2 | Switch cleanup | Close any open approval requests from the previous run [V]. Acknowledge outstanding outcomes. Archive the previous incident rooms (`archive_room`, which is reversible [S]). The new incident room name uses the incident number, and the incident counter restarts at `1042`. *(Whether the Switch/Mattermost channel name can be reused after archiving is **UNKNOWN**. If it cannot, append the run letter: `incident-payment-service-1042-b`.)* |
| 3 | Restart Mattermost | This mitigates the emulation instability observed in S1/S6 (recon §19–§20). Wait for the bridge log line *"Websocket authentification OK"*. |
| 4 | Rebuild the demo repo | Delete `payment-service.git` and all RootCause worktrees, the executor's frozen repo and `releases/*`. Run `build_repo.py` and check that the C1–C5 SHAs equal the pinned values in `sim/seed/expected_shas.txt`. |
| 5 | Deployment metadata | Restore `deployments.jsonl` from `deployments.seed.jsonl` (#838–#841). |
| 6 | Database | `TRUNCATE payments`; reset sequences. |
| 7 | Deploy the baseline | Launcher `POST /deploy {sha: C4, deploy_id: "841", source: "reset"}`. The deployment record is *not* appended twice: it is already in the seed. |
| 8 | BLACKBOX state | Move `blackbox.db` to `runs/<timestamp>/` (kept for audit) and create a fresh DB. Seed the dashboard's history with **clearly labelled** sample incidents (`seeded: true`, shown with a "sample data" badge). |
| 9 | Warm up | Start `loadgen`, then `monitor`. The reset is **done only when 3 consecutive samples are HEALTHY** with `requests ≥ 1000`, which takes about 30 s. |
| 10 | Report | Print the pinned SHAs, the deployed SHA, a baseline sample, and "READY". |

**Idempotent:** running reset twice yields the same state. **Target duration:** under 90 s.

---

## 18. Resource budget

The Docker VM observed was 2 CPU / 2 GB, which is too small given Mattermost runs emulated.

| Component | CPU | Memory |
|---|---|---|
| Switch stack (incl. emulated Mattermost) | 1.5 | 1.5 GB |
| payment-service + launcher | 0.3 | 150 MB |
| payments-db | 0.3 | 150 MB |
| loadgen (120 rps) | 0.3 | 80 MB |
| monitor | 0.05 | 40 MB |
| BLACKBOX api, agents, executor, UI | 0.6 | 600 MB |
| Coding/test sandboxes (bursty) | 1.0 | 1 GB |
| **Recommended Docker VM** | **≥ 4** | **≥ 6 GB** |

If resources are constrained, lower the arrival rate to **60 rps** and `simulated_db_work` to **200 ms**. That gives the same capacity ratio (pool 10 → 50 rps capacity → 16.7% excess), so the error-rate story is identical. p50 rises to about 220 ms when healthy.
