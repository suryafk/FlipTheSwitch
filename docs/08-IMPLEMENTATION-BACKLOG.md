# 08 — Implementation Backlog

**Effort** is engineer-hours, for an engineer fluent in Python and React working with AI coding assistance.

**Criticality:**

| Level | Meaning |
|---|---|
| **C1** | Demo fails without it |
| **C2** | The demo works but is less credible or less safe |
| **C3** | Nice to have |

**Lanes** (who owns what; see the critical path):

| Lane | Owns |
|---|---|
| **A** | Switch and agents |
| **B** | Simulation and executor |
| **C** | API, UI and demo |

Spikes from `07-TECHNICAL-SPIKES.md` are folded in as the first tasks of their lanes.

---

## P0 — Must work for the demo (≈ 140 h)

| ID | Title | Description | Depends on | Effort | Acceptance criteria | Component | Lane | Crit. |
|---|---|---|---|---|---|---|---|---|
| P0-00 | **Demo host readiness (SP-7)** | Resize the Docker VM (≥ 4 CPU / 6 GB) or provision an amd64 host. 15-minute soak with the Switch stack under load. | — | 1 (+ soak) | 0 Mattermost panics in 15 min; ping p99 < 500 ms | Infra | B | C1 |
| P0-01 | **Claude Code headless spike (SP-1)** | 5 normal and 2 injected runs on a throwaway repo; probe A1 (reading outside cwd) and A2 (auth) | — | 3 | ≥ 4/5 correct; tools ⊆ {Read, Edit, Grep, Glob}; no escape; p50 ≤ 60 s; ≤ $0.50 per run; the `stream-json` sample is recorded | RootCause | A | C1 |
| P0-02 | **Decisions locked** | Record the LLM runtime (Anthropic SDK tool loop), the coding-agent mode (host vs. container, from P0-01), the model ids, the team lanes, and the run-suffixed room names | P0-01 | 0.5 | A decision log entry in `docs/08-CRITICAL-PATH.md` | Arch | A | C1 |
| P0-03 | **Switch bootstrap script** | Bring up the pinned 0.31.0 stack; mint the personal registration key; register control, sentinel, rootcause and recovery; create `blackbox-intake`; capture the IC participant id (post as `user` and read `sender`); write per-service env files | P0-00 | 4 | One command produces a ready stack and env files; it is idempotent on re-run | Infra | A | C1 |
| P0-04 | **SwitchConnection library** | WebSocket with pong, reconnect and backoff, a persisted cursor, `connect_to_room` restore after a > 6 s drop, `message_id` dedupe, an ops client, an approvals client (open, close, outcomes, delivered), a `/message` fallback | P0-03 | 6 | Promoted S3/S4/S6 cases pass as unit/integration tests | Shared | A | C1 |
| P0-05 | **Shared models and schemas** | Pydantic models for the alert, handoff, ack, progress, incident context, binding, execution and timeline event; canonical-JSON hashing | — | 3 | All `03-HANDOFF-SCHEMAS` examples validate; negative fixtures fail | Shared | A | C1 |
| P0-06 | **blackbox-api core** | SQLite (WAL): incidents, an append-only `events` table (triggers block UPDATE/DELETE), handoffs, remediations, approvals, revocations, executions, tasks; the transition table; per-agent token scopes; a **single-active-incident lock**; SSE to the UI | P0-05 | 10 | State-machine tests (every illegal transition rejected); scope tests; SSE delivers events in order | API | C | C1 |
| P0-07 | **Control service** | HMAC webhook (timestamp window, replay cache, schema, service catalog) → post the alert to intake; Control's WebSocket persists **all** incident-room Switch events for the UI; gap → `read_context` backfill | P0-04, P0-06 | 5 | A forged webhook gets 401; the alert appears in intake; UI events carry `switch.sequence`; a forced gap is backfilled | API | C | C1 |
| P0-08 | **Agent runtime library** | Anthropic SDK tool loop; tool registry with a per-agent allowlist; `<room_message>` / `<tool_result>` tagging; per-incident budget caps; **outbound secret scanner**; prompt and model version recording | P0-04, P0-05 | 6 | A disallowed tool is never offered and the server refuses it; a seeded fake secret is blocked; the budget stops a runaway loop | Shared | A | C1 |
| P0-09 | **Demo repo and payment-service** | The app (api, pool, `pg_sleep` work, metrics plus a `/metrics/summary` window, health, JSON logs), config files, **26 tests**, `capacity.yml`, `RUNBOOK.md`; deterministic `build_repo.py` with C1–C5; pinned SHAs; the reference fix patch | — | 10 | Pinned SHAs reproduce; 26 green on C4 and C5; the reference fix gives 27 green; the new test fails on C5 | Sim | B | C1 |
| P0-10 | **Simulation infrastructure (SP-5)** | payments-db, launcher (deploy by SHA, tree check, auto-revert, deployments.jsonl), open-loop loadgen, monitor rules plus a signed webhook; calibration run (verify A3/A4) | P0-09, P0-00 | 8 | L2 ranges are met for HEALTHY, DEGRADED and AFTER on the demo host (or on the 60 rps profile); deploy ≤ 5 s; a bad release auto-reverts | Sim | B | C1 |
| P0-11 | **Test runner (SP-3)** | `network=none`, read-only container image; JUnit parsing; a single "sandbox runner" service owns Docker access | P0-09 | 3 | SP-3 cases (a)–(d) pass; counts equal pytest's output; runs ≤ 10 s | RootCause | B | C1 |
| P0-12 | **Sentinel agent** | Overhear alerts from Control; create the incident (idempotent) and the room (run-suffixed name); evidence tools (metrics, log signatures, deployments, deploy diff summary); severity; hypotheses; the handoff (store first, then post); ACK handling | P0-07, P0-08, P0-10 | 7 | Correct evidence cites #842, the log signature and the pool; the handoff is schema-valid and ACKed; re-delivery causes no duplicate incident | Agent | A | C1 |
| P0-13 | **RootCause agent** | Verify the handoff (sender plus store hash), ACK, repo tools, worktree per remediation, **Claude Code adapter** (mode per P0-02), commit and canonical hash, test runner, post-checks (allowlist, no test-infra edits, no deleted tests, ≤ 200 lines), up to 2 attempts, **fallback patch labelled SIMULATED**, the proposal handoff | P0-01, P0-11, P0-12 | 12 | E2E: finds `config/database.yml`; a real patch plus a new test; tests green; a forged handoff is rejected; the fallback is labelled when forced | Agent | A | C1 |
| P0-14 | **Recovery agent** | Verify the handoff; deterministic validation; binding; the **card with deterministic fields first and a diff excerpt (≤ 1 500 chars)**; free text quoted and capped; open the approval; handle outcomes (approve → request execution; reject → `NEEDS_REVISION`, then a revision handoff); report trigger; preventive tasks | P0-13, P0-15 | 8 | The card shows the action, target, full hash, diff excerpt, tests and rollback; reject → no execution; approve → an execution request | Agent | A | C1 |
| P0-15 | **Executor** | Freeze the commit into its own repo; the verification algorithm (read-back from Switch, hash, `request_id`, approver allowlist, expiry, revocation, first use); atomic consume; launcher deploy; verification sampling (3/3); rollback on failure; `/delivered` last | P0-04, P0-06, P0-10 | 8 | SP-4 V-1 to V-12 verdicts are correct; the happy path deploys the approved SHA; a negative path never changes `git_sha` | Executor | B | C1 |
| P0-16 | **Report generator** | The report from the event log: timeline, root cause, diff, tests, approval record, before/after samples, preventive tasks, simulation disclosure; published to the room | P0-06 | 4 | Golden test: every number traces to an event; posted in Switch | API | C | C1 |
| P0-17 | **UI shell and war-room feed** | Design tokens (the dark subset); app shell; the war room: header, feed row types (human, SN, RC, RV, handoff card, task, decision card, execution, state, security) with provenance badges; execution strip; SSE plus backfill | P0-06, P0-07 | 12 | Every feed row kind renders from real events; provenance badges link to the raw JSON; reconnect causes no duplicates | UI | C | C1 |
| P0-18 | **War-room right-panel tabs** | Root cause plus evidence list; diff viewer (unified); tests panel; approval (binding, live Switch status, verification checklist, "Open in Mattermost ↗") | P0-17 | 8 | The diff and its hash match the store; approval status updates live; no approve button | UI | C | C1 |
| P0-19 | **Dashboard-lite** | Three live charts (5xx, p50, pool) with deploy/alert/approval markers; the trigger button with preconditions; the active incident card | P0-06, P0-10 | 5 | The charts read `/metrics/summary` samples only; the trigger is disabled unless HEALTHY 3/3 | UI | C | C1 |
| P0-20 | **Report page** | Render the generated report; before/after tiles linked to samples | P0-16 | 3 | Matches the report JSON | UI | C | C1 |
| P0-21 | **Demo reset** | Quiesce; close open approvals; archive the previous rooms; **restart Mattermost**; rebuild the repo; restore the deployments; truncate the DB; deploy C4; archive and recreate the BLACKBOX DB; warm up to HEALTHY 3/3 | P0-03, P0-10, P0-06 | 4 | Two consecutive resets give identical state in ≤ 90 s | Sim | B | C1 |
| P0-22 | **E2E happy path and repeat** | A headless E2E-1 driver (presses Approve through the Mattermost API), the E2E-9 typed-approve check, the E2E-2 reject check; 3× repeat with reset | All above | 6 | Acceptance criteria B–H MUST items pass 3 times in a row | QA | C | C1 |
| P0-23 | **Required docs and recording** | `README`, `ARCHITECTURE`, `AGENTS`, `DEMO`, `.env.example`; the real-vs-simulated table; rehearsal; final recording plus a **backup recording** | P0-22 | 4 | Docs let a newcomer run the demo; two good takes exist | Demo | C | C1 |

