"""Domain enumerations shared across all application layers."""

from enum import Enum


class RiskLevel(str, Enum):
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


class TransactionType(str, Enum):
    """Payment method used for the transaction."""

    PIX = "PIX"
    TED = "TED"
    DOC = "DOC"
    BOLETO = "BOLETO"
    CARD = "CARD"


class AlertStatus(str, Enum):
    """Lifecycle status of a fraud alert."""

    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"
    FALSE_POSITIVE = "FALSE_POSITIVE"
