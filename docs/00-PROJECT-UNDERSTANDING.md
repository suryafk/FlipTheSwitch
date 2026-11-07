# 00 — Project Understanding: BLACKBOX

| | |
|---|---|
| **Status** | Pre-implementation analysis. No code written. |
| **Source of truth reviewed** | `Blackbox.md` (repository root; the only file in the workspace) |
| **Author role** | Principal Software Architect |
| **Date** | 2026-11-06 |

> **Critical finding up front.** `Blackbox.md` instructs us (§1, §3.1, §18) to inspect "the available project repository, Switch documentation, APIs, SDKs, protocol implementation and existing code." **None of these exist in the workspace.** The repository contains only `Blackbox.md`. Every Switch capability described below is therefore a *claim made by the context document*, not a verified fact. Per the constraint "do not assume undocumented Switch functionality", this document treats all Switch primitives as **unverified** until the Switch documentation or SDK is obtained (see §8 and Question 1 in §11).

---

## 1. BLACKBOX in my own words

BLACKBOX is a hackathon product for the *Flip the Switch* hackathon (WeMakeDevs × Switch). It is a **governed, multi-agent incident-response system**. Three AI agents with separate identities and permissions (Sentinel, RootCause, Recovery) work alongside a human incident commander inside a per-incident **Switch room**. Together they take a production incident from alert to verified recovery:

- Sentinel turns a raw alert into a structured evidence package.
- RootCause uses a real coding agent (Claude Code or Codex) to find the defect in a real demo repository, write a patch and run real tests.
- Recovery packages the patch into an approval request, waits for a **genuine Switch decision** from the human, checks that decision independently and binds it to the exact patch through a content hash, and only then applies the patch to a sandboxed demo service. It then checks that the service recovered and writes the postmortem.
- The system finishes by creating preventive engineering tasks, so each incident leaves a durable lesson.

A React dashboard mirrors the Switch room, shows the investigation, diff, approval and execution state, and renders a postmortem built from the recorded event history.

The point of the hackathon is to show that Switch can be the **coordination and governance layer** for agents: rooms, identities, shared memory, tasks and decisions. It should not be used as a mere message pipe.

## 2. Core business problem

The problem is **coordination, not detection**. Monitoring tools already detect outages and page people. The expensive part of an incident is what happens next:

- The evidence is spread across metrics, logs, git history, deployment systems and chat.
- Several roles must coordinate under time pressure: SRE, backend, DBA, infra, managers and the incident commander.
- People do the diagnosis, write the fix, check it and roll it out by hand. Each hand-off between people loses context.
- Postmortems are written after the fact from memory, and preventive actions are often never tracked.

BLACKBOX's value proposition: **shorten mean time to resolution by automating investigation, patching and verification, without giving up human authority over consequential actions, and turn every incident into recorded organizational learning.**

## 3. Primary user personas

| Persona | Role in BLACKBOX | What they need |
|---|---|---|
| **Incident Commander / on-call SRE** *(primary)* | The only human actor in the core flow. Reviews proposals and approves or rejects them through a Switch decision. | Fast situational awareness, trustworthy evidence, a clear approval with a bounded blast radius, proof of recovery. |
| **Service owner / backend engineer** | Consumer of RootCause's diagnosis and diff; owner of the preventive tasks. | A readable root cause, a real diff, real test output, follow-up tasks. |
| **Engineering manager / director** | Consumer of the dashboard and postmortem. | MTTR, active and critical incidents, pending approvals, a credible audit trail. |
| **Security / platform reviewer** *(implicit)* | Judges whether agent autonomy is safe. | Permission separation, a fail-closed authorization path, an append-only audit trail. |
| **Hackathon judge** *(meta-persona that drives scope)* | Watches a 3-minute demo. | Clear evidence that Switch is used meaningfully, plus a working end-to-end story with no faked steps. |

DBAs and infrastructure teams are named in §2.2 as stakeholders but have **no interaction in the MVP flow**.

## 4. The three agents

