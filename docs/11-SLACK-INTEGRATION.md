# 11 · Slack integration

Slack is BLACKBOX's **human-facing notification layer**. It is not a second agent bus.

| Layer | Role |
|---|---|
| **Switch** | Where agents collaborate (handoffs, approval cards). Unchanged by this work. |
| **BLACKBOX backend** | Owns incident state and the append-only audit log. |
| **Slack** | Tells humans what is happening, one thread per incident. Read-only `/blackbox` command. |
| **Frontend** | The detailed dashboard. Every Slack message links to `/incidents/<id>`. |

Slack never approves, deploys or reaches an agent. Approvals happen only on the Switch approval
card (docs/04). A Slack message saying "approve" changes nothing.

## 1. How it works

```
agents / executor ──(act through Switch)──► incident store + audit_events (append-only)
                                                        │  poll by seq, after a stored cursor
                                                        ▼
                                              Slack Event Bridge (app/slack/bridge.py)
                                                        │  chat.postMessage / chat.update
                                                        ▼
                                              #incidents: root message + thread per incident
```

- **Source.** The bridge reads `audit_events`, the records BLACKBOX already writes as agents act
  (`incident.created`, `incident.transition`, `evidence.collected`, `handoff.sent`,
  `remediation.staged`, `approval.*`, `execution.*`, and `agent.progress`, which RootCause now also
  writes for its milestones). It doesn't subscribe to Switch and doesn't add an agent channel.
- **Mapping.** `app/slack/messages.py` maps each event to an *activity*: incident id, agent,
  summary, level and timestamp. Events that aren't meant for humans are skipped. The same mapping
  feeds the dashboard timeline (`GET /api/incidents/{id}`).
- **Threads.** The first event of an incident posts a root message (id, service, severity,
  status and a dashboard link). Every later event is a reply in that thread. The root's status
  line is kept current with `chat.update` when the incident's state changes.
- **Exactly-once intent.** Each event is claimed in `slack_deliveries` (one row per audit `seq`)
  before posting. A re-run, a lost cursor or a second bridge process can't post the same event
  twice.
- **Ordering and retries.** The cursor doesn't move past an undelivered event. The API client
  retries 429s (honouring `Retry-After`), 5xx responses, network errors and Slack's transient
  errors. After that the event stays `failed` and is retried on the next poll.
  - Auth errors (`token_revoked`, `invalid_auth`, …) deactivate the installation, so the
    workspace shows as disconnected.
  - Channel errors (`not_in_channel`, `channel_not_found`, `is_archived`, `missing_scope`, …) set
    the health to `degraded` until an admin fixes the channel.
  - Any other non-transient error is skipped after 5 attempts, so one bad event can't block the
    stream.
- **First start.** The bridge starts from the current end of the log. It doesn't replay history
  into Slack.
- **Where it runs.** By default the bridge runs inside the API process (`SLACK_BRIDGE_IN_API=true`;
  the stack runs one API worker). To run it separately, set it to `false` and run
  `make slack-bridge`.

### Published events

| Event | Message (agent) |
|---|---|
| Incident detected | Incident detected from a verified monitoring alert (Sentinel) |
| Investigation started | Investigation started (Sentinel) |
| Logs and metrics inspected, evidence collected | Evidence counts by kind, plus the top hypothesis. Never raw values. (Sentinel) |
| Repository inspected | `agent.progress REPO_INSPECTED` (RootCause) |
| Root cause identified | The transition reason (RootCause) |
| Patch generated | Coding mode (Claude Code or the labelled fallback), remediation id and short hash (RootCause) |
| Tests started and completed | `TESTS_RUNNING` / `TESTS_RESULT` (RootCause) |
| Handoffs | "Handed … to … through Switch (HND-…)" |
| Approval requested | "…in the Switch incident room. Slack cannot approve." (Recovery) |
| Approval accepted, rejected or expired | Who answered in Switch (Recovery) |
| Sandbox deployment started | Executor |
| Recovery verification completed | Before and after 5xx rate, p50 latency and pool utilisation (Executor) |
| Rollback, failure, needs-human | Warning-level messages |
| Incident resolved | "Incident report and preventive engineering tasks created" (Recovery) |

