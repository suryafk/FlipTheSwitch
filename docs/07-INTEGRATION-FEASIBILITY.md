# 07 — Integration Feasibility

| | |
|---|---|
| **Purpose** | Decide which integrations are already proven, which must be proven before implementation starts, and what each costs |
| **Inputs** | `01-SWITCH-RECONNAISSANCE.md` (§19–§20: live runs S1–S7, 2026-11-07, Switch `0.31.0`), `03-*` (architecture), `04-*` (security), `05-*` (simulation) |
| **Companions** | [`07-TECHNICAL-SPIKES.md`](07-TECHNICAL-SPIKES.md) (experiments) · [`07-DEPENDENCY-RISK-REGISTER.md`](07-DEPENDENCY-RISK-REGISTER.md) |

**Evidence levels:**

| Tag | Meaning |
|---|---|
| **[V]** | Exercised live against a running system in this project |
| **[H]** | Confirmed only from a CLI's `--help` output on this machine (Claude Code 2.1.311) |
| **[S]** | Read in source or docs |
| **UNPROVEN** | Not yet exercised |

**Priority:**

| Priority | Meaning |
|---|---|
| **P0** | Must be proven before implementation starts |
| **P1** | Must be proven before the MVP is complete |
| **P2** | Can come later |

**Effort** is engineering time to build the *production-quality* integration for the MVP, assuming the proof already exists.

---

## Summary

| # | Integration | Priority | Status | Effort |
|---|---|---|---|---|
| 1 | Switch agent registration | P0 | **Proven [V]** (S2) | 2 h |
| 2 | Switch room creation | P0 | **Proven [V]** (S3); Mattermost stability caveat | 3 h |
| 3 | Agent Protocol event subscription | P0 | **Proven [V]** (S3, S6) | 6 h |
| 4 | Sending messages into rooms | P0 | **Proven [V]** (S3, S4) | 2 h |
| 5 | Addressed vs. overheard handling | P0 | **Proven [V]** (S3) | 3 h |
| 6 | Agent-to-agent handoffs | P0 | **Proven [V]** (S4): transport. Schema and verification are custom. | 6 h |
| 7 | Shared context retrieval | P1 | **Proven [V]** (S3 `connect_to_room`, S4 `read_context`, S7 documents) | 5 h |
| 8 | Switch Tasks | P2 | **Not available [V]**: no task operations in the registry. Replaced by custom BLACKBOX tasks (P1, 4 h). | 0 h (Switch) |
| 9 | Switch Decisions (approval requests) | P0 | **Proven [V]** (S5) | 4 h |
| 10 | Decision verification by Recovery/executor | P0 | Switch side **proven [V]**; executor algorithm **UNPROVEN** | 8 h |
| 11 | Claude Code (or Codex) integration | P0 | **UNPROVEN**. CLI flags present **[H]**. Codex **not installed**. | 8 h |
| 12 | Sandboxed repository modifications | P0 | **UNPROVEN** | 8 h |
| 13 | Actual test execution | P0 | **UNPROVEN** (straightforward) | 4 h |
| 14 | Approval-gated execution (executor + launcher + verification) | P0 | **UNPROVEN** | 12 h |
| + | Simulation calibration (real pool saturation, measured) | P0 *(added)* | **UNPROVEN**; the mechanism is analytical (`05-SIMULATION-ARCHITECTURE.md` §1) | 6 h |
| + | Demo host capacity (emulated Mattermost on arm64) | P0 *(added)* | **Risk observed [V]**: 3 Mattermost hangs under load | 1 h |

P0s already proven: 1–6 and 9. **P0s still to prove: 10 (executor half), 11, 12, 13, 14, plus calibration and host capacity.** These are the spikes in `07-TECHNICAL-SPIKES.md`.

---

## Detailed assessments

### 1. Switch agent registration (P0, proven)

