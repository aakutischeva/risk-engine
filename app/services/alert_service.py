"""Service layer for fraud alert lifecycle management."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import AlertStatus, RiskLevel
from app.domain.schemas.alert import AlertListResponse, AlertResponse, AlertUpdateRequest
from app.infra.database.models import AlertRecord
from app.infra.database.repositories.alert_repository import AlertRepository


def _record_to_response(record: AlertRecord) -> AlertResponse:
    return AlertResponse(
        alert_id=record.alert_id,
        transaction_id=record.transaction_id,
        risk_level=record.risk_level,
        fraud_score=record.fraud_score,
        rules_triggered=record.rules_triggered,
        status=record.status,
        created_at=record.created_at,
        resolved_at=record.resolved_at,
    )


class AlertService:
    """Orchestrates alert retrieval and status transitions.

    Args:
        session: Async database session (injected per request).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._repo = AlertRepository(session)

    async def get_by_id(self, alert_id: str) -> AlertResponse | None:
        """Return a single alert by ID, or None if it does not exist.

        Args:
            alert_id: UUID string of the alert.

        Returns:
            The alert response, or None.
        """
        record = await self._repo.get_by_id(alert_id)
        if record is None:
            return None
        return _record_to_response(record)

    async def update_status(
        self,
        alert_id: str,
        request: AlertUpdateRequest,
    ) -> AlertResponse | None:
        """Transition an alert to a new lifecycle status.

        Args:
            alert_id: UUID string of the alert to update.
            request: The desired status and optional analyst notes.

        Returns:
            The updated alert response, or None if the alert was not found.
        """
        record = await self._repo.get_by_id(alert_id)
        if record is None:
            return None
        updated = await self._repo.update_status(record, request.status, request.notes)
        return _record_to_response(updated)

    async def list_alerts(
        self,
        status: AlertStatus | None = None,
        risk_level: RiskLevel | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> AlertListResponse:
        """Return a paginated, optionally filtered list of fraud alerts.

        Args:
            status: Optional filter by alert lifecycle status.
            risk_level: Optional filter by risk level.
            limit: Page size (max 100).
            offset: Number of records to skip.

        Returns:
            Paginated list response with total count.
        """
        records, total = await self._repo.list_alerts(
            status=status,
            risk_level=risk_level,
            limit=min(limit, 100),
            offset=offset,
        )
        return AlertListResponse(
            total=total,
            items=[_record_to_response(r) for r in records],
        )
