"""Slack integration: installations, settings, incident threads, deliveries, OAuth states.

Revision ID: 0003_slack
Revises: 0002_incident_store
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_slack"
down_revision: str | None = "0002_incident_store"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _now() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True),
                     server_default=sa.func.current_timestamp(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "slack_installations",
        sa.Column("team_id", sa.String(32), primary_key=True),
        sa.Column("team_name", sa.String(200), nullable=False),
        sa.Column("bot_user_id", sa.String(32), nullable=True),
        sa.Column("scopes", sa.Text(), nullable=False),
        # Fernet ciphertext of the bot token; the plaintext is never stored.
        sa.Column("token_encrypted", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("deactivated_reason", sa.Text(), nullable=True),
        _now(),
    )
    op.create_table(
        "slack_settings",
        sa.Column("key", sa.String(60), primary_key=True),
        sa.Column("value", sa.Text(), nullable=True),
    )
    op.create_table(
        "slack_threads",
        sa.Column("incident_id", sa.String(40), primary_key=True),
        sa.Column("team_id", sa.String(32), nullable=False),
        sa.Column("channel_id", sa.String(32), nullable=False),
        sa.Column("thread_ts", sa.String(32), nullable=False),
        _now(),
    )
    op.create_table(
        "slack_deliveries",
        # One row per audit event: the duplicate-publishing guard.
        sa.Column("event_seq", sa.Integer(), primary_key=True),
        sa.Column("incident_id", sa.String(40), nullable=True),
        sa.Column("kind", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("slack_ts", sa.String(32), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.current_timestamp(), nullable=False),
    )
    op.create_table(
        "slack_oauth_states",
        sa.Column("state", sa.String(64), primary_key=True),
        sa.Column("used", sa.Boolean(), nullable=False, server_default=sa.false()),
        _now(),
    )


def downgrade() -> None:
    for table in ("slack_oauth_states", "slack_deliveries", "slack_threads", "slack_settings",
                  "slack_installations"):
        op.drop_table(table)
