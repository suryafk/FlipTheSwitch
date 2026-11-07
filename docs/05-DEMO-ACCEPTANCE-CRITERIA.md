# 05 — Demo Acceptance Criteria

The demo is **ready to record** only when every **MUST** below passes in an automated run (E2E-1 in `05-TEST-STRATEGY.md`) and in one human rehearsal.

- Each criterion maps to `Blackbox.md` §19 where it applies.
- "Measured" means read from `/metrics/summary`, logs, git, the Switch API or the BLACKBOX event log. It never means read from UI text.

---

## A. Environment and reset

| ID | Criterion | Verification | Level |
|---|---|---|---|
| A-1 | `demo-reset` reaches READY in ≤ 90 s and is idempotent (two runs → identical state) | Reset report; L1 reset test | MUST |
| A-2 | Demo-repo SHAs C1–C5 equal the pinned values | `git rev-parse` vs `expected_shas.txt` | MUST |
| A-3 | After reset the deployed SHA is C4, and `/health.status == healthy` for 3 consecutive samples with `requests ≥ 1000` | `/health`, `/metrics/summary` | MUST |
| A-4 | No open Switch approvals and no active incident remain from the previous run | `GET /agent-sessions/approvals/outcomes` empty; BLACKBOX DB fresh | MUST |
| A-5 | The demo profile refuses to start with any `SIM_*` failure knob set | Startup check | MUST |
| A-6 | The Docker VM has ≥ 4 CPU / 6 GB, and Mattermost was restarted within 10 min | Pre-flight script | SHOULD |

## B. HEALTHY and DEGRADED states (real, measured)

| ID | Criterion | Verification | Level |
|---|---|---|---|
| B-1 | HEALTHY: 5xx 0–0.6%, p50 80–180 ms, pool 10–45% | Calibration ranges (L2) | MUST |
| B-2 | The fault is introduced by deploying **commit C5**, whose diff changes `config/database.yml` `pool_size: 50 → 10` | `git show C5`; `deployments.jsonl` #842 | MUST |
| B-3 | DEGRADED within 25 s of the trigger: 5xx 12–24%, p50 2.5–4.5 s, pool ≥ 95%, `pool_timeouts > 0` | `/metrics/summary` | MUST |
| B-4 | Application logs contain SQLAlchemy's `QueuePool limit of size 10` errors after the trigger, and none before it | `/logs/app.jsonl` | MUST |
| B-5 | No component writes or overrides metric values; all charts read `/metrics/summary` | Code review checklist; grep for literal metric values in UI and agents | MUST |

## C. Detection and collaboration (`Blackbox.md` §19: Monitoring, Switch integration, Agent identities, Multi-agent collaboration, Shared memory)

| ID | Criterion | Verification | Level |
|---|---|---|---|
| C-1 | The monitor fires `PaymentServiceHigh5xx` from measured samples, and an HMAC-verified alert reaches `blackbox-api` | Monitor log; webhook audit event | MUST |
| C-2 | Control posts the alert in `#blackbox-intake`; Sentinel receives it as a Switch event | Switch `event_id` in the BLACKBOX log | MUST |
| C-3 | Sentinel creates `incident-payment-service-1042` through Switch `create_room`; the channel exists in Mattermost with the 4 BLACKBOX identities and `user` | Switch room detail; Mattermost API | MUST |
| C-4 | Three distinct agent identities (plus the labelled system voice) post in the room | `read_context` senders | MUST |
| C-5 | Handoff Sentinel → RootCause and handoff RootCause → Recovery are **Switch messages** with schema-valid JSON, acknowledged by addressed ACKs, and verified against store hashes | Switch history; handoff records with `switch_event_id` | MUST |
| C-6 | Sentinel's evidence cites deploy #842, the pool-timeout log signature and the pool utilization, each with a value that matches its source | Compare the handoff evidence with the logs, metrics and deployments | MUST |
| C-7 | RootCause reads the incident context (version recorded) and the room history before acting | Tool-call log | MUST |
| C-8 | Simulated items are tagged `[SIMULATED]` in messages and the report | `read_context` grep | MUST |

## D. Diagnosis, patch and tests (`Blackbox.md` §19: Root-cause analysis, Remediation, Testing)

