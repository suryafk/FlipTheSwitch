# 01 — Switch Reconnaissance

| | |
|---|---|
| **Status** | Pre-implementation investigation. No application code written. |
| **Source inspected** | `github.com/suryafk/switch` (fork of `sandbox-quantum/switch`), branch `main`, commit `0977b48e77d9d83e9b17d8609af4c81d685d6d02` (2026-11-06). switch-core `0.31.0`. Agent protocol: server `speaks: 9, accepts: 1`. |
| **Method** | Read the official docs synced into the repo (`docs/official/`), then verified each claim against the server source (`core/switch_core/`). Where the two disagree, **the code wins**, as the repo's own `CLAUDE.md` says. |
| **Runtime verification** | **S1–S5 run on 2026-11-07** against the published `0.31.0` images (`ghcr.io/sandbox-quantum/switch-core:0.31.0`). Result: 38 pass / 2 non-blocking observations (§19). **S6 and S7 run the same day: 18/18 pass, plus one restart finding** (§20). |
| **Companion** | [`01-SWITCH-CAPABILITY-MATRIX.md`](01-SWITCH-CAPABILITY-MATRIX.md) |

All file references are relative to the Switch repo root at the commit above.

### Classification legend

| Tag | Meaning |
|---|---|
| **OFFICIAL** | Documented in `docs/official/` (published at docs.switchagents.ai) **and** present in code |
| **SOURCE-ONLY** | Present in code, but undocumented or documented for a different use than BLACKBOX's |
| **CUSTOM** | Not provided by Switch; BLACKBOX must build it |
| **NOT-72H** | Cannot reasonably be delivered in 72 hours |
| **UNKNOWN** | Could not be verified from the docs or the code |

---

## 0. Headline findings (read this first)

1. **Switch is real, open source and self-hostable.** A Docker Compose stack runs Postgres, switch-core, a bundled Mattermost and the Gateway. Agents speak HTTP plus a push stream. A custom Python agent is a first-class client.
2. **Switch has no "Decision" primitive.** No operation, table or event is called *decision* in the sense `Blackbox.md` uses. The closest real mechanism is the **approval request** in the *session activity* API (`/agent-sessions/{session_id}/approvals`):
   - It has a server-side state machine: open, answered, expired, closed.
   - It enforces server-side authorization of the answerer.
   - It records `answered_by` and `answered_at`.
   - It enforces an expiry of at most 24 h.
   - It renders a card with buttons in Mattermost, Slack, Teams, Discord and Telegram.

   It was built for Switch Console's coding-session hosts, not for third-party workflow approvals. Using it from a custom agent is **SOURCE-ONLY** and must be smoke-tested first.
3. **Tasks are not usable.** The internals doc says *"The task protocol is present but not ready for use. Handle the frames; build nothing on them."* The code agrees: no task operation is registered in `operations/definitions.py`. Only the event kinds are reserved, in `protocol/event_buffer.py:176`. The user-facing doc `using/hand-off-work.md` describes delegating tasks, which **contradicts the code**. → BLACKBOX tasks are **CUSTOM**.
4. **The transport has moved past the official doc.** `internals/agent-protocol.md` says "SSE down, HTTP up". The code says the live connection is now a **WebSocket at `/agents/{id}/connection/ws`** (protocol revision 8+). SSE and `POST /connection/beat` are kept *"for a compatibility window"*, and both are slated for removal (`artifacts.yaml`, revision 8 note; `handlers.py:772-779`).
5. **Structured handoff payloads have no native slot.** `POST /agents/{id}/message` accepts a `metadata` field (`api/schemas.py:99-102`), but the handler drops it (`handlers.py:487-509`). Handoffs must travel in the message **body**, or in a **room document**.
6. **The approver is defined by addressing policy, not by room membership.** Who may answer an approval card is whoever may *address the agent* (`session_activity/service.py:600-632`). A new agent starts owner-only. The incident commander must therefore **own** the agents, or be admitted by their policy, **and** have their Mattermost account **linked** to their Switch user. The standalone `collab` setup links the seeded Mattermost user to the seeded Gateway admin automatically (`deploy/shared_resources/setup.py:357`).

---

## 1. Agent registration and authentication

| | |
|---|---|
| **Exists** | Yes, **OFFICIAL** |
| **References** | `docs/official/internals/agent-protocol.md` §"Writing a client"; `core/switch_core/bridges/agent/api/handlers.py:206` (`POST /agents`), `:318` (`POST /agents/register-known`); `api/schemas.py:20-48`; `protocol/types.py:28-43` (`IntegrationProfile`); `bridges/agent/registration_bootstrap.py`; `gateway/api_keys.py:72` (mint key) |

**Operations**

- `POST /agents`: low-level registration with a full integration profile. **This is the right one for custom Python agents.**
- `POST /agents/register-known`: registration for a known provider type (`claude-code`, `codex`, `opencode`, and so on). Intended for Console-run CLI agents.
- `POST /gateway/api-keys`: mints a personal key of type `registration`. Needs Gateway cookie auth.

**Authentication**

- Registration uses `Authorization: Bearer <registration token>`. The token is either:
  - the deployment `AGENT_REGISTRATION_TOKEN` (key type `bootstrap`), or
  - a personal `registration` key minted in the Gateway.
- All later calls use `Authorization: Bearer <agent api_key>`.
- A registration token cannot open a stream, and an API key cannot register.

**Request schema (`POST /agents`)**, verified in `schemas.py:20`:

```json
{
  "name": "blackbox-sentinel",
  "description": "…",
  "display_name": "BLACKBOX Sentinel",
  "icon_url": null,
  "connector_type": "<free string>",
  "integration_profile": {
    "connection_model": "always_on",
    "message_exchange": true,
    "pre_invocation_mediation": [],
    "post_invocation_mediation": [],
    "event_reporting": []
  },
  "tools": [],
  "models": [],
  "metadata": {},
  "overwrite": false
}
```

The `connection_model` values are `always_on`, `session_addressable`, `session_passive` and `auto_session`. `loadtest/seed.py:25-33,186-197` registers custom agents exactly this way, with `connector_type: "e2e"` and `connection_model: "always_on"`.

**Response:** `{"id": "<agent_id>", "api_key": "<key>"}`. **The API key is returned once.**

**Ownership: critical for BLACKBOX**

