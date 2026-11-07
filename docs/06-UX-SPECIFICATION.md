# 06 — UX Specification

Specification for the nine BLACKBOX screens. The visual tokens are in [`06-DESIGN-SYSTEM.md`](06-DESIGN-SYSTEM.md), and navigation is in [`06-SCREEN-FLOWS.md`](06-SCREEN-FLOWS.md).

The product context comes from the 03–05 docs. Tags: **[V]** Switch behavior verified live · **[S]** Switch source only.

---

## 0. Foundations that apply to every screen

### 0.1 "Real Switch events, not a fake chat"

- `blackbox-control` is a member of every incident room. It holds a `scope=all` WebSocket that receives **every** room message, overheard or addressed [V].
- `blackbox-api` persists each event exactly as received: `switch.event_id`, `sequence`, `room_id`, `sender` (participant id), `sender_name`, `sender_kind`, `addressed`, `body`, `thread_id`, `timestamp`. It serves them to the UI.
- The war-room feed **is** that persisted Switch timeline. Each row carries a provenance badge: `SW · seq 4821`. Clicking the badge shows the raw event JSON.
- Rows that are not Switch messages (state transitions, tool calls, executor steps) are BLACKBOX events. They carry a `BB · evt 311` badge and different styling, so they never look like chat.
- **The UI has no message composer that posts as a human.** BLACKBOX does not hold the incident commander's chat credentials. Humans talk, and approve, in Mattermost through Switch. The UI offers **"Reply in Mattermost ↗"** and **"Open approval card ↗"** deep links. [PROD] option: post through the user's own linked Switch session.

### 0.2 Data contract used by the UI (design level)

| Source | Shape | Transport |
|---|---|---|
| `GET /api/incidents?state=&sev=&q=&cursor=` | Incident summaries | REST |
| `GET /api/incidents/{id}/context` | `blackbox.incident_context/v1` (`03-HANDOFF-SCHEMAS.md` §3) | REST |
| `GET /api/incidents/{id}/timeline?after_seq=` | Unified events (below) | REST, for backfill |
| `GET /api/stream?incident={id}` | Same event shape, live | **SSE** from `blackbox-api` to the browser |
| `GET /api/services/{svc}/metrics?window=` | `/metrics/summary` samples (`05-SIMULATION-ARCHITECTURE.md` §7) | REST + SSE every 5 s |
| `GET /api/incidents/{id}/remediations/{rid}` | Patch artifact, diff, test run (JUnit-derived), post-checks | REST |
| `GET /api/incidents/{id}/approvals` | Bindings + Switch outcome + verification record | REST + SSE |
| `GET /api/incidents/{id}/executions` | Executor steps, samples | REST + SSE |
| `GET /api/incidents/{id}/report?version=` | Generated report + chain verification | REST |
| `GET /api/agents` | Agent runtime status, current work, recent tool calls | REST + SSE |
| `GET /api/system/health` | Switch API, bridge, stream, monitor, loadgen health | REST + SSE |

**Unified timeline event** (rendered by the feed):

```json
{"evt_seq": 311, "ts": "…", "incident_id": "INC-1042",
 "provenance": "switch | blackbox",
 "switch": {"event_id": "…", "sequence": 4821, "room_id": "…", "sender": "@…", "sender_name": "…",
            "sender_kind": null, "addressed": true, "thread_id": null},
 "actor": {"kind": "human|sentinel|rootcause|recovery|control|executor", "display": "…", "is_ic": false},
 "kind": "human_message | agent_message | handoff | ack | progress | task | approval | execution | state_change | security | report",
 "body": "…", "structured": {…}, "simulated": false}
```

`structured` is the parsed fenced JSON (handoff, ack, progress), or the approval or execution record.

### 0.3 Global states

| State | Treatment |
|---|---|
| Loading | Static skeletons at the final geometry. Never a full-page spinner. Live panels show "Connecting to live stream…". |
| Stream reconnecting | Amber banner: "Live updates paused, reconnecting (last event seq 4821). Data shown may be stale." The feed backfills by `after_seq` on reconnect. |
| Switch API down | Red banner: "Switch unreachable. Agents cannot coordinate; no executions will run (fail-closed)." |
| Mattermost bridge degraded | Amber banner: "Chat bridge reconnecting. Human messages and approval clicks may not reach Switch. Retry after it recovers." This comes from the S6 finding [V]. |
| Simulation | The `SANDBOX` pill is always visible. `[SIMULATED]` badges appear on simulated items. Seeded history carries `SAMPLE DATA`. |
| Roles | `incident-commander` (can revoke, can trigger the demo), `observer` (read only), `operator` (reset, settings). The MVP has one local login with all roles. |

---

## 1. Incident Command Dashboard (`/dashboard`)

