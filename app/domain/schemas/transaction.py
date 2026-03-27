"""Pydantic schemas for transaction request/response contracts."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.domain.enums import RiskLevel, TransactionType


class TransactionSubmit(BaseModel):
    """Payload for submitting a new transaction for risk scoring.

    The ``transaction_id`` field is used as an idempotency key: submitting
    the same ID a second time returns the original scoring result without
    reprocessing the transaction.
    """

    transaction_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Client-supplied unique identifier (idempotency key).",
        examples=["txn_2024_001234"],
    )
    amount: float = Field(
        ...,
        gt=0,
        description="Transaction amount in BRL.",
        examples=[1500.00],
    )
    payer_key: str = Field(
        ...,
        min_length=1,
        max_length=256,
        description="Pix key, account number or any unique payer identifier.",
        examples=["payer@email.com"],
    )
    receiver_key: str = Field(
        ...,
        min_length=1,
        max_length=256,
        description="Pix key, account number or any unique receiver identifier.",
        examples=["receiver@email.com"],
    )
    transaction_type: TransactionType = Field(
        default=TransactionType.PIX,
        description="Payment method used for this transaction.",
    )
    metadata: Optional[dict] = Field(
        default=None,
        description="Optional free-form metadata stored alongside the transaction.",
    )

    @field_validator("amount")
    @classmethod
    def amount_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Amount must be greater than zero.")
        return round(v, 2)


class RuleResult(BaseModel):
    """Score contributed by a single fraud detection rule."""

    rule: str = Field(..., description="Rule class name.")
    score: float = Field(..., ge=0, le=100, description="Score from 0 (safe) to 100 (fraud).")
    triggered: bool = Field(..., description="Whether this rule fired above its threshold.")
    reason: str = Field(..., description="Human-readable explanation of the score.")


class RiskAssessment(BaseModel):
    """Full fraud assessment result attached to a scored transaction."""

    fraud_score: float = Field(..., ge=0, le=100, description="Weighted composite score (0–100).")
    risk_level: RiskLevel
    rules_evaluated: list[RuleResult]
    scored_at: datetime


class TransactionResponse(BaseModel):
    """Full transaction resource returned by the API."""

    transaction_id: str
    amount: float
    payer_key: str
    receiver_key: str
    transaction_type: TransactionType
    risk_assessment: RiskAssessment
    created_at: datetime
    idempotent: bool = Field(
        default=False,
        description="True when the response was served from a previous scoring result.",
    )

    model_config = {"from_attributes": True}


class TransactionListResponse(BaseModel):
    """Paginated list of transaction resources."""

    total: int
    items: list[TransactionResponse]