- An agent **inherits exactly its owner's permissions** (`internals/identity-and-access.md`).
- Agents registered with the **bootstrap** token are owned by a dedicated non-admin account (`registration_bootstrap.py:1-20`), *not* by the admin.
- Agents registered with a **personal registration key** are owned by the user who minted the key.

**Limitations**

- Names must match `^[a-z0-9][a-z0-9._-]*$`.
- Re-registering a name returns 409 unless `overwrite`.
- Connection models are flagged as *"expected to go"*.
- Allowed `connector_type` values are not validated anywhere we found. That is UNKNOWN as an intended contract.

**Suitable for BLACKBOX:** **Yes.** Register three `always_on` agents with a personal registration key minted by the incident-commander user. Do not use the bootstrap token, so that the commander owns the agents and can answer their approval cards.

---

## 2. Room creation and management

| | |
|---|---|
| **Exists** | Yes, **OFFICIAL** |
| **References** | `operations/definitions.py:923` (`create_room`), `:2524` (`get_room_detail`), `:2552` (`update_room`), `:2633` (`archive_room`), `:2661` (`unarchive_room`), `:220` (`list_rooms`), `:1518` (`list_all_rooms`), `:1649` (`create_room_from_yaml`); `room_service.py` |
| **Operation** | `POST /agents/{agent_id}/ops/create_room`. The body is the arguments object itself, not wrapped. Discover operations with `GET /agents/{agent_id}/ops`. |
| **Auth** | Agent API key. Room-level authorization resolves to the agent's owner. |

**Request schema** (signature at `definitions.py:923-945`):

| Argument | Type and notes |
|---|---|
| `name` | Required |
| `description` | Required |
| `agent_names: list[str]` | Required. The caller is a member only if listed. |
| `user_names` | Messaging-app usernames or e-mails |
| `channel_type` | `channel_public` / `channel_private` / `direct` |
| `bridge_id` | Omit to use the default bridge. On standalone this is **the bundled Mattermost**. |
| `internal_only` | |
| `admin_mode` | |
| `security_config` | |
| `instructions` | The room-level system prompt agents receive on connect |
| `reference_ids`, `package_ids` | |
| `linked_rooms` | |
| `read_visibility`, `write_visibility` | `public` / `private` |
| `group_name` | |
| `roles: list[{name, instructions, exclusive}]` | |
| `include_subagents_for` | |
| `join_event_listeners` | |
| `aliases` | |
| `include_owner` | |

**Response:** `{"id", "name", "transport_room_id", "matrix_room_id" (deprecated), "caller_is_member", "failed_attachments": [...]}`

**Example**

```
POST /agents/{sentinel_id}/ops/create_room
{"name":"incident-payment-service-1042","description":"INC-1042 SEV-1 payment-service",
 "agent_names":["blackbox-sentinel","blackbox-rootcause","blackbox-recovery"],
 "user_names":["user"],"channel_type":"channel_public",
 "instructions":"<incident room brief>",
 "roles":[{"name":"investigator","instructions":"…","exclusive":true}]}
```

**Limitations**

- Room creation is an **agent** operation. The BLACKBOX ingestion service must call it *as one of the agents* (we suggest Sentinel), or the Gateway must be driven with a cookie.
- Archive is reversible metadata, not deletion.
- The skill tells agents to *"Confirm with the user first"*. That is etiquette, not enforcement.

**Suitable for BLACKBOX:** **Yes.** This gives one bridged Mattermost channel per incident.

---

## 3. Room membership and agent roles

| | |
|---|---|
| **Exists** | Yes, **OFFICIAL** |
| **References** | `definitions.py:1436` (`invite_agent_to_room`), `:1460` (`remove_agent_from_room`), `:1485` (`add_users_to_room`), `:758` (`list_participants`); roles `:1083-1201` (`list_roles`, `get_role_detail`, `define_role`, `edit_role`, `delete_role`, `assume_role`, `release_role`); `docs/official/using/hand-off-work.md`; `docs/official/internals/identity-and-access.md` §"Addressing policy" |
| **Auth** | Agent API key. `define_role`, `edit_role` and `delete_role` require write access to the room. |

**Schemas**

| Operation | Arguments | Returns |
|---|---|---|
| `invite_agent_to_room` | `{room_id, agent_name, include_subagents}` | `{ok}` |
| `add_users_to_room` | `{room_id, user_names}` | `{ok, added, unresolved}` |
| `define_role` | `{name, instructions, exclusive}` | |
| `assume_role` | `{role}` | `{role, instructions}` |

`list_participants` returns entries of `{id, name, type, status, alias}`.

**What a Switch "role" is**

- It is an **addressable instruction bundle**. `@role` reaches its holder.
- An exclusive role is a lease that auto-releases when the holder's session ends.
- **A role changes how an agent works, not what it is allowed to do** (`hand-off-work.md`). Permissions come only from the owner.
- An agent may hold **one role at a time, across all rooms**.

**Permission boundaries** come from:

1. Owner inheritance, which governs resource authorization.
2. The **addressing policy**: who may make the agent respond. It is set in the Gateway by the owner or an admin. An agent cannot widen its own policy.

**Limitations: no per-agent tool permissions in Switch**

- Switch does not stop Recovery from calling any operation its owner may call.
- Sentinel, RootCause and Recovery owned by one user have **identical Switch authority**.
- The tool and permission separation in `Blackbox.md` must be enforced **inside BLACKBOX**: separate processes, separate API keys, and per-agent tool allowlists.
- Switch Trust guardrails (`docs/design/switch-trust-guardrails-v1.md`, mediation routes `handlers.py:1825-1900`) exist but are commercial or advanced. Treat them as **NOT-72H**.

**Suitable for BLACKBOX:**

- Membership: **yes**.
- Roles: **optional**. They are useful for addressing `@investigator` and `@release-engineer`, but they are not a security boundary.

---

## 4. Room messages and shared context

| | |
|---|---|
| **Exists** | Yes, **OFFICIAL** |
| **References** | `definitions.py:788` (`post_message`), `:827` (`send_targeted_message`), `:657` (`read_context`), `:252` (`connect_to_room`); `handlers.py:487` (`POST /agents/{id}/message`), `:1714` (`GET /agents/{id}/rooms/{room_id}/history`); `docs/official/using/shared-context.md`; `docs/official/internals/life-of-a-message.md` |
| **Auth** | Agent API key. `post_message`, `send_targeted_message` and `read_context` without a `room_id` need the `X-Switch-Connection-Id` header bound to a claimed room. `POST /agents/{id}/message` needs **no connection**. |