Every message carries the incident id, agent name, current status, an RFC 3339 timestamp and a
**View in BLACKBOX** link to `BLACKBOX_PUBLIC_URL/incidents/<id>`.

Checked against the real INC-1042 run: the 14 messages read from "Incident detected" through
"5xx 20.66% → 0.22%, p50 4066.0 ms → 100.9 ms" to "Incident resolved".

## 2. Slack app setup

You need a Slack workspace where you can install apps, and a **public HTTPS URL** for the backend.
Slack calls the OAuth redirect and the slash-command endpoint, so they must be reachable from the
internet. Locally, use a tunnel (for example `cloudflared tunnel --url http://localhost:8010` or
`ngrok http 8010`) and call its URL `https://<tunnel>`.

1. **Create the app.** Go to https://api.slack.com/apps, choose **Create New App**, then
   **From a manifest**, pick your workspace and paste:

   ```yaml
   display_information:
     name: BLACKBOX
     description: Incident notifications from BLACKBOX (read-only)
   features:
     bot_user:
       display_name: BLACKBOX
       always_online: false
     slash_commands:
       - command: /blackbox
         url: https://<tunnel>/api/slack/commands
         description: Incident status (read-only)
         usage_hint: status [INC-1234] | help
         should_escape: false
   oauth_config:
     redirect_urls:
       - https://<tunnel>/api/slack/oauth/callback
     scopes:
       bot:
         - chat:write
         - commands
         - channels:read
   settings:
     org_deploy_enabled: false
     socket_mode_enabled: false
     token_rotation_enabled: false
   ```

2. **Copy the credentials.** From **Basic Information**, copy the Client ID, Client Secret and
   Signing Secret into `.env`:

   ```bash
   SLACK_CLIENT_ID=...
   SLACK_CLIENT_SECRET=...
   SLACK_SIGNING_SECRET=...
   SLACK_REDIRECT_URI=https://<tunnel>/api/slack/oauth/callback
   SLACK_ALLOWED_TEAM_IDS=T0XXXXXXX     # your workspace id
   BLACKBOX_SECRET_KEY=<output of: make slack-key>
   BLACKBOX_ADMIN_TOKEN=<a long random string>
   BLACKBOX_PUBLIC_URL=http://localhost:5180   # where people open the dashboard
   ```

   Restart the backend.

3. **Create a channel.** Create a public channel such as `#incidents` and invite the bot:
   `/invite @BLACKBOX`.

4. **Install.** Open the dashboard at **Slack** (`/settings/slack`) and click
   **Connect to Slack**, or browse to `https://<tunnel>/api/slack/install`. Approve the scopes.
   You land back on the settings page with `?connected=1`.

5. **Set the channel.** Use the channel id (channel details, then the bottom of the About tab):

   ```bash
   curl -X POST https://<tunnel>/api/slack/admin/channel \
     -H "Authorization: Bearer $BLACKBOX_ADMIN_TOKEN" -H 'Content-Type: application/json' \
     -d '{"channel_id":"C0XXXXXXX"}'
   ```

   The backend calls `conversations.info` and refuses a channel the bot isn't a member of, or one
   that is archived. `SLACK_INCIDENT_CHANNEL_ID` in `.env` also works.

6. **Test.**

   ```bash
   curl -X POST https://<tunnel>/api/slack/admin/test -H "Authorization: Bearer $BLACKBOX_ADMIN_TOKEN"
   ```

   Then try `/blackbox help` in the channel.

**Without a tunnel (manual token import).** Install the app from the Slack app page
(**Install App**), copy the **Bot User OAuth Token** (`xoxb-…`) and import it. The backend checks
it with `auth.test`, encrypts it and stores it. Slash commands still need a public URL.

```bash
curl -X POST http://localhost:8010/api/slack/admin/token \
  -H "Authorization: Bearer $BLACKBOX_ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"token":"xoxb-..."}'
```

**Disconnect.** `POST /api/slack/admin/disconnect` with the admin token. To revoke the token fully,
remove the app in Slack. A revoked token is also detected automatically on the next post.

## 3. Slash commands (read-only)

