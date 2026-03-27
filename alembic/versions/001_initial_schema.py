"""Initial schema: transactions and alerts tables.

Revision ID: 001
Revises:
Create Date: 2025-01-01 00:00:00.000000
"""

import sqlalchemy as sa

from alembic import op

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "transactions",
        sa.Column("transaction_id", sa.String(length=64), nullable=False),
        sa.Column("amount", sa.Numeric(precision=15, scale=2), nullable=False),
        sa.Column("payer_key", sa.String(length=255), nullable=False),
        sa.Column("receiver_key", sa.String(length=255), nullable=False),
        sa.Column("transaction_type", sa.String(length=20), nullable=False),
        sa.Column("fraud_score", sa.Float(), nullable=False),
        sa.Column("risk_level", sa.String(length=20), nullable=False),
        sa.Column("rules_evaluated", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("transaction_id"),
    )
    op.create_index("ix_transactions_risk_level", "transactions", ["risk_level"])
    op.create_index("ix_transactions_created_at", "transactions", ["created_at"])
    op.create_index("ix_transactions_payer_key", "transactions", ["payer_key"])

    op.create_table(
        "alerts",
        sa.Column("alert_id", sa.String(length=64), nullable=False),
        sa.Column("transaction_id", sa.String(length=64), nullable=False),
        sa.Column("risk_level", sa.String(length=20), nullable=False),
        sa.Column("fraud_score", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["transaction_id"], ["transactions.transaction_id"]),
        sa.PrimaryKeyConstraint("alert_id"),
    )
    op.create_index("ix_alerts_status", "alerts", ["status"])
    op.create_index("ix_alerts_transaction_id", "alerts", ["transaction_id"])


def downgrade() -> None:
    op.drop_table("alerts")
    op.drop_table("transactions")
