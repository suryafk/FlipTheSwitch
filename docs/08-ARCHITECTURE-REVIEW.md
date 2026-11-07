# 08 — Independent Architecture Review

| | |
|---|---|
| **Scope** | Deliverables 00–07 (17 documents), plus the live Switch verification S1–S7 |
| **Reviewer stance** | Adversarial. The design team's claims are tested against evidence, effort and the 72-hour constraint. |
| **Verdict summary** | The **governance core is sound and unusually well evidenced**. The **scope is roughly 2–3× what 72 hours supports**. The **coding-agent path and the demo host are unproven**, and they are what the demo's credibility rests on. See [`08-GO-NO-GO.md`](08-GO-NO-GO.md). |
| **Companions** | [`08-IMPLEMENTATION-BACKLOG.md`](08-IMPLEMENTATION-BACKLOG.md) · [`08-CRITICAL-PATH.md`](08-CRITICAL-PATH.md) |

Rating scale:

| Rating | Meaning |
|---|---|
| 🟢 | Sound |
| 🟡 | Acceptable with changes |
| 🔴 | Must change before implementation |

---

## 1. Evaluation by area

| # | Area | Rating | Findings |
|---|---|---|---|
| 1 | **72-hour product feasibility** | 🔴 | The designed scope (9 screens, 4 agents, executor, launcher, monitor, loadgen, sandbox containers, egress proxy, hash-chain anchoring, revocation, expiry re-request, 3 revision cycles, reset with Switch cleanup) is about **270+ engineer-hours** (backlog totals: P0 ≈ 140 h, P1 ≈ 60 h, P2 ≈ 70 h). The design never states team size, so the doc set over-promises. **It must be cut to the P0 backlog**, which needs about 3 engineers × 3 days (§5). |
| 2 | **Correctness of Switch assumptions** | 🟢 | This is the strongest part of the work. Every load-bearing Switch behavior was exercised live: registration, WebSocket, rooms with the bridge, addressed vs. overheard, targeted handoff, approval cards, answerer authorization, the content-conflict 409, expiry, outcome push and pull, resume, eviction, and document ownership. The design correctly abandoned the brief's assumed Switch "Tasks" and "Decisions". **Residual:** the approval API is undocumented for this use (pin `0.31.0`); room-name reuse after archive is UNKNOWN; the message-size limit is UNKNOWN; the API for changing addressing policy is UNKNOWN; the per-card Mattermost permalink is unavailable. |
| 3 | **Actual multi-agent collaboration** | 🟡 | All agent-to-agent traffic goes through Switch, verified. **But the choreography is a one-way pipeline** (Sentinel → RootCause → Recovery), with deterministic gates around every LLM. A sceptical judge could call it "a scripted pipeline with LLM narration". **Required:** at least one genuine **two-way exchange** in the happy path. For example, RootCause asks Sentinel a clarifying question ("pool utilization at onset and the deploy timestamp delta?"), and Sentinel answers with a tool-backed fact, all in Switch. This costs about 3 h and materially improves the "collaboration" story. |
| 4 | **Agent responsibility separation** | 🟡 | The contracts are clear and the separation-of-duties invariants are good. **Weakness:** separation is enforced only inside BLACKBOX. In Switch all agents share one **admin** owner, so a compromised agent *process* (not just its LLM) can call any Switch operation. That is acceptable for a sandbox demo only if **disclosed**, which the docs do. **Weakness:** the executor holds Recovery's Switch key, so the "executor cannot open approvals" property rests on code discipline. It is documented; accept it for the MVP. |
| 5 | **Shared context consistency** | 🟡 | The model is right (the BLACKBOX store is authoritative; Switch is the visible medium and the decision anchor). **Over-built:** the Control-owned state document and the task-board room document duplicate the store, and add a third copy that can drift. **Cut them from the MVP.** **Missing:** what happens when the store write succeeds and the Switch post fails (or the reverse). The rule must be "store first, post with retry; the receiver requires both; the watchdog re-posts an unacknowledged handoff with the same id". It is implied but never written as an invariant. |
| 6 | **Event delivery and reliability** | 🟡 | Resume semantics are verified. **Gaps:** (a) the UI feed depends on Control's WebSocket. If Control drops for more than 6 s, it must backfill with `read_context` rather than trusting `start_from` alone. This is not specified. (b) Human input is lost during a switch-core restart (verified); the only mitigation is operational. (c) There is no explicit "one active incident at a time" guard, and concurrent incidents multiply all of these risks. **Add a single-incident lock to the MVP.** |
| 7 | **Human approval security** | 🟢 / 🔴 | The executor verification is rigorous and the chat-text non-authorization is proven. **Must fix (🔴):** the approval card shows **only a hash and metadata**. A human cannot verify a hash. In practice they approve whatever the BLACKBOX UI shows next to it, which makes BLACKBOX's UI part of the trusted path. **Put the actual diff excerpt (≤ 1 500 characters, rendered as a code block) into the card's `detail`**, so the decision content stored in Switch *contains the change being approved*. The free-text "expected outcome" written by the LLM must be rendered as quoted text (prompt-injection risk) and capped. |
| 8 | **Coding-agent integration** | 🔴 | **Entirely unproven.** No `claude -p` run has happened. The design assumes that tool restriction is enforced, that `stream-json` is parseable, that runs take ≤ 60 s, and that runs cost ≤ $0.50. **Undocumented assumptions:** (a) whether Claude Code's Read tool can read **outside the working directory** (`~/.ssh`, the Switch `.env`) under `--permission-mode acceptEdits`. That decides whether a container is P0. (b) Auth: the local CLI is logged in with the *user's personal account*. Using it unattended in a product demo may differ from API-key auth, and `--bare` requires an API key. **SP-1 is a hard gate.** |
| 9 | **Sandbox isolation** | 🟡 | The test runner design (`network=none`, read-only, no secrets) is cheap and correct: keep it as P0. **The coding-agent container with an egress allowlist proxy is over-engineering for 72 h** *if* SP-1 shows that `--tools Read,Edit,Grep,Glob` with cwd scoping cannot read outside the worktree. In that case run the CLI on the host with a scrubbed env (P0) and add the container as P1. If SP-1 shows it *can* escape, the container becomes P0 and the proxy is replaced by `--network` restricted to the host firewall. **The Docker socket** (needed by whatever launches the sandboxes) is root-equivalent. Confine it to one tiny "sandbox runner" service, and never mount it into agents. |
| 10 | **Incident state management** | 🟡 | The corrected state machine is correct, but it has **22 states**. The demo exercises about 12. **Keep the transition table complete** (it is cheap) but implement *handlers* only for the happy path plus reject and rollback. `REOPENED`, `APPROVAL_EXPIRED` → `:r2` re-request, `REVOKED` after approval, and three-cycle revision limits are P1 or P2 handlers that move the incident to `NEEDS_HUMAN` in the MVP. |
| 11 | **Auditability** | 🟡 | An append-only event log with SQLite triggers is right and cheap (P0). A **hash chain is P1**. **Anchoring chain heads into the Switch room is P2.** It adds noise to the war room and buys little in a sandbox. Report-from-log is P0 and correct. |
| 12 | **Frontend complexity** | 🔴 | Nine screens, a complete design system, keyboard maps, presenter mode, seeded history, compare views. Realistically about **60–70 h** of frontend work. **MVP: three surfaces** (≈ 28 h). (1) **War room** with the feed, execution strip and right-panel tabs (root cause, diff, tests, approval). (2) A **dashboard-lite**: service health charts, the trigger button, the active incident. (3) The **report**. The Agent Monitor, the full Investigation page, the Execution page, History and presenter mode are cut or folded into tabs. |
| 13 | **Demo reproducibility** | 🟡 | Deterministic repo SHAs, seeded load and the reset design are good. **Risks:** the coding agent is nondeterministic (acceptable: assert properties, not diffs). The reset touches Switch (archive rooms, close approvals), which is the most fragile part; it is acceptable if rooms get a run suffix by default (avoids the UNKNOWN name reuse). **The Mattermost emulation hang reproduced 3 times.** Reproducibility is *currently not achieved on this host*. |
| 14 | **External dependencies** | 🟡 | Required: Switch 0.31.0 images (pulled and verified), Mattermost 11.9 (amd64 only, unstable here), the Anthropic API plus the Claude Code CLI (unproven), Docker, Postgres. **Unnecessary for the MVP:** a Squid/tinyproxy egress proxy, Codex, MCP, Recharts for anything beyond three line charts (keep it, but only there), WorkOS/OIDC, GHCR tag discovery tooling. |
| 15 | **Operational failure handling** | 🟡 | Fail-closed is designed thoroughly. **Over-designed for the MVP:** rejection feedback in the thread with a 10-minute wait, expiry re-request, revoke-to-abort-and-rollback, auto-clear of transient alerts, and watchdog nudges with 2-step escalation. **MVP:** fail closed, roll back on failed verification, and send everything else to `NEEDS_HUMAN` with a clear room message. |

