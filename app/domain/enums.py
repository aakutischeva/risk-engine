"""Domain enumerations shared across all application layers."""

from enum import StrEnum


class RiskLevel(StrEnum):
    """Qualitative risk classification assigned to a transaction.

    The thresholds mapping fraud scores to risk levels are:

    - LOW      → score < 30
    - MEDIUM   → 30 ≤ score < 60
    - HIGH     → 60 ≤ score < 80
    - CRITICAL → score ≥ 80
    """

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class TransactionType(StrEnum):
    """Payment method used for the transaction."""

    PIX = "PIX"
    TED = "TED"
    DOC = "DOC"
    BOLETO = "BOLETO"
    CARD = "CARD"


class AlertStatus(StrEnum):
    """Lifecycle status of a fraud alert."""

    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"
    FALSE_POSITIVE = "FALSE_POSITIVE"
