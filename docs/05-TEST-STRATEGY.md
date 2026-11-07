# 05 — Test Strategy

**Goal:** prove that every claim the demo makes is produced by real mechanisms, that the governance controls hold under attack, and that the scenario reproduces identically.

**Test levels**, from fast to slow:

| Level | Name |
|---|---|
| L0 | Demo-repo tests (the ones RootCause runs) |
| L1 | Simulation component tests |
| L2 | Calibration (state) tests |
| L3 | BLACKBOX unit and contract tests |
| L4 | Switch integration tests |
| L5 | Security and approval tests |
| L6 | End-to-end scenario tests (happy and failure paths) |
| L7 | Repeatability and soak |

**Where they run:**

| Level | Runs in |
|---|---|
| L0–L3 | On every commit (CI or local) |
| L4–L7 | Against the local stack before each rehearsal and recording |

---

## L0 — Demo-repo tests (inside `payment-service.git`)

| Suite | Count at C4/C5 | Purpose |
|---|---|---|
| `test_payments.py` | 10 | Validation, deterministic declines (`…0002` → 402), idempotency (409), amount bounds |
| `test_api.py` | 6 | Status mapping, including **SQLAlchemy `TimeoutError` → 503 `DB_POOL_TIMEOUT`** and processor flake → 502 |
| `test_config.py` | 5 | YAML loading, types, defaults, missing-file errors |
| `test_health.py` | 3 | `healthy` / `degraded` / `unhealthy` rules on synthetic windows |
| `test_metrics.py` | 2 | Counter increments; percentile maths of the rolling window |
| **Total** | **26** | Green on both C4 and C5: they don't check capacity. That gap is intentional. |
| Added by the remediation | ≥ 1 | `test_pool_capacity_meets_peak_concurrency` (asserts `pool_size + max_overflow ≥ peak_concurrent_db_sessions`). **It must fail on C5 and pass on the fix.** |

**Rules:**

- The tests run with **no network and no database**. `conftest.py` provides a fake session and a fake pool.
- The command is fixed: `pytest -q -p no:cacheprovider --junitxml=/out/junit.xml tests/`.
- The count is parsed from the JUnit XML, never from LLM text.
- **Meta-test M1** (BLACKBOX CI): the reference fix (`sim/fallback/REM-842.patch`) applied to C5 gives `27 passed`, and the same new test run against C5 fails. This proves the regression test really regresses.

---

## L1 — Simulation component tests

| Component | Tests |
|---|---|
| `build_repo.py` | Produces SHAs equal to `sim/seed/expected_shas.txt`, on two machines and two runs (determinism) |
| launcher | Deploys a staged SHA; rejects an unstaged SHA; rejects a tree-hash mismatch; rejects a missing or wrong token (401); auto-reverts on boot crash (using a deliberately broken test release); `/health.git_sha` equals the deployed SHA; appends to `deployments.jsonl` exactly once per deploy |
| loadgen | Achieves 120 ± 3 rps for 60 s (open-loop: its send schedule does not slow when the server slows); deterministic payload sequence for a given seed; reports client-side status counts |
| monitor | Rules fire after exactly 2 breaching samples; one alert per state change; HMAC is correct; payload validates against `blackbox.alert/v1`; a `resolved` notice is sent on recovery |
| metrics | The window drops samples older than 15 s; `error_rate_pct` counts only `/payments*` 5xx; pool gauges match `pool.checkedout()` |
| reset | Twice in a row it reaches an identical state (SHAs, deployments file, empty `payments` table, HEALTHY samples). It closes open Switch approvals. |

---

## L2 — Calibration tests (the "no hardcoded numbers" proof)

These run the real stack (service, DB, loadgen) for 60 s in each state and assert **measured ranges**. If a range fails, tune the parameters (`simulated_db_work`, rate, `pool_timeout`), never the reported numbers.

| Metric (15 s window, 3 samples) | HEALTHY (C4) | DEGRADED (C5) | AFTER fix (C6) |
|---|---|---|---|
| `error_rate_pct` | 0.0–0.6 | 12–24 | 0.0–0.6 |
| `latency_ms_p50` | 80–180 | 2 500–4 500 | 80–180 |
| `pool_utilization_pct` | 10–45 | ≥ 95 | 10–45 |
| `pool_timeouts` in window | 0 | > 0 | 0 |
| `requests` in window | ≥ 1 600 | ≥ 1 600 | ≥ 1 600 |
| Log signature `QueuePool limit of size 10` | absent | present | absent |
| Monitor alert fires | no (10 min soak) | yes, within 25 s of the deploy | resolves within 30 s |

The low-resource profile (60 rps, 200 ms) has its own range table with the same shape.

---

## L3 — BLACKBOX unit and contract tests

- **Schemas:** every example in `03-HANDOFF-SCHEMAS.md` validates. Negative fixtures fail (a wrong `from_agent`/`to_agent` pair, a missing integrity field, an oversize message).
- **State machine:** every allowed `(from, to, actor)` transition passes; **every** other one is rejected. Invariants: no path from `REJECTED`, `REVOKED`, `APPROVAL_EXPIRED` or `NEEDS_REVISION` to `EXECUTING`, and no `RESOLVED` without a passing verification record.
- **Patch hash:** the canonical diff hash is stable across runs and machines; a whitespace change in the diff changes the hash.
- **Idempotency:** each key in `03-HANDOFF-SCHEMAS.md` §6 is tested with duplicate delivery.
- **Tool authorization:** for each agent token, every tool outside its scope returns 403 and `security.tool_denied` (generated from the matrix in `04-AGENT-PERMISSIONS.md`).
- **Redaction:** the outbound scanner blocks seeded fake secrets (marked test values) in messages.
- **Report generator:** fed a recorded event log, the output numbers equal the logged samples (golden test).

