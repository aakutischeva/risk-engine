"""Repository for AlertRecord persistence operations."""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import AlertStatus, RiskLevel
from app.domain.schemas.transaction import RiskAssessment
from app.infra.database.models import AlertRecord


class AlertRepository:
    """Data-access layer for the alerts table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, alert_id: str) -> Optional[AlertRecord]:
        """Return an alert record by its primary key, or None.

        Args:
            alert_id: UUID string of the alert.

        Returns:
            The ORM record if it exists, otherwise None.
        """
        result = await self._session.execute(
            select(AlertRecord).where(AlertRecord.alert_id == alert_id)
        )
        return result.scalar_one_or_none()

    async def create_from_assessment(
        self,
        transaction_id: str,
        assessment: RiskAssessment,
    ) -> AlertRecord:
        """Create a fraud alert from a risk assessment.

        Args:
            transaction_id: ID of the transaction that triggered the alert.
            assessment: The risk assessment containing score and triggered rules.

        Returns:
            The newly created and flushed ORM record.
        """
        triggered_rules = [r.rule for r in assessment.rules_evaluated if r.triggered]
        record = AlertRecord(
            alert_id=str(uuid.uuid4()),
            transaction_id=transaction_id,
            risk_level=assessment.risk_level.value,
            fraud_score=assessment.fraud_score,
            rules_triggered=triggered_rules,
            status=AlertStatus.OPEN.value,
        )
        self._session.add(record)
        await self._session.flush()
        return record

    async def update_status(
        self,
        alert: AlertRecord,
        status: AlertStatus,
        notes: Optional[str] = None,
    ) -> AlertRecord:
        """Transition an alert to a new lifecycle status.

        Sets ``resolved_at`` automatically when the status is RESOLVED or
        FALSE_POSITIVE.

        Args:
            alert: The ORM record to update.
            status: The target status.
            notes: Optional analyst notes to attach.

        Returns:
            The updated ORM record.
        """
        alert.status = status.value
        if notes is not None:
            alert.notes = notes
        if status in (AlertStatus.RESOLVED, AlertStatus.FALSE_POSITIVE):
            alert.resolved_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await self._session.flush()
        return alert

    async def list_alerts(
        self,
        status: Optional[AlertStatus] = None,
        risk_level: Optional[RiskLevel] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AlertRecord], int]:
        """Return a paginated, optionally filtered list of alerts.

        Args:
            status: If provided, filter by alert lifecycle status.
            risk_level: If provided, filter by risk level.
            limit: Maximum number of records to return.
            offset: Number of records to skip.

        Returns:
            Tuple of (records, total_count).
        """
        query = select(AlertRecord)
        count_query = select(func.count()).select_from(AlertRecord)

        if status is not None:
            query = query.where(AlertRecord.status == status.value)
            count_query = count_query.where(AlertRecord.status == status.value)
        if risk_level is not None:
            query = query.where(AlertRecord.risk_level == risk_level.value)
            count_query = count_query.where(AlertRecord.risk_level == risk_level.value)

        query = query.order_by(AlertRecord.created_at.desc()).limit(limit).offset(offset)

        total_result = await self._session.execute(count_query)
        records_result = await self._session.execute(query)

        return list(records_result.scalars().all()), (total_result.scalar() or 0)
