# 03 — Handoff, Context and Idempotency Schemas

These are the wire formats agents exchange **through Switch messages**, the shared incident context contract, and the idempotency rules.

- JSON Schemas below use draft 2020-12, abbreviated: `$schema` and `$id` are omitted.
- All timestamps are RFC 3339 UTC.
- All ids are strings.

---

## 1. How a structured payload travels in a Switch message

Switch messages are text, and the HTTP `metadata` field is dropped [S]. So each structured payload is embedded in the message body:

````
@blackbox-rootcause Handing investigation INC-1042 to RootCause: 5xx 18% after deploy #842; hypothesis: DB pool regression (0.72).

```json
{"schema":"blackbox.handoff/v1", ... }
```
````

**Rules:**

1. **Line 1 is the human summary.** It is at most 280 characters and readable in Mattermost.
   - `send_targeted_message` prefixes `@<target>` automatically [V], so the sender must not add it.
2. **Exactly one fenced `json` block**, whose object has a top-level `"schema"` key.
   - The receiver ignores blocks without a known `schema`.
   - It does not parse prose.
3. **Size:** at most 4 000 characters in the whole message.
   - Bulky data (logs, diffs, test output) is referenced by id (`evidence_ref`, `artifact_ref`) and fetched from BLACKBOX.
4. **Integrity:** the sender first writes the record to the state store. The record holds `payload_sha256` = SHA-256 of the canonical JSON (RFC 8785 JCS) of the payload object **without** its `integrity` field.
   - The receiver accepts the payload only if all three of these hold:
     - the Switch `sender` equals the expected agent's participant id;
     - the store record exists;
     - the hashes match.
   - A human pasting JSON into the room therefore cannot forge a handoff.
5. **Delivery facts the receiver records:**
   - Switch `payload.message_id`, `sequence` and `room_id` from the event [V].
   - The sender records the `event_id` returned by the send [V].

---

## 2. Payload schemas

### 2.1 `blackbox.alert/v1`

Posted by `blackbox-control` in `blackbox-intake`.

```json
{
  "type": "object",
  "required": ["schema", "alert_id", "source", "service", "severity", "signals", "observed_at", "fingerprint"],
  "properties": {
    "schema": {"const": "blackbox.alert/v1"},
    "alert_id": {"type": "string", "pattern": "^ALT-[0-9]+$"},
    "source": {"enum": ["synthetic-monitor", "prometheus-compatible", "human-report"]},
    "simulated": {"type": "boolean"},
    "service": {"type": "string"},
    "severity": {"enum": ["critical", "high", "medium", "low"]},
    "signals": {
      "type": "object",
      "properties": {
        "error_rate_pct": {"type": "number"},
        "latency_ms_p50": {"type": "number"},
        "db_connection_usage_pct": {"type": "number"}
      },
      "additionalProperties": {"type": "number"}
    },
    "observed_at": {"type": "string", "format": "date-time"},
    "fingerprint": {"type": "string", "description": "sha256(service|alertname|labels), dedupe key"}
  }
}
```

### 2.2 `blackbox.handoff/v1`

This is the standard handoff, used for every agent-to-agent transfer.

