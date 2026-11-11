"""Track persistent signed-webhook delivery attempts.

Revision ID: alert002
Revises: alert001
"""

import sqlalchemy as sa
from alembic import op


revision = "alert002"
down_revision = "alert001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "alert_notifications",
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "alert_notifications", sa.Column("last_attempt_at", sa.DateTime(), nullable=True)
    )
    op.add_column(
        "alert_notifications", sa.Column("next_attempt_at", sa.DateTime(), nullable=True)
    )
    op.add_column("alert_notifications", sa.Column("sent_at", sa.DateTime(), nullable=True))
    op.add_column("alert_notifications", sa.Column("last_error", sa.Text(), nullable=True))
    op.create_index(
        "ix_alert_notifications_dispatch",
        "alert_notifications",
        ["status", "next_attempt_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_alert_notifications_dispatch", table_name="alert_notifications")
    op.drop_column("alert_notifications", "last_error")
    op.drop_column("alert_notifications", "sent_at")
    op.drop_column("alert_notifications", "next_attempt_at")
    op.drop_column("alert_notifications", "last_attempt_at")
    op.drop_column("alert_notifications", "attempts")
