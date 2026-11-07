# 10 — Switch Integration Proof (Task 10)

| | |
|---|---|
| **Result** | **All six steps were demonstrated against a real Switch server.** A test approval was created in Switch, answered by the incident commander's Mattermost identity, and independently verified (ALLOWED) by a separate process. Three negative controls were all REFUSED. |
| **Run** | 2026-11-06 19:53–19:54 UTC (2026-11-07 local), run id `20261106195356-a2fb`; evidence file `backend/data/switch/evidence/20261106195356-a2fb.json`; audit-log seqs 5–13 |
| **Switch** | Self-hosted `0.31.0` (published images), agent bridge `http://127.0.0.1:8100`, bundled Mattermost 11.9 |
| **Code** | `backend/app/switch/` (`client.py`, `stream.py`, `approvals.py`, `proof.py`, `mattermost_harness.py`, `config.py`); tests in `backend/tests/test_switch_approvals.py` |

## 1. What was proven, and how

| # | Step | Mechanism (all verified Switch endpoints, docs/01 §19–§20) | Live result |
|---|---|---|---|
| 1 | Register one BLACKBOX agent | Gateway login → `POST /gateway/api-keys` (a personal registration key, so the IC owns the agent) → `POST /agents` with an `always_on` profile | `blackbox-recovery`, id `dfe02b51-…`, 53 operations. Credentials saved to `backend/data/switch/agent.json` (mode 0600, git-ignored) and reused on later runs. |
| 2 | Create an incident room | `POST /agents/{id}/ops/create_room` (bridged to Mattermost; members: the agent and `user`) | `incident-proof-20261106195356-a2fb`, id `7e995f8b-…`; the channel exists in Mattermost |
| 3 | Send a message from the agent | `POST /agents/{id}/message` | `event_id sw_ebbecf6b…`; visible in the channel and in `read_context` |
| 4 | Receive an event from Switch | The agent's WebSocket `/agents/{id}/connection/ws` (`scope=all`, protocol 8 declared; the server negotiated 9) | A `message` event, `seq 9007194959773775`, from `user`, `addressed=false`. The sender's participant id is captured as the **approver allowlist**. |
| 5 | Create a test approval decision | The binding is persisted first (`audit_events` seq 9, `approval.bound`), then `POST /agent-sessions/{INC}/approvals` | A card with Approve and Reject buttons in Mattermost. The IC pressed **Approve**. Switch pushed `approval_outcome {state: answered, answer: approve, answered_by: @switch-mattermost-…-user:localhost}`. |
| 6 | Read and independently verify | **A separate process** (`python -m app.switch.proof verify`) loads the binding **only from the append-only audit log**, re-derives `request_id`, re-hashes the stored card body, and **re-reads the decision from Switch** (`GET /agent-sessions/approvals/outcomes`, falling back to an identical re-open). It then checks the state, answer, allowlisted approver, expiry and first use. | **ALLOWED**, 10/10 checks passed. Re-running `make switch-verify` later gave the same result (audit seq 13). |

**Negative controls (same run, same approval):**

| Control | Expected | Result |
|---|---|---|
| A. Verify before any human decision | REFUSED | REFUSED: `state_answered`, `answer_is_approve`, `approver_allowlisted`, `answered_before_expiry` failed |
| B. The IC types `APPROVE INC-PROOF-…` as plain chat, then verify | REFUSED; state unchanged | REFUSED, Switch `state=open`. **Chat text does not create a decision.** |
| C. Verify with a tampered binding (a different patch hash) | REFUSED before contacting Switch | REFUSED: `request_id_matches_binding`, `card_shows_patch_hash` failed |

**Unit tests:** 14 new tests cover the verifier's fail-closed rules: approved, re-open fallback, open, rejected, stranger, expired, expired now, answered late, second use, tampered hash, modified card body, Switch unreachable, and other requests ignored. Backend total: **28 passed**. Frontend: **8 passed**. Lint: clean.

## 2. What is real and what is the test harness

