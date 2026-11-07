# 04 — Agent Permissions (Least Privilege)

Tags: **[V]** verified live · **[S]** Switch source · **[MVP]** / **[PROD]**.

Enforcement points:

| Point | Name | What it covers |
|---|---|---|
| **E1** | BLACKBOX tool layer | `blackbox-api` checks the caller's scoped token against this matrix on every call |
| **E2** | Switch operation proxy | Inside each agent process. The LLM can reach only the listed Switch operations; there is no raw HTTP tool. |
| **E3** | Switch server | API key identity [V], room membership [S], addressing policy [S], approval answerer check [V], document ownership [V] |
| **E4** | OS / container | Separate processes, env files, mounts and networks |
| **E5** | Executor | Independent verification (`04-APPROVAL-SECURITY.md`) |

Switch does **not** distinguish between agents of the same owner [S]. E1, E2 and E4 provide the separation in the MVP. [PROD] adds an egress proxy that enforces E2 outside the process.

---

## 1. Principal inventory

| Principal | Type | Credentials held | Network reach |
|---|---|---|---|
| Sentinel | LLM agent process | Sentinel Switch key; `bb-token:sentinel` | switch-core, blackbox-api, model API |
| RootCause | LLM agent process | RootCause Switch key; `bb-token:rootcause` | switch-core, blackbox-api, model API, sandbox launcher |
| Recovery | LLM agent process | Recovery Switch key; `bb-token:recovery`; `executor-caller` bearer | switch-core, blackbox-api, model API, executor (internal) |
| `blackbox-control` | Deterministic code in blackbox-api | Control Switch key | switch-core |
| Executor | Deterministic privileged process | Recovery Switch key (read use only); deploy credential | switch-core (read), target service, its own repo |
| Coding agent | Sandboxed subprocess | Model API key (MVP) / none (PROD, proxy-injected) | Model API through the egress proxy only |
| Test runner | Sandboxed container | None | None (`network=none`) |
| Incident commander | Human | Mattermost account linked to a Switch user [V]; BLACKBOX UI session | Mattermost, UI |
| Operator | Human | Gateway admin; Docker host | Everything (out of band) |
| Webhook source | External system | HMAC secret | blackbox-api `/webhooks/alert` only |

---

## 2. Agent permission matrix (capabilities)

Legend:

| Mark | Meaning |
|---|---|
| ✅ | Allowed |
| 🔸 | Allowed, with the constraint noted |
| ❌ | Denied (enforced) |

| Capability | Sentinel | RootCause | Recovery | Control | Executor | Coding agent |
|---|---|---|---|---|---|---|
| Read observability (metrics, logs, deploys, health) | ✅ | 🔸 evidence by id only | 🔸 health probes only | ❌ | 🔸 post-deploy health | ❌ |
| Read the repository | 🔸 deploy-diff summary | ✅ (path allowlist) | 🔸 diff of a staged remediation (read-only) | ❌ | ✅ its frozen copy | 🔸 worktree only |
| Write code | ❌ | 🔸 only through the coding agent, in the worktree | ❌ | ❌ | ❌ | 🔸 `/work` only, path allowlist |
| Run tests | ❌ | 🔸 fixed command via the test runner | ❌ | ❌ | ❌ | ❌ (shell disabled) |
| Compute or record the patch hash | ❌ | 🔸 the tool computes it | 🔸 the tool recomputes it | ❌ | ✅ authoritative recompute | ❌ |
| Create incident | ✅ (idempotent) | ❌ | ❌ | ❌ | ❌ | ❌ |
| Transition incident state | 🔸 DETECTED / INVESTIGATING / NEEDS_HUMAN | 🔸 ROOT_CAUSE_IDENTIFIED / REMEDIATION_STAGED / NEEDS_HUMAN | 🔸 VALIDATING … RESOLVED, report states | 🔸 REOPENED (on alert) | 🔸 EXECUTING / VERIFYING / ROLLING_BACK / ROLLED_BACK | ❌ |
| Record a handoff | 🔸 `investigation` | 🔸 `remediation_proposal`, `ack` | 🔸 `revision_request`, `ack` | ❌ | ❌ | ❌ |
| Open a Switch approval | ❌ | ❌ | ✅ (catalog actions only) | ❌ | ❌ | ❌ |
| Close a Switch approval | ❌ | ❌ | 🔸 revoke or withdraw | ❌ | ❌ | ❌ |
| Answer a Switch approval | ❌ (Switch: 401 [V]) | ❌ | ❌ | ❌ | ❌ | ❌ |
| Read approval outcomes | ❌ | ❌ | ✅ | ❌ | ✅ (read-only) | ❌ |
| `/delivered` acknowledgement | ❌ | ❌ | 🔸 only when no execution occurs | ❌ | ✅ | ❌ |
| Record a revocation | ❌ | ❌ | 🔸 when requested by the incident commander's id | ❌ | ❌ | ❌ |
| Request execution | ❌ | ❌ | 🔸 by `approval_ref` only | ❌ | n/a | ❌ |
| Change the target service | ❌ | ❌ | ❌ | ❌ | ✅ (catalog actions, exact commit) | ❌ |
| Create preventive tasks / report | ❌ | ❌ | ✅ | 🔸 render the task-board document | ❌ | ❌ |
| Access secrets | own keys (env) | own keys (env) | own keys (env) | own key | deploy credential | model key (MVP) |

