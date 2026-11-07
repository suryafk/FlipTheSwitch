# 05 — Demo Scenario: "Deploy #842"

**One sentence:** A teammate's "harmless" staging change shrinks the payment-service DB pool from 50 to 10. Real traffic overruns it. BLACKBOX's agents find the change, write and test a real fix, get a genuine Switch approval from the incident commander, deploy it to the sandbox, and prove recovery with measured metrics.

The mechanism and components are in [`05-SIMULATION-ARCHITECTURE.md`](05-SIMULATION-ARCHITECTURE.md).

---

## 1. The two reproducible states

| | HEALTHY (deploy #841, commit C4) | DEGRADED (deploy #842, commit C5) |
|---|---|---|
| `config/database.yml` `pool_size` | 50 | 10 |
| Traffic | 120 rps open-loop, seeded | Same |
| HTTP 5xx rate (15 s window) | ≈ 0.2% (simulated processor flake only) | ≈ 17% (`DB_POOL_TIMEOUT` 503s + baseline) |
| Latency p50 | ≈ 110 ms | ≈ 3.5–4.0 s (queueing up to `pool_timeout = 4 s`) |
| DB pool utilization | ≈ 24% | 100% (10/10 checked out) |
| `/health.status` | `healthy` | `degraded` (3 reasons) |
| Log signature | INFO `payment_captured` samples | ERROR `QueuePool limit of size 10 overflow 0 reached, connection timed out, timeout 4.00` |
| How you reach it | `demo-reset` | The `TRIGGER PAYMENT INCIDENT` button (a real deployment of C5) |

Both states come from the same code paths. They differ only by the deployed commit.

---

## 2. Cast

| Participant | Where it appears |
|---|---|
| **BLACKBOX Control** (system voice, non-AI) | Mattermost `#blackbox-intake` and the incident channel |
| **BLACKBOX Sentinel**, **RootCause**, **Recovery** | Mattermost incident channel, and the BLACKBOX war room (mirroring the Switch room) |
| **Incident commander** (`user`, the presenter) | Mattermost: approves with the card button |
| **"ci-bot (simulated)"** | Author of deploy #842 in the deployment metadata |

---

## 3. Three-minute run of show

Timings are targets. Each beat lists what is **real**.

| Time | On screen | What happens (real mechanism) |
|---|---|---|
| **0:00–0:10** | BLACKBOX `/dashboard`: payment-service HEALTHY, live charts (5xx ≈ 0.2%, p50 ≈ 110 ms, pool ≈ 24%) | Charts read `/metrics/summary` from the running service |
| **0:10–0:15** | The presenter clicks **TRIGGER PAYMENT INCIDENT** ("simulates a teammate's faulty deploy #842") | The launcher deploys commit C5 |
| **0:15–0:30** | Charts turn red: 5xx climbs to about 17%, p50 to about 4 s, pool to 100% | Genuine pool saturation under 120 rps |
| **0:30–0:40** | Mattermost `#blackbox-intake`: Control posts the alert. Sentinel opens `#incident-payment-service-1042`. | The monitor rule fired on measured data. HMAC webhook → Control → Switch → Sentinel overhears [V]. Sentinel `create_room` [V]. |
| **0:40–0:55** | Sentinel posts: *"INC-1042 SEV-1: 5xx 17.4%, p50 3.9 s, pool 10/10, onset 18 s after deploy #842 [SIMULATED deploy metadata]. Log: QueuePool limit of size 10 (412×). Hypothesis H1 (0.74): #842 reduced DB pool. → @RootCause"* | A handoff over Switch [V], with JSON verified by sender id and store hash |
| **0:55–1:30** | RootCause: `ACK` → `ROOT_CAUSE_FOUND config/database.yml:5 pool_size 50→10 (C5); log_level change ruled out` → `PATCH_GENERATED` (diff in the war room's right panel) → `TESTS_RUNNING` → **`TESTS_RESULT 27/27 passed`** (the real runner count) | The coding agent edits the worktree; tests run in a `network=none` container. The hash is computed by BLACKBOX. |
| **1:30–1:45** | Recovery: validation passed → approval card in Mattermost: *"Permission needed · request A1 · INC-1042 · Apply DB pool fix to payment-service (staging-sandbox) · Patch sha256:3f9a… · Tests 27/27 · Risk LOW · Rollback: revert to C5 · Expires 10:50Z"*, with buttons **Approve remediation** / **Reject** | A Switch approval request [V], content-bound by `request_id` [V] |
| **1:45–2:00** | *(Optional, 5 s)* The presenter types `APPROVE INC-1042` in chat. Recovery: *"Chat text can't authorize. Use the approval card."* Then the presenter **presses Approve**. The UI shows "Switch decision recorded: answered by user at 10:41:12". | Plain text does nothing [V]. The button is processed by Switch's answerer check [V]. The `approval_outcome` push arrives in about 0.04 s [V]. |
| **2:00–2:10** | Recovery → executor: verification checklist ticks (decision read back from Switch ✓, hash ✓, approver ✓, not expired ✓, not revoked ✓, first use ✓) | The executor's independent verification (`04-APPROVAL-SECURITY.md` §5) |
| **2:10–2:35** | Deploy #843 (C6). Charts go green: 5xx 17.4% → 0.2%, p50 3.9 s → 108 ms, pool 100% → 24%. *"Recovered (3/3 checks)"*. | Launcher deploy by SHA. Verification samples (§15 of the architecture doc). |
| **2:35–3:00** | `/incidents/INC-1042/report`: timeline, root cause, diff, tests, approval (who, when, which hash), before/after samples, preventive tasks: *add a capacity regression test to CI*, *validate pool vs. capacity at startup*, *require a review for config/database.yml*, *alert on pool utilization > 80%*, *update the RUNBOOK capacity section* | The report is generated from the event log. The tasks are BLACKBOX records, posted in the room. |

Closing line: *"BLACKBOX doesn't just resolve incidents. It makes every incident an opportunity to build a more resilient engineering organization."*

### 3.1 Timing honesty

The **coding agent** is the long pole: a real Claude Code run plus tests may take 40–90 s. Options, in order of preference:

1. **Pre-warmed run.** Keep the brief small and use a fast model setting. The test suite runs in about 3 s. Measure it during rehearsal.
2. **If it still runs over:** edit the recording with an on-screen **"⏩ 48 s skipped"** marker. Never fake the output.
3. **The deterministic fallback** is used only if the agent fails. It is labelled `[SIMULATED coding agent]` everywhere.

---

## 4. What is real and what is simulated (shown on a slide or in the README)

| Element | Real / Simulated |
|---|---|
| Switch rooms, messages, handoffs, approval card, answerer check, read-back | **Real** (verified S1–S7) |
| Mattermost (the chat surface) | **Real**, self-hosted |
| payment-service, its Postgres, pool saturation, 5xx and latency | **Real** behavior of real software |
| Customer traffic | **Simulated** (load generator) |
| Card processor and its 0.2% flake | **Simulated** (deterministic) |
| `pg_sleep(0.1)` "fraud score" query | **Simulated** DB work time (a real query) |
| Deploy #842 author ("ci-bot") and deploy ids #838–#841 | **Simulated** metadata pointing at real commits |
| Monitoring vendor | **Simulated** (our monitor), with real rules on real measurements |
| Root-cause analysis, patch, tests | **Real** (fallback labelled if used) |
| Deployment and rollback | **Real** process restart in the sandbox container |
| Before/after metrics | **Real** measurements, timestamped |

---

## 5. Pre-flight checklist (before recording)

1. Docker VM has ≥ 4 CPU / 6 GB. Mattermost was restarted within the last 10 min (emulation instability [V]).
2. `demo-reset` finished with READY, and the pinned SHAs matched.
3. A rehearsal run completed end to end within the last 30 min (this warms the caches and the model connection).
4. Mattermost is logged in as `user`; the browser windows are arranged (dashboard on the left, Mattermost on the right).
5. The incident counter is at 1042. No open approvals are left (reset closes them).
6. The model API key's budget is checked. The fallback patch is present.
7. Screen recorder runs at 1080p; notifications are muted.

---

## 6. Alternate (failure) demos: optional bonus clips

These are triggered by **test-profile knobs**. They are disabled in the default profile, labelled on screen, and never mixed into the main run.

| Clip | Knob | Shows |
|---|---|---|
| "Reject" | none (the presenter presses Reject) | Recovery asks why. RootCause revises. A new hash means a new card. The old approval can't be reused. |
| "Wrong patch" | `SIM_FORCE_PATCH=pool_20` | The capacity test fails → retry → a correct patch |
| "Deploy fails" | `SIM_LAUNCHER_FAIL_NEXT=1` | The launcher auto-reverts; `ROLLED_BACK` → revision |
| "Doesn't recover" | `SIM_TRAFFIC_SURGE_AFTER_DEPLOY=600` (loadgen jumps to 600 rps, above even a 50-connection pool's 500 rps capacity: a second, concurrent cause) | Verification fails at 60 s → rollback → Recovery reports "fix applied but the service didn't recover: traffic 600 rps > capacity" → `NEEDS_REVISION` / `NEEDS_HUMAN` |
| "Stranger approves" | A second Mattermost user presses Approve | Switch refuses it [V]; the card stays open |