```json
{
  "type": "object",
  "required": ["schema", "handoff_id", "incident_id", "from_agent", "to_agent", "handoff_type",
               "summary", "context_version", "required_actions", "created_at", "room_id", "integrity"],
  "properties": {
    "schema": {"const": "blackbox.handoff/v1"},
    "handoff_id": {"type": "string", "pattern": "^HND-[0-9]+-[0-9]{3}$"},
    "incident_id": {"type": "string", "pattern": "^INC-[0-9]+$"},
    "from_agent": {"enum": ["sentinel", "rootcause", "recovery"]},
    "to_agent": {"enum": ["sentinel", "rootcause", "recovery"]},
    "handoff_type": {"enum": ["investigation", "remediation_proposal", "revision_request"]},
    "summary": {"type": "string", "maxLength": 500},
    "severity": {"enum": ["SEV-1", "SEV-2", "SEV-3", "SEV-4"]},
    "service": {"type": "string"},
    "context_version": {"type": "integer", "minimum": 1,
      "description": "IncidentContext.version the sender saw; receiver reloads if newer"},
    "evidence": {"type": "array", "maxItems": 12, "items": {"$ref": "#/$defs/evidence_ref"}},
    "hypotheses": {"type": "array", "maxItems": 5, "items": {"$ref": "#/$defs/hypothesis"}},
    "required_actions": {"type": "array", "items": {"type": "string", "maxLength": 200}, "maxItems": 8},
    "remediation_ref": {"$ref": "#/$defs/remediation_ref"},
    "revision": {"$ref": "#/$defs/revision"},
    "override_by": {"type": ["string", "null"], "description": "IC participant id if a human forced this handoff"},
    "deadline_at": {"type": "string", "format": "date-time"},
    "room_id": {"type": "string"},
    "created_at": {"type": "string", "format": "date-time"},
    "integrity": {
      "type": "object", "required": ["payload_sha256", "store_record"],
      "properties": {
        "payload_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "store_record": {"type": "string", "description": "blackbox-api path, e.g. /handoffs/HND-1042-001"}
      }
    }
  },
  "allOf": [
    {"if": {"properties": {"handoff_type": {"const": "investigation"}}},
     "then": {"required": ["evidence", "hypotheses", "severity", "service"],
              "properties": {"from_agent": {"const": "sentinel"}, "to_agent": {"const": "rootcause"}}}},
    {"if": {"properties": {"handoff_type": {"const": "remediation_proposal"}}},
     "then": {"required": ["remediation_ref"],
              "properties": {"from_agent": {"const": "rootcause"}, "to_agent": {"const": "recovery"}}}},
    {"if": {"properties": {"handoff_type": {"const": "revision_request"}}},
     "then": {"required": ["revision"],
              "properties": {"from_agent": {"const": "recovery"}, "to_agent": {"const": "rootcause"}}}}
  ],
  "$defs": {
    "evidence_ref": {
      "type": "object", "required": ["evidence_id", "kind", "label"],
      "properties": {
        "evidence_id": {"type": "string"},
        "kind": {"enum": ["metric", "log_signature", "deployment", "config_change", "health", "human_report"]},
        "label": {"type": "string", "maxLength": 160},
        "value": {"type": ["number", "string", "null"]},
        "simulated": {"type": "boolean"}
      }
    },
    "hypothesis": {
      "type": "object", "required": ["id", "statement", "confidence"],
      "properties": {
        "id": {"type": "string"},
        "statement": {"type": "string", "maxLength": 300},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "supporting_evidence": {"type": "array", "items": {"type": "string"}}
      }
    },
    "remediation_ref": {
      "type": "object", "required": ["remediation_id", "patch_hash", "tests", "changed_files"],
      "properties": {
        "remediation_id": {"type": "string", "pattern": "^REM-[0-9]+-[0-9]+$"},
        "patch_hash": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
        "commit": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
        "base_commit": {"type": "string", "pattern": "^[0-9a-f]{40}$"},
        "changed_files": {"type": "array", "items": {"type": "string"}},
        "tests": {"type": "object", "required": ["run_id", "passed", "failed", "errors"],
                  "properties": {"run_id": {"type": "string"}, "passed": {"type": "integer"},
                                 "failed": {"type": "integer"}, "errors": {"type": "integer"},
                                 "new_tests": {"type": "array", "items": {"type": "string"}}}},
        "coding_agent": {"type": "object", "properties": {
          "mode": {"enum": ["claude_code", "codex", "deterministic_fallback"]},
          "run_id": {"type": "string"}}}
      }
    },
    "revision": {
      "type": "object", "required": ["reason_kind", "rejected_remediation_id"],
      "properties": {
        "reason_kind": {"enum": ["rejected_by_human", "revoked", "validation_failed", "verification_failed", "expired_twice"]},
        "rejected_remediation_id": {"type": "string"},
        "rejected_patch_hash": {"type": "string"},
        "human_feedback": {"type": ["string", "null"], "maxLength": 1000},
        "failed_checks": {"type": "array", "items": {"type": "string"}},
        "decided_by": {"type": ["string", "null"]}
      }
    }
  }
}
```

**Example: Sentinel → RootCause**

