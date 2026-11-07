# 12 · Switch integration: implementation

Milestone goal: Switch is the real agent-coordination and approval layer for BLACKBOX.

Platform: self-hosted Switch **0.31.0** (switch-core and Gateway at `SWITCH_API_URL`, bundled
Mattermost bridge). Every call below is one that Switch itself exposes. Operation signatures
were read from the live server (`GET /agents/{id}/ops`, which returns 53 operations).

> **Inputs.** The brief asked for `context.md`. There is no such file in the repository. This
> milestone used `Blackbox.md`, `docs/01-SWITCH-RECONNAISSANCE.md`,
> `docs/01-SWITCH-CAPABILITY-MATRIX.md`, `docs/10-SWITCH-INTEGRATION-PROOF.md`, the Slack docs
> (`docs/11-SLACK-*.md`) and the code.

> **Scope.** The three agents (Sentinel, RootCause, Recovery) already existed from Task 12.
> This milestone does **not** add autonomy to them. It hardens the Switch layer they share
> (runtime, client, stream, handoffs, approvals) and drives them step by step in a live check.

## 1. What existed, what was only documented, what was missing

| Requirement | Before this milestone | This milestone |
|---|---|---|
| Agent registration | Real: `POST /agents` with a personal registration key from the Gateway (`make agents-bootstrap`) | Re-verified live; invalid key → 401 |
| Credential handling | Real: API keys in `backend/data/switch/agents/*.json`, mode 0600, gitignored | Unchanged; the live check keeps its key in memory only |
| Reconnect after failure | **Missing.** A dropped socket ended the agent process | **Added**: reconnect with backoff, resume from the last sequence, re-claim rooms, `gap` handling |
| HTTP retries and timeouts | Partial: a 90 s timeout, no retries | **Added**: 10 s connect timeout; retries for idempotent calls only |
| Incident room and id ↔ room | Real: Sentinel's `create_room`, stored on the incident | Re-verified live, including membership |
| Room roles | Documented, unused | **Used**: `define_role`, `assume_role`, `list_roles`, `release_role`; role-addressed messages |
| Send and receive messages | Real: `/message`, `send_targeted_message`, WebSocket stream | Re-verified: broadcast, name-addressed and role-addressed |
| Preserve event ids and timestamps | Partial: handoff records kept the sender's Switch event id; the ACK's id was dropped (`null`) | **Added**: `switch_events` ledger; ACK event id stored |
| Duplicate event processing | In-memory set, lost on restart | **Durable**: ledger keyed by (agent, Switch event id) |
| Handoff between two agents | Real: fenced JSON through `send_targeted_message`; receiver checks sender identity, store record and hash; ACK | Re-verified live, including forged and tampered copies |
| Tasks | Not available in Switch (documented in docs/01) | Re-verified live: **unsupported** (§3) |
| Decision for human approval | Real: Switch approval request (card), read back from Switch, fail-closed verification | Re-verified live: approve, reject, expire, conflict, replay |
| Revoked approvals | **Missing** | **Added**: BLACKBOX revocation, which the executor checks; the Switch request is closed while it is still open |
| Slack bridge | Published BLACKBOX audit events | **Extended** with Switch-level events (§5) |
| Fail closed | Real (`verify_decision`) | Plus `not_revoked`; a revocation also wins after the execution claim |

## 2. Implementation

### 2.1 Identity and credentials

- **Registration.** `app/switch/client.py: register_agent()` calls `POST /agents` with a
  personal registration key minted through the Gateway (`mint_registration_key()`). The agents
  are therefore owned by the incident commander, which Switch requires before that person can
  answer their approval cards. The `always_on` integration profile is declared.
- **Authentication.** Every agent call sends `Authorization: Bearer <api_key>`.
- **Credential storage.** Keys are written once to `backend/data/switch/agents/<role>.json`
  (chmod 600; `backend/data/` is gitignored) and never logged.
- **Least privilege.** Switch has no per-agent permission boundary: an agent has its owner's
  rights (docs/01). BLACKBOX limits what each agent *does* (tool allowlists, the transition
  table in `incidents/store.py`) and binds approvals to a single approver identity.
- **Live check identity.** The live check registers a separate `blackbox-livecheck` identity
  to prove registration, and keeps that key in memory only.