**Schemas**

| Call | Arguments | Returns |
|---|---|---|
| `post_message` | `{body, thread_id?}` | `{event_id, trust?}` |
| `send_targeted_message` | `{body, target_names?, target_roles?, thread_id?}` | `{event_id, target_statuses: {name: live\|awaiting_manual_poll\|no_session\|disconnected\|not_permitted}, trust?}` |
| `read_context` | `{limit=50, since?, before?, room_id?}` | `{threads: [{root, replies}], truncated, oldest_timestamp}` |
| `POST /agents/{id}/message` | `{room_id, content, metadata}` (**`metadata` is ignored**) | `{ok, event_id, trust?}` |

A `read_context` entry has the shape `{id, kind: message|room_join, sender, sender_name, body, timestamp, attachments}`.

**`connect_to_room` returns:** `{agent_id, room_id, name, description, participants, instructions, reference_types, references, documents, packages, linked_rooms, roles, warning}` (`definitions.py:271-279`). This is the "late joiner reconstructs state" primitive.

**Limitations**

- Messages are plain text or Markdown with attachments. There is **no structured or typed payload field** on the wire.
- Switch Trust may redact or block a message. The result says so, and a blocked message raises an error.
- `read_context` uses ISO-8601 for `before` but returns `oldest_timestamp` in epoch milliseconds.

**Suitable for BLACKBOX:** **Yes.** Use it for every visible agent post. Recommended pattern: a human-readable summary, then a fenced JSON block carrying the typed handoff.

---

## 5. Agent Protocol event subscription

| | |
|---|---|
| **Exists** | Yes, **OFFICIAL** (with drift; see §6) |
| **References** | `docs/official/internals/agent-protocol.md` §"Connections", §"Claiming a room", §"The event stream", §"The event catalog"; `handlers.py:747` (`GET /agents/{id}/events`), `:1227` (WebSocket), `:1475` (`/connection/subscribe`), `:1577` (`/connection/placements`) |

**Open parameters:**

| Parameter | Values |
|---|---|
| `connection_id` | Client UUID, **reused across reconnects** |
| `scope` | `single` / `all` |
| `filter` | `all` (default) / `addressed` |
| `start_from` | `head` or a sequence |
| `start_at` | Revision 9 position |
| `rooms` | Comma-separated room ids |
| `protocol` | Revision number |
| `protocol_accepts` | |
| `client` | |
| `client_version` | |
| `spawn_capable` | |
| `expected_generation` | |

**Domain events:** `message`, `command`, `room_join`, and the reserved task kinds `task_*`.

**Control frames:**

- `connection_state`: always the first frame.
- `gap`
- `evicted`
- `subscription_changed`
- `room_released`: revision 6+.
- `session_command`: revision 5+.
- `approval_outcome`: revision 4+, and **only on `scope=all` connections that declare `protocol>=4`** (`protocol/stream.py:223-240`; `agent_connections.py:100`).

**`message` payload:** `{addressed, sender, sender_name, sender_kind?, on_behalf_of?, message_id, body, timestamp(ms), thread_id, attachments}`. The envelope is `{type, room_id, bridge_id, channel_type, payload, sequence}`.

**Limitations**

- Rooms must be declared **on the open URL**. A room claimed later loses its catch-up.
- One connection per agent may *act* in a room.
- At most 32 connections per agent.
- Reopening a live `connection_id` is a takeover.

**Suitable for BLACKBOX:** **Yes.** Each agent should open one `scope=all` connection with `filter=all` and declare `protocol>=4`, so it receives approval outcomes.

---

## 6. Transport details (SSE / WebSocket / HTTP), from the actual implementation

| | |
|---|---|
| **Exists** | Yes. The **code supersedes the official doc**. |
| **References** | `handlers.py:747-811` (event endpoint, three modes), `:1168-1215` (beat), `:1227-1300` (WebSocket); `artifacts.yaml` agent-protocol revision notes 2–9 (lines ~157-234); `loadtest/agents.py:1-20` (a reference WebSocket client in Python) |

| Mode | How | Status in code |
|---|---|---|
| **WebSocket** | `GET /agents/{id}/connection/ws?...`. Frames are JSON `{event, data, id}`. The server sends `ping` and the client answers `{"type":"pong","cursor":n}`. Refusals arrive as a `refused` frame, then close code `4000+HTTP status`. | **Primary** since revision 8 ("the live connection") |
| **SSE** | `GET /agents/{id}/events` with `Accept: text/event-stream`, plus `POST /agents/{id}/connection/beat` every 2 s (6 s TTL) | **Compatibility window.** It *"goes once `switch.agents.connected{transport:sse}` stays at zero"*. Still the only transport in the official doc. |
| **Long poll** | `GET /agents/{id}/events` without the SSE `Accept` header. Returns 204 when empty. | Fallback |
| **HTTP up** | `POST /agents/{id}/ops/{name}`, `POST /agents/{id}/message`, media, and `/agent-sessions/*` | Stable |

**Heartbeat (SSE)**

- `{connection_id, cursor, generation?}`. Since revision 2, the beat carries the connection *incarnation*.
- Errors carry `code`: `taken_over` (terminal, **do not reopen**), `no_stream`, or `unfenced`.

**Auth:** Bearer API key. On the WebSocket it is checked once, at open.

**Limitations:** the drift between the doc and the code is itself the risk. A client written to the official doc (SSE) works today, but is on a deprecation path with no date.

**Suitable for BLACKBOX:**

- **Recommended:** WebSocket, declaring a revision between 4 and 8. Use `loadtest/agents.py` as the reference implementation in Python.
- **Fallback:** SSE plus beat, which is simpler and documented, for the first spike.
- Wrap the transport behind a single `SwitchConnection` interface.

---

## 7. Addressed versus overheard events

| | |
|---|---|
| **Exists** | Yes, **OFFICIAL** |
| **References** | `agent-protocol.md` §"The `addressed` filter"; `core/switch_core/delivery/addressing.py:1-20` (intent and permission kept separate); `identity-and-access.md` §"Asymmetric refusal" |

**Rules**

- With `filter=all`, every message in the agent's rooms is delivered. **Overhearing is the default.** `payload.addressed` tells the agent whether the message was aimed at it.
- With `filter=addressed`, only notifiable events are delivered:
  - `message` when `addressed` is true
  - `room_join` when `listening` is true
  - all task kinds
  - **never** `command`
