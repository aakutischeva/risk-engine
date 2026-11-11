"""Validated, versioned event contract shared by producer and consumer."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

RiskLevelValue = Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"]


class ScoredTransaction(BaseModel):
    schema_version: Literal[1]
    transaction_id: str = Field(min_length=1, max_length=64)
    fraud_score: float = Field(ge=0, le=100)
    risk_level: RiskLevelValue
    rules_triggered: list[str]
    scored_at: datetime


class EventEnvelope(BaseModel):
    event_id: UUID
    event_type: Literal["transaction.scored"]
    aggregate_id: str = Field(min_length=1, max_length=64)
    payload: ScoredTransaction

    @model_validator(mode="after")
    def transaction_matches_aggregate(self) -> "EventEnvelope":
        if self.aggregate_id != self.payload.transaction_id:
            raise ValueError("transaction_id and aggregate_id must match")
        return self

    @property
    def requires_notification(self) -> bool:
        return self.payload.risk_level in ("HIGH", "CRITICAL")