**Purpose:** answer "is anything on fire, and does anyone need me?" in 3 seconds.

**Layout (≥ 1440 px):**

```
┌ Top bar ─────────────────────────────────────────────────────────────────────────────┐
├ Nav ┬ KPI row: [Active 1] [Critical 1] [Pending approvals 1 ◆] [MTTR 6m 40s] [Resolved 24h 3] [Agents 3/3 ●] ┤
│     ├───────────────────────────────────────────────┬──────────────────────────────────┤
│     │ ACTIVE INCIDENTS (table)                      │ NEEDS YOU                         │
│     │ INC-1042 SEV-1 payment-service ◆ Awaiting appr│ ◆ INC-1042 approval · expires 08:12│
│     │                                               │   [Open approval ↗] [View]        │
│     ├───────────────────────────────────────────────┼──────────────────────────────────┤
│     │ SERVICE HEALTH: payment-service               │ AGENT STATUS                      │
│     │ 5xx ─╮  p50 ─╮  pool ─╮  (live 15m, markers)  │ SN ● idle  RC ● working INC-1042  │
│     │                                               │ RV ● awaiting decision            │
│     ├───────────────────────────────────────────────┴──────────────────────────────────┤
│     │ RECENT TIMELINE (cross-incident, last 20 consequential events)                    │
│     └──────────────────────────────────────────────────────────────────────────────────┤
│  SIMULATION: [TRIGGER PAYMENT INCIDENT]  "Simulates a teammate's faulty deploy #842"   │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

| Aspect | Specification |
|---|---|
| **Components** | KPI tiles ×6; active-incidents table (ID, severity, service, state chip, age, owner agent, next action); "Needs you" queue (pending approvals with countdowns, NEEDS_HUMAN items); service health card with three live mini-charts and event markers; agent status list; recent timeline; simulation control bar (demo profile only) |
| **Hierarchy** | 1) "Needs you" (decisions) → 2) active incidents → 3) service health → 4) agents → 5) history and KPIs |
| **Interactions** | A table row opens the war room. "Open approval ↗" deep-links to the Mattermost channel (the card handle is shown, e.g. `A1`). The trigger button opens a confirm popover listing its preconditions (HEALTHY 3/3, deployed = C4); it is disabled with a reason otherwise. KPI tiles filter the table. |
| **Data sources** | `/api/incidents`, `/api/services/payment-service/metrics`, `/api/agents`, `/api/system/health`, the SSE stream (all incidents) |
| **Loading** | Skeleton tiles and table rows; charts show axes with "Collecting samples (first 15 s)…" |
| **Empty** | No active incidents: "All clear. payment-service healthy for 42 m." Healthy KPIs, with history below. MTTR with no resolved incidents: "—" and a tooltip saying "no resolved incidents yet". |
| **Errors** | KPI fetch fails: the tile shows "—" plus ⚠ with a retry. Metrics unavailable: the chart shows "Metrics source unreachable since 10:31:20Z", and the last known values are dimmed. |
| **Responsive** | `md`: KPI row wraps 3×2; "Needs you" moves above the table. `sm`: single column, ordered Needs you → Active → Health → Agents; charts become sparklines. |
| **Honesty** | MTTR and "Resolved 24h" exclude seeded incidents unless the "include sample data" toggle is on (it is off by default and labelled). |

---

## 2. Live Incident War Room (`/incidents/:id`): the primary screen

**Purpose:** watch, understand and govern one incident end to end. Show exactly what the agents and the human said in Switch, and what BLACKBOX did.

### 2.1 Layout (≥ 1440 px)

```
┌ Top bar ──────────────────────────────────────────────────────────────────────────────────────────────┐
├ INC-1042 · payment-service · SEV-1 ▲ · ◆ AWAITING APPROVAL · age 03:12 · room #incident-payment-service-1042 ↗ · LIVE ● seq 4833 ┤
├──────────────────────┬──────────────────────────────────────────────────┬─────────────────────────────┤
│ OVERVIEW (320)        │ SWITCH ROOM FEED (flex)                           │ INVESTIGATION & REMEDIATION │
│ State stepper         │ [All][Humans][SN][RC][RV][Handoffs][Decisions]   │ (400, tabs)                  │
│  ✓ Detected 10:30:42  │ [Tasks][System]        ⌕ filter   ⤓ jump to live  │ [Root cause][Diff][Tests]    │
│  ✓ Investigating      │──────────────────────────────────────────────────│ [Plan][Approval]             │
│  ✓ Root cause         │▌SN Sentinel · agent msg        SW·seq 4802 10:30:58│ Root cause card              │
│  ✓ Staged             │  INC-1042 SEV-1: 5xx 17.4% …  [SIMULATED deploy] │  config/database.yml:5       │
│  ◆ Awaiting approval  │┏━ HANDOFF  SN ──▶ RC  HND-1042-001 ━━━━━━━━━━━━━┓│  pool_size 50 → 10 (C5 #842) │
│  ○ Executing          │┃ evidence: 5xx 17.4 · deploy #842 · QueuePool…  ┃│  confidence 0.91             │
│  ○ Verified           │┃ hypothesis H1 0.74  ✓ sender ✓ hash  ACK 1.2 s ┃│ Ruled out: log_level change  │
│ Metrics (live 15m)    │┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ seq4806 ┛│ Evidence (4) ▸               │
│  5xx  17.4% ▲        │▌RC RootCause · progress   ROOT_CAUSE_FOUND  seq4811│                              │
│  p50  3.9 s ▲         │▌RC RootCause · progress   TESTS_RESULT 27/27 ✓     │                              │
│  pool 100% ▲          │┏━ HANDOFF  RC ──▶ RV  HND-1042-002 … ACK 0.8 s ┓  │                              │
│ Timeline (compact)    │▌BB Task · Obtain approval  ◐ open        evt 318   │                              │
│ People & agents       │┏━ DECISION (Switch approval)  ◆ OPEN   A1  ━━━━━┓  │                              │
│  user (IC) HUMAN      │┃ Apply DB pool fix → staging-sandbox             ┃  │                              │
│  SN ● RC ● RV ● CT ●  │┃ patch sha256:3f9a0c1d2e3b… · tests 27/27 · LOW  ┃  │                              │
│                       │┃ expires 10:50:00Z (08:12)  [Open card ↗]        ┃  │                              │
│                       │┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛  │                              │
│                       │▌user HUMAN IC · "approve INC-1042"      SW·seq 4830│                              │
│                       │   ⓘ chat text does not authorize; use the card     │                              │
│                       │▌RV Recovery · "Chat can't authorize. Use card A1."  │                              │
├──────────────────────┴──────────────────────────────────────────────────┴─────────────────────────────┤
│ EXECUTION STRIP: Decision ◆ open · Executor idle · Deployed C5 (#842) · Health DEGRADED (5xx 17.4%) · [Reply in Mattermost ↗] │
└───────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Feed row types: how each is distinguished

| Kind | Provenance | Visual treatment | Content |
|---|---|---|---|
| **Human message** | `SW · seq` | Neutral white rail; initials avatar; name plus a `HUMAN` tag (plus `IC` if the person is on the approver allowlist); body at full width, 14 px | Text exactly as received from Switch. `addressed` messages show "→ @agent". If the text looks like an approval phrase, an inline info note says "chat text does not authorize; decisions use the approval card". |
| **Sentinel activity** | `SW · seq` | Teal rail; `SN` avatar; name | The human line. The structured block is collapsed under "data ▸". Evidence chips are clickable. |
| **RootCause activity** | `SW · seq` | Violet rail; `RC` | Progress posts render as a **milestone row**: the milestone label (`TESTS_RESULT`) as a small caps chip, plus the one-line text, plus artifact links (diff, tests) |
| **Recovery activity** | `SW · seq` | Magenta rail; `RV` | Validation results, approval announcements, execution progress, report link |
| **Agent-to-agent handoff** | `SW · seq` | **A full-width bordered card** with a double border in `border.strong` and a header strip `HANDOFF  SN ──▶ RC`, both agents' chips in their colors, and the `handoff_id` | Summary; evidence chips; hypotheses with confidence bars; required actions; **verification badges** (`sender ✓`, `store hash ✓`); **ACK status**: pending (amber, with a timer) → `ACK 1.2 s` (green) → `duplicate`/`rejected` (red, with the reason). The ACK row itself is folded into the card, not shown separately. |
| **Task** | `BB · evt` | Compact 32 px row; no avatar; a checkbox-like status glyph; a `TASK · BLACKBOX` tag (honest: Switch tasks aren't used [V]) | Task type, owner agent chip, status, duration |
| **Decision** | Switch approval state plus `BB` binding | **A decision card** with a 2 px border colored by state: open = amber, approved = green, rejected = red, expired or closed = neutral, revoked = red dashed. Header `DECISION · Switch approval · A1` (the card handle). | Action, target, short patch hash (copyable), tests, risk, rollback, expiry countdown, `request_id` (mono, truncated), then the outcome: `answered by user (IC) · 10:41:12Z · via Mattermost button` and the verification checklist after execution. Buttons: "Open card in Mattermost ↗", "Details" (opens the Approval tab). **There is no Approve button.** |
| **Execution status** | `BB · evt` (the executor) | Steel rail; `EX` lock avatar; a mini stepper inline | "Verifying decision… ✓ 7/7 checks", "Deploying C6 as #843", "Verifying 2/3 samples passing"; failures in red with the reason |
| **State change** | `BB · evt` | A thin divider row: `── state: AWAITING_APPROVAL → APPROVED · by Recovery · 10:41:13Z ──` | |
| **Security event** | `BB · evt` | A red outline row with a shield-alert icon | "Handoff rejected: sender mismatch", "tool denied", "outbound message blocked (secret pattern)" |
| **System (Control)** | `SW · seq` | Slate rail; 12 px text; `SYSTEM` tag | Alerts in intake, watchdog nudges, state notices |

**Grouping and threading:**

- Consecutive rows from the same actor within 2 minutes are grouped.
- Switch `thread_id` replies are indented under their root, with a "3 replies" collapse.
- Handoff ACKs are merged into their handoff card.

### 2.3 Right panel tabs

The tabs are summarized versions of screens 4–6. Each has a "pop out ↗" to the full screen:

- **Root cause:** cause card, evidence, ruled-out items.
- **Diff:** a compact diff of the current remediation.
- **Tests:** counts, new tests, failures.
- **Plan:** recovery plan, rollback, risk rationale.
- **Approval:** the binding, the Switch status and the verification checklist.

### 2.4 Bottom execution strip (always visible)

`Decision state` · `Executor state` · `Deployed SHA / deploy id` · `Live health (status + 5xx + p50)` · `Reply in Mattermost ↗`

The strip changes color only for decision-pending (amber), executing (blue), verified (green) and failure or rollback (red).

### 2.5 Specification table

| Aspect | Specification |
|---|---|
| **Information hierarchy** | 1) Header state, severity and live indicator → 2) decision card when present (also pinned at the top of the feed as "Pending decision" when scrolled away) → 3) feed → 4) execution strip → 5) investigation tabs → 6) overview details |
| **Interactions** | Filter chips (multi-select) and text filter. "Jump to live". Click a provenance badge → raw-event drawer (Switch JSON or BLACKBOX event). Click an evidence chip → the Investigation screen anchored to that item. Click a chart marker → scroll the feed. Copy hash, SHA or `request_id`. "Open room in Mattermost ↗". **Revoke approval** (IC role; confirm dialog stating "Revocation only stops execution; it never starts one"). Keyboard: `j/k` move between rows, `f` focuses the filter, `g l` jumps to live, `1–5` switch right tabs. |
| **Data sources** | `/api/incidents/{id}/context`, `/timeline` (backfill), `/api/stream?incident=` (SSE), `/approvals`, `/executions`, `/remediations`, metrics SSE |
| **Loading** | The header renders from context first. Feed skeleton rows (6). "Backfilling Switch history… (n events)". Charts start "collecting". |
| **Empty** | New incident with no feed yet: "Waiting for first Switch event in #incident-…". No remediation: the Diff and Tests tabs say "RootCause hasn't staged a remediation yet", with the latest RootCause milestone. No decision: the strip says "No decision requested". |
| **Errors** | SSE drop → banner plus auto-reconnect, with backfill by `after_seq` (no duplicates: rows are keyed by `evt_seq` and `switch.sequence`). Room not found → "Incident room missing in Switch", with the incident still viewable from BLACKBOX records. Unparseable structured block → shown raw, with "⚠ unrecognized structured data". |
| **Responsive** | `lg` (≥ 1440): three columns plus the strip. `md` (1024–1439): overview collapses into a 56 px icon rail with a popover, and the right panel becomes a drawer toggled by tab buttons in the header. `sm` (< 1024): tabs `Feed · Investigation · Execution · Overview`; the decision card is pinned at the top of the Feed tab; the strip is a sticky bottom bar with 2 fields (decision, health). On phones the decision itself happens in the Mattermost mobile app (deep link). |
| **Accessibility** | The feed is a `role=log` live region. New decision cards are announced. Every row has an accessible name: "Sentinel, agent message, sequence 4802, 10:30:58". |