---

## 2. Over-engineered components (cut or defer)

| Component | Where | Decision |
|---|---|---|
| 9-screen frontend, full design system, keyboard map, presenter mode, compare views | 06-* | **Cut to 3 surfaces** (P0). The rest is P2. |
| Egress allowlist proxy for the coding agent | 04 §5, SP-2 | **P1/P2**, depending on the SP-1 result |
| Hash-chain anchoring into the Switch room | 04 §11 | **P2** |
| Control-owned state document and task-board document | 03 §2.1, 03-HANDOFF §3 | **P2**. The UI and the store are enough. |
| Rejection: thread question with a 10-minute wait for feedback | 03-CONTRACTS C | **Simplify.** The revision handoff carries "no reason given" unless the IC has already replied. P1. |
| Expiry re-request (`:r2`) | 04-APPROVAL §4.1 | **P2**. Expiry → `NEEDS_HUMAN`. |
| Revoke via chat command | 04-APPROVAL §4.2 | **P2**. Keep the UI revoke (P1) and the executor's revocation check (P0, cheap). |
| Watchdog nudge plus escalation ladder | 03 §5 | **P1**, a single nudge |
| Separate processes for 4 agents, the executor, the api, the launcher, the monitor, loadgen, payments-db and the UI, next to 5 Switch containers, on a 2-CPU VM | 03 §2.3, 05 §18 | Keep **process** separation (it is a security property). Run the 3 LLM agents as **3 processes from one image** (one Compose service each, sharing the image) and **merge `monitor` into the simulation-control process**. The constraint is resources, not architecture. |
| Investigation page correlation-timeline visualization | 06 §4 | **Fold into a tab** (evidence list plus root-cause card) |
| Seeded "sample data" history and MTTR analytics | 06 §1, §9 | **P2** |
| Failure-demo knobs (`SIM_*`) | 05-DEMO §6 | Keep only what E2E tests need: **reject** and **launcher fail** are P1; the rest P2 |