```json
{
  "schema": "blackbox.handoff/v1",
  "handoff_id": "HND-1042-001",
  "incident_id": "INC-1042",
  "from_agent": "sentinel",
  "to_agent": "rootcause",
  "handoff_type": "investigation",
  "summary": "payment-service 5xx 18% / p50 4.2s began 4 min after deploy #842; DB pool 94% used.",
  "severity": "SEV-1",
  "service": "payment-service",
  "context_version": 4,
  "evidence": [
    {"evidence_id": "EV-1042-01", "kind": "metric", "label": "http_5xx_rate", "value": 18.0, "simulated": false},
    {"evidence_id": "EV-1042-02", "kind": "deployment", "label": "deploy #842 at 10:26Z", "value": "842", "simulated": true},
    {"evidence_id": "EV-1042-03", "kind": "log_signature", "label": "TimeoutError: QueuePool limit of size 10 reached", "value": 412}
  ],
  "hypotheses": [{"id": "H1", "statement": "Deploy #842 reduced DB pool size", "confidence": 0.72,
                  "supporting_evidence": ["EV-1042-02", "EV-1042-03"]}],
  "required_actions": ["Inspect deploy #842 diff", "Review config/database.yml", "Reproduce under load"],
  "override_by": null,
  "deadline_at": "2026-11-06T10:36:00Z",
  "room_id": "d6814721-6d09-4d8a-af87-d310bd87863b",
  "created_at": "2026-11-06T10:31:05Z",
  "integrity": {"payload_sha256": "<64 hex>", "store_record": "/handoffs/HND-1042-001"}
}
```

### 2.3 `blackbox.ack/v1`

The receiver posts this within 5 s, through `send_targeted_message` back to the sender.

```json
{
  "type": "object",
  "required": ["schema", "handoff_id", "incident_id", "by_agent", "status", "at"],
  "properties": {
    "schema": {"const": "blackbox.ack/v1"},
    "handoff_id": {"type": "string"},
    "incident_id": {"type": "string"},
    "by_agent": {"enum": ["sentinel", "rootcause", "recovery"]},
    "status": {"enum": ["accepted", "rejected", "duplicate"]},
    "reason": {"type": ["string", "null"]},
    "at": {"type": "string", "format": "date-time"}
  }
}
```

### 2.4 `blackbox.progress/v1`

Posted with `post_message` (broadcast, not addressed).

```json
{
  "type": "object",
  "required": ["schema", "progress_id", "incident_id", "agent", "milestone", "at"],
  "properties": {
    "schema": {"const": "blackbox.progress/v1"},
    "progress_id": {"type": "string", "description": "<incident>:<agent>:<milestone>:<attempt>"},
    "incident_id": {"type": "string"},
    "agent": {"enum": ["sentinel", "rootcause", "recovery", "control"]},
    "milestone": {"enum": [
      "INCIDENT_CREATED", "EVIDENCE_COLLECTED", "EVIDENCE_GAP", "HANDOFF_SENT",
      "ACK", "REPO_INSPECTED", "ROOT_CAUSE_FOUND", "PATCH_GENERATED", "TESTS_RUNNING", "TESTS_RESULT", "PROPOSAL_READY",
      "VALIDATION_PASSED", "VALIDATION_FAILED", "APPROVAL_REQUESTED", "APPROVAL_OUTCOME",
      "EXECUTION_REQUESTED", "EXECUTION_REFUSED", "EXECUTING", "VERIFYING", "RECOVERED", "ROLLED_BACK",
      "REPORT_PUBLISHED", "PREVENTIVE_TASKS_CREATED", "NEEDS_HUMAN", "STATE_CHANGED"]},
    "attempt": {"type": "integer", "minimum": 1},
    "detail": {"type": "object"},
    "artifact_refs": {"type": "array", "items": {"type": "string"}},
    "simulated": {"type": "boolean"},
    "at": {"type": "string", "format": "date-time"}
  }
}
```

### 2.5 Evidence package and remediation proposal (state-store objects)

These are referenced by handoffs and are not inlined in chat.

```text
EvidencePackage { evidence_id[], incident_id, collected_by="sentinel", window{from,to},
                  items[{evidence_id, kind, source_tool, query, summary, value, unit, observed_at,
                         raw_ref, simulated}], gaps[{kind, reason}], context_version }

RemediationProposal { remediation_id, incident_id, attempt, root_cause{statement, file, line_range,
                      introducing_commit, confidence}, patch: PatchArtifact (§5), tests{run_id, command,
                      passed, failed, errors, duration_s, new_tests[], log_ref}, impact, risk_inputs{
                      diff_lines, files_touched, config_only}, rollback{strategy:"revert", target_commit},
                      coding_agent{mode, run_id, duration_s}, created_at }
```

---

## 3. Incident context contract (shared between agents)

`GET /incidents/{id}/context` on `blackbox-api` returns this. It is the **only** shared memory agents read to understand an incident. It is a projection of the append-only event log, so it is never written directly.