| ID | Criterion | Verification | Level |
|---|---|---|---|
| D-1 | The root cause names `config/database.yml`, `pool_size`, and commit C5 / deploy #842 | The `ROOT_CAUSE_FOUND` record | MUST |
| D-2 | The log-level change is explicitly considered and ruled out | The analysis record | SHOULD |
| D-3 | The coding agent run is real (`coding_agent.mode = claude_code`) and edited files in the worktree; or the fallback is used and labelled everywhere | Remediation record; room messages | MUST |
| D-4 | The patch restores capacity (`pool_size + max_overflow ≥ 30`), touches only allowlisted paths, and adds ≥ 1 regression test that **fails on C5 and passes on the fix** | Post-checks; meta-run of the new test on C5 | MUST |
| D-5 | Tests run in the `network=none` runner; the counts come from JUnit; result `failed = 0`, `passed ≥ 27` | Test-run record | MUST |
| D-6 | `patch_hash` is computed by BLACKBOX from the canonical diff and appears identically in the handoff, the approval card and the executor record | Compare the three | MUST |

## E. Human approval and authorization (`Blackbox.md` §19: Human approval, Authorization)

| ID | Criterion | Verification | Level |
|---|---|---|---|
| E-1 | A Switch approval card appears in the incident channel, showing the action, target, full hash, tests, risk, rollback and expiry | Mattermost post; Switch approval row | MUST |
| E-2 | **Typing `APPROVE INC-1042` does not authorize anything** (no outcome, no execution) | E2E-9; AP-1 [V] | MUST |
| E-3 | Execution begins only after the incident commander presses **Approve**, and the BLACKBOX record shows `answered_by` = the commander's Switch participant id and `answered_at` | Executor record; Switch outcome | MUST |
| E-4 | The executor's verification record lists: decision read back from Switch, recomputed hash match, `request_id` match, approver allowlisted, not expired, not revoked, first use. Any failure means no deploy. | Executor event; AP-7 to AP-14 green | MUST |
| E-5 | The rejection path (E2E-2) and the stranger-press refusal (AP-2) pass | Test results | MUST |

## F. Execution and verification (`Blackbox.md` §19: Execution, Verification)

| ID | Criterion | Verification | Level |
|---|---|---|---|
| F-1 | The executor deploys exactly the approved commit through the launcher; `/health.git_sha` equals that commit afterwards | Launcher response; `/health` | MUST |
| F-2 | AFTER: 3 consecutive samples with 5xx < 1%, p50 < 300 ms, pool < 80%, `requests ≥ 1000` within 60 s of the deploy | Verification record | MUST |
| F-3 | Before/after numbers shown in the UI and report are the exact recorded samples (with `sampled_at`), not literals | Compare UI and report vs. the event log | MUST |
| F-4 | The monitor sends `resolved` for the alert | Monitor log | SHOULD |
| F-5 | Failure paths E2E-5 (deploy fails) and E2E-6 (no recovery) end in `ROLLED_BACK` with the service back on the pre-execution SHA, and never `RESOLVED` | Test results | MUST |

## G. Report and learning (`Blackbox.md` §19: Audit, Learning)

| ID | Criterion | Verification | Level |
|---|---|---|---|
| G-1 | The report at `/incidents/INC-1042/report` is generated from the event log. It contains the timeline, initial alert, root cause, evidence, diff, tests, approval record, execution, verification, and preventive tasks. | Golden test (L3) and visual check | MUST |
| G-2 | The report states "audit chain verified to seq N", and the chain verifies | Verify job | MUST |
| G-3 | ≥ 3 preventive tasks are created as BLACKBOX records and posted to the room (for example: a capacity test in CI, startup validation, a config review rule, a pool-utilization alert, a runbook update) | Tasks table; room message | MUST |
| G-4 | The report is published in the Switch room (summary plus link) | `read_context` | MUST |

## H. Repeatability (`Blackbox.md` §19: Reproducibility)

| ID | Criterion | Verification | Level |
|---|---|---|---|
| H-1 | Three consecutive happy-path runs, each preceded by a reset, all pass sections B–G | R-1 | MUST |
| H-2 | Trigger → `RESOLVED` ≤ 3 min 30 s wall clock with the agent warmed; the on-screen video is ≤ 3 min (time skips marked, if any) | Event timestamps | SHOULD |
| H-3 | No Mattermost crash during the three runs (otherwise apply A-6 and re-run) | Container restart count | SHOULD |

## I. Honesty

| ID | Criterion | Level |
|---|---|---|
| I-1 | The README and the closing slide include the "real vs. simulated" table (`05-DEMO-SCENARIO.md` §4) | MUST |
| I-2 | No claim of cryptographic immutability; the audit is described as "tamper-evident (hash chain plus Switch-anchored checkpoints)" | MUST |
| I-3 | Any edited time skip in the video is marked on screen | MUST |
