# 12 · Switch integration: tests and evidence

There are two kinds of tests:

- **Isolated unit tests** use mocks: `httpx.MockTransport`, and a local WebSocket server that
  sends the frame shapes recorded from Switch.
- **Integration tests** run against the **real Switch platform**, with no mocks.

The executable demonstration (`make switch-livecheck`) runs the same steps as the integration
tests and writes JSON evidence.

## 1. Commands

```bash
make test-backend                          # unit tests; live tests are skipped
make test-frontend
make lint
```

Against the running Switch stack (after `make agents-bootstrap`):

```bash
SWITCH_LIVE_TEST=1 make switch-live-test   # pytest, 11 integration tests
```

```bash
make switch-livecheck                      # executable demonstration plus JSON evidence
```

```bash
make switch-livecheck ARGS=--no-harness    # a human presses the cards in Mattermost
```

```bash
make switch-proof-auto                     # Task 10 proof (regression check)
```

The **test harness** (`app/switch/mattermost_harness.py`) logs in to Mattermost as the
incident commander, using `MATTERMOST_IC_PASSWORD`, and presses the card's button as that person
would. Switch authorizes the press. Every harness action is printed with
`[HARNESS acting as IC]`. Without the harness, the human answers within 5 minutes.

The live check uses an isolated SQLite database (`backend/data/livecheck/*.db`), so it never
touches the real incident store.

## 2. Results (2026-11-07, Switch 0.31.0, arm64 host)

### Unit tests (mocked; `make test-backend`)

**77 passed, 12 skipped, 0 failed.** The 12 skipped are the 11 Switch live tests and the
1 Slack live test, which are opt-in.

| File | What it covers |
|---|---|
| `tests/test_switch_reliability.py` (13) | Retries on 503 and network errors for idempotent ops. **No retry** for `post_message` or `send_targeted_message`. No retry on 4xx. Gives up after 3 attempts. Stream reconnects with the **same connection id** and `start_from=<last seq>`. No reconnect after `taken_over` or close code 4401. `gap` triggers a re-read. The ledger processes an event once across stream, catch-up and resume. Revocation is final and refused after execution. |
| `tests/test_switch_approvals.py` (+1) | Adds `not_revoked`. Existing cases cover expiry, late answer, replay, tampered hash, modified body, Switch unreachable, wrong answer or approver. |
| `tests/test_slack.py` (+1) | Switch-level events (room created, handoff sent and ACK with event ids, approval opened, revoked) all land in one incident thread. |

Frontend: **15 passed**. Lint (ruff, eslint, prettier, tsc): clean.

### Integration tests against real Switch (`SWITCH_LIVE_TEST=1 make switch-live-test`)

**11 passed in 44.1 s** (run twice, both 11/11):

```
PASSED test_01_agent_registration_and_auth
PASSED test_02_event_subscription
PASSED test_03_room_creation_membership_roles
PASSED test_04_message_publish_and_receive
PASSED test_05_duplicate_events_processed_once
PASSED test_06_agent_handoff
PASSED test_07_tasks_unsupported
PASSED test_08_approval_creation_and_independent_verification
PASSED test_09_invalid_and_duplicate_approvals
PASSED test_10_reconnection
PASSED test_11_slack_notification_delivery
```

### Executable demonstration (`make switch-livecheck`)

The first run had **10 of 11 passing**. The invalid-approvals step failed with a `ReadTimeout`
from the **test harness**: Mattermost had hung (ping timed out after 5 s, open risk G2). The
recovery was:

1. `docker restart blackbox-switch-mattermost-1`
2. wait about 20 s for Switch's bridge to re-authenticate
3. rerun

The rerun: **`LIVE CHECK PASSED: 11 passed, 0 failed, 0 skipped`**. Evidence is in
`backend/data/livecheck/livecheck-20261107T080821Z.json`:

| Step | Evidence from the run |
|---|---|
| 1 Registration | `blackbox-livecheck` id `c2d0969d-…`; 53 operations; invalid key → **HTTP 401**; key kept in memory only |
| 2 Subscription | 3 WebSocket connections, `scope=all`, `filter=all`, reconnect on |
| 3 Room | INC-1042 ↔ room `livecheck-inc-1042-a55456` (`901a1a28-…`). Members: the 3 agents plus `user` (IC). Exclusive role `investigator` held by RootCause. |
| 4 Messages | Broadcast `sw_d4f0…` (seq 9007194959774037, ts 1794038905362) overheard by both agents. Name-addressed `sw_6837…`: RootCause `addressed=true`, Recovery `false`. Role-addressed `sw_8deb…`: RootCause `addressed=true`. |
| 5 Duplicates | `read_context` replayed 10 messages; **0 re-processed**. Its ids equal the stream's `message_id`. |
| 6 Handoff | `HND-1042-001` Sentinel → RootCause in Switch event `sw_e726…`, with incident context plus 2 evidence items. Verified and **ACKed** through Switch, status `acked`. Forged copy from Recovery refused ("sender 'blackbox-recovery' is not blackbox-sentinel"). Tampered copy refused ("payload hash does not match the store record"). |
| 7 Tasks | **Unsupported**: 0 task or decision operations of 53 |
| 8 Decision | `APR-1042-1` bound to INC-1042, `apply_remediation_commit`, `sha256:47af3a11…`, `staging-sandbox`. Refused before the answer. IC pressed Approve. Verified from `GET /agent-sessions/approvals/outcomes` (all 11 checks pass). Execution claimed; replay refused (`first_use`). |
| 9 Invalid | Content change → **HTTP 409 `REQUEST_CONFLICT`**. Swapped patch hash → refused (`request_id_matches_binding`, `card_shows_patch_hash`). Revoked → Switch `close` → `state: closed`, refused (`not_revoked`, …). IC pressed Reject → refused (`answer_is_approve`). Unanswered with 15 s expiry → expired outcome, refused. |
| 10 Reconnect | Socket dropped; reconnected in **1.05 s** with the same connection id, `start_from=9007194959774048`. The message posted during the drop was processed **once**. Room re-claim audited (`switch.reconnected`). |
| 11 Slack | **13 messages in one thread** in the real workspace (channel `<incidents-channel>`, thread ts `1794038936.683859`); replay from cursor 0 posted nothing |

### Regression: Task 10 proof (`make switch-proof-auto`)

**ALLOWED**, with every check passing, including the new `not_revoked` (evidence
`backend/data/switch/evidence/20261107081110-a09a.json`).

## 3. What is real and what is not

- **Real Switch:**
  - registration and authentication
  - WebSocket subscriptions and reconnect
  - rooms, membership and roles
  - broadcast and addressed messages
  - read_context
  - the handoff and ACK messages
  - approval requests, cards, outcomes, 409 conflicts, close and expiry
- **Real Slack:** `chat.postMessage` and `chat.update` to the configured workspace.
- **Test harness:** pressing Approve or Reject as the IC in Mattermost. This is a human action
  performed through Mattermost's own API.
- **Labelled simulated values:** the live check's patch is a synthetic string whose hash is
  bound and approved. Nothing is deployed. Its two evidence items are marked
  `"simulated": true`.
- **Not run here:** the three autonomous agents end to end (out of scope for this milestone).
  The last full run is documented in docs/11.

## 4. Side effects of running the live tests

Each run creates real artefacts:

- a Switch room and Mattermost channel (`livecheck-…`)
- four approval cards
- a `blackbox-livecheck` agent (re-registered with `overwrite`)
- when Slack is installed, one thread in the incident channel

Rooms are left un-archived so they can be inspected. Archive them from Mattermost or with
`archive_room`.

## 5. Known flakiness

Mattermost runs emulated on arm64 and can hang (it did once during this milestone). Symptoms are
`ReadTimeout` from the harness, or `create_room` timing out. Recovery: restart the container,
wait about 20 s, rerun. This is a host and environment limit, not a BLACKBOX defect, and it stays
open as gate G2.