**P0 total ≈ 141.5 h**

| Lane | Hours |
|---|---|
| A | ≈ 49.5 |
| B | ≈ 34 |
| C | ≈ 58 |

Lane C is over-subscribed: give it the strongest frontend-capable engineer, and let lane B absorb P0-21 and P0-16.

---

## P1 — Important for product quality (≈ 58 h)

| ID | Title | Description | Depends on | Effort | Acceptance criteria | Component | Crit. |
|---|---|---|---|---|---|---|---|
| P1-01 | Coding-agent container | Run Claude Code in a read-only, non-root container (P0 if SP-1 shows a cwd escape) | P0-01 | 6 | SP-2 probes pass | RootCause | C2 |
| P1-02 | Two-way agent exchange | RootCause asks Sentinel one clarifying question through Switch; Sentinel answers with a tool-backed fact | P0-12, P0-13 | 3 | Visible Q/A in the room within 10 s | Agents | C2 |
| P1-03 | Audit hash chain plus verify | `prev_hash`/`hash`; verify on startup and before the report; "chain verified" in the report | P0-06 | 3 | Tampering is detected (SEC-6) | API | C2 |
| P1-04 | Executor security tests AP-7 to AP-14 automated | Promote SP-4 into CI | P0-15 | 4 | All green | Executor | C2 |
| P1-05 | L4 Switch integration suite | Promote the S1–S7 harnesses | P0-04 | 4 | Green against 0.31.0 | Shared | C2 |
| P1-06 | UI revoke | IC-only revoke button, confirm modal, executor check | P0-15, P0-18 | 3 | E2E-8 passes | UI/Executor | C2 |
| P1-07 | Revision loop polish | A revision handoff with feedback, if given; a new card; at most 3 cycles → `NEEDS_HUMAN` | P0-14 | 4 | E2E-2 full | Agents | C2 |
| P1-08 | NEEDS_HUMAN flows | Failure A (no evidence) and Failure B (all attempts red) room messages and UI states | P0-12, P0-13 | 4 | E2E-4 passes | Agents/UI | C2 |
| P1-09 | Watchdog single nudge | Control nudges an unacknowledged handoff after 60 s | P0-07 | 2 | E2E-10 passes | API | C2 |
| P1-10 | Degraded-dependency banners | Switch, bridge and stream status banners | P0-17 | 2 | Shown during a Mattermost restart (E2E-11) | UI | C2 |
| P1-11 | Launcher-fail and no-recovery E2E | The `SIM_LAUNCHER_FAIL_NEXT` and `SIM_TRAFFIC_SURGE_AFTER_DEPLOY` knobs and tests | P0-15 | 4 | E2E-5 and E2E-6 pass | Sim/QA | C2 |
| P1-12 | Agent status panel (compact) | A small panel in the war-room overview: connection, current step, last tool | P0-17 | 3 | It shows a stale agent | UI | C3 |
| P1-13 | Investigation detail | Hypotheses with confidence; ruled-out list; evidence raw drawer | P0-18 | 4 | — | UI | C3 |
| P1-14 | Execution stepper detail | Per-check verification rows; samples table | P0-18 | 4 | — | UI | C3 |
| P1-15 | Prompt-injection test set | `03-PROMPT-DESIGNS` §D automated | P0-12–14 | 4 | No out-of-scope tool calls; no secrets | QA | C2 |
| P1-16 | Responsive `md` layout | War room at 1024–1439 px | P0-17 | 4 | Usable at 1280 × 800 | UI | C3 |

---

## P2 — Optional enhancements (≈ 70 h, not in the 72-hour plan)

| ID | Title | Effort |
|---|---|---|
| P2-01 | Agent Activity Monitor page | 6 |
| P2-02 | Incident History page and analytics, with seeded sample data | 6 |
| P2-03 | Full Investigation page with a correlation timeline | 5 |
| P2-04 | Presenter mode and keyboard map | 3 |
| P2-05 | Egress allowlist proxy for the sandbox | 6 |
| P2-06 | Switch room anchoring of the audit chain | 2 |
| P2-07 | Control-owned state and task-board room documents | 4 |
| P2-08 | Expiry re-request (`:r2`) and chat-command revoke | 4 |
| P2-09 | Codex adapter | 6 |
| P2-10 | Multi-incident support | 8 |
| P2-11 | Light and print theme for the postmortem; PDF export | 3 |
| P2-12 | Compare-attempts diff-of-diffs | 3 |
| P2-13 | Non-admin Switch owner plus addressing-policy automation | 6 |
| P2-14 | Approval-reader service (separate key holder) | 4 |
| P2-15 | Responsive `sm` (phone) layouts | 4 |