| | **Sentinel** | **RootCause** | **Recovery** |
|---|---|---|---|
| **Persona** | Detection and investigation | Senior debugging engineer | Cautious release engineer |
| **Trigger** | Alerts (5xx, latency, DB saturation, payment failures, post-deploy spikes) and human incident reports, seen through room-event subscription ("overhearing", filtered) | Structured handoff from Sentinel | Structured handoff from RootCause (the remediation proposal) |
| **Responsibilities** | Gather metrics, logs and deployments; correlate with recent deployments; assign severity; create the incident and investigation task; build the evidence package; hand off to RootCause | Read the shared context; inspect the repo, git history and deployment diff; diagnose; generate a patch with the coding agent; run regression tests; produce the remediation proposal; hand off to Recovery | Validate the patch and tests; assess risk and dependencies; write recovery and rollback plans; create the Switch approval decision; **verify the decision independently**; execute in the sandbox; run health checks; record evidence; write the incident report |
| **Permission boundary** | Read operational data; create findings and tasks | Read the repo; write **only** to an isolated workspace; run permitted tests | Execute **only** authorized sandbox actions; cannot approve its own requests |
| **Named tools** | `get_service_metrics`, `get_recent_logs`, `get_recent_deployments`, `get_deployment_diff`, `get_service_health`, `create_incident`, `create_investigation_task` | `inspect_repository`, `read_source_file`, `get_git_history`, `get_commit_diff`, `run_diagnostics`, `generate_patch`, `run_regression_tests`, `get_test_results`, `create_pull_request` | `validate_remediation`, `calculate_patch_hash`, `generate_rollback_plan`, `create_approval_request`, `verify_switch_decision`, `execute_sandbox_deployment`, `run_health_checks`, `generate_incident_report` |

**Recovery's hard "must NOT" list** (non-negotiable): it must not treat chat as approval, act on claims generated by the LLM, self-approve, reuse an approval across incidents, execute a patch other than the approved one, execute after an approval expires or is revoked, or claim success without verification. An approval must be bound to **incident ID, action, patch hash, target environment, approver and decision status**.

## 5. Complete incident lifecycle

### 5.1 Narrative (the payment-service scenario)

1. **Baseline.** The demo `payment-service` is healthy (pool size 50, about 0.2% errors, about 100 ms latency).
2. **Fault injection.** The dashboard button `TRIGGER PAYMENT INCIDENT` deploys a real commit to the demo repo that sets the DB pool size to 10 ("deployment #842"). The service degrades for real (target: about 18% 5xx, about 4200 ms latency, 94% of DB connections in use).
3. **Alert.** A monitoring webhook (`ALT-1042`) reaches BLACKBOX ingestion.
4. **Room creation.** Ingestion creates the incident record and the Switch room `incident-payment-api-1042`, with the human and the three agents as members.
5. **Sentinel investigates.** It pulls metrics, logs, deployments and commits, correlates the alert with deployment #842, posts its findings, creates an investigation task and sends a **structured handoff** (schema in §8 of the source) to RootCause. → `DETECTED → INVESTIGATING`
6. **RootCause diagnoses.** It acknowledges the handoff, reads the room context, runs the coding agent against the demo repo, finds `config/database.yml`, generates a fix plus a regression test, runs the tests and reports the root cause, changed files, patch hash, test results and rollback. → `ROOT_CAUSE_IDENTIFIED → REMEDIATION_STAGED`
7. **Recovery validates.** It independently re-hashes the patch, checks the test results, target and risk, writes the recovery and rollback plans, and creates a **Switch decision**. → `VALIDATING → AWAITING_APPROVAL`
8. **Human decides.** The commander approves (for example with `APPROVE INC-1042`, which must *produce or confirm* a real Switch decision) or rejects.
9. **Execution (if approved).** Recovery re-fetches the decision from Switch, checks the binding (incident, hash, environment, approver, status and that it has not expired), applies the patch to the sandbox, restarts the service if needed, runs health checks and confirms the error rate dropped (target: about 0.4% 5xx, about 120 ms). → `APPROVED → EXECUTING → VERIFYING → RESOLVED` (or `FAILED`)
10. **Rejection path.** Execution stops, the rejection is recorded, the incident stays open and changes can be requested. → `REJECTED → NEEDS_REVISION`
11. **Postmortem.** A report is generated from the recorded events and published in the room and in the `/incidents/:id/report` view. → `POSTMORTEM_GENERATED`
12. **Organizational learning.** Preventive tasks are created (pool regression test, config validation, deployment checklist, alert thresholds, runbook update). → `PREVENTIVE_TASKS_CREATED`

### 5.2 State machine (as specified, with defects noted)

