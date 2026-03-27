"""Integration tests for the /transactions API endpoints.

Uses httpx.AsyncClient with the FastAPI app directly — no live database or
Redis required; both are patched at the service boundary.
"""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.domain.enums import AlertStatus, RiskLevel, TransactionType
from app.domain.schemas.transaction import RuleResult, TransactionAssessment
from app.main import app

_BASE = "/transactions"

_SAMPLE_ASSESSMENT = TransactionAssessment(
    transaction_id="api-test-001",
    fraud_score=15.0,
    risk_level=RiskLevel.LOW,
    rules_evaluated=[
        RuleResult(rule="HighAmountRule", score=0.0, triggered=False, reason="Amount below threshold"),
    ],
    alert_id=None,
    alert_status=None,
)

_HIGH_RISK_ASSESSMENT = TransactionAssessment(
    transaction_id="api-test-002",
    fraud_score=85.0,
    risk_level=RiskLevel.CRITICAL,
    rules_evaluated=[
        RuleResult(rule="HighAmountRule", score=100.0, triggered=True, reason="Critical amount"),
    ],
    alert_id="alert-uuid-001",
    alert_status=AlertStatus.OPEN,
)

_VALID_PAYLOAD = {
    "transaction_id": "api-test-001",
    "amount": 150.00,
    "payer_key": "alice@bank.com",
    "receiver_key": "bob@bank.com",
    "transaction_type": "PIX",
}


@pytest.fixture
async def client():
    """Provide an async test client with lifespan events suppressed."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


class TestScoreEndpoint:
    @pytest.mark.asyncio
    async def test_valid_transaction_returns_200(self, client: AsyncClient):
        with patch(
            "app.api.routes.transactions.TransactionService.score_transaction",
            new_callable=AsyncMock,
            return_value=_SAMPLE_ASSESSMENT,
        ):
            response = await client.post(_BASE, json=_VALID_PAYLOAD)

        assert response.status_code == 200
        body = response.json()
        assert body["transaction_id"] == "api-test-001"
        assert body["fraud_score"] == 15.0
        assert body["risk_level"] == "LOW"

    @pytest.mark.asyncio
    async def test_high_risk_transaction_contains_alert_id(self, client: AsyncClient):
        payload = {**_VALID_PAYLOAD, "transaction_id": "api-test-002", "amount": 30_000.00}
        with patch(
            "app.api.routes.transactions.TransactionService.score_transaction",
            new_callable=AsyncMock,
            return_value=_HIGH_RISK_ASSESSMENT,
        ):
            response = await client.post(_BASE, json=payload)

        assert response.status_code == 200
        body = response.json()
        assert body["alert_id"] == "alert-uuid-001"
        assert body["alert_status"] == "OPEN"

    @pytest.mark.asyncio
    async def test_missing_required_field_returns_422(self, client: AsyncClient):
        incomplete = {k: v for k, v in _VALID_PAYLOAD.items() if k != "amount"}
        response = await client.post(_BASE, json=incomplete)
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_negative_amount_returns_422(self, client: AsyncClient):
        payload = {**_VALID_PAYLOAD, "amount": -1.00}
        response = await client.post(_BASE, json=payload)
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_transaction_type_returns_422(self, client: AsyncClient):
        payload = {**_VALID_PAYLOAD, "transaction_type": "WIRE"}
        response = await client.post(_BASE, json=payload)
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_duplicate_transaction_returns_409(self, client: AsyncClient):
        from app.core.exceptions import DuplicateTransactionError

        with patch(
            "app.api.routes.transactions.TransactionService.score_transaction",
            new_callable=AsyncMock,
            side_effect=DuplicateTransactionError("api-test-001"),
        ):
            response = await client.post(_BASE, json=_VALID_PAYLOAD)

        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_response_time_header_present(self, client: AsyncClient):
        with patch(
            "app.api.routes.transactions.TransactionService.score_transaction",
            new_callable=AsyncMock,
            return_value=_SAMPLE_ASSESSMENT,
        ):
            response = await client.post(_BASE, json=_VALID_PAYLOAD)

        assert "x-response-time" in response.headers


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self, client: AsyncClient):
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
