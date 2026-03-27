"""SQLAlchemy ORM models for the Risk Engine persistence layer."""

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    Index,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class TransactionRecord(Base):
    """Persisted transaction with its full risk assessment snapshot.

    The ``risk_details`` column stores the serialised :class:`RiskAssessment`
    so that historical scoring results are preserved even if rule logic changes
    in future deployments.
    """

    __tablename__ = "transactions"

    transaction_id = Column(String(64), primary_key=True, index=True)
    amount = Column(Float, nullable=False)
    payer_key = Column(String(256), nullable=False, index=True)
    receiver_key = Column(String(256), nullable=False)
    transaction_type = Column(String(16), nullable=False)
    fraud_score = Column(Float, nullable=False)
    risk_level = Column(String(16), nullable=False, index=True)
    risk_details = Column(JSON, nullable=False)
    metadata_ = Column("metadata", JSON, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)

    __table_args__ = (
        Index("ix_transactions_payer_created", "payer_key", "created_at"),
        Index("ix_transactions_risk_created", "risk_level", "created_at"),
    )


class AlertRecord(Base):
    """Fraud alert generated for HIGH and CRITICAL transactions.

    Each alert tracks the rules that fired and its lifecycle status
    (OPEN → ACKNOWLEDGED → RESOLVED or FALSE_POSITIVE).
    """

    __tablename__ = "alerts"

    alert_id = Column(String(64), primary_key=True, index=True)
    transaction_id = Column(String(64), nullable=False, index=True)
    risk_level = Column(String(16), nullable=False, index=True)
    fraud_score = Column(Float, nullable=False)
    rules_triggered = Column(JSON, nullable=False)
    status = Column(String(32), nullable=False, default="OPEN", index=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=_utcnow)
    resolved_at = Column(DateTime, nullable=True)