---

## 3. Agent Activity Monitor (`/agents`)

**Purpose:** observe and debug the agents: what each is doing, connected or not, and what it touched.

```
┌ AGENTS ──────────────────────────────────────────────────────────────────────────────┐
│ ┌ SN Sentinel ─────────────┐ ┌ RC RootCause ────────────┐ ┌ RV Recovery ─────────────┐ ┌ CT Control ┐ │
│ │ ● Switch WS live (seq…)  │ │ ● live · working INC-1042│ │ ● live · awaiting decision│ │ ● live      │ │
│ │ current: idle            │ │ step: TESTS_RUNNING 0:41 │ │ approval A1 expires 08:12 │ │ watchdog ok │ │
│ │ prompt v3 · sonnet-5-5   │ │ coding agent: claude_code│ │ prompt v2                 │ │             │ │
│ │ 24h: 3 incidents, 0 err  │ │ attempts 1/3 · 0 denied  │ │ executions 1 · refused 0  │ │             │ │
│ └──────────────────────────┘ └──────────────────────────┘ └──────────────────────────┘ └────────────┘ │
├ ACTIVITY (selected: RootCause) ─────────────────────────────────────────────────────────────────────┤
│ time      kind        detail                                 dur    result   incident            │
│ 10:31:07  switch.recv handoff HND-1042-001 (addressed)              accepted INC-1042            │
│ 10:31:08  tool        get_commit_diff C4..C5                  120ms  ok                           │
│ 10:31:15  tool        generate_patch (sandbox #7)             31.2s  ok                           │
│ 10:31:47  tool        run_regression_tests (net=none)         3.4s   27/27                        │
│ 10:31:49  switch.send send_targeted_message → recovery              event …                      │
│ 10:31:50  security    tool_denied: open_approval (scope)            denied   ⚠                    │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

| Aspect | Specification |
|---|---|
| **Components** | Agent cards (connection, current work with an SLA timer, model and prompt version, counters); activity table (Switch received and sent, tool calls, LLM calls with token usage, security events); per-agent permission summary (from `04-AGENT-PERMISSIONS.md`, read only); handoff Sankey-style counts (in/out); error log |
| **Hierarchy** | Connection health → current work → anomalies (denied tools, errors, stale) → history |
| **Interactions** | Select an agent to filter the table. Click a row → a drawer with the arguments hash, result summary, linked Switch event, and the prompt version. Filter by kind or incident. "Show permissions". No control actions in the MVP: pause and restart are operator CLI. |
| **Data sources** | `/api/agents` (runtime rows: `connection_id`, last heartbeat, cursor, state), the BLACKBOX event log (tool calls, security), the Switch event provenance |
| **Loading / Empty / Error** | Skeleton cards. "No activity in the selected window". An agent stale for > 10 s: amber card plus "last heartbeat 10:31:02Z (14 s ago)". Disconnected: red, with the last eviction code (`taken_over`, `heartbeat_lapsed` [V]). |
| **Responsive** | Cards in a 4-up grid → 2-up (`md`) → a stacked list (`sm`). The table becomes a card list on `sm`. |

---

## 4. Investigation and Root Cause Analysis (`/incidents/:id/investigation`)

```
┌ INC-1042 · Investigation ─────────────────────────────────────────────────────────────┐
│ CORRELATION TIMELINE  ─────────|deploy #842 10:30:24|────▲alert 10:30:42───▲handoff── │
│   5xx ____________/‾‾‾‾‾‾‾‾‾‾‾   p50 ____/‾‾‾‾‾‾   pool ___/‾‾‾‾‾ (onset 10:30:35)        │
├──────────────────────────────┬────────────────────────────────────────────────────────┤
│ EVIDENCE BOARD               │ ROOT CAUSE                                              │
│ ▸ metric  http_5xx 17.4%  SN │ config/database.yml:5  pool_size: 50 → 10                │
│ ▸ log     QueuePool…size 10 ×412│ introduced by C5 (deploy #842) "chore(db): reduce pool…"│
│ ▸ deploy  #842 [SIMULATED meta]│ mechanism: 120 rps × 0.1 s hold > 10 connections       │
│ ▸ config  database.yml, app.yml│ violates config/capacity.yml peak_concurrent=30        │
│ ▸ gap     none                │ confidence 0.91 (RootCause)                              │
│ HYPOTHESES                    │ RULED OUT                                               │
│ H1 #842 reduced pool  ▇▇▇▇▇▇▇ 0.74→confirmed│ log_level INFO→WARNING: explains missing INFO lines, │
│ H2 DB host degraded   ▇▇ 0.15 → rejected  │ not 5xx                                              │
└──────────────────────────────┴────────────────────────────────────────────────────────┘
```

| Aspect | Specification |
|---|---|
| **Components** | Correlation timeline (metrics plus deployment, alert and handoff markers); evidence board (grouped by kind, each item with source tool, query, value, collected-by agent, `[SIMULATED]` flag, raw-data link); hypothesis list (confidence bars, status: open, confirmed or rejected, linked evidence); root cause card; ruled-out list; evidence gaps panel |
| **Hierarchy** | Root cause (once known) → correlation timeline → evidence → hypotheses → gaps |
| **Interactions** | Hovering an evidence item highlights it on the timeline. "View raw" opens a drawer with log lines (mono, redacted) or a metric series. A file reference opens the Diff screen at that file. Clicking a hypothesis shows its supporting evidence. |
| **Data sources** | `/context` (hypotheses, evidence summary), the evidence store, metrics history, `deployments.jsonl` via the API, RootCause analysis records |
| **Loading** | "Sentinel collecting evidence (3/5 sources)…" with a per-source status |
| **Empty** | Before the handoff: "RootCause has not started. Evidence so far from Sentinel." Failure A (insufficient evidence): the gaps panel is prominent, listing what was checked and what was missing, plus the NEEDS_HUMAN prompt. |
| **Errors** | Source unavailable: the evidence item shows `gap: metrics unavailable` (explicit, never omitted) |
| **Responsive** | Two columns → stacked (root cause first). The timeline becomes horizontally scrollable on `sm`. |

---

## 5. Code Diff and Test Results (`/incidents/:id/remediation/:rid`)

```
┌ REM-1042-1 · attempt 2 of 3 ✓ · coding agent: claude_code (real) ───────────────────────┐
│ base 4be2…(C5) → commit 9c1e… · patch sha256:3f9a0c1d2e3b… [copy] · computed by BLACKBOX │
│ Attempts: [1 ▲ tests failed] [2 ✓ staged]                                                  │
├──────────────┬───────────────────────────────────────────────┬─────────────────────────────┤
│ FILES (3)    │ DIFF (unified | split)                         │ TESTS  27 passed · 0 failed │
│ M config/database.yml +1 −1 ✓│ @@ -2,7 +2,7 @@                   │ run TR-1042-1-2 · 3.4 s · net=none│
│ A tests/test_capacity.py +18 ✓│ - pool_size: 10                │ ★ NEW test_pool_capacity_meets… ✓ │
│ M src/…/config.py +9 −0 ✓    │ + pool_size: 50                  │   (fails on C5 ✓ verified)        │
│              │                                               │ test_payments.py 10/10 ✓    │
│ POST-CHECKS  │                                               │ test_api.py 6/6 ✓ …         │
│ ✓ paths allowlisted ✓ no test infra edits ✓ no deleted tests ✓ diff 28 lines ≤200      │
└──────────────┴───────────────────────────────────────────────┴─────────────────────────────┘
```

| Aspect | Specification |
|---|---|
| **Components** | Integrity strip (base, commit, hash, who computed it); attempt tabs; file tree with allowlist badges; diff viewer (§8 of the design system); tests panel (counts from JUnit, per-suite rollup, new tests starred, failures expandable with the assertion output, run metadata: duration, `network=none`, command); post-checks list; coding-agent brief (collapsible, so humans can see exactly what the agent was asked) |
| **Hierarchy** | Integrity and attempt status → the diff → test results → post-checks → the brief |
| **Interactions** | Unified or split toggle; expand context; jump to a file; copy the hash; "view on base"; compare attempts (diff of diffs); download `.patch` |
| **Data sources** | `/remediations/{rid}`: the patch artifact, the diff, the JUnit-derived results, post-checks, the brief, coding-agent metadata |
| **Loading** | "Coding agent working in sandbox #7 (0:31)": shows elapsed time, not a fake progress bar. "Tests running (net=none)…" |
| **Empty** | "No remediation staged". If RootCause is still diagnosing, a link to Investigation. |
| **Errors** | Failed attempt: a red attempt tab, failing tests expanded first, and the "next attempt in progress" status. All attempts failed (Failure B): a banner "NOT VALIDATED: never sent to Recovery", plus the NEEDS_HUMAN options. Fallback used: a persistent `[SIMULATED coding agent: deterministic fallback]` banner. |
| **Responsive** | Three panes → the file tree becomes a dropdown (`md`) → the diff gets the full width with tests in a tab (`sm`). Split view only at ≥ 1440. |

---

## 6. Human Approval Interface (`/incidents/:id/approval`)

**Purpose:** give the incident commander everything needed to decide, then send them to **the real decision point in Switch**, and afterwards show proof of what Switch recorded and what the executor verified.

```
┌ DECISION REQUIRED · INC-1042 ◆ ──────────────────────────────── expires 10:50:00Z  (08:12) ┐
│ You are approving:  Apply remediation commit 9c1e… to payment-service in STAGING-SANDBOX   │
│ ┌ WHAT CHANGES ────────────────┐ ┌ EVIDENCE ───────────────┐ ┌ RISK & ROLLBACK ───────────┐ │
│ │ config/database.yml 50←10    │ │ 5xx 17.4% · p50 3.9 s   │ │ Risk LOW (config-only, 28 ln)│ │
│ │ + regression test, + startup │ │ root cause conf 0.91    │ │ Rollback: revert to C5       │ │
│ │ validation  [view diff]      │ │ tests 27/27 ✓ (new ✓)   │ │ (pre-approved, automatic on  │ │
│ └──────────────────────────────┘ └─────────────────────────┘ │  failed verification)        │ │
│ BINDING  incident INC-1042 · remediation REM-1042-1 · patch sha256:3f9a0c1d2e3b4a59…(full) │
│          action apply_remediation_commit · env staging-sandbox · approvers: user (IC)      │
│          request_id INC-1042:REM-1042-1:3f9a0c1d2e3b4a59:staging-sandbox                   │
│ ┌ HOW TO DECIDE ──────────────────────────────────────────────────────────────────────────┐ │
│ │ Decisions are made in Switch. Press Approve or Reject on card A1 in                    │ │
│ │ #incident-payment-service-1042.   [Open in Mattermost ↗]   Typing "approve" does nothing.│ │
│ └─────────────────────────────────────────────────────────────────────────────────────────┘ │
│ SWITCH STATUS   ◆ open (live)   ·   history: opened 10:39:58Z by Recovery                    │
│ AFTER DECISION  executor verification: ○ read-back ○ hash ○ request_id ○ approver           │
│                  ○ not expired ○ not revoked ○ first use                                     │
│                                                       [Revoke…] (IC only; stops, never starts)│
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

| Aspect | Specification |
|---|---|
| **Components** | Decision header with a countdown; a three-card summary (changes, evidence, risk and rollback); the binding block (all six elements, mono, copyable; the full hash is shown); a "How to decide" block with the deep link; a live Switch status (open → answered → …) with `answered_by` / `answered_at`; the executor verification checklist (7 checks, filled in after the decision); the Revoke action; an approval history for the incident (all attempts, `:r2`, previous rejected remediations with their hashes) |
| **Hierarchy** | 1) What exactly is being authorized (action + target + hash) → 2) why (evidence, tests) → 3) risk and rollback → 4) where to decide → 5) status and proof |
| **Interactions** | "Open in Mattermost ↗" (channel deep link; the card handle is shown because a per-post permalink is **UNKNOWN**: Switch doesn't expose the card's post id to agents); "view diff"; copy the binding; **Revoke** (IC role: modal "Revoke approval for REM-1042-1? This prevents execution if it hasn't started. If execution has started, the pre-approved rollback runs." → writes a revocation; Recovery closes the open card [V]). **There is no approve button.** |
| **Data sources** | `/approvals` (binding, Switch outcome, verification record), `/remediations`, `/context` |
| **Loading** | The binding renders first (from the store); then "Reading status from Switch…" |
| **Empty** | No approval requested: "No decision pending. Recovery requests approval after validation." Previous decisions are listed. |
| **Error / edge states** | **Expired** (Switch pushed `expired` [V]): neutral card, "Expired at 10:50:00Z, never executed", plus the re-request status. **Rejected:** red, "Rejected by user 10:41:12Z", then "Recovery asked for feedback in the card's thread", plus a revision link. **Answered by a non-allowlisted id** (policy misconfiguration): red, "Switch accepted an answer from X, who is not an authorized approver. Executor refused." **Switch unreachable:** "Cannot read decision status; execution is blocked (fail-closed)." **Bridge degraded:** "Your click may not reach Switch; wait for the bridge." |
| **Responsive** | Cards stack. The binding block wraps with horizontal scroll for hashes. On phones, "Open in Mattermost" opens the Mattermost app. |