## 3. Missing components (add before or during implementation)

| Missing | Why it matters | Priority |
|---|---|---|
| **LLM runtime decision**: how agents run their tool loops | Undecided between the raw Anthropic Messages API, the Agent SDK, or the Claude Code CLI for every agent. **Recommendation:** agents use the **Anthropic Python SDK Messages API with a ~150-line tool loop** (simple, testable, typed tools). Only RootCause's *coding step* uses the Claude Code CLI. | P0 (decision, 0.5 h) |
| **Shared agent runtime library** | The common Switch connection, dispatcher, tool registry, untrusted-content tagging and budget caps. Without it, three agents duplicate about 1 000 lines. | P0 |
| **Bootstrap script** | Mint a registration key, register 4 agents, create intake, capture the IC's exact participant id for `approver_allowlist`, write per-service env files. Exists only as prose. | P0 |
| **Diff excerpt in the approval card** (§1.7) | Humans must see *what* they approve inside the Switch decision | P0 |
| **Single-active-incident lock** | Prevents concurrency bugs in a demo-scale system | P0 |
| **UI backfill through `read_context`** when Control's stream has gaps | Feed correctness | P0 |
| **Store-then-post handoff invariant** written into the code contract | Consistency | P0 |
| **Outbound secret scanner** (simple regex) | Cheap, and blocks the worst prompt-injection outcome | P0 |
| **Spend and turn caps per incident** for all LLM calls | Cost runaway, budget during rehearsals | P0 |
| **Rehearsal and recording plan with a pre-recorded fallback video** | A live demo on an unstable host needs a safety net | P0 |
| **The required docs** (`README`, `ARCHITECTURE`, `AGENTS`, `DEMO`, `.env.example`) | `Blackbox.md` §18 mandates them; the backlog had omitted them | P0 (light) |
| A two-way agent exchange (§1.3) | Collaboration credibility | P1 |
| Model and prompt version recording | Auditability of agent behavior | P1 (cheap) |