- A message is addressed by `@name`, `@alias`, `@role`, or by being in a 1:1 room.
- A message that is addressed but **not permitted** is demoted to chatter, and the agent posts one refusal.

**Limitations**

- Scoping is by room membership only. There is no topic or keyword filter.
- "Not every conversation in every room" (`Blackbox.md`) is achieved by membership: Sentinel joins only the intake room and incident rooms.

**Suitable for BLACKBOX:** **Yes.** Sentinel uses `filter=all` to overhear alerts and human reports. RootCause and Recovery can use `filter=all` and act only on `addressed` messages, or on handoffs addressed to them.

---

## 8. Agent-to-agent communication and handoffs

| | |
|---|---|
| **Exists** | Partially. **OFFICIAL** primitives, **CUSTOM** handoff semantics |
| **References** | `definitions.py:827` (`send_targeted_message`); `docs/official/using/hand-off-work.md` (roles); `identity-and-access.md` (`owner_agents` addressing subject); `docs/official/building/` (worked examples) |

**Mechanism**

- An agent-to-agent handoff is an **addressed room message**: `send_targeted_message(target_names=["blackbox-rootcause"], body=…)`.
- The target receives a `message` event with `addressed: true`.
- `target_statuses` reports the target's reachability, or `not_permitted`.
- Agents owned by the same user can address each other under the default `owner_and_owner_agents` policy (`SKILL.md` §"Questions and approvals").

**There is no native handoff object, schema or acknowledgement.** The tasks that would provide one are not ready (§10).

**Limitations**

- The payload is the message body. Structure must be serialized into it.
- No delivery receipt beyond `target_statuses` at send time.
- The ack and retry protocol is BLACKBOX's job.

**Suitable for BLACKBOX:** **Yes, with a CUSTOM envelope.**

- The body is a short human summary plus a fenced `json` block holding the `Blackbox.md` §8 handoff object.
- The receiver posts an addressed ack that carries the `handoff_id`.
- The canonical copy lives in BLACKBOX's SQLite.

This keeps handoffs visible in the room and does not depend on natural-language parsing.

---

## 9. Room documents and persistent memory

| | |
|---|---|
| **Exists** | Yes, **OFFICIAL** |
| **References** | `definitions.py:539` (`load_internal_documents`), `:566` (`create_room_document`), `:606` (`update_room_document`), `:639` (`delete_room_document`), `:482` (`list_references`); `docs/official/using/shared-context.md`; `docs/official/internals/rooms-and-resources.md` |
| **Auth** | Agent API key and a claimed room (connection id header) |

**Schemas**

| Operation | Arguments | Returns |
|---|---|---|
| `create_room_document` | `{name, description, instructions, content}` | `{document_id}` |
| `update_room_document` | `{document_id, name?, description?, instructions?, content?}` | |
| `load_internal_documents` | `{ids}` | `[{id, description, content}]` |

**Limitations**

- Content can be at most **1 MiB** (`definitions.py:563`).
- **Only the creating agent can update or delete** a document. Humans can delete one from the room UI.
- There is no versioning or history: `update_room_document` overwrites.
- There is no structured query. Content is text.
- Documents are listed in the `connect_to_room` payload, so a late joiner sees them.

**Suitable for BLACKBOX:** **Yes, as the human- and agent-readable projection of incident state** (an "incident dossier" document). It is **not** the source of truth: it overwrites rather than appends, and it has a single writer. Because only its creator can update a document, either:

- one agent owns the dossier document, or
- each agent owns its own section document.

---

## 10. Task creation, assignment, updates and completion

| | |
|---|---|
| **Exists** | **No.** Reserved only. |
| **References** | `docs/official/internals/agent-protocol.md` §"Task events": *"The task protocol is present but not ready for use. Handle the frames; build nothing on them"*. The operation catalog lists `delegate_task`, `accept_task`, `update_task`, `finalise_task`, `cancel_task` and `list_tasks` as *"present but not ready for use"*. **Code:** none of these is defined in `operations/definitions.py` (grep finds no match); the event kinds appear only in `protocol/event_buffer.py:176`; `protocol/types.py:29-30` drops a *"retired `task_protocol` key"*. `console/.../switch-skill/SKILL.md` §"Tool index" lists no task tools. |
| **Contradiction** | `docs/official/using/hand-off-work.md:109-136` describes delegating and finalizing tasks as a user-facing feature. |
| **Operation / schemas** | UNKNOWN. Not callable. |

**Suitable for BLACKBOX:** **No. CUSTOM.** BLACKBOX keeps its own task table (investigation, patch, tests, approval, verify, postmortem and preventive tasks) and renders it into:

- room messages, and
- a "task board" room document.

Preventive engineering tasks are BLACKBOX records, optionally mirrored as room messages or documents.

---

## 11. Decision creation and approval workflows

| | |
|---|---|
| **Exists** | **No "decision" primitive.** The **approval request** mechanism is **SOURCE-ONLY** for BLACKBOX's use. |
| **References** | `core/switch_core/bridges/agent/api/activity_routes.py:231` (open), `:264` (close), `:274` (delivered), `:286` (outcomes); `core/switch_core/session_activity/service.py:358-516` (open and answer logic), `:55-74` (limits), `:96` (`Decision` literal); `session_activity/bridge_answers.py` (button press and typed `A<n> yes`); `core/switch_core/gateway/agent_sessions.py:160,169` (Gateway list and answer); `core/switch_core/bridges/collaboration/*/adapter.py` (`draws_session_activity = True` on Mattermost `:442`, Slack `:487`, Teams `:793`, Discord `:582`, Telegram `:586`); `docs/session-activity-handoff.md`; `SKILL.md` §"Questions and approvals" |
| **Intended purpose** | *"What an agent's session host reports … its approval requests"* (`activity_routes.py:1-6`). Built so a coding agent's tool-permission prompt can be answered from chat. |

**Create an approval**

`POST /agent-sessions/{session_id}/approvals`, with Bearer agent API key. The agent is taken from the key, so a host can only write for its own agent.

Request (`activity_routes.py:120-132`, `extra="forbid"`):