---

## 3. Tool-level authorization

### 3.1 BLACKBOX tools (E1)

Each token carries a `scopes` list. The server rejects any call whose scope is absent, and logs `security.tool_denied`.

| Tool | Scope | Sentinel | RootCause | Recovery | Extra server-side checks |
|---|---|---|---|---|---|
| `create_incident` | `incident:create` | ✅ | ❌ | ❌ | Valid alert or human-report reference required; dedupe |
| `get_incident_context` | `incident:read` | ✅ | ✅ | ✅ | Only incidents whose room the agent belongs to |
| `transition_incident` | `incident:transition:<set>` | DETECT set | RC set | REC set | The `(from, to, actor)` table |
| `get_service_metrics`, `get_recent_logs`, `get_recent_deployments`, `get_deployment_diff`, `get_service_health` | `obs:read` | ✅ | ❌ | 🔸 `get_service_health` only | Service must be in the catalog; window ≤ 6 h |
| `record_evidence` | `evidence:write` | ✅ | ❌ | ❌ | Max 50 items per incident |
| `get_evidence`, `get_handoff` | `evidence:read`, `handoff:read` | ✅ | ✅ | ✅ | Same incident |
| `record_handoff` | `handoff:write:<types>` | investigation | remediation_proposal, ack | revision_request, ack | `from_agent` = token identity; `to_agent` per the schema rules |
| `inspect_repository`, `read_source_file`, `get_git_history`, `get_commit_diff` | `repo:read` | ❌ | ✅ | 🔸 `get_commit_diff` for a staged remediation | Path allowlist; no `.env`, keys or `.git/config` |
| `run_diagnostics` | `diag:run` | ❌ | ✅ | ❌ | Fixed probe catalog |
| `generate_patch` | `patch:generate` | ❌ | ✅ | ❌ | Max 3 per remediation; sandbox (§5) |
| `run_regression_tests`, `get_test_results` | `tests:run`, `tests:read` | ❌ | ✅ / ✅ | ❌ / ✅ | Fixed command |
| `record_remediation` | `remediation:write` | ❌ | ✅ | ❌ | Requires a green test record and a tool-computed hash |
| `validate_remediation`, `calculate_patch_hash`, `generate_rollback_plan` | `remediation:validate` | ❌ | ❌ | ✅ | Deterministic |
| `create_approval_record` | `approval:bind` | ❌ | ❌ | ✅ | Validation passed; triggers the executor's freeze |
| `open_approval`, `close_approval`, `list_outcomes` | `switch:approval` (E2) | ❌ | ❌ | ✅ | Body is generated from the binding template, never from LLM text (except two free-text fields ≤ 300 characters) |
| `record_revocation` | `approval:revoke` | ❌ | ❌ | ✅ | `requested_by` must be in `approver_allowlist`, or come from a UI session |
| `request_execution` | `exec:request` | ❌ | ❌ | ✅ | Executor verifies (E5) |
| `run_health_checks` | `health:read` | ❌ | ❌ | ✅ | Read-only |
| `generate_incident_report`, `create_task(preventive)` | `report:write`, `task:write:preventive` | ❌ | ❌ | ✅ | Built from the event log |
| `create_task(investigation)` | `task:write:investigation` | ✅ | ❌ | ❌ | |
| `create_pull_request` (local branch) | `repo:branch` | ❌ | ✅ | ❌ | Local only in the MVP |

### 3.2 Switch operations per agent (E2)

These are taken from the 53 operations the server exposes [V]. Anything not listed is denied.

