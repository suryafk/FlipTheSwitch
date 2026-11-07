"""Switch event ledger: every Switch event an agent processed, with its ids and timestamps.

Revision ID: 0004_switch_events
Revises: 0003_slack
Create Date: 2026-11-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_switch_events"
down_revision: str | None = "0003_slack"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "switch_events",
        # One row per (agent, Switch message id): the durable duplicate-processing guard.
        sa.Column("agent", sa.String(40), primary_key=True),
        sa.Column("event_id", sa.String(80), primary_key=True),  # Switch message_id / event_id
        sa.Column("room_id", sa.String(80), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=True),  # stream sequence (frame id)
        sa.Column("sender", sa.String(200), nullable=True),
        sa.Column("sender_name", sa.String(120), nullable=True),
        sa.Column("addressed", sa.Boolean(), nullable=True),
        sa.Column("switch_timestamp_ms", sa.BigInteger(), nullable=True),
        sa.Column("via", sa.String(20), nullable=False),  # stream | read_context
        sa.Column("received_at", sa.DateTime(timezone=True),
                  server_default=sa.func.current_timestamp(), nullable=False),
    )
    op.create_index("ix_switch_events_room", "switch_events", ["room_id"])


def downgrade() -> None:
    op.drop_index("ix_switch_events_room", table_name="switch_events")
    op.drop_table("switch_events")