| Command | Reply (ephemeral, only the caller sees it) |
|---|---|
| `/blackbox status` | Active incident, or the latest one: id, service, severity, status, title, link |
| `/blackbox status INC-1042` | That incident |
| `/blackbox help` | Usage, plus a reminder that approvals happen only in Switch |
| `/blackbox approve …`, `deploy`, `run`, `exec`, `reject`, `rollback` | Refused: "Slack cannot approve, deploy or run anything" |

Commands work only for the connected, allow-listed workspace, and only in the incident channel
(or the channels in `SLACK_COMMAND_CHANNEL_IDS`). Each command is recorded in the audit log as
`slack.command`.

## 4. Settings page

`/settings/slack` polls `GET /api/slack/status` every 5 s. It shows:

- connection status and the reason it was deactivated
- workspace name and id
- incident channel
- last successful delivery
- last error and when it happened
- delivery counts (delivered, failing, skipped) and the unprocessed backlog
- granted scopes and whether slash commands are enabled
- health: `healthy`, `idle`, `degraded`, `not_configured`, `blocked`, `disconnected` or `error`

The page is read-only. Configuration changes go through the admin API, which needs
`BLACKBOX_ADMIN_TOKEN`.

## 5. API reference

| Method and path | Auth | Purpose |
|---|---|---|
| `GET /api/slack/status` | none (no secrets in the response) | Health for the settings page |
| `GET /api/slack/install` | none | Redirect to Slack OAuth with a single-use `state` |
| `GET /api/slack/oauth/callback` | `state` | Exchange the code (`oauth.v2.access`), store the token encrypted |
| `POST /api/slack/admin/channel` | admin token | Set the incident channel (checked with `conversations.info`) |
| `POST /api/slack/admin/token` | admin token | Manual bot-token import (checked with `auth.test`) |
| `POST /api/slack/admin/test` | admin token | Post a test message |
| `POST /api/slack/admin/disconnect` | admin token | Deactivate the installation |
| `POST /api/slack/commands` | Slack signature | `/blackbox` slash command |
| `GET /api/incidents`, `GET /api/incidents/{id}` | none | Dashboard incident view and sanitized timeline |

## 6. Code map

| File | Contents |
|---|---|
| `backend/app/slack/config.py` | Settings and least-privilege `BOT_SCOPES` |
| `backend/app/slack/crypto.py` | Fernet encryption of bot tokens |
| `backend/app/slack/api.py` | Web API client: retries and error classification |
| `backend/app/slack/store.py` | Installations, settings, threads, delivery ledger, OAuth states |
| `backend/app/slack/messages.py` | Event-to-activity mapping, sanitization, Block Kit rendering |
| `backend/app/slack/bridge.py` | The event bridge and `health()` |
| `backend/app/slack/routes.py` | OAuth, admin, status, slash commands |
| `backend/app/api/incidents.py` | Incident view API |
| `backend/migrations/versions/0003_slack.py` | Tables |
| `frontend/src/pages/SlackSettingsPage.tsx` | Settings page |
| `frontend/src/pages/IncidentPage.tsx` | Link target for Slack messages |
| `backend/tests/test_slack.py` | Unit tests against a mocked Slack API |
| `backend/tests/integration/test_slack_live.py` | Genuine Slack test |

## 7. Tests

```bash
make test-backend        # includes tests/test_slack.py (mocked Slack, no network)
make test-frontend       # includes the settings page and incident page tests
SLACK_LIVE_TEST=1 SLACK_LIVE_BOT_TOKEN=xoxb-... SLACK_LIVE_CHANNEL_ID=C... make slack-live-test
```

The live test posts a real `[live test]` incident thread to the channel through the real bridge.
It checks Slack's `ts` values and health, then checks that replaying from a reset cursor posts
nothing. It also checks that an invalid token is classified as an auth error. It needs a bot that
has been invited to the channel. OAuth isn't covered, because it needs a browser.

## 8. Known limits

- If the process crashes between Slack accepting a root message and BLACKBOX saving its `ts`, a
  second root can be posted for that incident. Thread replies are protected by the delivery ledger.
  A pending claim left by a crash is retried after 5 minutes.
- One active workspace and one incident channel at a time.
- The bridge polls (2 s by default). It doesn't push.
