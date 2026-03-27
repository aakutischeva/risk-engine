"""Custom exception hierarchy for the Risk Engine."""

from fastapi import HTTPException, status


class RiskEngineError(Exception):
    """Base exception for all domain errors raised by this application."""


class TransactionNotFoundError(RiskEngineError):
    """Raised when a requested transaction does not exist in the database."""

    def __init__(self, transaction_id: str) -> None:
        super().__init__(f"Transaction not found: {transaction_id}")
        self.transaction_id = transaction_id


class DuplicateTransactionError(RiskEngineError):
    """Raised when a transaction with the same idempotency key already exists.

    The engine is idempotent: submitting the same transaction ID twice returns
    the original result instead of processing it again.
    """

    def __init__(self, transaction_id: str) -> None:
        super().__init__(f"Transaction already processed: {transaction_id}")
        self.transaction_id = transaction_id


class AlertNotFoundError(RiskEngineError):
    """Raised when a requested fraud alert does not exist."""

    def __init__(self, alert_id: str) -> None:
        super().__init__(f"Alert not found: {alert_id}")
        self.alert_id = alert_id


class InvalidTransactionError(RiskEngineError):
    """Raised when a transaction payload fails domain-level validation."""


# ── HTTP exception factories ──────────────────────────────────────────────────


def http_not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def http_conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def http_unprocessable(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)
