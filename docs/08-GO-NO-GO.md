# 08 — GO / NO-GO Recommendation

| | |
|---|---|
| **Recommendation** | **CONDITIONAL GO** for the reduced P0 scope (`08-IMPLEMENTATION-BACKLOG.md`), with a team of 3 engineers and blocking gates at hours 4 and 12. **NO-GO** for the full designed scope (00–07) in 72 hours. **NO-GO** for a single engineer at P0 scope. |
| **Basis** | The architecture review ([`08-ARCHITECTURE-REVIEW.md`](08-ARCHITECTURE-REVIEW.md)); live Switch verification S1–S7 on 2026-11-07 (Switch `0.31.0`); the critical path ([`08-CRITICAL-PATH.md`](08-CRITICAL-PATH.md)) |
| **Implementation may begin** | Only after gates **G1** (SP-1) and **G2/G3** (host, calibration) have a **verified result or an explicitly accepted fallback**, as required by the review mandate |

---

## Conditions attached to the GO

1. **Team of ≥ 3 engineers**, assigned to the lanes A, B and C. With 2, use the duo cut and accept zero slack.
2. **G1, Claude Code (SP-1):** passes, **or** the team accepts "fallback-primary" with `[SIMULATED coding agent]` labelling in the room, the UI and the report.
3. **G2, demo host:** the 15-minute soak is stable, **or** recording moves to an amd64 host.
4. **G3, outage physics (SP-5):** the measured ranges are met on the chosen host, or the 60 rps profile is used. Numbers are never scripted.
5. **Scope discipline:** everything in review §7 ("Remove from MVP") stays out until the hour-48 end-to-end run is green.
6. **Design change adopted:** the approval card carries a diff excerpt, and LLM free text in the card is quoted and capped.

---

## Required answers

### 1. Can BLACKBOX be built in 72 hours?

**The reduced P0 scope can; the full design cannot.**

- P0 is about **141 engineer-hours**, against about **144 productive hours** for three engineers. That is feasible with zero slack and strict gates.
- The full designed scope (P0 + P1 + P2 ≈ 270 h) is not feasible in 72 hours with any realistic team.
- One engineer cannot deliver P0. Two can deliver the duo cut (about 95 h), at real risk.

### 2. Which Switch integrations have been technically verified?

These were verified live against Switch `0.31.0` with the bundled Mattermost (S1–S7, 56 checks):

- **Agent registration** with a personal registration key (owner = the incident commander), and per-agent API keys.
- **WebSocket event subscription** (`/connection/ws`, protocol 9): ping/pong heartbeat; eviction codes `taken_over` and `heartbeat_lapsed`; resume with the same `connection_id` and `start_from`, with no loss and no replay; `gap` on a bad cursor.
- **Room creation** via `create_room`, producing a bridged Mattermost channel with all agents and the incident commander.
- **Messages:** `post_message`, `send_targeted_message` (with `target_statuses`), `POST /agents/{id}/message`, and history through `read_context`.
- **Addressed vs. overheard** delivery (`payload.addressed`).
- **Agent-to-agent structured handoff:** fenced JSON round-trips intact; the body is prefixed with `@target`.
- **Shared context:** the `connect_to_room` payload; room documents (create, late-joiner listing, load, owner-only update and delete, unique names, 1 MiB cap).
- **Decisions via approval requests:**
  - the card renders with buttons and a typed handle;
  - the server authorizes the answerer (a stranger is refused);
  - plain-text `APPROVE INC-…` does nothing;
  - the content is bound to the `request_id` (409 on change);
  - expiry is enforced and pushed;
  - agent close works;
  - an agent cannot answer (401);
  - the `approval_outcome` push arrives in about 0.04 s;
  - outcomes can be pulled via `GET`, re-read via an identical re-open (422 after expiry), and acknowledged with `/delivered`.
- **Confirmed absent:** Switch Tasks (no task operations among the 53 operations). There is no "decision" primitive besides approvals.

### 3. Which integrations remain uncertain?