```
DETECTED → INVESTIGATING → ROOT_CAUSE_IDENTIFIED → REMEDIATION_STAGED → VALIDATING → AWAITING_APPROVAL
AWAITING_APPROVAL → APPROVED → EXECUTING → VERIFYING → {RESOLVED | FAILED}
AWAITING_APPROVAL → REJECTED → NEEDS_REVISION → VERIFYING   ← (defect, see §9)
RESOLVED → POSTMORTEM_GENERATED → PREVENTIVE_TASKS_CREATED
```
Invariants: a rejected action never executes; a failed verification never becomes resolved; reopening keeps history; every transition is validated; every consequential transition is audited.

## 6. Switch primitives the product must demonstrate

Each item below is **required by `Blackbox.md`** and **unverified against Switch itself**. Status will change to Verified, Partial or Absent once the Switch docs or SDK are reviewed.

| # | Primitive (as named in the doc) | How BLACKBOX uses it | Verification status |
|---|---|---|---|
| P1 | **Rooms**: programmatic creation, one per incident | `incident-payment-api-1042` created by ingestion | Unverified |
| P2 | **Room membership** for humans and agents | Commander plus three agents in the room | Unverified |
| P3 | **Distinct agent identities and roles** with per-identity permissions | Sentinel, RootCause and Recovery as separate participants | Unverified (are roles enforced by Switch or only labels?) |
| P4 | **Authentication** of each agent to Switch | Separate credentials per agent | Unverified |
| P5 | **Agent Protocol: inbound event subscription** (the doc suggests SSE) | Agents observe room events | Unverified (transport unknown) |
| P6 | **Direct mentions** | Explicit addressing of an agent | Unverified |
| P7 | **Overhearing**: receiving non-mention room events, with filtering | Sentinel sees alerts and human reports without being @-mentioned | Unverified |
| P8 | **Outbound room messages** (the doc suggests HTTP) | All agent posts | Unverified |
| P9 | **Agent-to-agent handoffs** (native or a convention carried in messages) | Sentinel → RootCause → Recovery, structured payloads | Unverified (native handoff object, or a convention we define?) |
| P10 | **Shared context / room memory** that persists across the lifecycle | A late-joining agent can reconstruct the incident state | Unverified (does Switch store structured state, or only message history?) |
| P11 | **Room artifacts** that can be updated | Evidence, diff, report attached to the room | Unverified |
| P12 | **Tasks**: create, assign, complete | Investigation, patch, tests, approval, verification, postmortem and preventive tasks | Unverified |
| P13 | **Decisions**: create, resolve by a human, query status | The approval gate for execution | Unverified, and **the most critical one** |
| P14 | **Decision metadata, approver identity, expiry and revocation** | Binding approval to hash, environment and approver; fail-closed on expiry or revocation | Unverified |
| P15 | **Enterprise chat bridges** (Slack, Teams, Mattermost, Discord) | Doc claims Switch connects agents to these | Unverified; listed as nice-to-have for the MVP |

Fallback rule (to be confirmed with the team): if a primitive is absent, we **do not fake it in Switch's name**. We implement it in BLACKBOX, label it as BLACKBOX-side in the UI and docs, and still use the nearest real Switch primitive (for example, a pinned structured message in place of an "artifact"). **P13 has no acceptable fallback.** The acceptance criteria require "a genuine Switch decision."

## 7. MVP requirements versus future enhancements

Nothing is removed here. Items are only classified. Where the document is internally inconsistent about classification, it is flagged.

### 7.1 Mandatory MVP (from §16 "Must-have" plus §19 acceptance criteria)

1. Switch integration through real Switch interfaces.
2. Three distinct agent identities visible in the room.
3. At least two genuine agent-to-agent handoffs (the target is two: S→RC, RC→R).
4. Event monitoring: an injected alert triggers the workflow.
5. Incident room creation.
6. Shared incident context that agents actually read.
7. Synthetic monitoring data (from the real simulated service).
8. Coding agent inspects the repository and identifies the seeded defect.
9. A real patch is generated against the real demo repo.
10. Real regression tests run against the demo repo.
11. Switch approval workflow, with a genuine Switch decision required.
12. Authorization verified independently, bound to the hash, failing closed.
13. Sandbox execution of the approved patch.
14. Recovery verified by health checks against the real simulated service.
15. Incident report generated from actual events.
16. Preventive engineering tasks created.
17. Live dashboard.
18. Reproducibility: the demo can be reset and re-run.

