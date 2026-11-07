"""Incident store: incidents, handoffs, remediations, approvals, executions.

Revision ID: 0002_incident_store
Revises: 0001_audit_events
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_incident_store"
down_revision: str | None = "0001_audit_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "incidents",
        sa.Column("incident_id", sa.String(40), primary_key=True),
        sa.Column("service", sa.String(80), nullable=False),
        sa.Column("severity", sa.String(10), nullable=True),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(80), nullable=False),
        sa.Column("room_id", sa.String(80), nullable=True),
        sa.Column("room_name", sa.String(120), nullable=True),
        sa.Column("alert_json", sa.Text(), nullable=False),
        _ts(),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
    )
    op.create_index("ix_incidents_fingerprint", "incidents", ["fingerprint"])
    op.create_table(
        "handoffs",
        sa.Column("handoff_id", sa.String(60), primary_key=True),
        sa.Column("incident_id", sa.String(40), sa.ForeignKey("incidents.incident_id"), nullable=False),
        sa.Column("from_agent", sa.String(20), nullable=False),
        sa.Column("to_agent", sa.String(20), nullable=False),
        sa.Column("handoff_type", sa.String(40), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("switch_event_id", sa.String(80), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        _ts(),
    )
    op.create_table(
        "remediations",
        sa.Column("remediation_id", sa.String(60), primary_key=True),
        sa.Column("incident_id", sa.String(40), sa.ForeignKey("incidents.incident_id"), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("base_commit", sa.String(40), nullable=False),
        sa.Column("commit_sha", sa.String(40), nullable=True),
        sa.Column("patch_hash", sa.String(80), nullable=True),
        sa.Column("worktree", sa.Text(), nullable=False),
        sa.Column("coding_mode", sa.String(40), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        _ts(),
        sa.UniqueConstraint("incident_id", "patch_hash", name="uq_remediation_patch"),
    )
    op.create_table(
        "approvals",
        sa.Column("approval_id", sa.String(60), primary_key=True),
        sa.Column("incident_id", sa.String(40), sa.ForeignKey("incidents.incident_id"), nullable=False),
        sa.Column("remediation_id", sa.String(60), sa.ForeignKey("remediations.remediation_id"), nullable=False),
        sa.Column("request_id", sa.String(200), nullable=False, unique=True),
        sa.Column("binding_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        _ts(),
    )
    op.create_table(
        "executions",
        sa.Column("execution_id", sa.String(60), primary_key=True),
        # One execution per approval, ever (replay / double-execution protection).
        sa.Column("approval_id", sa.String(60), sa.ForeignKey("approvals.approval_id"), nullable=False, unique=True),
        sa.Column("incident_id", sa.String(40), sa.ForeignKey("incidents.incident_id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False, server_default="{}"),
        _ts(),
    )


def downgrade() -> None:
    for table in ("executions", "approvals", "remediations", "handoffs"):
        op.drop_table(table)
    op.drop_index("ix_incidents_fingerprint", table_name="incidents")
    op.drop_table("incidents")