## 4. Undocumented or under-documented assumptions

| # | Assumption | Status | Action |
|---|---|---|---|
| A1 | Claude Code's tools can't read outside cwd in `-p` mode | UNVERIFIED | SP-1 probe `Read ~/.ssh/config` |
| A2 | The local `claude` login may be used unattended for the demo; API-key auth works in `--bare` | UNVERIFIED | SP-1; prefer an API key with a spend cap |
| A3 | The SQLAlchemy async pool's timeout message contains `QueuePool limit of size 10` (async engines use `AsyncAdaptedQueuePool`) | UNVERIFIED | SP-5. Sentinel's log signature must match whatever is really emitted. |
| A4 | One uvicorn worker holds 120 rps on the demo host | UNVERIFIED | SP-5 |
| A5 | Switch/Mattermost accept a post of about 4 000 characters | UNVERIFIED (Mattermost default max is about 16k) | Test in B-02 |
| A6 | An archived room's name can be reused | UNKNOWN | **Avoid:** always suffix with the run id |
| A7 | Bot identities for agents in new rooms are created fast enough (card rendering relies on them) | Observed OK [V], but under emulation it is slow | Pre-warm by having agents join intake at bootstrap |
| A8 | The demo host clock is shared by Switch, BLACKBOX and the simulation | True (one host) | Document it |
| A9 | Approval read-back after `/delivered` uses an identical re-open (valid only before expiry) | Verified 422 after expiry [V]; before expiry unexercised after delivered | SP-4 V-10 |
| A10 | Team size and skills | **Never stated** | §5 assumes 3 engineers |

## 5. Security weaknesses (ranked)

1. **The approval card lacks the diff:** humans approve a hash (§1.7). **Fix: P0.**
2. **The coding agent may read host secrets** if tool scoping doesn't hold. Mitigated by SP-1 → container if needed. **Gate: P0.**
3. **Agents inherit Gateway-admin authority in Switch.** Disclosed. PROD fix.
4. **The executor holds Recovery's key.** Disclosed. PROD approval-reader.
5. **The Docker socket for sandboxes.** Confine it to one service, never agents. **P0 rule.**
6. **LLM-authored free text inside the approval card** could socially engineer the approver. Cap it at 300 characters, render it quoted, and keep the deterministic fields first. **P0.**
7. **Audit tamper-evidence is weak without a hash chain.** P1.

## 6. Architectural bottlenecks

| Bottleneck | Effect | Mitigation |
|---|---|---|
| Coding-agent latency (one serial step, 30–90 s) | Dominates the demo timeline | SP-1 timing; a small brief; a marked time skip |
| A single small Docker VM running emulated Mattermost plus 120 rps load plus LLM sandboxes | Crashes, noisy metrics | ≥ 4 CPU / 6 GB, or an amd64 recording host; the 60 rps profile |
| The Mattermost bridge as the only human channel | Lost clicks during restarts | Never restart mid-run; a banner |
| SQLite single writer | Fine at demo scale | WAL; a single incident |

## 7. Remove from the MVP

The Agent Activity Monitor page · the History page · presenter mode · keyboard shortcuts · seeded analytics · the compare-attempts view · chat-command revoke · expiry re-request · thread feedback waiting · Control-owned room documents · Switch hash-chain anchoring · the egress proxy (conditional) · Codex · MCP · multi-incident support · the light/print theme · `SIM_*` knobs beyond reject and launcher-fail.

## 8. Add before implementation (blocking)

1. SP-1 result (Claude Code) and the A1/A2 decisions.
2. Demo host decision (SP-7) or an amd64 host.
3. SP-5 calibration on the chosen host (including A3, A4).
4. The LLM runtime decision (§3).
5. The diff excerpt in the card (a design change, folded into the backlog).
6. A team-size assumption, written down with owners per lane.
