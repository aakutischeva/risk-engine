"""Alert-service-owned persistence; not shared with the Risk Engine ORM."""

from datetime import UTC, datetime

from sqlalchemy import JSON, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import DeclarativeBase


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class ProcessedEvent(Base):
    """Deduplication ledger: every successfully handled event ID is unique."""

    __tablename__ = "processed_events"

    event_id = Column(String(64), primary_key=True)
    event_type = Column(String(100), nullable=False)
    transaction_id = Column(String(64), nullable=False, index=True)
    risk_level = Column(String(16), nullable=False)
    processed_at = Column(DateTime, nullable=False, default=utc_now)


class AlertNotification(Base):
    """Notification work item for a HIGH or CRITICAL scored transaction.

    This is separate from the original Risk Engine's analyst-facing alerts.
    A separate dispatcher retries signed webhook delivery until successful.
    """

    __tablename__ = "alert_notifications"

    event_id = Column(String(64), ForeignKey("processed_events.event_id"), primary_key=True)
    transaction_id = Column(String(64), nullable=False, index=True)
    risk_level = Column(String(16), nullable=False)
    fraud_score = Column(Float, nullable=False)
    rules_triggered = Column(JSON, nullable=False)
    status = Column(String(16), nullable=False, default="PENDING")
    created_at = Column(DateTime, nullable=False, default=utc_now)
    attempts = Column(Integer, nullable=False, default=0, server_default=text("0"))
    last_attempt_at = Column(DateTime, nullable=True)
    next_attempt_at = Column(DateTime, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_alert_notifications_dispatch", "status", "next_attempt_at", "created_at"),
    )