| | |
|---|---|
| **Prove** | A custom Python process can register three or four distinct agents owned by the incident-commander user, and receive per-agent API keys |
| **Reference** | `core/switch_core/bridges/agent/api/handlers.py:206` (`POST /agents`), `api/schemas.py:20`, `gateway/api_keys.py:72`; `loadtest/seed.py:25,186` |
| **Credentials** | Gateway admin email and password (to mint a personal key), from the Switch `.env`; the registration key (type `registration`) |
| **Request** | `POST /gateway/api-keys {"label":…}` (cookie) → `{key}`. Then `POST /agents` (Bearer reg key) `{name, description, display_name, connector_type:"blackbox", integration_profile:{connection_model:"always_on", message_exchange:true, pre_invocation_mediation:[], post_invocation_mediation:[], event_reporting:[]}}` |
| **Response** | `200 {"id": "<uuid>", "api_key": "<once>"}` [V] |
| **Behavior** | The agent is owned by the key's user. 409 on a duplicate name unless `overwrite`. |
| **Success** | Three agents registered; `GET /agents/{id}/ops` returns 53 operations [V] |
| **Failure** | 401 (bad key); 409 (name taken → use `overwrite` in the bootstrap script); agents registered with the *bootstrap* token are owned by a system account, so the incident commander can't answer their approvals |
| **Effort** | 2 h (an idempotent bootstrap script that writes per-agent env files) |

### 2. Switch room creation (P0, proven with a caveat)

| | |
|---|---|
| **Prove** | Sentinel creates a bridged incident room with all agents and the incident commander, and a Mattermost channel appears |
| **Reference** | `operations/definitions.py:923`; S3 |
| **Credentials** | Sentinel's API key |
| **Request** | `POST /agents/{id}/ops/create_room {name, description, agent_names[4], user_names:["user"], channel_type:"channel_public", instructions}` |
| **Response** | `200 {"result":{"id", "name", "transport_room_id", "failed_attachments":[]}}` [V] |
| **Success** | Room id returned; the channel is visible to `user` in Mattermost within 30 s [V] |
| **Failure** | **Mattermost read timeout → 500 [V]** (seen when emulated Mattermost hung). The name collides with an existing room (behavior after archive is UNKNOWN). |
| **Effort** | 3 h, including the idempotency guard (store `room_id` first; recover with `list_rooms`) |

### 3. Agent Protocol event subscription (P0, proven)

| | |
|---|---|
| **Prove** | A long-lived agent connection receives room events, survives drops, and resumes without loss or duplication |
| **Reference** | `handlers.py:747,1227`; `artifacts.yaml` revisions 8–9; `loadtest/agents.py`; S3, S6 |
| **Credentials** | Per-agent API key |
| **Request** | `GET ws://…/agents/{id}/connection/ws?connection_id=<uuid>&scope=all&filter=all&start_from=<seq\|head>&protocol=8&protocol_accepts=1&client=…`; the client answers each `ping` with `{"type":"pong","cursor":n}` |
| **Response** | First frame `connection_state` (`protocol=9`, `heartbeat_interval_seconds=2`), then `{event, data, id}` frames [V] |
| **Success** | Events delivered with 0.12 s latency [V]. 2 s and 12 s drops resume with no replay and no loss [V]. Eviction codes `taken_over` and `heartbeat_lapsed` [V]. |
| **Failure** | After a drop longer than 6 s the room claim is lost: call `connect_to_room` again [V]. A switch-core restart loses human messages posted during the bridge reconnect [V]. A cursor ahead of head gives a `gap` (re-read the context) [V]. |
| **Effort** | 6 h (a shared `SwitchConnection` class: reconnect and backoff, cursor persistence, claim restore, gap handling) |

### 4. Sending messages into rooms (P0, proven)