```json
{
  "request_id": "<≤200 chars, unique per (agent, session)>",
  "turn_id": "<≤200 chars>",
  "kind": "approval",
  "title": "<1-500 chars>",
  "detail": "<≤4000 chars or null>",
  "options": [
    {"id": "approve", "label": "Approve remediation", "decision": "accept"},
    {"id": "reject",  "label": "Reject",              "decision": "decline"}
  ],
  "questions": [],
  "room_id": "<switch room id or null>",
  "thread_id": null,
  "expires_at": "<ISO-8601, ≤24h ahead, or null>"
}
```

- `decision` is one of `accept`, `acceptForSession`, `decline` and `cancel`.
- An approval takes 1–10 options and no questions.

Response (`ApprovalView`, camelCase): `{sessionId, requestId, kind, state: open|answered|expired|closed, answer, answers, answeredBy, answeredAt, expiresAt, deliveredAt}`

**Server-enforced semantics** (`service.py:358-516`)

- **Idempotent and content-bound.** Re-opening the same `request_id` with *identical* content returns the existing row, including its current state. Different content raises `REQUEST_CONFLICT`. → Encoding the incident ID, remediation ID, **patch hash** and environment into `request_id` and `detail` binds the approval to the exact artifact, enforced by the server.
- **Membership check.** The agent must be a member of `room_id`.
- **Expiry.** A request may stay open for at most 24 h. A maintenance loop expires overdue requests every 5 s (`docs/session-activity-handoff.md`). An answer that arrives after expiry is refused and the state becomes `expired`.
- **Answer authorization** (`service.py:600-632`). The answerer must be allowed to *address the agent* under its addressing policy, judged in the request's room. With no room, only the agent's owner may answer. Answerers are either:
  - a platform person, recorded by their `@…` participant id, or
  - a Gateway or Console user, recorded as `user:<id>`.
- **An agent cannot answer through the agent API.** No agent-bridge route answers. Answers enter only through the platform bridge (`bridge_answers.py`) or `POST /gateway/agent-sessions/{agent}/{session}/approvals/{request}/answer` (Gateway cookie).
- **Close and revoke.** `POST /agent-sessions/{session_id}/approvals/{request_id}/close` moves an *open* request to `closed`. The agent can withdraw a request, but **there is no human-side revoke after an approval is answered**.

**Human UX in Mattermost**

- The card is posted as the agent's bot, with action buttons.
- A typed answer uses the card's handle: `A3 yes`, or a bare yes or no as the first reply to the card.
- **The `Blackbox.md` phrase `APPROVE INC-1042` is not a recognized grammar.** It would arrive as an ordinary message.

**Limitations and unknowns**

- **UNKNOWN:** whether a card renders for a custom (non-Console) agent with an arbitrary `session_id` and `turn_id`, and no reported session activity. The publisher is gated on the agent being online (`agent_online=connections.is_live`, `collaboration_core.py:346-357`). **This must be smoke-tested first** (§17, test S5).
- **UNKNOWN:** whether the Gateway web frontend renders approvals. No match was found in `gateway/src`; Switch Console does render them.
- `detail` is capped at 4000 chars, so the full diff will not fit. Put the hash and a summary in the card, and the diff in a room document or message.
- `docs/session-activity-handoff.md` still says *"Nothing here is merged or released"*. The code is on `main`, so that header is stale. Treat the API as **young**.

**Suitable for BLACKBOX:** **Yes. This is the best available stand-in for the "genuine Switch decision"**, pending S5. If S5 fails, the fallback is described in §18, Risk 1.

---

## 12. Decision retrieval and verification

| | |
|---|---|
| **Exists** | Partially, **SOURCE-ONLY** |
| **References** | `protocol/stream.py:218-312` (push `approval_outcome`), `session_activity/outcomes.py:1-52` (frame fields and redelivery semantics), `activity_routes.py:274-292` (`/delivered`, `GET /agent-sessions/approvals/outcomes`), `service.py:544-563` |

**Ways for Recovery to learn and verify**

1. **Push.** An `approval_outcome` frame on a `scope=all`, `protocol>=4` stream carries `{session_id, request_id, kind, state, answer, answers, answered_by, answered_at}`. It is a wake signal; the server is the record.
2. **Pull (undelivered).** `GET /agent-sessions/approvals/outcomes` returns every answered or expired request the agent has **not yet acknowledged**, oldest first. The same row keeps being returned until `POST …/{request_id}/delivered`.
3. **Pull (by id, indirect).** Re-POST the identical open body. The server returns the current row unchanged (§11). No dedicated agent-side `GET` by id exists.

**Verification recipe for BLACKBOX's executor** (fail closed on any mismatch):

1. Recompute the patch hash from the staged artifact, and the expected `request_id`.
2. Fetch the row from Switch with method 2 or 3. **Never trust the push frame alone.**
3. Assert all of the following:
   - `state == "answered"`
   - `answer == "approve"`
   - `answered_by` is in BLACKBOX's allowlist of incident-commander identities
   - `answered_at` is before `expires_at`
   - the request's `room_id` is this incident's room
4. Execute.
5. Only then call `/delivered`.

**Limitations**

- No `GET`-by-id route for agents.
- Once marked delivered, the row is no longer returned by method 2. Method 3 still works.
- Gateway `GET /gateway/agent-sessions/approvals` lists only *open* requests, for the owner.
- `answered_by` is an opaque participant id or `user:<id>`. Map it to a person through `list_participants`.

**Suitable for BLACKBOX:** **Yes.** The independent verification and approval binding that `Blackbox.md` demands can be implemented on real Switch state.

---

## 13. Event history and auditability

| | |
|---|---|
| **Exists** | Yes, **OFFICIAL** (history); audit semantics **CUSTOM** |
| **References** | `CLAUDE.md` §"The message bus is PostgreSQL" (messages table, per-room `seq` under an advisory lock, `pg_notify`); `definitions.py:657` (`read_context`); `handlers.py:1714` (`/rooms/{room_id}/history`); `config.py:818` (`agent_event_retention_seconds` defaults to 24 h); `session_activity/service.py:349` (activity pruned after more than 7 days); `approval_requests` table (`migrations/versions/545f80e11f13_…`) |

**What Switch keeps**

- Room messages in Postgres, with a monotonically increasing per-room `seq`. **No message pruning was found in the code.**
- Approval rows, with `answered_by` and `answered_at`.
- A per-agent event buffer kept for **24 h** by default.
- Session-activity items kept for **7 days**.

**What Switch does not give us**

- An append-only or immutable guarantee, signatures, or a per-incident audit export.
- Humans can delete room documents.
- A "Disconnect app" in Console deletes the rooms on that app *along with their history* (`how-connections-work.md`).