### 2.2 Connection, reconnect and resume (`app/switch/stream.py`)

`AgentStream(reconnect=True)` is the WebSocket at `/agents/{id}/connection/ws`
(`scope=all`, `filter=all`). The protocol and behaviour come from smoke test S6 in docs/01 §20.

- **Heartbeat.** Every server `ping` is answered with `{"type":"pong","cursor":n}`.
- **Resume.** It reopens with the **same `connection_id`** and `start_from=<last sequence>`, so
  Switch delivers what was missed without replaying what was already seen.
- **Backoff.** 1 s, doubling to 30 s; it resets after a successful open.
- **Terminal conditions.** `evicted{code:"taken_over"}` (another process owns this connection),
  a `refused` frame, close codes 4401, 4403 and 4404, and HTTP 401, 403 or 404 on open. The
  stream stops and the agent exits instead of fighting for the connection.
- **Room claims.** Claims lapse after the 6 s heartbeat TTL, so `on_reconnect` makes the runtime
  re-claim every room (`connect_to_room`) and re-read it (`read_context`). This is recorded as
  `switch.reconnected` in the audit log.
- **Gaps.** A `gap` frame means resume was impossible. `on_gap` re-reads every room, and the
  event is audited as `switch.gap`.

### 2.3 HTTP client (`app/switch/client.py`)

- **Timeouts.** 10 s to connect, and 90 s overall (`create_room` waits on the chat bridge).
- **Retries.** On network errors and 502, 503 and 504, up to 3 attempts with backoff, but
  **only for idempotent calls**:
  - reads: `read_context`, `list_rooms`, `list_participants`, outcomes, and similar
  - `connect_to_room`
  - `open_approval`, which is content-idempotent in Switch
  - `mark_delivered`
- **No retries for writes that could duplicate:** `post_message`, `send_targeted_message`,
  `create_room` and `close_approval`. A failure there is raised to the caller.
- **Errors.** All failures become `SwitchError` and carry Switch's own status and body.

### 2.4 Rooms, membership and roles

- **Room creation.** Sentinel calls `create_room` with all three agents plus the IC
  (`user_names`). Switch creates the bridged Mattermost channel. The creator is **not**
  implicitly a member, so Sentinel lists itself.
- **Incident link.** `incidents.room_id` and `incidents.room_name` link the incident to its
  room (`store.update_incident`, audited as `incident.updated`).
- **Roles.** Room-scoped `define_role(name, instructions, exclusive)`, `assume_role(role)`,
  `list_roles` and `release_role`. Roles are an addressing mechanism, not a permission
  boundary (docs/01). The live check defines an exclusive `investigator` role held by
  RootCause and addresses it with `send_targeted_message(target_roles=[…])`.

### 2.5 Messages, events and duplicate prevention

- **Sending.**
  - Broadcast: `POST /agents/{id}/message`.
  - Addressed: `send_targeted_message` with `target_names` or `target_roles`. Switch prefixes
    `@target`, and the target receives `addressed: true` while everyone else overhears it.
