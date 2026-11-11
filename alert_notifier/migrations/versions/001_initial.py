"""Initialize separate alert notification storage.

Revision ID: alert001
Revises:
"""

import sqlalchemy as sa
from alembic import op


revision = "alert001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "processed_events",
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("transaction_id", sa.String(length=64), nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(
        "ix_processed_events_transaction_id", "processed_events", ["transaction_id"]
    )

    op.create_table(
        "alert_notifications",
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("transaction_id", sa.String(length=64), nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("fraud_score", sa.Float(), nullable=False),
        sa.Column("rules_triggered", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["event_id"], ["processed_events.event_id"]),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index(
        "ix_alert_notifications_transaction_id", "alert_notifications", ["transaction_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_alert_notifications_transaction_id", table_name="alert_notifications")
    op.drop_table("alert_notifications")
    op.drop_index("ix_processed_events_transaction_id", table_name="processed_events")
    op.drop_table("processed_events")