**Also mandatory, though it appears outside §16** (from §§9, 10, 12, 13):
- Explicit, validated lifecycle state machine with an audit record.
- Append-oriented audit store (SQLite).
- The three frontend views: `/dashboard`, `/incidents/:id`, `/incidents/:id/report`.
- The `TRIGGER PAYMENT INCIDENT` button that runs the whole flow with no typed agent messages.
- A demo app that has a payment API, DB config, `/health`, `/metrics`, deployment metadata and synthetic logs.
- Execution security controls 1–10 from §13.
- Simulated components clearly labelled as simulated.
- Docs: `README.md`, `ARCHITECTURE.md`, `AGENTS.md`, `DEMO.md`, `.env.example`.
- Phase 5 polish: error handling, recovery from interruptions, duplicate-event handling, demo reset.

### 7.2 Nice-to-have (explicitly deferred by §16)

Multiple simultaneous incidents; multiple service dependencies; agent confidence scoring; automated runbook generation; Slack/Teams integrations; real Prometheus; GitHub PR integration; Grafana; advanced analytics; organizational knowledge graph.

### 7.3 Future vision (out of scope)

Automatic classification, predictive detection, cross-service RCA, autonomous test generation, deployment risk scoring, intelligent rollback, runbook maintenance, recurring-incident prevention, organizational engineering memory.

### 7.4 Classification conflicts to resolve (not removals)

| Item | Where it appears as required | Where it appears as deferred | Proposed handling |
|---|---|---|---|
| `create_pull_request` tool | RootCause tool list (§5.2) | "GitHub PR integration" is nice-to-have | Implement as a **local git branch and commit** in the demo repo; defer the GitHub PR |
| Enterprise chat (Slack/Teams) | Product overview (§2.1), architecture | Nice-to-have (§16) | MVP surface = Switch room + BLACKBOX UI; chat bridges only if Switch provides them for free |
| `confidence: float` on Investigation | Data model (§7) | "Agent confidence scoring" is nice-to-have | Keep the field; populate it simply (LLM self-report, labelled as such) |
| MCP for tool integrations | Tech stack (§4.2) | Not in MVP list | Use MCP **only** if Switch or the coding agent needs it; plain Python tools otherwise |
| Prometheus/Grafana in architecture diagram | §4.1 | Nice-to-have | A Prometheus-*compatible* `/metrics` endpoint on the demo app; no Prometheus server |

## 8. Assumptions that require technical verification