- **Receiving.** `message` frames carry `id` (stream sequence) and a payload with `message_id`
  (equal to the sender's returned `event_id`), `sender`, `sender_name`, `addressed`, `body` and
  `timestamp` (ms).
- **Validation.** A frame without a message id, or with a non-string body, is ignored and
  logged.
- **Ledger.** Before an agent's handler runs, `store.claim_switch_event()` inserts
  `(agent, event_id, room_id, sequence, sender, sender_name, addressed, switch_timestamp_ms, via)`
  into `switch_events` (migration `0004`). The primary key `(agent, event_id)` makes processing
  exactly-once across:
  - stream resume
  - `read_context` catch-up (same ids, verified live)
  - process restarts
- **Trade-off.** An event is marked as processed *before* its handler runs. A handler crash is
  logged and not retried, so it can't act twice. The incident state machine keeps a half-done
  step visible.

### 2.6 Agent handoff (`app/agents/handoff.py`)

Every handoff goes through Switch. Nothing in BLACKBOX calls another agent's Python code.

1. **Record.** The sender writes the handoff (incident context plus evidence) to the store and
   computes the payload hash.
2. **Send.** It posts the handoff as an addressed Switch message: a one-line summary plus a
   fenced `blackbox.handoff/v1` JSON block.
3. **Verify.** The receiver accepts it only if all of these hold:
   - the type matches and the handoff is addressed to it
   - the Switch sender id equals the **sender's identity pinned at bootstrap**
   - the store has a record with that id
   - the payload hash matches the record
4. **Acknowledge.** The receiver sends an addressed `blackbox.ack/v1` back through Switch.

Status moves `recorded → sent → acked`, and each step carries its Switch event id. Refusals are
audited as `handoff.rejected` with the reason. A handoff with no ACK stays `sent`.

### 2.7 Decisions: approval requests (`app/switch/approvals.py`, `executor.py`, `revoke.py`)

Switch has no Decision primitive. Its **approval request** is the real one: a card in the room
with buttons, answerable only by a human Switch authorizes.

1. **Bind.** `create_binding()` fixes the incident, remediation, action
   (`apply_remediation_commit`), **patch hash**, **target environment** (`staging-sandbox`), the
   approver allowlist (the IC's Switch participant id) and the expiry. The request id embeds all
   of it: `{incident}:{remediation}:{hash[:16]}:{env}:{nonce}`. The card text shows the hash.
2. **Open.** `POST /agent-sessions/{incident}/approvals`. Re-opening identical content returns
   the same row. Different content under the same id → **409 `REQUEST_CONFLICT`** (Switch
   enforces it).
3. **Verify independently.** `verify_decision()` doesn't trust memory, Recovery's message or
   chat. Every check must pass:
   - re-derive the request id
   - re-hash the stored card body
   - re-read the decision **from Switch** (`GET /agent-sessions/approvals/outcomes`, or an
     identical re-open)
   - `state=answered` and `answer=approve`
   - the answerer is allowlisted
   - answered before expiry, and not expired now
   - first use
   - **not revoked**
4. **Single use.** The executor claims the approval atomically (`executions.approval_id`
   unique). It then re-checks for a revocation that landed in between, and only acknowledges
   the Switch outcome (`/delivered`) after recording its result.
5. **Revoke.** `make switch-revoke ID=APR-… REASON=…` (`app/switch/revoke.py`):
   - records `approval.revoked`; the revocation is sticky, so a later Switch answer can't
     overwrite it, and it is refused once executed
   - calls Switch `…/approvals/{id}/close`, which works while the request is open; Switch
     returns an error once it was answered or expired, and that is recorded
6. **Fail closed.** If Switch is unreachable, or anything doesn't match, there is no
   execution. A local boolean is never accepted: `already_consumed` and `revoked` can only add
   refusals.

### 2.8 Consequential actions logged

The append-only `audit_events` table records all of these:

- `incident.created`, `incident.transition`, `incident.updated` (room link)
- `handoff.recorded`, `handoff.sent`, `handoff.acked`, `handoff.rejected`
- `approval.bound`, `approval.open`, `approval.approved`, `approval.rejected`,
  `approval.expired`, `approval.revoked`, `approval.switch_close`, `approval.verified`
- `execution.started`, `execution.refused`, `execution.succeeded`
- `switch.reconnected`, `switch.gap`

## 3. Unsupported capabilities and fallback decisions

| Capability | Status in Switch 0.31.0 (evidence) | Fallback |
|---|---|---|
| **Tasks** (delegate, accept, update, finalise) | Not implemented: 0 of 53 live operations; protocol frames reserved, "build nothing on them" (docs/01) | Work items are BLACKBOX handoff records plus the incident state machine, carried over Switch messages |
| **Decision primitive** | None: no operation, table or event | Switch **approval request** plus BLACKBOX binding and independent verification |
| **Revoke after answer** | Only `close` while open (verified: `state: closed`) | BLACKBOX revocation, checked by the executor (sticky, refused after execution) |
| **GET one approval by id** | No agent endpoint | `GET …/approvals/outcomes` (undelivered), or an identical re-open as a read (422 after expiry) |
| **Structured message metadata** | `metadata` ignored | Fenced JSON in the body, hashed and recorded in the store |
| **Per-agent permission boundary** | Agents inherit their owner's rights | BLACKBOX tool allowlists, transition table, pinned identities, approver allowlist |
| **Immutable audit log** | None | BLACKBOX append-only `audit_events` (UPDATE and DELETE blocked by triggers) |
| **Human messages during a switch-core restart** | Can be lost (S6 finding) | Not recoverable from Switch; restart Mattermost or Switch only between incidents |
| **Mattermost stability on arm64** | amd64 image runs emulated; it hung once during this milestone | Restart the container (`docker restart blackbox-switch-mattermost-1`), wait about 20 s for the bridge, retry |

## 4. Configuration

No new variables are required. The ones that matter here (see `.env.example`):

| Variable | Use |
|---|---|
| `SWITCH_API_URL` | switch-core plus Gateway base URL |
| `SWITCH_GATEWAY_ADMIN_EMAIL` / `SWITCH_GATEWAY_ADMIN_PASSWORD` or `SWITCH_REGISTRATION_KEY` | Mint the personal registration key (bootstrap and live check) |
| `SWITCH_IC_CHAT_USERNAME` | The incident commander on the bridged chat |
| `MATTERMOST_URL` / `MATTERMOST_IC_PASSWORD` | **Test harness only**: unattended answers in the live check |
| `SWITCH_LIVE_TEST=1` | Enables `tests/integration/test_switch_live.py` |
| `BLACKBOX_APPROVAL_TTL_MINUTES` | Approval expiry used by Recovery |

## 5. Slack event bridge

The bridge still reads only BLACKBOX's audit log. It doesn't subscribe to Switch and isn't a
source of truth. It now also publishes the Switch-level events, in the incident's existing
thread:

- Switch incident room created (room name and Switch room id)
- handoff sent, **with its Switch event id**
- handoff acknowledged (sender identity and hash verified; ACK event id)
- handoff rejected (reason)
- Switch approval request opened: action, patch hash, environment, expiry
- approval revoked in BLACKBOX
- decision re-read from Switch and verified independently; execution claimed

The same delivery ledger prevents duplicates. Room-link updates also refresh the thread's root
message. The live check posted a 13-message thread to the real workspace, and a replay from
cursor 0 posted nothing.

## 6. Files

| File | Change |
|---|---|
| `backend/app/switch/stream.py` | Reconnect, resume, terminal conditions, `gap`, `drop()` |
| `backend/app/switch/client.py` | Timeouts, idempotent-only retries, `SwitchError` for network failures |
| `backend/app/agents/runtime.py` | Reconnect wiring, room re-claim, gap re-read, durable ledger, event validation |
| `backend/app/agents/handoff.py` | ACK records its Switch event id |
| `backend/app/switch/approvals.py` | `not_revoked` check |
| `backend/app/agents/executor.py` | Passes revocation; re-checks after the claim |
| `backend/app/switch/revoke.py` | New: revoke CLI |
| `backend/app/incidents/store.py` | Ledger, sticky revocation, `is_revoked` |
| `backend/migrations/versions/0004_switch_events.py` | New table |
| `backend/app/slack/messages.py`, `bridge.py` | Switch-level messages |
| `backend/app/switch/livecheck.py` | New: executable live demonstration |
| `backend/tests/test_switch_reliability.py`, `tests/integration/test_switch_live.py` | New tests |
| `Makefile` | `switch-livecheck`, `switch-live-test`, `switch-revoke` |

## 7. Approving from Slack (Switch's own Slack connection)

Incident rooms and approval cards can run in Slack instead of Mattermost. **Switch stays the decider.** The card is a Switch approval request posted through *Switch's* Slack connection. The human answers it there, and Switch checks that they're allowed to. BLACKBOX then re-reads the decision from Switch and verifies it exactly as in §2.7. The BLACKBOX notification bot (docs/11) can't approve anything.

### Setup (done 2026-11-07)

1. **The Agent Switch Slack app**, built from Switch's own manifest (`GET /gateway/collaborations/setup/slack`).
   - It uses Socket Mode, so no public URL is needed.
   - The `agent_view` block and `assistant:write` were removed, so the workspace doesn't get Slack's irreversible "Agent" mode.
   - It has 20 bot scopes, 4 message events and interactivity.
   - It is **separate** from the BLACKBOX notification app. Giving Switch the BLACKBOX token fails: `users.list` → `missing_scope`.
2. **The Switch connection**, made in the Gateway (Messaging apps → Connect → Slack) with the app's `xapp-` and `xoxb-` tokens. `make switch-bridges` lists the connection ids.
3. **The account link.** The IC's Slack account was claimed for the Switch user who owns the agents (`POST /gateway/collaborations/{bridge}/identities`). The 0.31.0 image links immediately; newer Switch sources add a `!link-account <code>` confirmation.
4. **BLACKBOX configuration.**
   - In `.env`: `SWITCH_INCIDENT_BRIDGE_ID=<slack connection id>` and `SWITCH_IC_CHAT_USERNAME=<slack username>`.
   - Then `make agents-bootstrap`, with the IC posting the printed check token in the new Slack intake channel. This pins the IC's **Slack** participant id (`@switch-slack-<bridge>-<user>:localhost`) as the only approver.

### How to answer

- **Press Approve or Reject on the card.** This is verified live.
- Or **reply with the card's handle**, for example `A1 approve` or `A1 reject`. The handle is printed on the card, and Switch resolves it to exactly one card.
- A message anywhere else, a reply to the BLACKBOX notification, or `/blackbox approve` does nothing.

### Live findings and the changes they forced

| Finding | Evidence | Change |
|---|---|---|
| **One reply answered two cards.** With every approval under one Switch session (`INC-1042`), a single `approved` answered card A1 *and* card A3, 6 s apart; the IC's later `reject` on A3 was refused. Switch matches a bare reply by the thread it is in. | Run `livecheck-20261107T085208Z.json`: A3 `answered/approve` at 08:52:43 | **One Switch session per approval** (`session_id = <incident>-<remediation>`), and instructions now say to use the button or the handle (`A1 approve`), never a bare word. |
| **The Slack connection silently died.** Socket Mode sessions went stale repeatedly ("disconnected for 1795+ seconds"), and a session reopened at 11:19 delivered no events, so presses and replies never reached Switch. | switch-core log, 09:55–11:30 | Operational: `docker restart blackbox-switch-switch-1` reopened a working session. If an approval press does nothing, check `docker logs blackbox-switch-switch-1 \| grep socket_mode`. BLACKBOX fails closed meanwhile (the request just expires). |
| **Duplicate card titles across test channels.** The IC pressed an old, already-answered card. | Three `livecheck-*` channels each have an "APR-1042-1" card | Use the channel named in the prompt; archive old test channels. |

### Result

The approval steps passed on Slack (`livecheck-20261107T113129Z.json`, 4/4):

- Approve press recorded for `@switch-slack-…-<ic>`; all 11 verification checks passed; replay refused.
- Reject press refused (`answer_is_approve`).
- Revoked card closed in Slack and refused.
- Content change → 409 `REQUEST_CONFLICT`.

The other live-check steps passed on Slack in the earlier run (`livecheck-20261107T085208Z.json`).

### Full autonomous run on Slack (INC-1043, 2026-11-07)

Stack: `make stack-up` with `SWITCH_INCIDENT_BRIDGE_ID` set to the Slack connection. Switch was restarted first to get a fresh Socket Mode session.

| Time (UTC) | Event |
|---|---|
| 11:40:17 | Bad deploy #842 (`9d33906`, pool 50 → 10) |
| 11:40:30 | Monitor alert → Control → Slack intake room |
| 11:40:31 | Sentinel opens INC-1043 and Slack channel `#incident-payment-service-1043-3bb1` |
| 11:40:50 | Evidence collected; HND-1043-001 → RootCause, ACKed |
| 11:40:51 | Root cause: `config/database.yml` `pool_size` |
| 11:41:07 | Claude Code patch `1e5032e`; 11/11 tests; new regression test fails on the bad commit |
| 11:41:08 | HND-1043-002 → Recovery; validation passed; Switch card A1 opened in Slack |
| 11:42:06 | **IC pressed Approve in Slack** (Switch: `@switch-slack-…-<ic>`) |
| 11:42:07 | Executor re-verified with Switch and claimed the approval; deploy #843 |
| 11:42:38 | Recovery verified (3/3 samples) |
| 11:42:39 | Report and 5 preventive tasks; `PREVENTIVE_TASKS_CREATED` |

Before → after (measured): 5xx **21.31% → 0.22%**, p50 **4054 → 100.8 ms**, DB pool **100% → 24.2%**.

- Alert to approval request: 38 s. Alert to resolution, including the human: 2 min 9 s.
- The BLACKBOX notification bot posted 23 thread messages to `#incidents`, with 0 failures; bridge health `healthy`.