---

## 7. Recovery Execution Monitor (`/incidents/:id/execution`)

```
┌ EXECUTION EXR-1042-1 · approval APR-1042-1 · deploy #843 ───────────────────────────────┐
│ STEPS                                  │ LIVE HEALTH (15 m, markers: #842 ▲ approve ◆ #843 ●) │
│ ✓ Decision read back from Switch 0.3 s │ 5xx  ‾‾‾‾‾‾‾‾\______  17.4% → 0.2%                  │
│ ✓ Patch hash matches (frozen copy)     │ p50  ‾‾‾‾‾‾‾‾\______  3.9 s → 108 ms                 │
│ ✓ request_id / approver / expiry / revocation / first-use │ pool ‾‾‾‾‾‾‾\______  100% → 24%   │
│ ✓ Release staged (tree hash ok)        │──────────────────────────────────────────────────│
│ ✓ Deployed C6 · ready 2.1 s · sha ok   │ BEFORE (10:41:10Z)      AFTER (10:42:05Z)          │
│ ◐ Flushing window (15 s)               │ 5xx 17.4% · p50 3912ms   5xx 0.2% · p50 108ms       │
│ ○ Verify 3 consecutive samples  1/3    │ pool 100% · 1802 req     pool 24% · 1796 req        │
│ ○ Resolved                             │ (measured samples, click for raw)                 │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

| Aspect | Specification |
|---|---|
| **Components** | Execution stepper (verification checks are expanded individually); a live chart trio with event markers; a samples table (every 5 s sample with pass or fail per criterion); before/after tiles bound to recorded samples; a rollback panel (appears only when triggered); a refusal panel (when the executor refuses) |
| **Hierarchy** | Current step → health trend → before/after → details |
| **Interactions** | Click a step to see its detail (raw verification record, launcher response). Click a sample to see its JSON. "View deployment record". No controls: execution is not user-driven from the UI, except Revoke (which moves to abort and rollback). |
| **Data sources** | `/executions` (executor events), metrics SSE, `deployments.jsonl` via the API |
| **Loading** | "Awaiting approved decision" (not started); step-level spinners are replaced by ◐ |
| **Empty** | "No execution. Execution begins only after a verified Switch approval." |
| **Errors** | **Refused:** a red panel listing the failed check, e.g. "answered_by not authorized", "hash mismatch (frozen 3f9a… vs worktree 77c1…)", "approval expired 10:50:00Z". **Deploy failed:** "Launcher reverted to C5: readiness timeout 20 s", with the logs link. **Verification failed:** "0/3 passing after 60 s: rolling back", then the rollback step list, then the "ROLLED BACK" state, with the samples kept. Never shows "Recovered" without 3/3. |
| **Responsive** | Two columns → stepper above the charts → charts become sparklines with numbers (`sm`) |

---

## 8. Incident Postmortem (`/incidents/:id/report`)

Document-style. Max width 880 px, centered. Light theme when printing.

| Section | Content (all generated from the event log) |
|---|---|
| Header | INC-1042 · payment-service · SEV-1 · duration (detected → verified) · report version · **"Audit chain verified to evt 412 · anchored in Switch seq 4840"** · generated at |
| Executive summary | 3–4 sentences (Recovery's narrative, with numbers from the log; numbers are hyperlinked to their samples) |
| Impact | The 5xx-rate × duration estimate of failed payments (from the counters), the peak p50, the window |
| Timeline | A table of consequential events (alert, handoffs, root cause, tests, approval opened and answered, execution, verification) with provenance badges |
| Root cause | File, line, commit, mechanism, capacity violation; ruled-out items |
| Agent contributions | One row per agent: what it did, its handoffs, its durations |
| Remediation | Diff (collapsed), hash, tests (counts and the new test) |
| Approval record | `request_id`, approver, answered at, via, expiry, executor verification checklist |
| Verification | Before/after samples and the charts |
| Lessons learned | Bullets |
| Preventive tasks | A table: task, owner role, link, status |
| Simulation disclosure | The real vs. simulated table |

| Aspect | Specification |
|---|---|
| **Interactions** | Version switcher; export Markdown or PDF (print stylesheet); copy the link; "Published to Switch room at seq 4842 ↗" |
| **Data sources** | `/report?version=` |
| **Loading / Empty** | "Report is generated after verification." If the incident isn't resolved, a "draft" watermark appears and the incomplete sections are listed. |
| **Errors** | Chain verification failed: a red header "INTEGRITY FAILURE: event log does not verify after evt N"; the report is still shown, but marked untrusted. |
| **Responsive** | A single column at all sizes; the timeline table scrolls horizontally on `sm` |

---

## 9. Incident History (`/history`)

```
┌ INCIDENT HISTORY ─ filters: [Service ▾][Severity ▾][Outcome ▾][Date ▾][☐ include sample data] ⌕ ┐
│ ID        Service          Sev   Outcome        Detected     MTTR    Approvals  Revisions  Run    │
│ INC-1042  payment-service  SEV-1 ✓ Resolved     10:30:42Z    11m 23s 1/1 ✓      0          r-07   │
│ INC-1041  payment-service  SEV-2 ↶ Rolled back  09:12:10Z    —       2 (1 ✗)    2          r-06   │
│ INC-0990  checkout-api     SEV-2 ✓ Resolved     SAMPLE DATA  14m     …                     seed   │
└──────────────────────────────────────────────────────────────────────────────────────────────────┘
```

| Aspect | Specification |
|---|---|
| **Components** | Filterable and sortable table; summary row (count, median MTTR, rollback rate: real incidents only by default); row drawer (mini timeline, report link, preventive-task status); "runs" grouping (each demo reset archives a run, `05-SIMULATION-ARCHITECTURE.md` §17) |
| **Interactions** | Search by ID, service, commit or hash. Open a report. Compare two incidents (side-by-side timelines). Export CSV. |
| **Data sources** | `/api/incidents?…` (including archived runs), report index |
| **Loading / Empty / Error** | Skeleton rows / "No incidents yet: trigger one from the dashboard (sandbox)" / "Couldn't load history" with retry |
| **Responsive** | The table collapses to cards on `sm` (ID, outcome, MTTR, date); filters move into a sheet |