**Switch (all blocked on obtaining docs or the SDK):**
- A1. Switch is accessible to us (hosted instance, or self-hostable locally), with credentials for at least four identities (three agents and one human).
- A2. Rooms can be created programmatically, with membership controlled by API.
- A3. Inbound transport is SSE (the doc hedges with "if"). The reconnect/resume semantics (event IDs, replay cursor) are unknown; they determine "recovery from interruptions" and "duplicate event handling".
- A4. "Overhearing" exists and supports some filtering, so not every event goes to every agent.
- A5. Switch has first-class **tasks**, **decisions** and **artifacts**, not just messages.
- A6. A Switch decision can carry or reference custom metadata (incident ID, patch hash, environment), **or** can at least be queried by ID with a trustworthy status and resolver identity.
- A7. Decisions can only be resolved by authorized humans, which an agent identity cannot spoof. Expiry and revocation are supported natively, or else must be enforced by BLACKBOX.
- A8. The Switch room is either the system of record for incident state, or a mirror of it. (If room memory is only message history, BLACKBOX's SQLite must be authoritative.)
- A9. Switch has a UI (or a chat bridge) where the human types `APPROVE INC-1042` or clicks to approve, or else the BLACKBOX UI must call a Switch API on the human's behalf, with the human's credentials.

**Coding agent:**
- A10. Claude Code (or Codex) can be called headlessly from Python, scoped to a single directory, with restricted tools, within the demo time budget (about 35 s on screen).
- A11. Its output is reliable enough that it finds and fixes the seeded defect on every run (it is nondeterministic).
- A12. API keys and usage quota are available for the hackathon.

**Demo environment:**
- A13. A pool-size change from 50 to 10 can be made to produce about 18% 5xx and about 4.2 s latency *for real*. This requires a load generator and a simulated DB-connection constraint; it does not happen on its own.
- A14. Recovery can apply a patch and restart the service without receiving dangerous host privileges (for example, without mounting the Docker socket).
- A15. Docker is available on the demo machine; Docker Compose is enough to run everything.
- A16. The demo can be fully reset (git reset of the demo repo, DB wipe, Switch room cleanup or a new room name per run).

## 9. Contradictions, unnecessary complexity, unrealistic requirements

### 9.1 Contradictions / ambiguities

1. **Instructions reference inputs that don't exist.** We are told to inspect the Switch repo and docs, but none are present. This blocks every Switch design decision.
2. **Chat approval versus "chat is not approval."** `APPROVE INC-1042` is the demo interaction, but a chat message must not authorize anything. If an *agent* listens for the message and creates or resolves the decision, that is effectively an agent approving for the human, which violates "must not approve its own tasks." The command must be handled natively by Switch, or the decision must be resolved with **the human's own credentials**.
3. **Who creates the incident and room?** §6 Step 1 says the ingestion service creates the room, but Sentinel has a `create_incident` tool. One owner is needed.
4. **Who creates the preventive tasks?** Step 9 has no owner agent. Recovery owns the report; the learning step is unassigned.
5. **State machine defects:**
   - `NEEDS_REVISION → VERIFYING` is wrong. A revision must loop back to `REMEDIATION_STAGED` (or `INVESTIGATING`), never to verification of a patch that was never executed.
   - There are no states for **approval expired or revoked**, although §5.3 requires handling them.
   - There is no exit from `FAILED` (rollback? re-investigate?), and no `ROLLED_BACK` state despite "rollback capability" being required.
   - "Reopening must preserve history" appears, but there is no `REOPENED` transition.
6. **Mock data versus real data.** §5.1 allows Sentinel's tools to use *deterministic mock data*, yet §14 requires the before/after metrics to be *actual results from the simulated service*, and §12 requires the faulty config to be a *real* repo change. Sentinel should read the real demo service's `/metrics`, logs and git log; only the *deployment metadata* would be synthetic.
7. **Seeded dashboard data versus "not hardcoded."** §10.1 asks for "realistic, seeded demo data" (MTTR, history), while §10.3 and §14 forbid hardcoded or manually updated values. Seeded historical incidents must be visibly labelled as seed data and kept apart from the live incident.
8. **"Production" versus "staging/sandbox."** The story is a production outage; Recovery targets `payment-service / staging`. In the demo, the sandboxed simulated service *is* the "production" being fixed. The naming should be made consistent, or the gap explained honestly.
9. **Room name and service name.** The room is `incident-payment-api-1042` but the service is `payment-service`. This is minor, but the naming convention should be fixed.
10. **Coding agent versus "no unrestricted shell."** Claude Code and Codex are shell-capable agents. §13 forbids exposing unrestricted shell execution. We need an explicit sandbox: directory-scoped, with a tool allowlist, possibly in a container.
11. **Hardcoded "27/27 tests passed."** The number must come from the actual test run.
12. **Where the human lives.** §2.1 says the humans work in Slack or Teams through Switch. §10.2 says the BLACKBOX UI displays Switch messages. It is unclear which surface the human uses to approve during the demo.

### 9.2 Unnecessary complexity (candidates to simplify, not to drop)

- **MCP** as an integration layer when the tools are local Python functions over a local demo app. Use it only if Switch or the coding agent requires it.
- **Docker sandbox for test execution *and* Docker Compose for the stack *and* Claude Code inside a container.** Choose one isolation story: for example, a dedicated git worktree plus a subprocess with a restricted environment, with Docker only for the demo service.
- **Recharts plus three full views plus a dense four-panel war room** inside 72 hours, alongside a nontrivial backend. The war room is the priority; the dashboard can be thin.
- **Dual state stores** (Switch room memory and SQLite). Pick one as authoritative and make the other a projection.
- **Thirteen-plus states** in the lifecycle. Keep them, because they are the spec, but they should be driven by a single transition table, not scattered across agents.

### 9.3 Unrealistic or high-risk requirements

- **A live coding agent within a 35-second demo window.** Latency and nondeterminism are real risks. We need a time budget, a pre-warmed run and an honest, labelled deterministic fallback (as §5.2 allows).
- **Exact metric values (18%, 4200 ms → 0.4%, 120 ms).** Real simulated load will produce approximate values. Treat them as targets, not fixed outputs.
- **"Cryptographic" binding.** We can hash the patch (for example, a SHA-256 of the diff, or use the git commit SHA) and check it at execution time. We must **not** claim immutability or signatures (as §13 itself warns).
- **Approval expiry and revocation** depend on unverified Switch features. They may have to be implemented in BLACKBOX and labelled as such.
- **Seventy-two hours** for Switch discovery, three agents, coding-agent integration, a sandboxed executor, a demo service with realistic failure, three UI views, five docs and a recorded demo is aggressive. The vertical slice must come first, as §6 and §15 insist.

## 10. What a successful hackathon submission is

A submission succeeds when **all 15 acceptance criteria in §19 are met with real behaviour**, demonstrated in **one uninterrupted, reproducible three-minute run**, starting from `TRIGGER PAYMENT INCIDENT`:

1. Three distinct agent identities post in a **real Switch room** created for the incident.
2. At least two **structured** handoffs (Sentinel → RootCause → Recovery) travel as Switch events, and each receiving agent acknowledges and reads the shared context.
3. The coding agent finds the seeded pool-size regression in the **real** demo repo, produces a **real diff**, and **real tests** run with their real counts shown.
4. Execution is blocked until a **genuine Switch decision** is resolved by the human. Recovery re-verifies that decision against Switch and the patch hash, and **fails closed** otherwise. A rejection run shows that nothing executes.
5. The patched service **actually recovers**: the before/after metrics are read from the running simulated service.
6. The postmortem is **generated from the audit/event log**, and preventive tasks are created (as Switch tasks, if they exist).
7. The demo resets and runs again identically enough to record.
8. **Honesty:** every simulated or fallback component is visibly labelled. No Switch capability is claimed that Switch does not have.
9. Judges can see that Switch is central to identities, context, handoffs, tasks and decisions, as §17 says it should be for all seven differentiators.
10. Setup is documented (`README`, `ARCHITECTURE`, `AGENTS`, `DEMO`, `.env.example`) and runs locally with Docker Compose.

**Failure modes that would sink the submission:** the approval is just a chat string; the coding-agent output is canned but presented as live; the metrics are changed by the UI; the agents are function calls inside one process that pretend to be room participants; or the demo cannot be re-run.

---

## 11. The ten most important technical questions to resolve before implementation

1. **Where are the Switch docs, SDK and Agent Protocol implementation, and how do we get an instance?** Is it hosted or self-hosted, how is it authenticated, and how do we provision four identities? Nothing else can be designed until this is answered.
2. **Does Switch have a first-class *Decision* primitive?** What is its schema; who can resolve it; can it carry or reference our metadata (incident ID, patch hash, environment); does it support expiry or revocation; and can an agent identity resolve one? (If agents can, our self-approval guard must be enforced on our side.)
3. **How does the human approve during the demo, and with whose credentials?** Through a native Switch UI, a chat bridge, or the BLACKBOX UI calling Switch as the human? How does `APPROVE INC-1042` become a genuine decision without an agent acting as the approver?
4. **What is the exact inbound event transport and its delivery semantics?** Is it SSE or something else; is there an event ID or replay cursor; is delivery at-least-once or at-most-once; how does mention versus overhear filtering work? This drives idempotency, reconnects and duplicate handling.
5. **Does Switch provide native Tasks, Artifacts and structured room memory, or only messages?** This decides whether SQLite or Switch is the authoritative incident state, and what a "structured handoff" physically is: a native object or a typed JSON payload in a message.
6. **How will the coding agent be invoked and sandboxed?** For example, a headless Claude Code run that is directory-scoped, with an allowlist of permitted tools and network policy, plus a time budget and success rate over repeated runs. Also: what is the exact trigger and labelling for the deterministic fallback?
7. **How do we make the demo service fail and recover for real?** That means a load generator, a simulated DB-connection pool constraint and the mapping from pool size to error rate and latency, so the before and after numbers are measured rather than scripted.
8. **What exactly is the "patch artifact" and its hash, and how does Recovery apply it safely?** A git commit SHA or a SHA-256 of a normalized diff? Does applying it mean a git checkout plus a service reload? How is the restart done without giving Recovery Docker-socket or host privileges?
9. **What is the corrected, authoritative lifecycle state machine?** That includes the `NEEDS_REVISION` loop target, expired/revoked approvals, `FAILED` exits, rollback and reopen. Which component owns transitions: a central orchestrator, or each agent with central validation?
10. **What is the process and identity topology?** Three separate agent processes with separate Switch credentials, or one service holding three identities? Who owns incident and room creation (ingestion or Sentinel) and the preventive tasks? How does the demo reset clean up Switch rooms, the repo, the DB and the service?