**Suitable for BLACKBOX:** **Partially.**

- Use Switch history as **corroborating evidence**. Store Switch `event_id`s and `seq` numbers in BLACKBOX's own append-only SQLite audit log, which is authoritative for the postmortem.
- Do **not** claim Switch provides immutability.

---

## 14. Agent reconnection and session recovery

| | |
|---|---|
| **Exists** | Yes, **OFFICIAL** |
| **References** | `agent-protocol.md` §"Connections", §"The heartbeat", §"The event buffer", §"Easy to miss"; `artifacts.yaml` revisions 2 (incarnation and fencing) and 9 (positions); `handlers.py:1168-1215`; `loadtest/agents.py` (reconnect with backoff, `taken_over` handling) |

**Mechanism**

- Generate the `connection_id` once and reuse it.
- Resume with `Last-Event-ID`, `start_from=<seq>`, or (revision 9) `start_at=<position>`.
- When resume is impossible, the server sends a `gap` frame, which is never silent. After a gap, re-read the room with `read_context`.
- The connection outlives its socket for the heartbeat TTL.
- `taken_over` is terminal.
- `evicted` carries a `code`.
- Cursors only move forward.
- Approval outcomes stay owed until acknowledged, so they survive reconnects.

**Limitations**

- The official doc describes the buffer as in-memory, so a restart resets the sequence and produces a `gap`. The code now keys agent events in Postgres (`migrations/…c4d8e2f6a913_agent_events_source_key.py`). Exact restart behavior is UNKNOWN until tested.
- If two processes share one `connection_id`, they evict each other in a loop.

**Suitable for BLACKBOX:** **Yes.**

- Each agent process persists its `connection_id` and last cursor or position in SQLite.
- On a `gap`, it calls `read_context` and reconciles against BLACKBOX state.
- Handlers are idempotent by `message_id` / `handoff_id` / `request_id`.

---

## 15. Supported chat platform integrations

| | |
|---|---|
| **Exists** | Yes, **OFFICIAL** |
| **References** | `README.md`; `docs/official/deploy/messaging-apps/{slack,microsoft-teams,discord,telegram,mattermost}.md`; `core/switch_core/bridges/collaboration/{slack,teams,discord,telegram,mattermost}/adapter.py`; `docs/old/bridges/*` |

**Platforms**

- Slack, Microsoft Teams, Discord, Telegram and Mattermost.
- All five set `draws_session_activity = True`, so all five render approval cards.
- Mattermost is bundled with the standalone stack (profile `collab`), and seeded with a team, a human user and a bridge registration.

**Limitations**

- Slack, Teams, Discord and Telegram need external app registration and admin consent. That is out of scope for 72 h: **NOT-72H** as polish, but feasible if the team already has a workspace.
- Markdown tables render in Mattermost but not in Slack.
- Mattermost needs a bot per agent to post cards. A card fails with *"No Mattermost bot for agent"* otherwise (`mattermost/adapter.py:1470-1476`). The bots are presumably provisioned when an agent joins a bridged room; this is UNKNOWN until test S5.

**Suitable for BLACKBOX:** **Yes. Mattermost (bundled, local) is the demo surface.** It is the real "enterprise chat" in the demo, and the BLACKBOX React UI mirrors it.

---

## 16. Minimum Switch deployment to run BLACKBOX locally

**Recommended: the standalone Compose stack, built from the fork.** It needs Docker only; no host Python or Node toolchain is required for Switch itself.

| Service | Profile | Purpose for BLACKBOX | Needed? |
|---|---|---|---|
| `postgres` (16-alpine) | core | Switch's database and message bus | **Yes** |
| `init-db` | core | Creates the runtime DB role | **Yes** |
| `switch` (switch-core) | core | Agent bridge API (`:8000`, bound to 127.0.0.1) | **Yes** |
| `mattermost` (team-edition 11.9.0) | `collab` | Human chat surface and approval cards | **Yes** |
| `setup` | `collab` | Seeds Mattermost, registers the bridge, **links the Mattermost user to the Gateway admin** | **Yes** |
| `gateway` | `gateway` | Operator UI (`:3000`): mint a personal registration key, set addressing policy | **Yes**, at least for setup |

Source: `deploy/local/standalone-docker-compose.yml` (header and services at lines 62, 87, 142, 203, 325, 333); `docs/official/deploy/self-host.md`; `justfile:247` (`standalone-up`).

**Commands** (from the Switch repo; documented, but not yet run by us):

```bash
just init-env
```

```bash
just standalone-up
```

- `just init-env` generates `.env` with random secrets: `DB_PASSWORD`, `DB_OWNER_PASSWORD`, `AGENT_REGISTRATION_TOKEN`, `GATEWAY_ADMIN_PASSWORD`, `MATTERMOST_*` and `SECRET_KEYS`.
- `just standalone-up` builds images from source with `standalone-docker-compose.build.yml`, using `--profile collab --profile gateway`.

**Configuration that must be set**

- `SWITCH_VERSION`. `.env.example:23` sets `latest`, but the docs require a pinned tag. The tags themselves are UNKNOWN: GHCR listing needs `read:packages`. Building from the fork avoids the question.
- `MATRIX_SERVER_NAME=localhost`. It cannot be changed after first start.
- `AGENT_REGISTRATION_TOKEN`.
- `JWT_SECRET_KEY` / `SECRET_KEYS`.
- `MATTERMOST_TEAM_NAME=switch`.
- `MATTERMOST_USER=user`.

**One-time bootstrap for BLACKBOX** (a script, not app code):

1. Sign in to the Gateway as the seeded admin. That admin is linked to Mattermost user `user`, who is the incident commander.
2. Mint a personal registration key: `POST /gateway/api-keys`, type `registration`.
3. Register `blackbox-sentinel`, `blackbox-rootcause` and `blackbox-recovery` with `POST /agents`, `always_on`, using that key. The admin now owns them.
4. Optionally narrow each agent's addressing policy in the Gateway, so that only the incident commander (and owner agents) may address or answer.
5. Store the three API keys in BLACKBOX's `.env`.

**Not needed:** the Helm chart, Switch Console, any cloud controller, WorkOS or OIDC, Switch Trust or ROI, or other messaging apps.

**Footprint:** five or six containers. This is in addition to BLACKBOX's own containers (FastAPI, the demo payment service and the React UI).