```json
{
  "schema": "blackbox.incident_context/v1",
  "incident_id": "INC-1042",
  "version": 7,
  "title": "payment-service 5xx spike after deploy #842",
  "service": "payment-service",
  "severity": "SEV-1",
  "state": "AWAITING_APPROVAL",
  "state_owner": "recovery",
  "state_history": [{"from": "DETECTED", "to": "INVESTIGATING", "by": "sentinel", "at": "…", "event_seq": 12}],
  "switch": {
    "room_id": "d681…", "room_name": "incident-payment-service-1042",
    "participants": {"sentinel": "@switch-agent-…", "rootcause": "@switch-agent-…",
                     "recovery": "@switch-agent-…", "control": "@switch-agent-…",
                     "incident_commander": "@switch-mattermost-<bridge>-user:localhost"},
    "documents": {"state": "doc-…", "tasks": "doc-…"}
  },
  "alert": {"alert_id": "ALT-1042", "fingerprint": "…", "signals": {"error_rate_pct": 18.0}, "simulated": true},
  "metrics_baseline": {"error_rate_pct": 0.2, "latency_ms_p50": 100},
  "metrics_current": {"error_rate_pct": 18.0, "latency_ms_p50": 4200, "sampled_at": "…"},
  "evidence_summary": [{"evidence_id": "EV-1042-01", "kind": "metric", "label": "http_5xx_rate", "value": 18.0}],
  "hypotheses": [{"id": "H1", "statement": "…", "confidence": 0.72, "status": "confirmed"}],
  "handoffs": [{"handoff_id": "HND-1042-001", "type": "investigation", "status": "acked", "switch_event_id": "…"}],
  "tasks": [{"task_id": "TSK-1042-investigate-1", "type": "investigate", "owner": "sentinel", "status": "done"}],
  "remediation": {"remediation_id": "REM-1042-1", "patch_hash": "sha256:…", "tests": {"passed": 27, "failed": 0},
                  "changed_files": ["config/database.yml", "tests/test_pool.py"], "status": "staged"},
  "approval": {"approval_id": "APR-1042-1", "request_id": "INC-1042:REM-1042-1:3f9a…:staging-sandbox",
               "state": "open", "expires_at": "…", "answered_by": null},
  "execution": null,
  "revision_cycles": 0,
  "human_inputs": [{"by": "@switch-mattermost-…-user:localhost", "message_id": "…", "summary": "…"}],
  "updated_at": "…"
}
```

**Rules**

- `version` increments on every appended event that changes the projection.
- A handoff carries `context_version`. If the receiver's fetched version is newer, it re-reads before acting.
- Agents never cache context across turns longer than one handler invocation.
- The `blackbox-control` state document [S] is a Markdown rendering of this object for humans and late joiners. It is not used by code.

---

## 4. Approval binding (Recovery and executor)

### 4.1 `ApprovalBinding` (state store)

```json
{
  "approval_id": "APR-1042-1",
  "incident_id": "INC-1042",
  "remediation_id": "REM-1042-1",
  "patch_hash": "sha256:3f9a…(64 hex)",
  "commit": "<40 hex>",
  "environment": "staging-sandbox",
  "action": "apply_remediation_commit",
  "rollback": {"strategy": "revert", "target_commit": "<40 hex>"},
  "switch": {"agent_id": "<recovery agent id>", "session_id": "INC-1042", "turn_id": "REM-1042-1",
             "request_id": "INC-1042:REM-1042-1:3f9a0c1d2e3b4a59:staging-sandbox",
             "room_id": "d681…", "open_body_sha256": "<64 hex>"},
  "approver_allowlist": ["@switch-mattermost-<bridge>-user:localhost"],
  "expires_at": "2026-11-06T10:50:00Z",
  "attempt": 1,
  "status": "open"
}
```

### 4.2 Switch open body

This is the verified request shape [V]. It is stored verbatim, so it can be re-posted to read the request back [V].

