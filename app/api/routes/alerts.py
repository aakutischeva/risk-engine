"""API routes for fraud alert management."""

from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import http_not_found
from app.domain.enums import AlertStatus, RiskLevel
from app.domain.schemas.alert import (
    AlertListResponse,
    AlertResponse,
    AlertUpdateRequest,
)
from app.infra.database.connection import get_session
from app.services.alert_service import AlertService

router = APIRouter(prefix="/alerts", tags=["Alerts"])


@router.get(
    "",
    response_model=AlertListResponse,
    summary="List fraud alerts with optional filters",
)
async def list_alerts(
    status_filter: Optional[AlertStatus] = Query(None, alias="status"),
    risk_level: Optional[RiskLevel] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> AlertListResponse:
    """Return a paginated list of fraud alerts.

    Args:
        status_filter: Optional lifecycle status filter.
        risk_level: Optional risk level filter.
        limit: Page size.
        offset: Pagination offset.
        session: Injected async database session.

    Returns:
        Paginated list with total count.
    """
    service = AlertService(session=session)
    return await service.list_alerts(
        status=status_filter,
        risk_level=risk_level,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{alert_id}",
    response_model=AlertResponse,
    summary="Retrieve a fraud alert by ID",
)
async def get_alert(
    alert_id: str,
    session: AsyncSession = Depends(get_session),
) -> AlertResponse:
    """Return a single fraud alert.

    Args:
        alert_id: UUID string of the alert.
        session: Injected async database session.

    Raises:
        404: If the alert does not exist.

    Returns:
        The alert resource.
    """
    service = AlertService(session=session)
    result = await service.get_by_id(alert_id)
    if result is None:
        raise http_not_found(f"Alert '{alert_id}' not found.")
    return result


@router.patch(
    "/{alert_id}",
    response_model=AlertResponse,
    summary="Update the status of a fraud alert",
    description=(
        "Transition an alert through its lifecycle: "
        "OPEN → ACKNOWLEDGED → RESOLVED or FALSE_POSITIVE."
    ),
)
async def update_alert_status(
    alert_id: str,
    request: AlertUpdateRequest,
    session: AsyncSession = Depends(get_session),
) -> AlertResponse:
    """Update the lifecycle status of a fraud alert.

    Args:
        alert_id: UUID string of the alert to update.
        request: The desired status and optional analyst notes.
        session: Injected async database session.

    Raises:
        404: If the alert does not exist.

    Returns:
        The updated alert resource.
    """
    service = AlertService(session=session)
    result = await service.update_status(alert_id, request)
    if result is None:
        raise http_not_found(f"Alert '{alert_id}' not found.")
    return result
