# 01 — Switch Capability Matrix

Source: `suryafk/switch` @ `0977b48` (switch-core 0.31.0; agent protocol server speaks revision 9). Full evidence, schemas and examples are in [`01-SWITCH-RECONNAISSANCE.md`](01-SWITCH-RECONNAISSANCE.md); the § numbers below point to its sections. **Nothing has been runtime-verified yet**; the smoke tests S1–S7 are listed in that document's §17.

Legend:

| Tag | Meaning |
|---|---|
| **OFFICIAL** | Documented in `docs/official/` and present in code |
| **SOURCE-ONLY** | Present in code; not documented, or documented for a different purpose |
| **CUSTOM** | BLACKBOX must build it |
| **NOT-72H** | Not realistic in 72 h |
| **UNKNOWN** | Not verifiable from the docs or the code |

## Capability matrix

| Feature | Supported | Integration method | Verified reference | Limitations |
|---|---|---|---|---|
| Agent registration | **Yes, OFFICIAL** | `POST /agents` (Bearer registration key), `integration_profile.connection_model="always_on"` → `{id, api_key}` | `bridges/agent/api/handlers.py:206`; `api/schemas.py:20`; `protocol/types.py:28`; `loadtest/seed.py:25,186` | API key returned once; name regex; 409 on duplicate; connection models "expected to go"; `connector_type` values UNKNOWN |
| Agent authentication | **Yes, OFFICIAL** | `Authorization: Bearer <api_key>` on every call; registration token only for registration | `docs/official/internals/identity-and-access.md` (Entry points) | Agent inherits its **owner's** permissions; agents registered with the bootstrap token are owned by a non-admin system account (`registration_bootstrap.py`) |
| Ownership via personal registration key | **Yes, OFFICIAL** | Gateway `POST /gateway/api-keys` (type `registration`) → register the agents with it | `gateway/api_keys.py:72`; `gateway/app.py:244` | Needs a Gateway cookie session; one-time setup step |
| Room creation | **Yes, OFFICIAL** | Operation `create_room` via `POST /agents/{id}/ops/create_room` | `operations/definitions.py:923` | Agent-only operation (ingestion must act as an agent); bridged to the default bridge (Mattermost on standalone) unless `internal_only` |
| Room update / archive | **Yes, OFFICIAL** | `update_room`, `archive_room`, `unarchive_room`, `get_room_detail` | `definitions.py:2524-2661` | Archive is reversible metadata, not deletion |
| Room membership: agents | **Yes, OFFICIAL** | `create_room(agent_names=…)`, `invite_agent_to_room`, `remove_agent_from_room` | `definitions.py:1436,1460` | Names must exist in the registry |
| Room membership: humans | **Yes, OFFICIAL** | `create_room(user_names=…)`, `add_users_to_room` | `definitions.py:1485` | Bridged rooms only; the user must be known to the bridge |
| Room roles | **Yes, OFFICIAL** | `define_role`, `assume_role`, `release_role`, `list_roles`; address with `@role` | `definitions.py:1083-1201`; `docs/official/using/hand-off-work.md` | Instruction bundle and address only, **not** a permission boundary; one role per agent across all rooms |
| Per-agent permission boundary | **No** (in Switch) | — | `identity-and-access.md` ("an agent is not a principal with its own grants") | Same-owner agents have identical authority → **CUSTOM** tool allowlists in BLACKBOX; Switch Trust mediation **NOT-72H** |
| Addressing policy (who may make an agent act) | **Yes, OFFICIAL** | Set in the Gateway by the owner or an admin; rules over rooms, groups, users and agents | `identity-and-access.md` §Addressing policy; `core/switch_core/addressing.py` | New agents start owner-only; the human must **link** their chat account; an agent cannot change its own policy |
| Post room message | **Yes, OFFICIAL** | `post_message` op, or `POST /agents/{id}/message {room_id, content}` (no connection needed) | `definitions.py:788`; `handlers.py:487` | Text/Markdown only; `metadata` field **ignored**; Switch Trust may redact or block |
| Targeted (addressed) message | **Yes, OFFICIAL** | `send_targeted_message {body, target_names, target_roles}` → `target_statuses` | `definitions.py:827` | Reachability is at send time only; `not_permitted` still sends |
| Room history / shared context read | **Yes, OFFICIAL** | `read_context {limit, since, before, room_id}`; `GET /agents/{id}/rooms/{room}/history`; `connect_to_room` payload | `definitions.py:657,252`; `handlers.py:1714` | Paginated; `truncated` flag; mixed timestamp formats |
| Event subscription | **Yes, OFFICIAL** | One connection per agent; `scope=single\|all`, `filter=all\|addressed`, rooms declared at open | `docs/official/internals/agent-protocol.md`; `handlers.py:747,1227` | Max 32 connections; one acting connection per room; declare rooms on the URL |
| Transport: WebSocket | **Yes, SOURCE-ONLY** (newer than the doc) | `GET /agents/{id}/connection/ws`; JSON frames; server `ping` / client `pong{cursor}` | `handlers.py:1227-1300`; `artifacts.yaml` revision 8; `loadtest/agents.py` | Not in the official doc; refusals use close code 4000+status |
| Transport: SSE + heartbeat | **Yes, OFFICIAL** (deprecated path) | `GET /agents/{id}/events` with `Accept: text/event-stream`; `POST /connection/beat` every 2 s, 6 s TTL | `agent-protocol.md`; `handlers.py:747-811,1168` | "Kept for a compatibility window", to be removed when unused; no date |
| Transport: HTTP outbound | **Yes, OFFICIAL** | `POST /agents/{id}/ops/{name}` + `X-Switch-Connection-Id`; `GET /agents/{id}/ops` to discover | `api/operations.py:284-296` | Arguments validated by signature (unexpected keys → 400) |
| Addressed vs overheard | **Yes, OFFICIAL** | `filter=all` delivers everything, with `payload.addressed`; `filter=addressed` delivers only notifiable events | `agent-protocol.md` §addressed filter; `delivery/addressing.py` | No topic or keyword filtering; scoped by membership; `addressed` excludes `command` events |
| Agent-to-agent handoff | **Partial**: OFFICIAL primitive, **CUSTOM** envelope | `send_targeted_message` to the next agent, with a fenced-JSON handoff in the body; ack by addressed reply | `definitions.py:827`; `SKILL.md` (`owner_and_owner_agents`) | No native handoff object, receipt or schema |
| Room documents (persistent memory) | **Yes, OFFICIAL** | `create_room_document`, `update_room_document`, `load_internal_documents`, `list_references` | `definitions.py:539-655`; `docs/official/using/shared-context.md` | ≤1 MiB; **creator-only update**; overwrite with no history; humans can delete |
| Tasks (delegate / accept / update / finalise) | **No**. Reserved, not implemented | — (frames reserved: `task_delegate`, …) | `agent-protocol.md` ("not ready for use… build nothing on them"); no operation in `definitions.py`; `event_buffer.py:176` | **CUSTOM**. `using/hand-off-work.md` contradicts the code |
| Decision primitive | **No** | — | No "decision" operation, table or event (grep of `core/switch_core`) | — |
| Approval request (stand-in for a decision) | **Yes, SOURCE-ONLY** | `POST /agent-sessions/{sid}/approvals {request_id, turn_id, kind:"approval", title, detail, options[{id,label,decision}], room_id, expires_at}` → card with buttons in chat | `bridges/agent/api/activity_routes.py:231`; `session_activity/service.py:358-516`; adapters `draws_session_activity=True` | Built for Console session hosts; card rendering for a custom agent UNKNOWN (smoke test S5); `detail` ≤4000 chars; ≤10 options |
| Approval: answerer authorization | **Yes, SOURCE-ONLY** | Server checks that the answerer may address the agent (room policy; owner-only if no room) | `service.py:600-650` | Requires a linked chat identity; agents cannot answer via the agent API |
| Approval: human answer UX | **Yes, SOURCE-ONLY** | Button press, or typed `A<n> yes` in Mattermost/Slack/Teams/Discord/Telegram; or Gateway `POST /gateway/agent-sessions/{agent}/{sid}/approvals/{rid}/answer` | `session_activity/bridge_answers.py`; `gateway/agent_sessions.py:169` | `APPROVE INC-1042` is **not** a recognized grammar; Gateway web UI rendering UNKNOWN |
| Approval: binding to an artifact | **Yes, SOURCE-ONLY** (by construction) | Deterministic `request_id`/`detail` with incident + patch hash + environment; identical re-open is idempotent, different content → `REQUEST_CONFLICT` | `service.py:425-460` | Binding is ours to design; the server enforces immutability of the content per `request_id` |
| Approval: expiry | **Yes, SOURCE-ONLY** | `expires_at` ≤ 24 h; maintenance loop expires every 5 s; late answers refused | `service.py:74,384-392,494-497`; `docs/session-activity-handoff.md` | Max 24 h |
| Approval: revocation | **Partial** | Agent `POST …/approvals/{rid}/close` (open → closed only) | `activity_routes.py:264`; `service.py:529-538` | No human revoke after an answer → **CUSTOM** revoke in BLACKBOX |
| Approval: retrieval / verification | **Partial, SOURCE-ONLY** | Push `approval_outcome` frame (`scope=all`, protocol≥4); `GET /agent-sessions/approvals/outcomes` (undelivered); idempotent re-open as a read | `protocol/stream.py:218-312`; `session_activity/outcomes.py`; `activity_routes.py:286` | No agent `GET` by id; acknowledge (`/delivered`) only after execution |
| Event history | **Yes, OFFICIAL** | Postgres `messages` with a per-room `seq`; `read_context` / history | `CLAUDE.md` (message bus); `definitions.py:657` | No pruning of messages found; per-agent event buffer 24 h (`config.py:818`) |
| Auditability (append-only, immutable) | **No** | — | — | **CUSTOM**: BLACKBOX's append-only SQLite log referencing Switch `event_id`/`seq`; never claim immutability |
| Reconnection / resume | **Yes, OFFICIAL** | Reuse `connection_id`; `Last-Event-ID` / `start_from` / `start_at`; `gap` frame; fenced generations | `agent-protocol.md`; `artifacts.yaml` revisions 2 and 9; `handlers.py:1168` | `taken_over` is terminal; restart behavior of the buffer UNKNOWN |
| Presence / typing / runtime state | **Yes, SOURCE-ONLY** | `POST /agents/{id}/typing`, `/status`, `/runtime-state` | `handlers.py:606,688,712` | Not needed for the MVP; nice for "agent is working" signals |
| Attachments | **Yes, OFFICIAL** | Media routes `GET/POST /agents/{id}/rooms/{room}/media` | `handlers.py:512,548` | Pointer (`mxc`) in events, never bytes |
| Mattermost bridge | **Yes, OFFICIAL** (bundled) | Standalone `collab` profile; seeded team, user and bridge; owner link auto-made | `deploy/local/standalone-docker-compose.yml:142,333`; `deploy/shared_resources/setup.py:357` | One bot per agent required for cards (UNKNOWN until S5) |
| Slack / Teams / Discord / Telegram | **Yes, OFFICIAL** | App registration per platform | `docs/official/deploy/messaging-apps/*.md` | External admin setup; **NOT-72H** as an addition beyond Mattermost |
| MCP surface for agents | **Yes, OFFICIAL** | `/mcp` on the bridge; same operation registry as HTTP | `bridges/agent/app.py:188`; `bridges/agent/mcp/server.py` | Useful if RootCause runs Claude Code as a *Switch agent*; not required |
| Templates (rooms/agents from YAML) | **Yes, OFFICIAL** | `create_room_from_yaml`, `run_template` | `definitions.py:1649,1983`; `examples/room-templates/` | Optional; could seed the incident room shape |
| Switch Trust guardrails / mediation | **Yes, SOURCE-ONLY / commercial** | `/agents/{id}/mediation/*` | `handlers.py:1825-1900`; `docs/design/switch-trust-guardrails-v1.md` | **NOT-72H** |

