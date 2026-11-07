"""Slack integration tests against a mocked Slack Web API (httpx.MockTransport).

No network. The genuine Slack test is tests/integration/test_slack_live.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.session import get_sessionmaker
from app.incidents import store as incidents
from app.slack import bridge as bridge_mod
from app.slack import routes
from app.slack import store as slack_store
from app.slack.api import SlackApiError, SlackClient
from app.slack.config import get_slack_settings
from app.slack.crypto import TokenCryptoError, decrypt_token, encrypt_token
from app.slack.messages import clean

SIGNING = "test-signing-secret"
ADMIN = "test-admin-token"
TEAM = "T0TEAM"
CHANNEL = "C0INCIDENT"
BOT_TOKEN = "xoxb-1111-2222-abcdefghijklmnop"


class FakeSlack:
    """Records every Web API call; responses are scripted per method."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], dict[str, str]]] = []
        self.scripts: dict[str, list[Callable[[dict], httpx.Response]]] = {}
        self.ts = 1000

    def script(self, method: str, *responses: Callable[[dict], httpx.Response]) -> None:
        self.scripts.setdefault(method, []).extend(responses)

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        if request.headers.get("content-type", "").startswith("application/json"):
            body = json.loads(request.content or b"{}")
        else:
            body = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.calls.append((method, body, dict(request.headers)))
        queue = self.scripts.get(method)
        if queue:
            return queue.pop(0)(body)
        return self.default(method, body)

    def default(self, method: str, body: dict) -> httpx.Response:
        if method == "chat.postMessage":
            self.ts += 1
            return ok({"channel": body["channel"], "ts": f"{self.ts}.000100"})
        if method == "chat.update":
            return ok({"channel": body["channel"], "ts": body["ts"]})
        if method == "auth.test":
            return ok({"team_id": TEAM, "team": "Acme", "user_id": "U0BOT", "bot_id": "B0BOT"})
        if method == "conversations.info":
            return ok(
                {
                    "channel": {
                        "id": body["channel"],
                        "name": "incidents",
                        "is_member": True,
                        "is_archived": False,
                    }
                }
            )
        if method == "oauth.v2.access":
            return ok(
                {
                    "access_token": BOT_TOKEN,
                    "token_type": "bot",
                    "scope": "chat:write,commands,channels:read",
                    "bot_user_id": "U0BOT",
                    "team": {"id": TEAM, "name": "Acme"},
                }
            )
        return ok({})

    def posts(self) -> list[dict]:
        return [b for m, b, _ in self.calls if m == "chat.postMessage"]

    def method_calls(self, method: str) -> list[dict]:
        return [b for m, b, _ in self.calls if m == method]


def ok(body: dict) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, **body})


def slack_error(error: str) -> Callable[[dict], httpx.Response]:
    return lambda _: httpx.Response(200, json={"ok": False, "error": error})


def http(status: int, headers: dict | None = None) -> Callable[[dict], httpx.Response]:
    return lambda _: httpx.Response(status, headers=headers or {}, text="upstream")


@pytest.fixture
def slack_env(db_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeSlack]:
    monkeypatch.setenv("BLACKBOX_SECRET_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("BLACKBOX_ADMIN_TOKEN", ADMIN)
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SIGNING)
    monkeypatch.setenv("SLACK_CLIENT_ID", "123.456")
    monkeypatch.setenv("SLACK_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv(
        "SLACK_REDIRECT_URI", "https://blackbox.example.test/api/slack/oauth/callback"
    )
    monkeypatch.setenv("SLACK_ALLOWED_TEAM_IDS", TEAM)
    monkeypatch.setenv("BLACKBOX_PUBLIC_URL", "https://blackbox.example.test")
    get_slack_settings.cache_clear()
    fake = FakeSlack()
    monkeypatch.setattr(routes, "client_factory", lambda: make_client(fake))
    from app.db.migrate import upgrade_to_head

    upgrade_to_head(db_url)
    yield fake
    get_slack_settings.cache_clear()


def make_client(fake: FakeSlack, attempts: int = 3) -> SlackClient:
    return SlackClient(
        "https://slack.test/api",
        transport=httpx.MockTransport(fake.handler),
        max_attempts=attempts,
        backoff_seconds=0,
        max_retry_after=0,
    )


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def connect(channel: str | None = CHANNEL) -> None:
    slack_store.save_installation(
        TEAM, "Acme", "U0BOT", "chat:write,commands,channels:read", BOT_TOKEN, actor="test"
    )
    if channel:
        slack_store.set_setting("channel_id", channel)
    slack_store.set_setting(bridge_mod.CURSOR_KEY, "0")