| Element | Real / harness |
|---|---|
| Switch server, agent registration, room, messages, WebSocket events, approval card, Switch's answerer authorization, the outcome push, the read-back | **Real Switch** |
| Mattermost channel and the button press | **Real Mattermost**. In `--as-ic` mode the **test harness** (`mattermost_harness.py`) logs in to Mattermost *as the human* (with that person's password) and posts or presses exactly as the UI would. Switch still applied its own authorization to the press. Every harness action is printed as `[HARNESS acting as IC]`. Product code never imports the harness. |
| The approved "artifact" | A **test artifact**: the hash of a no-op JSON. **Nothing is executed** on approval in this phase. |
| Interactive mode (`make switch-proof`, a person clicks in Mattermost) | Implemented, **not executed in this session** (it needs a person at the keyboard) |

## 3. How to reproduce

The Switch stack must be running (see `docs/01` §16), and `.env` must contain the `SWITCH_*` settings from `.env.example`.

Run with a human acting in Mattermost when prompted (post a message, then press Approve):

```bash
make switch-proof
```

Run unattended, with the test harness acting as the IC (requires `MATTERMOST_IC_PASSWORD`):

```bash
make switch-proof-auto
```

Re-verify the latest stored approval independently, in a new process:

```bash
make switch-verify
```

## 4. Findings, limitations and fallbacks

| # | Finding | Impact | Handling |
|---|---|---|---|
| F1 | **Switch has no "decision" API.** The only human-decision primitive is the approval request in the session-activity API, built for Switch Console's session hosts and not documented for third-party agents. It worked end to end for a custom agent. | The governance story depends on an undocumented API | Pin Switch `0.31.0`; keep this proof as a regression test before any upgrade. **Fallback, if it ever becomes unavailable**, must be clearly labelled: *"BLACKBOX-local approval (not a Switch decision)"*: an approval recorded in BLACKBOX's audit log through an authenticated UI action, announced in the Switch room. It would **not** satisfy "genuine Switch decision" and must be disclosed as a deviation. A second option is a Console-hosted Claude Code Recovery agent, whose native permission prompt produces the same card through Switch's supported path (more complex). |
| F2 | **New rooms aren't delivered on an already-open `scope=all` connection.** The first live run (`20261106194811-30f1`) timed out at step 4: the human's message reached Switch (it was visible in `read_context`) but not the stream. | Agents miss events in rooms created after they connect | **Fixed:** claim the room on the live connection with `connect_to_room` (plus the connection header) right after creating it, or after being invited. The second run received the event. The architecture docs must make this an agent-runtime rule. |
| F3 | **Switch posts an automatic greeting in the agent's name** when it joins a room (*"Hi! I'm here to help — mention @blackbox-recovery anytime."*). | The war-room feed would show text the agent never wrote | Turn off **Agent greetings** in the Gateway (Messaging Apps setting, per the Switch docs) or label such rows as Switch-generated. That they carry the `com.switch.auto_reply` marker is **unverified**. |
| F4 | **There is no `GET` by id for an approval.** Read-back uses the undelivered-outcomes list, or an identical re-open, which returns 422 after expiry. | Verification after expiry, or after `/delivered`, is impossible | By design. The proof never calls `/delivered`; the executor (later) calls it only after recording its result. Expiry makes verification fail closed. |
| F5 | **`answered_by` is a bridge-scoped participant id**, not a person or user id | An allowlist must use that exact string | It is captured from the IC's own message in the room (step 4), so the allowlist is the identity Switch actually reports |
| F6 | **The agent is owned by the Gateway admin** (whose personal key registered it), so it inherits admin authority in Switch | Accepted for the demo (`docs/08` D-10) | PROD: a non-admin owner account |
| F7 | Each run creates a new room and leaves it open | Clutter in Mattermost | Room archiving belongs to the demo-reset work (backlog P0-21) |
| F8 | Registration uses `overwrite=true` when no saved credentials exist | Re-running after deleting `agent.json` rotates the agent's key | Intended for a single-owner demo. Production uses explicit provisioning. |
| F9 | Mattermost runs emulated on this arm64 host and has hung before | Runs can fail at room creation | Restart Mattermost if `create_room` times out (`docs/07` SP-7) |

## 5. Files added or changed

- **Added:**
  - `backend/app/switch/{__init__,config,client,stream,approvals,proof,mattermost_harness}.py`
  - `backend/tests/test_switch_approvals.py`
  - this document
- **Changed:**
  - `backend/pyproject.toml` / `uv.lock`: `httpx` and `websockets` became runtime dependencies.
  - `.env.example`: the documented `SWITCH_*` / `MATTERMOST_*` settings, without values.
  - `Makefile`: `switch-proof`, `switch-proof-auto` and `switch-verify`.
  - `README.md`: a Switch proof section.
- **Local only (git-ignored):** `.env` gained the local Switch stack's admin login and the IC's Mattermost password, copied from the Switch stack's own generated `.env` and never printed. Also local: `backend/data/switch/agent.json` (0600) and the evidence JSON.