## Classification summary

**Officially supported (use directly):**

- Registration and authentication.
- Rooms: create, update and archive.
- Membership.
- Roles (addressing only).
- Post and targeted messages.
- `read_context` and history.
- Event stream with addressed/overheard filtering.
- Reconnection and resume.
- Room documents.
- Mattermost bridge.
- Addressing policy.

**In source but undocumented (or documented for another purpose), to be smoke-tested before relying on it:**

- WebSocket transport (revision 8+).
- The approval-request API: open, answer authorization, expiry, close, outcomes, and the `approval_outcome` frame. **This is our decision mechanism.**
- Runtime state, typing and presence.

**Needs custom implementation in BLACKBOX:**

- Tasks and the task board.
- A typed handoff envelope and acknowledgement.
- Approval-to-patch binding logic and the executor-side verification.
- Revocation after an answer.
- Per-agent tool allowlists.
- An append-only audit log.
- The incident state machine.
- Mapping `answered_by` to the incident commander.
- The postmortem and preventive tasks.

**Not reasonable in 72 h:**

- Native Switch tasks (they are not implemented upstream).
- Switch Trust mediation as a permission boundary.
- Additional chat platforms (Slack, Teams, Discord, Telegram) beyond the bundled Mattermost.
- Upstream changes to Switch, such as adding a Decision primitive or honoring message `metadata`.