| | |
|---|---|
| **Prove** | Agents post broadcast and threaded messages; humans see them in Mattermost |
| **Reference** | `definitions.py:788,827`; `handlers.py:487` |
| **Request** | `ops/post_message {body, thread_id?}` (with the connection header) or `POST /agents/{id}/message {room_id, content}` (no connection needed) |
| **Response** | `{"result":{"event_id":…}}` / `{"ok":true,"event_id":…}` [V] |
| **Success** | The message appears in the channel and in `read_context` [V] |
| **Failure** | Switch Trust redaction or blocking (off locally); `metadata` is silently dropped [S]; message-length limit UNKNOWN (design for ≤ 4 000 characters) |
| **Effort** | 2 h |

### 5. Addressed vs. overheard (P0, proven)

| | |
|---|---|
| **Prove** | Agents can tell the difference between being addressed and overhearing |
| **Reference** | `agent-protocol.md` §addressed filter; S3 |
| **Expected** | A human message with no mention reaches Sentinel with `addressed=false`; `@sentinel` gives `addressed=true`; RootCause receives Sentinel's mention with `addressed=false` [V] |
| **Success** | It routes correctly in the agent runtime's dispatcher (act vs. context) |
| **Failure** | None observed. Note that `filter=addressed` drops `command` events. |
| **Effort** | 3 h (dispatcher rules per agent contract) |

### 6. Agent-to-agent handoffs (P0, transport proven)

| | |
|---|---|
| **Prove** | A structured handoff travels through Switch and is verifiable by the receiver |
| **Reference** | `send_targeted_message`; `03-HANDOFF-SCHEMAS.md` §1; S4 |
| **Request** | `ops/send_targeted_message {body:"<summary>\n\n```json\n{…}\n```", target_names:["blackbox-rootcause"]}` |
| **Response** | `{"result":{"event_id", "target_statuses":{"blackbox-rootcause":"live"}}}` [V] |
| **Behavior** | The receiver gets `addressed=true`. The body is prefixed with `@<target>` [V]. The JSON round-trips intact [V]. |
| **Success** | The receiver validates the schema, sender id and store hash, and ACKs within 5 s |
| **Failure** | `not_permitted` (addressing policy); the target is disconnected (`no_session`); a forged JSON from a human (rejected by the sender-id check) |
| **Effort** | 6 h (schemas, canonical JSON hash, store record, ACK and re-send) |

### 7. Shared context retrieval (P1, proven)

| | |
|---|---|
| **Prove** | A late-joining agent reconstructs the incident from Switch plus the BLACKBOX store |
| **Reference** | `connect_to_room` (`definitions.py:252`), `read_context`, room documents; S3, S4, S7 |
| **Expected** | `connect_to_room` returns `documents[]` (id, name, description, instructions, scope, `created_by_agent_id`) [V]; `load_internal_documents` returns exact content [V]; a non-creator update gives 403 [V] |
| **Success** | The RootCause handler builds its prompt from `get_incident_context` plus `read_context(limit=20)` plus the documents |
| **Failure** | `truncated=true` (page with `before`); a duplicate document name → 400 [V] |
| **Effort** | 5 h (context projection endpoint plus the Control-owned state document) |

### 8. Switch Tasks (P2, not available)

| | |
|---|---|
| **Prove** | — |
| **Evidence** | `GET /ops` lists 53 operations with **no** task operations [V]; the internals doc says "not ready for use" [S] |
| **Decision** | Build BLACKBOX task records (P1, 4 h), rendered as `TASK · BLACKBOX` feed rows and a Control-owned task-board document. Revisit native tasks when Switch ships them (P2). |

### 9. Switch Decisions via approval requests (P0, proven)

