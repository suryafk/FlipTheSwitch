"""Monitoring webhook → BLACKBOX Control posts the verified alert into the Switch intake room.

Untrusted input (docs/04 §6): HMAC-SHA256 over "<timestamp>.<body>", a 300 s timestamp window,
a replay cache, a strict schema, a size limit and a service catalog. The alert text is never
interpreted: Control re-serializes it as canonical JSON inside a fenced block, and Sentinel only
accepts alerts posted by Control's Switch identity.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agents.common import load_credentials, load_topology
from app.core.config import get_settings
from app.core.errors import ApiError
from app.incidents import store
from app.switch.client import SwitchAgentClient
from app.switch.config import get_switch_settings

logger = logging.getLogger("app.webhooks")
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

SERVICE_CATALOG = {"payment-service"}
MAX_BODY = 16 * 1024
_seen: dict[str, float] = {}


class Signals(BaseModel):
    model_config = ConfigDict(extra="forbid")
    error_rate_pct: float | None = Field(default=None, ge=0, le=100)
    latency_ms_p50: float | None = Field(default=None, ge=0, le=600_000)
    pool_utilization_pct: float | None = Field(default=None, ge=0, le=100)
    requests_in_window: int | None = Field(default=None, ge=0)


class Alert(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alert_schema: Literal["blackbox.alert/v1"] = Field(alias="schema")
    status: Literal["firing", "resolved"]
    alert_id: str = Field(pattern=r"^ALT-[0-9]{1,9}$")
    source: Literal["synthetic-monitor"]
    simulated: bool
    service: str = Field(pattern=r"^[a-z0-9-]{1,64}$")
    severity: Literal["critical", "high", "medium", "low"]
    rules: list[str] = Field(max_length=10)
    signals: Signals
    deploy_id: str | None = Field(default=None, pattern=r"^[0-9a-z-]{1,20}$")
    git_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{7,40}$|^unknown$")
    observed_at: str = Field(max_length=40)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


def _verify_signature(body: bytes, ts: str | None, sig: str | None) -> None:
    secret = get_settings().webhook_secret
    if not secret:
        raise ApiError(503, "webhook_not_configured", "BLACKBOX_WEBHOOK_SECRET is not set.")
    if not ts or not sig or not ts.isdigit():
        raise ApiError(401, "unauthorized", "Missing signature.")
    if abs(time.time() - int(ts)) > 300:
        raise ApiError(401, "unauthorized", "Stale timestamp.")
    expected = (
        "sha256=" + hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    )
    if not hmac.compare_digest(expected, sig):
        raise ApiError(401, "unauthorized", "Bad signature.")
    digest = hashlib.sha256(body).hexdigest()
    now = time.time()
    for k in [k for k, t in _seen.items() if now - t > 600]:
        _seen.pop(k, None)
    if digest in _seen:
        raise ApiError(409, "replay", "Duplicate webhook body.")
    _seen[digest] = now


@router.post("/alert", status_code=202)
async def receive_alert(request: Request) -> dict:
    body = await request.body()
    if len(body) > MAX_BODY:
        raise ApiError(413, "too_large", "Body too large.")
    _verify_signature(
        body,
        request.headers.get("X-Blackbox-Timestamp"),
        request.headers.get("X-Blackbox-Signature"),
    )
    try:
        alert = Alert.model_validate_json(body)
    except ValidationError as exc:
        raise ApiError(
            400,
            "invalid_alert",
            "Alert does not match blackbox.alert/v1.",
            [e["loc"] for e in exc.errors()],
        ) from exc
    if alert.service not in SERVICE_CATALOG:
        raise ApiError(400, "unknown_service", f"Service {alert.service!r} is not in the catalog.")

    data = json.loads(alert.model_dump_json(by_alias=True))
    store.audit("alert.received", "blackbox-control", data)
    topo = load_topology()
    client = SwitchAgentClient(get_switch_settings().api_url, load_credentials("control"))
    s = alert.signals
    if alert.status == "firing":
        line = (
            f"ALERT {alert.alert_id} [{alert.severity.upper()}] {alert.service}: "
            f"5xx {s.error_rate_pct}% · p50 {s.latency_ms_p50} ms · "
            f"DB pool {s.pool_utilization_pct}% (rules: {', '.join(alert.rules)})"
        )
    else:
        line = (
            f"RESOLVED {alert.alert_id} {alert.service}: 5xx {s.error_rate_pct}% · "
            f"p50 {s.latency_ms_p50} ms · DB pool {s.pool_utilization_pct}%"
        )
    text = f"{line}\n\n```json\n{json.dumps(data, indent=1, sort_keys=True)}\n```"
    try:
        sent = await client.post_message(topo.intake_room_id, text.replace("````", "'''"))
    finally:
        await client.aclose()
    logger.info(
        "alert forwarded to Switch",
        extra={"alert_id": alert.alert_id, "event_id": sent.get("event_id")},
    )
    return {"accepted": True, "switch_event_id": sent.get("event_id")}
