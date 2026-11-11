"""Initial schema matching the current SQLAlchemy ORM models.

Revision ID: 001
Revises:

This corrected baseline is intended for fresh databases.  The existing local
database was created from ORM metadata before being stamped at revision 001;
changing this script does not change that database's tables.
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
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("payer_key", sa.String(length=256), nullable=False),
        sa.Column("receiver_key", sa.String(length=256), nullable=False),
        sa.Column("transaction_type", sa.String(length=16), nullable=False),
        sa.Column("fraud_score", sa.Float(), nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("risk_details", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("transaction_id"),
    )
    op.create_index(
        "ix_transactions_payer_created", "transactions", ["payer_key", "created_at"]
    )
    op.create_index("ix_transactions_payer_key", "transactions", ["payer_key"])
    op.create_index(
        "ix_transactions_risk_created", "transactions", ["risk_level", "created_at"]
    )
    op.create_index("ix_transactions_risk_level", "transactions", ["risk_level"])
    op.create_index("ix_transactions_transaction_id", "transactions", ["transaction_id"])

    op.create_table(
        "alerts",
        sa.Column("alert_id", sa.String(length=64), nullable=False),
        sa.Column("transaction_id", sa.String(length=64), nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=False),
        sa.Column("fraud_score", sa.Float(), nullable=False),
        sa.Column("rules_triggered", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("alert_id"),
    )
    op.create_index("ix_alerts_alert_id", "alerts", ["alert_id"])
    op.create_index("ix_alerts_risk_level", "alerts", ["risk_level"])
    op.create_index("ix_alerts_status", "alerts", ["status"])
    op.create_index("ix_alerts_transaction_id", "alerts", ["transaction_id"])


def downgrade() -> None:
    op.drop_index("ix_alerts_transaction_id", table_name="alerts")
    op.drop_index("ix_alerts_status", table_name="alerts")
    op.drop_index("ix_alerts_risk_level", table_name="alerts")
    op.drop_index("ix_alerts_alert_id", table_name="alerts")
    op.drop_table("alerts")

    op.drop_index("ix_transactions_transaction_id", table_name="transactions")
    op.drop_index("ix_transactions_risk_level", table_name="transactions")
    op.drop_index("ix_transactions_risk_created", table_name="transactions")
    op.drop_index("ix_transactions_payer_key", table_name="transactions")
    op.drop_index("ix_transactions_payer_created", table_name="transactions")
    op.drop_table("transactions")