| | |
|---|---|
| **Prove** | A genuine, server-authorized human decision bound to content |
| **Reference** | `activity_routes.py:231-292`; `session_activity/service.py:358-650`; S5 |
| **Credentials** | Recovery's API key; the incident commander's Mattermost login (linked to the Switch owner) |
| **Request** | `POST /agent-sessions/{session_id}/approvals {request_id, turn_id, kind:"approval", title, detail, options:[{id:"approve",label,decision:"accept"},{id:"reject",label,decision:"decline"}], questions:[], room_id, thread_id:null, expires_at}` |
| **Response** | `{sessionId, requestId, kind, state:"open", answer:null, answeredBy:null, expiresAt, …}` [V] |
| **Behavior** | A card with buttons in Mattermost [V]. A stranger's press is refused [V]. A plain-text "APPROVE" does nothing [V]. Re-opening with different content → 409 [V]. Expiry is pushed [V]. An agent cannot answer (401) [V]. |
| **Success** | All of the above reproduced in the L4 suite |
| **Failure** | Undocumented API; it may change on a Switch upgrade (pin `0.31.0`) |
| **Effort** | 4 h |

### 10. Decision verification by Recovery and the executor (P0, half proven)

| | |
|---|---|
| **Prove** | The executor can independently and reliably establish `answered/approve/answered_by` for an exact `request_id`, in every timing case: before and after `/delivered`, before and after expiry, and across a restart |
| **Reference** | `protocol/stream.py:218-312`; `session_activity/outcomes.py`; `04-APPROVAL-SECURITY.md` §5 |
| **Credentials** | Recovery's API key, used read-only by the executor |
| **Request / response** | `GET /agent-sessions/approvals/outcomes` → `[{sessionId, requestId, state, answer, answeredBy, answeredAt, …}]` [V]; identical re-open → current row [V] (422 after expiry [V]) |
| **Unproven** | (a) Ordering rule: `/delivered` only after execution keeps the outcome readable. (b) Behavior when the same agent has many undelivered outcomes. (c) The executor refusal matrix AP-7 to AP-14. (d) Whether `answered_by` is stable across bridge restarts. |
| **Success** | All of AP-7 to AP-14 refuse correctly; the happy path reads back within 1 s |
| **Failure** | The outcome can't be read back after expiry if it was already delivered (by design, a refusal); key sharing between Recovery and the executor |
| **Effort** | 8 h |

### 11. Claude Code integration (P0, unproven)

| | |
|---|---|
| **Prove** | Headless Claude Code, scoped to one directory with a restricted toolset and no shell, finds and fixes the seeded defect from a brief, within time and cost budgets, and reports machine-readable results |
| **Reference** | Local CLI `claude` 2.1.311 [H]: `-p/--print`, `--output-format json\|stream-json`, `--tools <list>` ("" disables all; names restrict the built-in set), `--allowedTools` / `--disallowedTools`, `--permission-mode` (`acceptEdits`, `auto`, `bypassPermissions`, `manual`, `dontAsk`, `plan`), `--max-budget-usd`, `--model`, `--append-system-prompt`, `--strict-mcp-config`, `--setting-sources`, `--bare` (skips hooks and settings; per the help text it needs API-key auth through `apiKeyHelper`/`--settings`). **Codex is not installed**, so the Codex path is P2. |
| **Credentials** | An Anthropic API key with a spend cap (`ANTHROPIC_API_KEY`), or a key helper for `--bare` |
| **Expected invocation** | `claude -p "<brief>" --output-format stream-json --tools Read,Edit,Grep,Glob --permission-mode acceptEdits --max-budget-usd 0.50 --strict-mcp-config --setting-sources "" --model <configured>`, with cwd = the worktree |
| **Expected output** | A stream of JSON events, including tool-use events (to audit which tools ran) and a final result with cost and duration. **Exact event schema UNPROVEN.** |
| **Success** | ≥ 4/5 runs produce a correct fix plus a regression test; 0 runs use a tool outside the set; p50 wall time ≤ 60 s; cost ≤ $0.50 per run |
| **Failure** | Shell or other tools used despite `--tools`; edits outside cwd; latency over 120 s; the brief over-specifies (the "agent" just transcribes); auth problems in `--bare` |
| **Effort** | 8 h (adapter, stream parsing, timeout and kill, fallback switch) |

