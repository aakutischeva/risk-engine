"""Service layer: orchestrates transaction scoring and persistence.

This layer is the only place allowed to call the fraud engine, the
repositories and the alert service together.  Routes delegate to this
service; repositories and the engine have no knowledge of each other.
"""

import uuid

import redis.asyncio as aioredis
from loguru import logger
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.domain.enums import RiskLevel
from app.domain.schemas.transaction import (
    TransactionListResponse,
    TransactionResponse,
    TransactionSubmit,
)
from app.fraud.engine import FraudEngine
from app.infra.cache.transaction_cache import (
    delete_cached_json,
    get_cached_json,
    set_cached_json,
)
from app.infra.database.models import OutboxEvent, TransactionRecord
from app.infra.database.repositories.alert_repository import AlertRepository
from app.infra.database.repositories.transaction_repository import TransactionRepository

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

    def __init__(self, session: AsyncSession, redis: aioredis.Redis | None = None) -> None:
        self._session = session
        self._redis = redis
        self._tx_repo = TransactionRepository(session)
        self._alert_repo = AlertRepository(session)
        self.cache_hit = False

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

        # Saved in the same PostgreSQL transaction as the scored transaction.
        # The separate publisher will send it only after commit.
        self._session.add(
            OutboxEvent(
                event_id=str(uuid.uuid4()),
                event_type="transaction.scored",
                aggregate_id=payload.transaction_id,
                payload={
                    "schema_version": 1,
                    "transaction_id": payload.transaction_id,
                    "fraud_score": assessment.fraud_score,
                    "risk_level": assessment.risk_level.value,
                    "rules_triggered": [
                        rule.rule for rule in assessment.rules_evaluated if rule.triggered
                    ],
                    "scored_at": assessment.scored_at.isoformat(),
                },
            )
        )

        # The dependency invalidates this key only after commit. The outbox
        # event is part of the same PostgreSQL transaction as the record.
        self._session.info.setdefault("transaction_cache_invalidate", set()).add(
            payload.transaction_id
        )

        return _record_to_response(record)

    async def get_by_id(self, transaction_id: str) -> TransactionResponse | None:
        """Return a single transaction by ID, or None if it does not exist.

        Args:
            transaction_id: The client-supplied unique identifier.

        Returns:
            The transaction response, or None.
        """
        self.cache_hit = False
        if self._redis is not None:
            cached = await get_cached_json(self._redis, transaction_id)
            if cached is not None:
                try:
                    result = TransactionResponse.model_validate_json(cached)
                except (ValidationError, ValueError):
                    logger.warning("Invalid cached transaction | id={}", transaction_id)
                    await delete_cached_json(self._redis, transaction_id)
                else:
                    self.cache_hit = True
                    return result

        record = await self._tx_repo.get_by_id(transaction_id)
        if record is None:
            return None
        result = _record_to_response(record)
        if self._redis is not None:
            await set_cached_json(
                self._redis,
                transaction_id,
                result.model_dump_json(),
                settings.TRANSACTION_CACHE_TTL_SECONDS,
            )
        return result

    async def list_transactions(
        self,
        risk_level: RiskLevel | None = None,
        payer_key: str | None = None,
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