**Unknown until runtime-tested:**

- Approval cards for a custom (non-Console) agent (**S5**).
- Mattermost bot provisioning per agent.
- Event-buffer behavior across a switch-core restart.
- Published GHCR image tags (we build from the fork instead).
- Whether the Gateway web UI renders approvals.
- Intended `connector_type` values.

## Minimum local deployment (summary)

- **Services:** `postgres`, `init-db`, `switch` (`:8000`), `mattermost` + `setup` (profile `collab`) and `gateway` (`:3000`, profile `gateway`), all from `deploy/local/standalone-docker-compose.yml`.
- **Commands:** `just init-env`, then `just standalone-up`.
- **Then** a one-time bootstrap:
  1. Mint a personal registration key as the Gateway admin, who is linked to Mattermost `user`.
  2. Register the three `always_on` agents with it.
  3. Optionally narrow their addressing policies.

Details are in the reconnaissance document, §16.

## Five most important integration risks

1. **The approval-request API is repurposed, young and undocumented for this use**, and it is the only credible "genuine Switch decision". Gate on smoke test S5.
2. **The transport is mid-migration.** The doc says SSE; the code is WebSocket-first, with SSE slated for removal. Pin the version and isolate the transport.
3. **No native tasks and no structured message metadata.** Tasks and handoff schemas are BLACKBOX-side, which creates a perception risk about Switch centrality.
4. **Identity coupling.** Approval rights depend on ownership, addressing policy and a linked chat account, and same-owner agents have identical Switch authority.
5. **Audit and verification gaps.** There is no agent `GET` by id for approvals, no immutability, no human revoke, and a 24 h event buffer. BLACKBOX's log must be authoritative.

