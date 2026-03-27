"""Repository for TransactionRecord persistence operations."""

from datetime import datetime
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import RiskLevel
from app.domain.schemas.transaction import RiskAssessment, TransactionSubmit
from app.infra.database.models import TransactionRecord


class TransactionRepository:
    """Data-access layer for the transactions table.

    All methods accept an injected :class:`AsyncSession` rather than
    creating their own, so the service layer controls transaction boundaries.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, transaction_id: str) -> Optional[TransactionRecord]:
        """Return a transaction record by its primary key, or None.

        Args:
            transaction_id: The client-supplied unique identifier.

        Returns:
            The ORM record if it exists, otherwise None.
        """
        result = await self._session.execute(
            select(TransactionRecord).where(
                TransactionRecord.transaction_id == transaction_id
            )
        )
        return result.scalar_one_or_none()

    async def exists(self, transaction_id: str) -> bool:
        """Check whether a transaction has already been processed.

        Used for idempotency enforcement before scoring.

        Args:
            transaction_id: The client-supplied unique identifier.

        Returns:
            True if the transaction already exists, False otherwise.
        """
        result = await self._session.execute(
            select(func.count()).where(
                TransactionRecord.transaction_id == transaction_id
            )
        )
        return (result.scalar() or 0) > 0

    async def create(
        self,
        transaction: TransactionSubmit,
        assessment: RiskAssessment,
    ) -> TransactionRecord:
        """Persist a new transaction with its risk assessment.

        Args:
            transaction: The submitted transaction payload.
            assessment: The risk assessment computed by the fraud engine.

        Returns:
            The newly created and flushed ORM record.
        """
        record = TransactionRecord(
            transaction_id=transaction.transaction_id,
            amount=transaction.amount,
            payer_key=transaction.payer_key,
            receiver_key=transaction.receiver_key,
            transaction_type=transaction.transaction_type.value,
            fraud_score=assessment.fraud_score,
            risk_level=assessment.risk_level.value,
            risk_details=assessment.model_dump(mode="json"),
            metadata_=transaction.metadata,
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def list_transactions(
        self,
        risk_level: Optional[RiskLevel] = None,
        payer_key: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[TransactionRecord], int]:
        """Return a paginated, optionally filtered list of transactions.

        Args:
            risk_level: If provided, filter by this risk level.
            payer_key: If provided, filter by this payer key.
            limit: Maximum number of records to return.
            offset: Number of records to skip (for pagination).

        Returns:
            Tuple of (records, total_count).
        """
        query = select(TransactionRecord)
        count_query = select(func.count()).select_from(TransactionRecord)

        if risk_level is not None:
            query = query.where(TransactionRecord.risk_level == risk_level.value)
            count_query = count_query.where(
                TransactionRecord.risk_level == risk_level.value
            )
        if payer_key is not None:
            query = query.where(TransactionRecord.payer_key == payer_key)
            count_query = count_query.where(TransactionRecord.payer_key == payer_key)

        query = query.order_by(TransactionRecord.created_at.desc()).limit(limit).offset(offset)

        total_result = await self._session.execute(count_query)
        records_result = await self._session.execute(query)

        return list(records_result.scalars().all()), (total_result.scalar() or 0)