---

## L4 — Switch integration tests (promoted from smoke S1–S7)

The scratch harnesses that verified S1–S7 (recon §19–§20) become a maintained suite, `tests/switch_integration/`:

- registration;
- WebSocket connect, takeover and lapse;
- `create_room` → Mattermost channel;
- addressed vs. overheard;
- targeted handoff round-trip;
- approval open → card → press → outcome → read-back;
- content conflict 409;
- expiry 422;
- stranger refused;
- agent cannot answer (401);
- room-document ownership 403;
- resume with `start_from`;
- `gap` handling.

These run against the pinned Switch image `0.31.0`. **Any Switch upgrade must pass L4 before it is adopted.**

---

## L5 — Security and approval tests

These are the executor acceptance tests AP-1 to AP-14 (`04-APPROVAL-SECURITY.md` §8), plus:

| ID | Test |
|---|---|
| SEC-1 | The prompt-injection set (`03-AGENT-PROMPT-DESIGNS.md` §D) against each agent: no out-of-scope tool calls, no secret in any outbound message |
| SEC-2 | Coding sandbox: an attempt to reach the network from tests fails; writes outside `/work` fail; shell unavailable to the coding agent; resource limits kill a fork bomb |
| SEC-3 | Post-checks reject a patch touching `conftest.py`, CI, `pyproject.toml` dependencies, or deleting a test |
| SEC-4 | Webhook: bad HMAC → 401; stale timestamp → 401; replayed body → 409; unknown service → 400; oversize → 413 |
| SEC-5 | Forged handoff JSON posted by a human → rejected and announced |
| SEC-6 | The audit chain: altering a row breaks verification; the report shows INTEGRITY FAILURE; execution is disabled |
| SEC-7 | Artifact freeze: modifying the RootCause worktree after binding does not change what deploys (TOCTOU) |
| SEC-8 | Launcher: no token → 401; unstaged SHA → 400; a request from outside the internal network → unreachable |

---

## L6 — End-to-end scenario tests

Each test starts from `demo-reset` and is driven headlessly. Mattermost button presses use the Mattermost API as `user` (as in S5).

| ID | Scenario | Knob | Pass criteria (all measured) |
|---|---|---|---|
| E2E-1 | **Happy path** | none | All `05-DEMO-ACCEPTANCE-CRITERIA.md` checks; total time from trigger to `PREVENTIVE_TASKS_CREATED` ≤ 4 min (target ≤ 3 min with the agent warmed) |
| E2E-2 | **Rejection then revision** | Press Reject on card 1 | No deploy after the rejection; RootCause receives a `revision_request` containing the feedback; card 2 has a new `request_id` and hash; approve → recovery |
| E2E-3 | **Incorrect patch** | `SIM_FORCE_PATCH=pool_20` for attempt 1 | The capacity test fails; attempt 2 is correct; no red patch reaches Recovery; the report records both attempts |
| E2E-4 | **Tests fail on all attempts** | `SIM_FORCE_PATCH=pool_20` for all attempts, with the fallback disabled | `NEEDS_HUMAN` after 3 attempts; no approval opened; the room shows the failing test names |
| E2E-5 | **Sandbox deployment fails** | `SIM_LAUNCHER_FAIL_NEXT=1` | The launcher auto-reverts; the deployed SHA is back to C5; `ROLLED_BACK → NEEDS_REVISION`; the approval is consumed and not reusable |
| E2E-6 | **Health does not recover** | `SIM_TRAFFIC_SURGE_AFTER_DEPLOY=600` | Verification fails after 60 s; rollback to C5; the report shows before/during/after samples; not `RESOLVED` |
| E2E-7 | **Approval expiry** | `APPROVAL_TTL=20s`, no press | An expired outcome [V]; one re-request (`:r2`); the second expiry → `NEEDS_HUMAN`; never executed |
| E2E-8 | **Revoke after approve** | `@blackbox-recovery revoke INC-1042` before execution (an executor delay knob widens the window) | The executor refuses; `REVOKED → NEEDS_REVISION` |
| E2E-9 | **Chat approval attempt** | Post `APPROVE INC-1042` and press nothing | No execution within 2 min; Recovery points to the card |
| E2E-10 | **Agent crash mid-flow** | Kill RootCause after `ACK` | Restart, resume scan, the same `handoff_id` and no duplicate posts; completes |
| E2E-11 | **Mattermost restart mid-flow** | Restart Mattermost while the card is open | The approval stays `open`; the press after restart works; no false approval |
| E2E-12 | **Coding-agent fallback** | `SIM_CODING_AGENT=unavailable` | The fallback patch is used and labelled `[SIMULATED…]` in the room, the UI and the report |

Knobs exist only in the `test` Compose profile. The demo profile refuses to start if any knob is set.

---

## L7 — Repeatability and soak

- **R-1:** E2E-1 three times back to back, with a reset between runs. Pinned SHAs are identical. The remediation commit hash may differ between real coding-agent runs, but its *effect* must not: capacity ≥ 30, tests pass, and the after-metrics stay within range.
- **R-2:** HEALTHY soak for 15 min: no alert fires, and no Mattermost crash. On failure, record the resource settings.
- **R-3:** reset duration under 90 s on the recommended VM.

---

## Test data and determinism

| Item | How it's kept deterministic |
|---|---|
| Load generator | Fixed seed for amounts, cards and idempotency keys; the decline pattern comes from the card token |
| Processor flake | A counter-based rule (every 500th), not random |
| Repository history | Pinned SHAs |
| Timestamps | Real wall-clock time. Assertions use relative windows, never absolute times. |
| Coding agent | Nondeterministic by nature. Tests assert properties (scope, tests, effect), never exact diffs. The fallback is deterministic. |