### 12. Sandboxed repository modifications (P0, unproven)

| | |
|---|---|
| **Prove** | Claude Code runs in an ephemeral container: non-root, read-only rootfs, only `/work` writable, egress only to the model API, no secrets beyond the model key. Injected instructions in the repo cannot cause network or file access outside `/work`. |
| **Reference** | `04-SECURITY-ARCHITECTURE.md` §5 |
| **Credentials** | The model key (env var or helper); Docker on the host (BLACKBOX's sandbox launcher holds the Docker access, never the agents) |
| **Expected** | `docker run --rm --user 1000 --read-only --tmpfs /home/agent --network <egress-net> -v <worktree>:/work -w /work --cpus 1 --memory 1g --pids-limit 256 <claude-sandbox-image> claude -p …` |
| **Unknowns** | Whether the CLI needs a writable `$HOME` (tmpfs mitigates it); whether a domain-allowlisting egress proxy works with the CLI (`HTTPS_PROXY` support UNPROVEN); image size and start time |
| **Success** | The SP-1 success criteria hold inside the container; negative probes (curl, write `/etc`, read the host's `~/.ssh`) all fail; container start ≤ 5 s |
| **Failure** | The CLI fails under a read-only rootfs; the proxy is not honored, which would force `network=bridge` (residual risk recorded) |
| **Effort** | 8 h |

### 13. Actual test execution (P0, unproven, low risk)

| | |
|---|---|
| **Prove** | The demo repo's pytest suite runs in a `network=none` container with a read-only worktree, produces JUnit, and BLACKBOX parses the counts. The new regression test fails on C5 and passes on the fix. |
| **Reference** | `05-TEST-STRATEGY.md` L0 |
| **Expected** | `docker run --rm --network none --read-only --tmpfs /tmp -v <wt>:/work:ro -v <out>:/out <test-image> pytest -q -p no:cacheprovider --junitxml=/out/junit.xml tests/` → exit 0 or 1 plus JUnit |
| **Success** | Counts parsed exactly; the run takes ≤ 10 s; a network call inside a test fails |
| **Failure** | Dependencies need network (fix: bake them into the image); pytest writes caches into the read-only mount (fix: `-p no:cacheprovider`, tmpfs) |
| **Effort** | 4 h |

### 14. Approval-gated execution (P0, unproven)

| | |
|---|---|
| **Prove** | The full governance chain on real components: binding → freeze → Switch approval → read-back → atomic consume → launcher deploys the exact SHA → measured verification → `/delivered`. Each refusal path blocks deployment. |
| **Reference** | `04-APPROVAL-SECURITY.md` §5–§6; `05-SIMULATION-ARCHITECTURE.md` §14–§15 |
| **Credentials** | Recovery's key (read use), the launcher deploy token, the incident commander's Mattermost login (scripted press, as in S5) |
| **Expected** | The launcher's `POST /deploy {sha, deploy_id, approval_id}` → `{deploy_id, sha, previous_sha, ready_ms}`; `/health.git_sha == sha` |
| **Success** | The happy path deploys the approved SHA in ≤ 5 s and verifies within 60 s; every negative case refuses with the correct reason and no deploy |
| **Failure** | Restart semantics for the launcher (graceful reload); race between revoke and consume |
| **Effort** | 12 h |

### Added: simulation calibration (P0) and demo host capacity (P0)

- **Calibration.** Prove that 120 rps × 100 ms hold with `pool_size` 10 vs. 50 produces the target ranges (`05-TEST-STRATEGY.md` L2) on the demo host. If not, use the 60 rps × 200 ms profile. 6 h.
- **Host capacity.** Mattermost (amd64 only) hung three times under load on a 2 CPU / 2 GB arm64 VM [V]. Prove stability with ≥ 4 CPU / 6 GB over a 15-minute load soak, or plan a native amd64 host for recording. 1 h, plus wait time.