## Runtime verification (S1–S5, 2026-11-07, images 0.31.0)

| Feature | Runtime result |
|---|---|
| Standalone stack + Mattermost seeding + owner link | **Verified** |
| Personal registration key → `POST /agents` (`always_on`) | **Verified** |
| Operation registry | **Verified**: 53 operations, **no task operations, no decision operations** |
| WebSocket transport (`/connection/ws`) | **Verified** (server negotiates protocol 9, 2 s heartbeat) |
| `create_room` → bridged Mattermost channel | **Verified** |
| Overheard (`addressed=false`) vs addressed (`addressed=true`) | **Verified** |
| `send_targeted_message` handoff with JSON body | **Verified**; body arrives prefixed with `@<target>` |
| Approval card for a custom agent | **Verified**: buttons and typed `A<n>` handle |
| Answerer authorization (unlinked user refused) | **Verified** |
| `approval_outcome` push / `GET outcomes` / re-open-as-read | **Verified**; re-open-as-read returns **422 after expiry** |
| Content-bound idempotency (`REQUEST_CONFLICT` on a hash change) | **Verified** |
| Expiry pushed as an outcome; agent `close` | **Verified** |
| Agent cannot answer (Gateway route → 401) | **Verified** |
| `APPROVE INC-…` free text | **Verified to do nothing** |
| `subscription_changed` on a new room (scope=all) | **Not observed**; events delivered regardless |
| Reconnect/resume (S6) | **Verified**: same `connection_id` + `start_from` resumes with no replay; >6 s drop loses the room claim (re-`connect_to_room`); `taken_over` and `heartbeat_lapsed` eviction codes; cursor-ahead → `gap`. **Human Mattermost messages posted during the bridge reconnect after a switch-core restart were lost.** |
| Room documents (S7) | **Verified**: create, late-joiner listing, load, owner update; **non-creator update/delete → 403**; unique name per room; 1 MiB limit; creation announced in the channel |

**New risk:** the Mattermost images are amd64-only. Emulated on arm64, Mattermost crashed once under load. Give Docker more resources, and include a restart in the demo reset.
