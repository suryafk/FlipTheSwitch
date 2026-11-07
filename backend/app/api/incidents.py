"""Read-only incident views for the dashboard (the target of Slack "View in BLACKBOX" links).

Returns the incident record and a human-readable timeline built from the audit log with the
same sanitized mapping Slack uses, so no raw logs, payloads or reasoning are exposed.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter
from sqlalchemy import text

from app.core.errors import ApiError
from app.db.session import get_sessionmaker
from app.incidents import store
from app.slack.messages import clean, describe_row

router = APIRouter(prefix="/api/incidents", tags=["incidents"])
INCIDENT_ID = re.compile(r"^INC-\d{1,8}$")
FIELDS = (
    "incident_id",
    "service",
    "severity",
    "state",
    "title",
    "room_name",
    "created_at",
    "updated_at",
)


def public(inc: dict[str, Any]) -> dict[str, Any]:
    out = {k: inc.get(k) for k in FIELDS}
    out["title"] = clean(out["title"], 300)
    return out


@router.get("")
def list_incidents() -> dict[str, Any]:
    return {"incidents": [public(i) for i in store.list_incidents(50)]}


@router.get("/{incident_id}")
def get_incident(incident_id: str) -> dict[str, Any]:
    if not INCIDENT_ID.match(incident_id):
        raise ApiError(404, "not_found", "No such incident.")
    inc = store.get_incident(incident_id)
    if not inc:
        raise ApiError(404, "not_found", "No such incident.")
    with get_sessionmaker()() as s:
        rows = [
            dict(r)
            for r in s.execute(
                text(
                    "SELECT seq, kind, actor, payload_json, created_at FROM audit_events "
                    "WHERE seq >= (SELECT COALESCE(MIN(seq), 0) FROM audit_events "
                    "WHERE kind='incident.created' AND payload_json LIKE :p) ORDER BY seq LIMIT 2000"
                ),
                {"p": f'%"incident_id":"{incident_id}"%'},
            )
            .mappings()
            .all()
        ]
    timeline = []
    for row in rows:
        a = describe_row(row)
        if a and a.incident_id == incident_id:
            timeline.append(
                {
                    "seq": a.event_seq,
                    "at": a.at,
                    "agent": a.agent,
                    "summary": a.summary,
                    "level": a.level,
                    "kind": a.kind,
                }
            )
    return {"incident": public(inc), "timeline": timeline}