def open_incident() -> str:
    inc = incidents.create_incident(
        "payment-service", "5xx spike on /pay", "fp-1", {"alert": 1}, "sentinel"
    )
    return inc["incident_id"]


def bridge_for(fake: FakeSlack, attempts: int = 3) -> bridge_mod.SlackBridge:
    return bridge_mod.SlackBridge(make_client(fake, attempts), get_slack_settings())


def deliveries() -> dict[int, dict]:
    with get_sessionmaker()() as s:
        rows = s.execute(text("SELECT * FROM slack_deliveries")).mappings().all()
        return {r["event_seq"]: dict(r) for r in rows}


def cursor() -> int:
    return int(slack_store.get_setting(bridge_mod.CURSOR_KEY) or 0)


# ---------------- authentication: token storage and OAuth ----------------


def test_tokens_are_encrypted_at_rest(slack_env: FakeSlack) -> None:
    connect()
    with get_sessionmaker()() as s:
        stored = s.execute(text("SELECT token_encrypted FROM slack_installations")).scalar_one()
    assert BOT_TOKEN not in stored
    assert decrypt_token(stored) == BOT_TOKEN
    assert slack_store.bot_token(TEAM) == BOT_TOKEN


def test_token_crypto_refuses_without_key(
    slack_env: FakeSlack, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BLACKBOX_SECRET_KEY", "")
    get_slack_settings.cache_clear()
    with pytest.raises(TokenCryptoError):
        encrypt_token(BOT_TOKEN)


def test_status_never_exposes_secrets(slack_env: FakeSlack, client: TestClient) -> None:
    connect()
    body = client.get("/api/slack/status").text
    for secret in (BOT_TOKEN, SIGNING, ADMIN, "client-secret"):
        assert secret not in body
    data = json.loads(body)
    assert data["connected"] is True
    assert data["workspace"] == {"team_id": TEAM, "team_name": "Acme"}
    assert data["channel_id"] == CHANNEL


def test_oauth_install_and_callback(slack_env: FakeSlack, client: TestClient) -> None:
    r = client.get("/api/slack/install", follow_redirects=False)
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["scope"] == ["chat:write,commands,channels:read"]
    assert q["team"] == [TEAM]
    state = q["state"][0]

    r = client.get(f"/api/slack/oauth/callback?state={state}&code=abc", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].endswith("/settings/slack?connected=1")
    assert slack_store.active_installation()["team_id"] == TEAM
    exchange = slack_env.method_calls("oauth.v2.access")[0]
    assert exchange["code"] == "abc"

    # State is single use (CSRF / replay protection).
    r = client.get(f"/api/slack/oauth/callback?state={state}&code=abc", follow_redirects=False)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_oauth_state"


def test_oauth_rejects_unknown_state(slack_env: FakeSlack, client: TestClient) -> None:
    r = client.get("/api/slack/oauth/callback?state=forged&code=abc", follow_redirects=False)
    assert r.status_code == 400
    assert not slack_env.calls


def test_oauth_rejects_disallowed_workspace(slack_env: FakeSlack, client: TestClient) -> None:
    slack_env.script(
        "oauth.v2.access",
        lambda _: ok(
            {
                "access_token": "xoxb-other-workspace-token",
                "token_type": "bot",
                "scope": "chat:write",
                "team": {"id": "T0EVIL", "name": "Other"},
            }
        ),
    )
    state = slack_store.new_oauth_state()
    r = client.get(f"/api/slack/oauth/callback?state={state}&code=abc", follow_redirects=False)
    assert r.headers["location"].endswith("error=workspace_not_allowed")
    assert slack_store.active_installation() is None


def test_oauth_rejects_excess_scopes(slack_env: FakeSlack, client: TestClient) -> None:
    slack_env.script(
        "oauth.v2.access",
        lambda _: ok(
            {
                "access_token": BOT_TOKEN,
                "token_type": "bot",
                "scope": "chat:write,commands,channels:read,channels:history,admin",
                "team": {"id": TEAM, "name": "Acme"},
            }
        ),
    )
    state = slack_store.new_oauth_state()
    r = client.get(f"/api/slack/oauth/callback?state={state}&code=abc", follow_redirects=False)
    assert r.headers["location"].endswith("error=excess_scopes")
    assert slack_store.active_installation() is None


def test_admin_endpoints_require_token(slack_env: FakeSlack, client: TestClient) -> None:
    for path, body in (
        ("/api/slack/admin/channel", {"channel_id": CHANNEL}),
        ("/api/slack/admin/token", {"token": BOT_TOKEN}),
        ("/api/slack/admin/disconnect", {}),
        ("/api/slack/admin/test", {}),
    ):
        assert client.post(path, json=body).status_code == 401
        assert (
            client.post(path, json=body, headers={"Authorization": "Bearer wrong"}).status_code
            == 401
        )
    assert not slack_env.calls


def test_admin_token_import_and_channel_checks(slack_env: FakeSlack, client: TestClient) -> None:
    auth = {"Authorization": f"Bearer {ADMIN}"}
    r = client.post("/api/slack/admin/token", json={"token": BOT_TOKEN}, headers=auth)
    assert r.status_code == 200, r.text
    assert slack_store.active_installation()["team_id"] == TEAM

    # Missing channel permissions: the bot is not a member.
    slack_env.script(
        "conversations.info",
        lambda b: ok(
            {"channel": {"id": b["channel"], "name": "x", "is_member": False, "is_archived": False}}
        ),
    )
    r = client.post("/api/slack/admin/channel", json={"channel_id": "C0NOTMEMBER"}, headers=auth)
    assert r.status_code == 400
    assert "Invite the BLACKBOX bot" in r.json()["error"]["message"]

    slack_env.script("conversations.info", slack_error("channel_not_found"))
    r = client.post("/api/slack/admin/channel", json={"channel_id": "C0MISSING"}, headers=auth)
    assert r.status_code == 400 and "channel_not_found" in r.json()["error"]["message"]

    r = client.post("/api/slack/admin/channel", json={"channel_id": CHANNEL}, headers=auth)
    assert r.status_code == 200
    assert client.get("/api/slack/status").json()["channel_id"] == CHANNEL

    r = client.post("/api/slack/admin/disconnect", headers=auth)
    assert r.json() == {"disconnected": True}
    assert client.get("/api/slack/status").json()["status"] == "disconnected"


# ---------------- Web API client: retries ----------------


def test_client_retries_rate_limit_and_5xx(slack_env: FakeSlack) -> None:
    slack_env.script("chat.postMessage", http(429, {"Retry-After": "1"}), http(503))
    resp = run(make_client(slack_env, attempts=4).post_message(BOT_TOKEN, CHANNEL, "hi"))
    assert resp["ok"]
    assert len(slack_env.posts()) == 3


def test_client_gives_up_after_max_attempts(slack_env: FakeSlack) -> None:
    slack_env.script("chat.postMessage", http(500), http(502), http(500))
    with pytest.raises(SlackApiError) as e:
        run(make_client(slack_env, attempts=3).post_message(BOT_TOKEN, CHANNEL, "hi"))
    assert e.value.transient and e.value.error == "http_500"


def test_client_does_not_retry_auth_errors(slack_env: FakeSlack) -> None:
    slack_env.script("chat.postMessage", slack_error("invalid_auth"))
    with pytest.raises(SlackApiError) as e:
        run(make_client(slack_env).post_message(BOT_TOKEN, CHANNEL, "hi"))
    assert e.value.auth_problem and not e.value.transient
    assert len(slack_env.posts()) == 1


def test_client_sends_bearer_token_header(slack_env: FakeSlack) -> None:
    run(make_client(slack_env).post_message(BOT_TOKEN, CHANNEL, "hi"))
    _, body, headers = slack_env.calls[0]
    assert headers["authorization"] == f"Bearer {BOT_TOKEN}"
    assert BOT_TOKEN not in json.dumps(body)


# ---------------- bridge: delivery, threads, updates ----------------


def test_bridge_publishes_incident_thread(slack_env: FakeSlack) -> None:
    connect()
    iid = open_incident()
    incidents.transition(iid, "INVESTIGATING", "sentinel", "evidence collection started")
    run(bridge_for(slack_env).run_once())

    posts = slack_env.posts()
    root, replies = posts[0], posts[1:]
    assert "thread_ts" not in root and root["channel"] == CHANNEL
    assert iid in root["text"]
    root_ts = "1001.000100"
    assert [p["thread_ts"] for p in replies] == [root_ts, root_ts]
    detected, investigating = (json.dumps(p["blocks"]) for p in replies)
    assert "Sentinel" in detected and "Incident detected" in detected
    assert "Investigation started" in investigating
    for msg in (detected, investigating):  # id, status, timestamp, dashboard link
        assert iid in msg and "INVESTIGATING" in msg
        assert f"https://blackbox.example.test/incidents/{iid}" in msg
        assert "T" in msg and "Z" in msg
    # Root status kept current with chat.update.
    updates = slack_env.method_calls("chat.update")
    assert updates and updates[-1]["ts"] == root_ts
    assert "INVESTIGATING" in json.dumps(updates[-1]["blocks"])
    health = bridge_mod.health()
    assert health["status"] == "healthy" and health["last_success_at"]


def test_bridge_maps_full_lifecycle(slack_env: FakeSlack) -> None:
    connect()
    iid = open_incident()
    seq = incidents.audit
    seq(
        "evidence.collected",
        "sentinel",
        {
            "incident_id": iid,
            "evidence": [
                {"kind": "metric", "value": "raw secret log line password=hunter2"},
                {"kind": "log", "value": "x"},
            ],
            "analysis": {"hypotheses": [{"confidence": 0.85, "statement": "pool size cut to 10"}]},
        },
    )
    seq(
        "agent.progress",
        "rootcause",
        {"incident_id": iid, "milestone": "REPO_INSPECTED", "text": "diff 0fccbb3..9d33906"},
    )
    seq(
        "agent.progress",
        "rootcause",
        {"incident_id": iid, "milestone": "TESTS_RUNNING", "text": "REM-1: running"},
    )
    seq(
        "agent.progress",
        "rootcause",
        {"incident_id": iid, "milestone": "TESTS_RESULT", "text": "REM-1: 12/12 passed"},
    )
    seq(
        "agent.progress",
        "rootcause",
        {
            "incident_id": iid,
            "milestone": "ROOT_CAUSE_FOUND",
            "text": "duplicate of the transition; not published",
        },
    )
    incidents.transition(iid, "INVESTIGATING", "sentinel", "started")
    incidents.transition(iid, "ROOT_CAUSE_IDENTIFIED", "rootcause", "pool_size 50 -> 10")
    seq(
        "remediation.staged",
        "rootcause",
        {
            "incident_id": iid,
            "remediation_id": "REM-1",
            "patch_hash": "sha256:" + "a" * 64,
            "coding_mode": "claude_code",
        },
    )
    run(bridge_for(slack_env).run_once())
    text_all = json.dumps(slack_env.posts())
    for phrase in (
        "Logs and metrics inspected",
        "Repository inspected",
        "Regression tests started",
        "Tests completed: REM-1: 12/12 passed",
        "Root cause identified: pool_size 50 -&gt; 10",
        "Patch generated by Claude Code",
    ):
        assert phrase in text_all, phrase
    assert "hunter2" not in text_all  # raw evidence values are never published
    assert "duplicate of the transition" not in text_all


def test_bridge_ignores_events_without_incident(slack_env: FakeSlack) -> None:
    connect()
    incidents.audit("switch.agent_ready", "sentinel", {"agent": "x"})
    run(bridge_for(slack_env).run_once())
    assert slack_env.posts() == []
    assert cursor() == slack_store.max_audit_seq()


def test_first_start_does_not_replay_history(slack_env: FakeSlack) -> None:
    connect()
    open_incident()
    slack_store.set_setting(bridge_mod.CURSOR_KEY, None)
    with get_sessionmaker()() as s:
        s.execute(text("DELETE FROM slack_settings WHERE key=:k"), {"k": bridge_mod.CURSOR_KEY})
        s.commit()
    run(bridge_for(slack_env).run_once())
    assert slack_env.posts() == []


# ---------------- duplicates ----------------


def test_no_duplicates_on_rerun_or_cursor_reset(slack_env: FakeSlack) -> None:
    connect()
    iid = open_incident()
    incidents.transition(iid, "INVESTIGATING", "sentinel", "started")
    b = bridge_for(slack_env)
    run(b.run_once())
    first = len(slack_env.posts())
    run(b.run_once())
    slack_store.set_setting(bridge_mod.CURSOR_KEY, "0")  # simulate a lost cursor
    run(bridge_for(slack_env).run_once())
    assert len(slack_env.posts()) == first == 3


def test_concurrent_claims_only_one_wins(slack_env: FakeSlack) -> None:
    assert slack_store.claim_delivery(42, "INC-1", "incident.created") is True
    assert slack_store.claim_delivery(42, "INC-1", "incident.created") is False  # held: pending
    slack_store.finish_delivery(42, "delivered", "1.1")
    assert slack_store.claim_delivery(42, "INC-1", "incident.created") is False


# ---------------- failures and retries ----------------


def test_transient_failure_is_retried_in_order(slack_env: FakeSlack) -> None:
    connect()
    before = slack_store.max_audit_seq()
    iid = open_incident()
    # Root post fails on all 3 client attempts.
    slack_env.script("chat.postMessage", http(500), http(500), http(500))
    run(bridge_for(slack_env).run_once())
    assert cursor() == before  # did not move past the undelivered event
    d = next(iter(deliveries().values()))
    assert d["status"] == "failed" and d["last_error"] == "http_500"
    assert bridge_mod.health()["status"] == "degraded"

    run(bridge_for(slack_env).run_once())  # Slack recovered
    texts = [p["text"] for p in slack_env.posts() if "thread_ts" in p]
    assert len(texts) == 1 and iid in texts[0]
    assert next(iter(deliveries().values()))["status"] == "delivered"
    assert bridge_mod.health()["status"] == "healthy"


def test_missing_channel_permission_degrades_without_disconnecting(slack_env: FakeSlack) -> None:
    connect()
    before = slack_store.max_audit_seq()
    open_incident()
    slack_env.script("chat.postMessage", slack_error("not_in_channel"))
    run(bridge_for(slack_env).run_once())
    h = bridge_mod.health()
    assert h["status"] == "degraded" and "not_in_channel" in h["last_error"]
    assert h["connected"] is True
    assert cursor() == before


def test_revoked_token_disconnects_workspace(slack_env: FakeSlack) -> None:
    connect()
    open_incident()
    slack_env.script("chat.postMessage", slack_error("token_revoked"))
    run(bridge_for(slack_env).run_once())
    h = bridge_mod.health()
    assert h["status"] == "disconnected" and h["connected"] is False
    assert "token_revoked" in (h["deactivated_reason"] or "")
    calls = len(slack_env.calls)
    run(bridge_for(slack_env).run_once())  # disconnected: nothing is attempted
    assert len(slack_env.calls) == calls


def test_disconnected_workspace_publishes_nothing(slack_env: FakeSlack) -> None:
    open_incident()
    slack_store.set_setting(bridge_mod.CURSOR_KEY, "0")
    run(bridge_for(slack_env).run_once())
    assert slack_env.calls == []
    assert bridge_mod.health()["status"] == "disconnected"


def test_workspace_outside_allow_list_is_blocked(
    slack_env: FakeSlack, monkeypatch: pytest.MonkeyPatch
) -> None:
    connect()
    open_incident()
    monkeypatch.setenv("SLACK_ALLOWED_TEAM_IDS", "T0OTHER")
    get_slack_settings.cache_clear()
    run(bridge_mod.SlackBridge(make_client(slack_env), get_slack_settings()).run_once())
    assert slack_env.calls == []
    assert slack_store.get_setting("health") == "blocked"


def test_poison_event_is_skipped_after_max_attempts(slack_env: FakeSlack) -> None:
    connect()
    iid = open_incident()
    run(bridge_for(slack_env).run_once())  # root + detected
    incidents.transition(iid, "INVESTIGATING", "sentinel", "started")
    slack_env.script("chat.postMessage", *[slack_error("invalid_blocks")] * bridge_mod.MAX_ATTEMPTS)
    for _ in range(bridge_mod.MAX_ATTEMPTS):
        run(bridge_for(slack_env).run_once())
    statuses = sorted(d["status"] for d in deliveries().values())
    assert statuses == ["delivered", "skipped"]
    assert cursor() == slack_store.max_audit_seq()


# ---------------- sanitization ----------------


def test_clean_escapes_mentions_and_redacts_secrets() -> None:
    out = clean(
        "<!channel> see <https://evil|here> xoxb-123-abc token=abc "
        "```print(secret)``` sk-ant-api03-XYZ"
    )
    assert "<!channel>" not in out and "&lt;!channel&gt;" in out
    assert "<https://evil" not in out
    assert "xoxb-" not in out and "sk-ant" not in out and "token=abc" not in out
    assert "print(secret)" not in out
    assert len(clean("x" * 1000, 50)) == 50


# ---------------- slash commands ----------------


def sign(body: str, ts: int | None = None, secret: str = SIGNING) -> dict[str, str]:
    ts = ts or int(time.time())
    sig = "v0=" + hmac.new(secret.encode(), f"v0:{ts}:{body}".encode(), hashlib.sha256).hexdigest()
    return {
        "X-Slack-Request-Timestamp": str(ts),
        "X-Slack-Signature": sig,
        "Content-Type": "application/x-www-form-urlencoded",
    }


def command(
    client: TestClient,
    text_: str,
    team: str = TEAM,
    channel: str = CHANNEL,
    headers: dict | None = None,
) -> httpx.Response:
    body = urlencode(
        {
            "command": "/blackbox",
            "text": text_,
            "team_id": team,
            "channel_id": channel,
            "user_id": "U0HUMAN",
        }
    )
    return client.post("/api/slack/commands", content=body, headers=headers or sign(body))


def test_command_rejects_bad_signature(slack_env: FakeSlack, client: TestClient) -> None:
    connect()
    body = urlencode({"text": "status", "team_id": TEAM, "channel_id": CHANNEL})
    for headers in (sign(body, secret="wrong"), {"X-Slack-Request-Timestamp": "1"}, {}):
        r = client.post("/api/slack/commands", content=body, headers=headers)
        assert r.status_code == 401
    tampered = sign(body)
    r = client.post("/api/slack/commands", content=body + "&x=1", headers=tampered)
    assert r.status_code == 401


def test_command_rejects_replayed_timestamp(slack_env: FakeSlack, client: TestClient) -> None:
    connect()
    body = urlencode({"text": "status", "team_id": TEAM, "channel_id": CHANNEL})
    r = client.post(
        "/api/slack/commands", content=body, headers=sign(body, ts=int(time.time()) - 600)
    )
    assert r.status_code == 401 and r.json()["error"]["code"] == "stale_request"


def test_command_status_and_help(slack_env: FakeSlack, client: TestClient) -> None:
    connect()
    iid = open_incident()
    r = command(client, "status")
    assert r.status_code == 200
    body = r.json()
    assert body["response_type"] == "ephemeral"
    assert iid in body["text"] and "DETECTED" in body["text"]
    assert f"https://blackbox.example.test/incidents/{iid}" in body["text"]
    assert iid in command(client, f"status {iid.lower()}").json()["text"]
    assert "No incident INC-9999" in command(client, "status INC-9999").json()["text"]
    assert "Usage" in command(client, "status INC-1;rm -rf /").json()["text"]
    assert "/blackbox status" in command(client, "help").json()["text"]
    assert "/blackbox status" in command(client, "").json()["text"]


def test_command_cannot_approve_or_execute(slack_env: FakeSlack, client: TestClient) -> None:
    connect()
    iid = open_incident()
    before = incidents.get_incident(iid)["state"]
    for verb in ("approve A1 approve", "reject", "deploy main", "run ls", "exec rm -rf /"):
        text_ = command(client, verb).json()["text"]
        assert "cannot approve" in text_ or "/blackbox status" in text_
    assert incidents.get_incident(iid)["state"] == before
    with get_sessionmaker()() as s:
        kinds = {r[0] for r in s.execute(text("SELECT kind FROM audit_events"))}
    assert not any(k.startswith(("approval.", "execution.")) for k in kinds)
    assert slack_env.calls == []  # commands never call out to Slack or anything else


def test_command_enforces_workspace_and_channel(slack_env: FakeSlack, client: TestClient) -> None:
    connect()
    open_incident()
    assert "not connected" in command(client, "status", team="T0EVIL").json()["text"]
    assert (
        "not enabled in this channel"
        in command(client, "status", channel="C0RANDOM").json()["text"]
    )


def test_command_on_disconnected_workspace(slack_env: FakeSlack, client: TestClient) -> None:
    connect()
    slack_store.deactivate_installation(TEAM, "test", "test")
    assert "not connected" in command(client, "status").json()["text"]


# ---------------- incidents API (dashboard link target) ----------------


def test_incident_view_has_sanitized_timeline(slack_env: FakeSlack, client: TestClient) -> None:
    iid = open_incident()
    incidents.transition(iid, "INVESTIGATING", "sentinel", "<!channel> started")
    r = client.get(f"/api/incidents/{iid}")
    assert r.status_code == 200
    data = r.json()
    assert data["incident"]["state"] == "INVESTIGATING"
    summaries = [t["summary"] for t in data["timeline"]]
    assert summaries == [
        "Incident detected from a verified monitoring alert",
        "Investigation started",
    ]
    assert client.get("/api/incidents/INC-0").status_code == 404
    assert client.get("/api/incidents/..%2fetc").status_code == 404
    assert client.get("/api/incidents").json()["incidents"][0]["incident_id"] == iid