| Integration | Uncertainty | Resolved by |
|---|---|---|
| Claude Code headless | Reliability, latency, cost, tool enforcement, reading outside cwd, unattended auth | SP-1 (G1) |
| Coding-agent sandbox | Whether the CLI works with a read-only rootfs and through a proxy | SP-2 (P1, or P0 if SP-1 shows an escape) |
| Test runner | Low risk, but not yet run | SP-3 |
| Executor composition | The read-back ordering around `/delivered` and expiry; the refusal matrix | SP-4 |
| Outage physics | The measured ranges; the async-pool error text (A3); 120 rps on one worker (A4) | SP-5 (G3) |
| Demo host | Emulated Mattermost hung 3 times | SP-7 (G2) |
| Switch details | Room-name reuse after archive (avoided by suffixes); maximum message size; the addressing-policy API; a per-card permalink (unavailable) | Workarounds designed |

### 4. What is the biggest implementation risk?

**The Claude Code coding step**, in two ways:

- **Technically:** it is unproven for unattended, restricted, timely execution.
- **Narratively:** it is the demo's centerpiece (0:55–1:30), so any weakness there undermines the "it fixes them" promise.

A close second is the **demo host**: emulated Mattermost has crashed three times, and without the chat bridge no human approval can happen. Both have defined fallbacks (a labelled deterministic patch; an amd64 host). Neither fallback can be improvised late, so both are gated in the first 12 hours.

### 5. What is the minimum viable demonstration?

One uninterrupted run from a clean reset:

1. **Trigger** deploys real commit C5. Real 120 rps traffic degrades the service (measured 5xx, p50 and pool).
2. The **monitor alert** fires from measured data. Control posts it in Switch, and Sentinel overhears it.
3. **Sentinel** creates the incident room in Switch and posts the evidence: deploy #842, the pool-timeout log signature, the pool utilization. It sends a **structured handoff** to RootCause through Switch.
4. **RootCause** acknowledges, finds `config/database.yml` `pool_size 50→10`, and produces a **real patch plus a regression test**: Claude Code, or the labelled fallback. It runs **real tests** (`network=none`) and hands off to Recovery through Switch.
5. **Recovery** validates and opens a **Switch approval card** with the action, target, hash, diff excerpt, tests and rollback.
6. *(Five-second proof)* typing `APPROVE INC-1042` does nothing. The incident commander **presses Approve** in Mattermost.
7. The **executor** independently reads the decision back from Switch and checks the binding. It deploys the exact commit. **Measured recovery** follows: 3 consecutive passing samples.
8. A **report generated from the event log**, with preventive tasks, is posted to the room and shown in the UI.

The UI needed for this is the **war room** (feed, tabs, execution strip), the **dashboard-lite** and the **report**.

### 6. What should the team absolutely avoid building?

- More than three UI surfaces: no history, agent monitor, presenter mode, keyboard maps or seeded analytics.
- Kubernetes, Kafka, Redis, Elasticsearch, real Prometheus or Grafana, MCP layers, or a Codex adapter.
- An egress proxy or microVM sandbox, unless SP-1 forces containment, and then only the simplest container.
- Anything that **authorizes from chat text**, or a UI "Approve" button.
- A BLACKBOX chat composer that posts as the human.
- **Modifying Switch**, or relying on undocumented Switch features beyond the verified set.
- Native Switch tasks (they don't exist), multi-incident support, or GitHub PR integration.
- Hash-chain anchoring in Switch, or room-document mirrors of state.
- Any **scripted or hardcoded metric, test count or success message.**

### 7. What must be completed during the first 12 hours?

1. **SP-1 Claude Code verdict** (H4), including the reading-outside-cwd and auth probes, and the resulting **decision log** (P0-02): host vs. container, fallback-primary or not, LLM runtime = Anthropic SDK tool loop, model ids, lanes.
2. **Demo host:** resized and soaked (SP-7), or an amd64 host provisioned.
3. **Demo repo and app skeleton, plus the SP-5 calibration** on that host: HEALTHY and DEGRADED ranges measured, and the async-pool error text confirmed.
4. **Bootstrap script** (stack up; 4 agents registered; intake room; incident commander id captured; env files) and **SwitchConnection** passing the S3/S4/S6 cases.
5. **Schemas and the api core skeleton** with the transition table and the single-incident lock.
6. **Gate H12 review:** every one of G1, G2 and G3 either passed or has its fallback explicitly accepted and written down. If not → stop and re-plan; do not proceed into agent implementation.

---

## Sign-off

| Role | Decision |
|---|---|
| Principal Architect (review) | **Conditional GO**, subject to the conditions above |
| Blocking items open at the time of writing | G1 (SP-1), G2 (SP-7), G3 (SP-5): **not yet run** |
| Re-review | At H12 (gates) and H48 (first end-to-end run) |
