"""Pydantic schemas for fraud alert request/response contracts."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.domain.enums import AlertStatus, RiskLevel


class AlertResponse(BaseModel):
    """Fraud alert resource returned by the API."""

    alert_id: str
    transaction_id: str
    risk_level: RiskLevel
    fraud_score: float
    rules_triggered: list[str]
    status: AlertStatus
    created_at: datetime
    resolved_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class AlertUpdateRequest(BaseModel):
    """Payload for updating the lifecycle status of a fraud alert."""

    status: AlertStatus = Field(..., description="New status to transition the alert into.")
    notes: Optional[str] = Field(
        default=None,
        max_length=1024,
        description="Optional analyst notes recorded with the status transition.",
    )


class AlertListResponse(BaseModel):
    """Paginated list of alert resources."""

    total: int
    items: list[AlertResponse]