```json
{
  "request_id": "INC-1042:REM-1042-1:3f9a0c1d2e3b4a59:staging-sandbox",
  "turn_id": "REM-1042-1",
  "kind": "approval",
  "title": "INC-1042 · Apply DB pool fix to payment-service (staging-sandbox)",
  "detail": "Patch sha256:3f9a…(full)\nCommit 9c1e…\nFiles: config/database.yml, tests/test_pool.py\nTests 28/28 passed (run TR-1042-1-2)\nRisk: LOW (config-only, 3 lines)\nRollback: revert to 4be2… (pre-approved)\nExpires 10:50Z",
  "options": [{"id": "approve", "label": "Approve remediation", "decision": "accept"},
              {"id": "reject", "label": "Reject", "decision": "decline"}],
  "questions": [],
  "room_id": "d681…",
  "thread_id": null,
  "expires_at": "2026-11-06T10:50:00Z"
}
```

`request_id` = `"{incident_id}:{remediation_id}:{patch_hash[7:23]}:{environment}"`, plus `":r{k}"` when `attempt > 1`. The executor recomputes it.

### 4.3 Verified outcome

This is the shape seen live [V], in both the frame and the `GET outcomes` form.

```json
{"session_id": "INC-1042", "request_id": "…", "kind": "approval", "state": "answered",
 "answer": "approve", "answers": null,
 "answered_by": "@switch-mattermost-039eb334-…-user:localhost", "answered_at": "2026-11-06T10:41:12Z"}
```

`state` is one of `open`, `answered`, `expired` or `closed`.

### 4.4 `ExecutionRequest` (Recovery → executor, local HTTP)

```json
{"execution_request_id": "EXR-1042-1", "approval_id": "APR-1042-1", "requested_by": "recovery"}
```

It carries no hash, approver or environment on purpose. The executor derives everything from the store and Switch.

---

## 5. Patch artifact and hash

```text
PatchArtifact {
  remediation_id, repo: "demo-payment-service", base_commit (40 hex), commit (40 hex),
  diff_ref, changed_files[], diff_stats{added, removed},
  patch_hash: "sha256:" + hex(SHA-256( canonical_diff ))
}
canonical_diff = `git diff --binary --no-color --full-index --no-ext-diff <base_commit> <commit>`
                 with LF line endings, computed by the BLACKBOX tool (never by an LLM)
```

The executor recomputes `canonical_diff` from the worktree at execution time. It also checks that the commit it deploys is `commit` and that its parent is `base_commit`.

---

## 6. Idempotency rules

| Object | Key | Rule |
|---|---|---|
| Inbound Switch event | `payload.message_id` (fallback `room_id`+`sequence`) [V] | The inbox table has a unique constraint. A duplicate means skip. Replays after reconnect are harmless. |
| Approval outcome | `(session_id, request_id)` | Switch may send it twice (push + resync) [S]. A processed-outcomes table enforces uniqueness. |
| Alert → incident | `fingerprint` among incidents not in a terminal state | A second alert with the same fingerprint attaches as evidence. It never creates a second incident. After resolution, it becomes `REOPENED`. |
| Incident room | `incident.room_id` | `create_room` is called only when `room_id` is null, under a row lock. The deterministic name `incident-<service>-<n>` lets a lost response be recovered with `list_rooms`. |
| Handoff | `handoff_id = HND-<inc>-<seq>` | The sender writes the record before sending. A re-send uses the same id. The receiver ACKs `duplicate` and does nothing else. |
| ACK | `(handoff_id, by_agent)` | The first ACK wins; later ones are logged. |
| Progress post | `progress_id` | At most one post per id. |
| Task | `(incident_id, task_type, attempt)` | Unique, so creating again is a no-op. |
| Remediation | `(incident_id, patch_hash)` | The same hash means the same remediation: no new proposal or approval. |
| Approval request | `request_id` (content-bound) [V] | An identical re-open returns the existing row [V]. Different content returns 409. A new attempt uses the `:r{k}` suffix. |
| Execution | `approval_id` (unique), and `(incident_id, patch_hash, environment)` with status ∈ {started, succeeded} | The check and insert run in one `BEGIN IMMEDIATE` transaction. A retried request returns the existing execution record. The executor never re-applies. |
| `/delivered` | `(session_id, request_id)` | Sent once, after the execution or rejection is recorded. It is idempotent on the Switch side [S]. |
| State transition | `(incident_id, from, to, idempotency_key)` | The same tuple is a no-op that returns the existing event. A different `from` than the current state is a 409. |
| Preventive task | `(incident_id, slug)` | Unique. |
| Report | `(incident_id, version)` | Regenerating creates a new version. Old versions are kept. |

**Cross-cutting:** every handler is `load state → check guard → act → record`, and it is safe to run again from the top after a crash. The resume scan (architecture §5) relies on this.
