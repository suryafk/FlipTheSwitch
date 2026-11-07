# 11 · Slack integration: security

Scope: the Slack Event Bridge, OAuth installation, admin endpoints and `/blackbox` commands
(docs/11-SLACK-INTEGRATION.md). It extends the threat model in docs/04.

## 1. Invariants

1. **Slack can't authorize anything.** No Slack input reaches Switch, an agent, the executor or
   incident state. Approvals exist only as Switch approval cards, verified by the executor with
   Switch (docs/04). `/blackbox approve …` is answered with a refusal. Tests check that no
   `approval.*` or `execution.*` record appears and the incident state doesn't change.
2. **One-way data flow.** The bridge reads the audit log and writes to Slack. It has no Switch
   client, no agent credentials and no write access to the incident store. It writes only its own
   tables and audit entries such as `slack.installed`.
3. **No command execution.** The slash-command handler only matches a fixed set of words and
   looks things up by an id validated against `^INC-\d{1,8}$`. Nothing reaches a shell, the
   launcher or the coding agent.
4. **Switch is unchanged.** No Switch integration code was modified. The only agent change:
   RootCause also writes its progress milestones to the audit log, besides posting them to Switch.

## 2. Threats and controls

| Threat | Control | Where |
|---|---|---|
| Forged slash-command request | `v0` HMAC-SHA256 signature over `v0:{ts}:{raw body}` with `SLACK_SIGNING_SECRET`, constant-time compare. Without a signing secret, commands are disabled (503). | `routes.verify_slack_signature` |
| Replayed command | Timestamp must be within 300 s | same |
| Oversized body | 8 KiB limit before parsing | `routes.slash_command` |
| Command from another workspace or channel | `team_id` must equal the active installation and be in `SLACK_ALLOWED_TEAM_IDS`. The channel must be the incident channel or in `SLACK_COMMAND_CHANNEL_IDS`. | `routes.slash_command` |
| OAuth CSRF or code injection | 256-bit random `state`, stored server-side, single use, 10-minute expiry | `store.new_oauth_state`, `consume_oauth_state` |
| Installing into an unintended workspace | `SLACK_ALLOWED_TEAM_IDS` is checked at OAuth callback, at token import and on every bridge run (`blocked`). With one allowed team, the authorize URL pins `team=`. | `routes`, `bridge.resolve_target` |
| Over-privileged token | Scopes are only `chat:write`, `commands` and `channels:read`. A token granted any extra scope is refused at install. User tokens are refused (`token_type` must be `bot`). | `config.BOT_SCOPES`, `routes.oauth_callback` |
| Token theft from the database | Bot tokens are stored only as Fernet ciphertext (AES-128-CBC + HMAC-SHA256) under `BLACKBOX_SECRET_KEY`, which lives in the environment, not the database. Without the key, tokens aren't stored (503). | `crypto.py`, `store.save_installation` |
| Secret leakage through APIs or logs | `/api/slack/status` returns no token, secret or key (tested). Tokens travel only in the `Authorization` header to `slack.com`. Log lines carry Slack error codes, never tokens. | `bridge.health`, `api.SlackClient` |
| Unauthorized configuration changes | `/api/slack/admin/*` requires `Authorization: Bearer <BLACKBOX_ADMIN_TOKEN>` (constant-time compare). They are disabled (503) when the admin token is unset. Channel changes are audited (`slack.channel_set`). | `routes.require_admin` |
| Revoked or stolen token still in use | `invalid_auth`, `token_revoked`, `account_inactive` and similar errors deactivate the installation immediately. Disconnect is available from the admin API. | `bridge.fail` |
| Sensitive content posted to Slack | Messages are built from fixed templates. Free text is limited to short fields (transition reasons, hypotheses, progress text). That text passes `clean()`, which strips code blocks, redacts token, key and password patterns, escapes `<`, `>` and `&` (no `<!channel>` pings or forged links), collapses whitespace and caps length. Raw evidence values, logs, diffs, payloads and LLM reasoning are never included: evidence is published as counts by kind. | `messages.py` |
| Mention or link injection through incident data | Same escaping. `unfurl_links` and `unfurl_media` are off. | `messages.clean`, `api.post_message` |
| Duplicate or flooding posts | Delivery ledger claimed atomically per event (insert-unique, conditional update for retries). The bridge doesn't replay history on first start. Poison events are skipped after 5 attempts. | `store.claim_delivery`, `bridge` |
| Slack outage or rate limit affecting incident response | The bridge is a passive reader. Agents and approvals don't depend on Slack. 429 `Retry-After` is honoured (capped at 30 s). | `api.SlackClient` |
| Approval confusion ("someone said approve in Slack") | The approval-requested message itself says "Slack cannot approve". The `/blackbox` help says the same. | `messages.TRANSITIONS`, `routes.HELP` |

## 3. Residual risks

- **Install endpoint is unauthenticated.** Anyone who can reach `/api/slack/install` can start an
  OAuth flow. Only an allow-listed workspace can complete it, so set `SLACK_ALLOWED_TEAM_IDS`.
  Re-installing the same workspace replaces its token.
- **Status and incident views are unauthenticated,** like the rest of the local dashboard. They
  expose operational metadata (workspace name, channel id, incident titles and timelines), not
  secrets. Put the dashboard behind your SSO before exposing it.
- **Key rotation.** Changing `BLACKBOX_SECRET_KEY` makes stored tokens unreadable. The bridge then
  reports `error` and you reinstall. No re-encryption tool exists yet.
- **Channel visibility.** Anyone in the incident channel sees incident summaries. Use a private
  channel if that matters. That needs the `groups:read` scope instead of `channels:read`, which the
  app doesn't request today.
- **Redaction is pattern-based.** It's defence in depth. The primary control is that raw logs and
  evidence values are never selected for publishing.

## 4. Verification

`backend/tests/test_slack.py` covers:

- encrypted storage, and refusal without a key
- no secrets in the status response
- OAuth state single use, forged state, disallowed workspace and excess scopes
- admin token enforcement
- bad, tampered, missing and replayed signatures
- workspace and channel restrictions
- disconnected workspace
- approve, deploy, run and exec refusals with no state change
- sanitization of mentions, links, tokens and code
- raw evidence values not published
- no duplicates after a rerun or cursor reset, and atomic claims
- revoked token disconnects, and missing channel permission degrades