| Switch operation / route | Sentinel | RootCause | Recovery | Control |
|---|---|---|---|---|
| `create_room` | 🔸 incident rooms; members from config | ❌ | ❌ | ❌ |
| `connect_to_room` | ✅ | ✅ | ✅ | ✅ |
| `post_message` / `POST /agents/{id}/message` | ✅ | ✅ | ✅ | ✅ |
| `send_targeted_message` | 🔸 targets = rootcause, incident commander | 🔸 recovery, sentinel, incident commander | 🔸 rootcause, incident commander | 🔸 agents (nudges), incident commander |
| `read_context`, `list_participants`, `get_room_detail` | ✅ | ✅ | ✅ | ✅ |
| `create_room_document` / `update_room_document` | 🔸 own evidence doc | 🔸 own analysis doc | 🔸 own plan/report docs | 🔸 state and tasks docs |
| `load_internal_documents`, `list_references` | ✅ | ✅ | ✅ | ✅ |
| `delete_room_document` | ❌ | ❌ | ❌ | ❌ |
| `invite_agent_to_room`, `add_users_to_room` | 🔸 BLACKBOX agents and the incident commander only | ❌ | ❌ | ❌ |
| `/agent-sessions/*/approvals` (open, close), `GET …/outcomes` | ❌ | ❌ | ✅ | ❌ |
| `archive_room`, `update_room`, roles (`define_role` …), `link_rooms`, templates, `create_room_from_yaml`, `run_template`, `update_agent_detail`, `request_agent_deletion`, `set_agent_service`, `remove_agent_from_room`, `create_room_group`, `create_reference`, `attach_reference_to_room` | ❌ | ❌ | ❌ | ❌ |
| `list_agents`, `list_all_rooms`, `list_bridges`, `get_my_context` | ❌ (bootstrap script only) | ❌ | ❌ | ❌ |

---

## 4. Room-level access boundaries

| Room | Members | Who may post | Who acts on what |
|---|---|---|---|
| `blackbox-intake` | Control, Sentinel, incident commander(s) | All members | Sentinel acts on alerts from Control's id only, and on human reports (with confirmation) |
| `incident-*` | Control, Sentinel, RootCause, Recovery, incident commander(s) | All members | Agents act only on addressed messages from expected senders (handoffs verified by sender id plus store hash). Human authority over execution is the approval card only. |

- **Event routing:** process-level routing by `room_id` to a per-incident handler. Events from rooms not mapped to an active incident (or to intake, for Sentinel) are dropped and logged as `security.unexpected_room`.
- **Prompt isolation:** a prompt contains exactly one incident's context. There is no cross-incident retrieval.
- **[PROD]** Private channels; `read_visibility=private` [S]; the incident commander list per service from the on-call system; room archiving after closure.

---

## 5. Coding-agent sandbox restrictions (summary)

The full table is in `04-SECURITY-ARCHITECTURE.md` §5.

- **Patch sandbox:**
  - ephemeral, non-root, read-only rootfs; `/work` is the only writable path;
  - **shell disabled**; tools are read, search and edit only;
  - egress to the model API only;
  - no host mounts and no Docker socket;
  - limits: 1 CPU, 1 GB, 256 pids, 180 s.
- **Test sandbox:** `network=none`, `/work` read-only, a fixed command, 120 s.
- **Post-checks** (deterministic): path allowlist; no edits to tests' infrastructure, CI, manifests or Dockerfiles; no deleted tests; diff ≤ 200 lines.

---

## 6. Human roles

| Role | Can | Cannot |
|---|---|---|
| Incident commander | Answer approval cards (if allowed by the addressing policy [V] **and** on the executor allowlist); revoke; request status; provide context | Execute directly; approve by typing text [V] |
| Other room members | Read; post context (treated as data) | Answer approvals (Switch refuses [V], and the executor allowlist refuses); revoke |
| Operator | Configure Switch and BLACKBOX; rotate keys; reset the demo | Approve as a side effect of being an operator: their Switch identity is not on the approver allowlist unless explicitly added |

---

## 7. Separation-of-duties invariants

These are tested in CI.

1. No principal holds both `patch:generate` and `exec:request`.
2. No principal holds both `switch:approval` (open) and the deploy credential. Recovery opens; the executor deploys.
   - **MVP caveat:** the executor holds Recovery's Switch API key, because only the opening agent's key can read its outcomes [S]. That key *could* open approvals, but the executor's code exposes no open path.
   - An approval opened by the executor would still need a human answer [V] **and** a BLACKBOX binding, which the executor cannot create.
   - **[PROD]** moves the key into a read-only approval-reader service.
3. The principal that answers an approval (human) is never the principal that requested it (Recovery). Switch enforces this [V].
4. Only the executor holds the deploy credential, and it has no LLM.
5. The coding agent holds no BLACKBOX or Switch credentials.