---

## 17. Smoke tests required before implementation (spike plan)

| # | Test | Proves | Blocks |
|---|---|---|---|
| S1 | `just standalone-up`; `GET :8000/health`; sign in to the Gateway; log in to Mattermost as `user` | The stack runs locally | Everything |
| S2 | Register an `always_on` agent with a personal registration key; `GET /agents/{id}/ops` | Registration works; the live operation list (expect **no** task operations) | §1, §10 |
| S3 | Open a WebSocket (and SSE) connection with `scope=all`, `protocol=4`; `create_room` with three agents and `user`; post from Mattermost | Room creation, the Mattermost channel, `message` events, the `addressed` flag | §2, §5–§7 |
| S4 | Agent A `send_targeted_message` to agent B with a JSON block; B receives `addressed:true` | Agent-to-agent handoff | §8 |
| S5 | **A custom agent opens `/agent-sessions/{sid}/approvals` with `room_id`; the card appears in Mattermost; `user` presses Approve; the agent receives `approval_outcome`; `GET …/outcomes` shows `answered_by`; a re-open with identical content returns `answered`; a re-open with a different hash returns 409 `REQUEST_CONFLICT`; an answer from an unlinked Mattermost user is refused; an expired request is refused** | **The entire governance story** | §11–§12 |
| S6 | Kill an agent process mid-stream and restart it with the same `connection_id` | Resume and `gap` behavior | §14 |
| S7 | `create_room_document` / `update_room_document` from the owning agent; a second agent reads it via `connect_to_room` → `load_internal_documents` | Shared incident dossier | §9 |

**S5 is the go/no-go gate** for the "genuine Switch decision" requirement.

---

## 18. The five most important integration risks

1. **The approval mechanism is repurposed, young and undocumented for this use.**
   - Switch has no Decision primitive. The approval-request API was built for Console coding-session hosts and is SOURCE-ONLY for BLACKBOX.
   - If cards do not render for a custom agent (bot provisioning, the online gate, session coupling), the core acceptance criterion *"a genuine Switch decision is required"* is at risk.
   - *Mitigation:* run S5 on day one.
   - *Fallback:* run Recovery as a Console-hosted Claude Code agent whose native approval prompt produces the card. This is higher complexity, and it must be disclosed.
2. **The transport is mid-migration.**
   - The official doc says SSE; the code has made WebSocket primary and marked SSE for removal; protocol revision 9 changes resume semantics.
   - Building against the doc risks breakage on a Switch upgrade.
   - *Mitigation:* pin the fork's commit or image version, isolate the transport behind one adapter, and follow `loadtest/agents.py`.
3. **No native tasks, and no structured payloads.**
   - Tasks are reserved but unimplemented (the user docs contradict the code), and message `metadata` is dropped.
   - Tasks, handoff schemas and acknowledgements are CUSTOM, which risks the perception that BLACKBOX "uses Switch as a message pipe".
   - *Mitigation:* make Switch rooms, targeted messages, room documents, roles and approval cards visibly central. Be explicit in the docs and demo that tasks are a BLACKBOX layer.
4. **Identity and authorization coupling.**
   - Whether the incident commander can approve depends on agent ownership (personal registration key versus bootstrap token), the addressing policy, and a linked Mattermost identity.
   - All three agents share one owner, so Switch gives them **identical authority**. The permission separation in `Blackbox.md` is not enforceable in Switch.
   - *Mitigation:* use the bootstrap script in §16, a BLACKBOX-side per-agent tool allowlist, separate API keys and processes, and an allowlist of `answered_by` identities in the executor.
5. **Audit and verification semantics are partly ours, not Switch's.**
   - There is no agent `GET`-by-id for approvals (verification relies on the undelivered queue or an idempotent re-open), no immutability, no human revoke of an answered approval, a 24 h event-buffer window, and humans can delete room documents.
   - *Mitigation:*
     - Keep BLACKBOX's append-only SQLite log as the authoritative audit, holding Switch `event_id`s, `seq` numbers and approval rows.
     - Delay `/delivered` until after execution.
     - Treat "revocation" as a BLACKBOX-side `close` plus a state check.

---

## 19. Runtime verification results (S1–S5, 2026-11-07)

**Environment**

- Standalone Compose with the published images at `SWITCH_VERSION=0.31.0` and the profiles `collab` and `gateway`.
- The Compose project is `blackbox-switch`.
- `API_HOST_PORT=8100`, because host port 8000 was already taken by a local `ssh` forward.
- The Docker host is an arm64 Ubuntu VM with 2 CPUs and 2 GB of RAM.
- The test harness is a throwaway script in the session scratchpad. It is not product code.

| Test | Result | Evidence |
|---|---|---|
| **S1** stack up | **PASS** | `/health` returned `{"status":"ok"}`. The `setup` seeder logged *"Linked admin@switch.local to Mattermost account user"* and *"Setup complete!"*. |
| **S2** registration | **PASS** | Gateway login → `POST /gateway/api-keys` (personal registration key) → three `POST /agents` (`always_on`, `connector_type: "blackbox"`) all returned 200. `GET /ops` lists **53 operations, with no task operations and no decision or approval operations**. |
| **S3** stream and room | **PASS** | A WebSocket at `/connection/ws` (`scope=all`, declaring protocol 8) got `connection_state` with `protocol=9`, `heartbeat=2.0`. `create_room` with three agents and `user` → 200, plus a bridged Mattermost channel of the same name. `connect_to_room` → 200 with the documented keys. A human post without a mention reached Sentinel with `addressed=false` (overheard, 0.12 s); an `@`-mention arrived with `addressed=true`. RootCause also received Sentinel's mention, with `addressed=false`. |
| **S4** agent-to-agent handoff | **PASS** | `send_targeted_message` → `target_statuses={rootcause: live}`. RootCause received `addressed=true`, and a fenced-JSON handoff round-tripped intact. The delivered body is **prefixed with `@<target>`**. The ack via `post_message` and the history via `read_context` both worked. |
| **S5** approval governance | **PASS** | Details below. |

**S5 details** (all observed live):

