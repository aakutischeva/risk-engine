"""Service layer: orchestrates transaction scoring and persistence.

This layer is the only place allowed to call the fraud engine, the
repositories and the alert service together.  Routes delegate to this
service; repositories and the engine have no knowledge of each other.
"""

from typing import Optional

import redis.asyncio as aioredis
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import RiskLevel
from app.domain.schemas.transaction import (
    TransactionListResponse,
    TransactionResponse,
    TransactionSubmit,
)
from app.fraud.engine import FraudEngine
from app.infra.database.repositories.alert_repository import AlertRepository
from app.infra.database.repositories.transaction_repository import TransactionRepository
from app.infra.database.models import TransactionRecord

_engine = FraudEngine()


def _record_to_response(record: TransactionRecord, idempotent: bool = False) -> TransactionResponse:
    """Convert a database record into an API response schema.

    Args:
        record: ORM record loaded from the database.
        idempotent: Set to True when returning a cached result.

    Returns:
        Fully populated :class:`TransactionResponse`.
    """
    from app.domain.schemas.transaction import RiskAssessment

    assessment = RiskAssessment(**record.risk_details)
    return TransactionResponse(
        transaction_id=record.transaction_id,
        amount=record.amount,
        payer_key=record.payer_key,
        receiver_key=record.receiver_key,
        transaction_type=record.transaction_type,
        risk_assessment=assessment,
        created_at=record.created_at,
        idempotent=idempotent,
    )


class TransactionService:
    """Orchestrates transaction scoring, persistence and alert generation.

    Args:
        session: Async database session (injected per request).
        redis: Optional async Redis client for velocity checks.
    """

    def __init__(self, session: AsyncSession, redis: Optional[aioredis.Redis] = None) -> None:
        self._session = session
        self._redis = redis
        self._tx_repo = TransactionRepository(session)
        self._alert_repo = AlertRepository(session)

    async def submit(self, payload: TransactionSubmit) -> TransactionResponse:
        """Score a transaction and persist the result.

        Idempotent: if ``payload.transaction_id`` was already processed, the
        original result is returned immediately without re-scoring.

        Args:
            payload: Validated transaction payload from the API layer.

        Returns:
            The transaction resource with its full risk assessment.
        """
        existing = await self._tx_repo.get_by_id(payload.transaction_id)
        if existing is not None:
            logger.info(
                "Idempotent response for transaction_id={}",
                payload.transaction_id,
            )
            return _record_to_response(existing, idempotent=True)

        assessment = await _engine.score(payload, redis=self._redis)
        record = await self._tx_repo.create(payload, assessment)

        if assessment.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            await self._alert_repo.create_from_assessment(
                transaction_id=payload.transaction_id,
                assessment=assessment,
            )
            logger.warning(
                "Alert created | transaction={} level={} score={:.1f}",
                payload.transaction_id,
                assessment.risk_level.value,
                assessment.fraud_score,
            )

        return _record_to_response(record)

    async def get_by_id(self, transaction_id: str) -> Optional[TransactionResponse]:
        """Return a single transaction by ID, or None if it does not exist.

        Args:
            transaction_id: The client-supplied unique identifier.

        Returns:
            The transaction response, or None.
        """
        record = await self._tx_repo.get_by_id(transaction_id)
        if record is None:
            return None
        return _record_to_response(record)

    async def list_transactions(
        self,
        risk_level: Optional[RiskLevel] = None,
        payer_key: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> TransactionListResponse:
        """Return a paginated list of processed transactions.

        Args:
            risk_level: Optional filter by risk level.
            payer_key: Optional filter by payer key.
            limit: Page size (max 100).
            offset: Number of records to skip.

        Returns:
            Paginated list response with total count.
        """
        records, total = await self._tx_repo.list_transactions(
            risk_level=risk_level,
            payer_key=payer_key,
            limit=min(limit, 100),
            offset=offset,
        )
        return TransactionListResponse(
            total=total,
            items=[_record_to_response(r) for r in records],
        )