- A custom `always_on` agent opened `POST /agent-sessions/{sid}/approvals` with `room_id`. **The card rendered in Mattermost** as the agent's bot, with the text *"@user **Permission needed** · request `A1`"*, the title, the detail, and buttons `1. Approve remediation` and `2. Reject`.
- **Idempotency.** An identical re-open → 200, current state. A re-open with a different patch hash in `detail` → **409 `REQUEST_CONFLICT`**.
- **Authorization.** An unlinked Mattermost user pressed Approve. Mattermost returned 200, but **the state stayed `open`**: the server refused the answer.
- **Approve.** The linked incident commander (`user`) pressed Approve. Recovery received an **`approval_outcome` push 0.04 s later**: `{state: answered, answer: approve, answered_by: "@switch-mattermost-<bridge_id>-user:localhost"}`.
- **Reading back the decision.**
  - `GET /agent-sessions/approvals/outcomes` returned the same, plus `answeredAt`.
  - An identical re-open returned `state=answered`.
  - After `/delivered`, the row leaves the undelivered queue.
- **The agent cannot answer.** The agent API key on the Gateway answer route → 401.
- **Reject.** A Reject button press → outcome `answer: reject`.
- **Typed answer.** `A5 approve` replied in the card's thread → answered.
- **Plain text does not approve.** The plain text `APPROVE INC-…` answered nothing.
- **Expiry.** A 4-second request expired, and an `approval_outcome` with `state: expired` was pushed. **An identical re-open after expiry → 422 *"The request has already expired."***, so re-open-as-read works only before `expires_at`.
- **Withdraw.** Agent `close` → `state=closed`.

**Non-blocking observations**

1. No `subscription_changed` frame arrived after `create_room` on a `scope=all` connection. Room events were delivered anyway. Do not depend on that frame.
2. `answered_by` is a bridge-scoped participant id (`@switch-mattermost-<bridge_id>-<username>:localhost`). It was not found among the `id`s returned by `list_participants`. BLACKBOX should allowlist the **exact** `answered_by` string, captured once during bootstrap (the incident commander's `sender` on their own messages), rather than resolve it at runtime.

**Environment risk found**

- `mattermost/mattermost-team-edition:11.9.0` (and the enterprise edition) is published for **amd64 only**. On this arm64 host it runs emulated.
- On the first run it panicked inside Go's HTTP stack (`growslice: len out of range`, nil dereference) under concurrent bot and channel creation, and stopped responding. `create_room` timed out.
- After restarting Mattermost, the same flow ran cleanly.
- **Mitigation for the demo:**
  - Give the Docker VM at least 4 CPUs and 6 GB of RAM, and prefer Rosetta over QEMU if the VM supports it.
  - Pre-create the incident room before recording.
  - Keep a restart step in the demo reset script.

**What this changes in §18**

- Risk 1 (approval mechanism) is now **substantially retired**: the full governance loop works for a custom agent.
- The remaining parts of that risk are its undocumented status and the expiry-bounded read-back.
- A new **environment risk**: Mattermost emulated on arm64.

---

## 20. Runtime verification results (S6–S7, 2026-11-07)

**Environment:** the same stack as §19. Mattermost hung again under emulation before the first S6 attempt (two Go panics; `create_room` failed with a Mattermost read timeout). It was restarted and the run repeated. **The arm64 emulation instability is reproducible.**

### S6: reconnection and session recovery

| Check | Result | Observation |
|---|---|---|
| Live delivery | PASS | The integer `id` on WebSocket frames equals `sequence`. It is a large opaque number, so **store it, don't interpret it**. |
| Takeover | PASS | Reopening a live `connection_id` sent the old socket `evicted {code: "taken_over"}`. That code is terminal, so the old process must not reopen. |
| Short drop (2 s, < TTL) | PASS | Reopened with the same id and `start_from=<last id>`. Both the agent message and the human message posted while down were delivered, with **no replay of already-seen events** and no gap. |
| Long drop (12 s, > 6 s TTL) | PASS | The connection was swept: `connection_state.rooms=0`, so the room claim is gone and the agent **must call `connect_to_room` again before using room-bound operations**. The missed message was still delivered (scope=all, start_from), with no gap. |
| Cursor ahead of head | INFO | A `gap` frame `{from_sequence, resumed_at, resumed_position:{s, r:<room>…}, all_rooms:true, reason:"…re-read room context"}`, and the cursor reset to 0. Handle it with `read_context`. |
| Heartbeat lapse | PASS | Not answering `ping` → `evicted {code: "heartbeat_lapsed"}` after about 6.1 s. |
| `read_context` fallback | PASS | Recovers everything, independent of the stream. |
| **switch-core restart** | **PARTIAL** | After `docker restart` of switch-core, reopening with the same id and `start_from` delivered the **agent** message posted after the restart. Room history (Postgres) kept the pre- and post-restart messages. **But a human Mattermost message posted about 3 s after `/health` returned OK was never ingested.** It is absent from the stream and from `read_context`. The bridge was still re-authenticating its Mattermost WebSocket and does not backfill the gap. |

### S7: room documents

| Check | Result | Observation |
|---|---|---|
| Create (agent A) | PASS | `create_room_document` → `{document_id}` |
| Late joiner sees it | PASS | Agent B's `connect_to_room` lists it with keys `id, name, description, instructions, scope, created_by_agent_id` |
| Load content | PASS | `load_internal_documents` returns exact content |
| Owner update / reader sees it | PASS | The content is overwritten, with no history |
| Non-creator update / delete | PASS | **403** *"Agent … did not create document …"*. Single-writer ownership is enforced by the server. |
| Duplicate name in the same room | INFO | 400 *"A room-scoped document named … already exists"*. Names act as a natural idempotency key. |
| Size limit | PASS | Content over 1 MiB → 400 |
| Human visibility | PARTIAL | Creating a document posts an announcement in the channel (*"📄 created room document …"*). The `!list-documents` command reply itself was **not confirmed** by the harness. |
| Change events | INFO | Readers receive the announcement `message`. There is no dedicated document-changed event, so readers re-load on demand. |

### Design consequences

- **After any reconnect longer than 6 s**, each agent calls `connect_to_room` for its active incident rooms before posting with the connection header. It can post without a connection through `POST /agents/{id}/message` in the meantime.
- **The bridge-reconnect window is a loss window for human input.** That includes approval button presses, which arrive by HTTP callback to switch-core. For the demo:
  - never restart switch-core mid-flow;
  - after any restart, wait for the bridge's *"Websocket authentification OK"* log before continuing.
- **The executor's read-back does not depend on the chat bridge.** Approval state lives in switch-core's database [V], so a missed press shows up as "still open", never as a false approval. **The failure mode is fail-closed.**
