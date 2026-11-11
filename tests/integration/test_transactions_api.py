"""API contract tests for transactions, without live PostgreSQL or Redis.

The transaction service is mocked at the boundary. These tests exercise
request validation, routing, and response serialization, not scoring rules.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.domain.enums import RiskLevel, TransactionType
from app.domain.schemas.transaction import (
    RiskAssessment,
    RuleResult,
    TransactionResponse,
)
from app.infra.database.connection import get_session
from app.main import app

_BASE = "/transactions"
_SENTINEL = object()

_VALID_PAYLOAD = {
    "transaction_id": "api-test-001",
    "amount": 150.00,
    "payer_key": "alice@bank.com",
    "receiver_key": "bob@bank.com",
    "transaction_type": "PIX",
}


def _response(
    *,
    transaction_id: str = "api-test-001",
    amount: float = 150.00,
    fraud_score: float = 15.0,
    risk_level: RiskLevel = RiskLevel.LOW,
    triggered: bool = False,
    idempotent: bool = False,
) -> TransactionResponse:
    now = datetime.now(UTC)
    return TransactionResponse(
        transaction_id=transaction_id,
        amount=amount,
        payer_key="alice@bank.com",
        receiver_key="bob@bank.com",
        transaction_type=TransactionType.PIX,
        risk_assessment=RiskAssessment(
            fraud_score=fraud_score,
            risk_level=risk_level,
            rules_evaluated=[
                RuleResult(
                    rule="HighAmountRule",
                    score=100.0 if triggered else 0.0,
                    triggered=triggered,
                    reason="Critical amount" if triggered else "Amount below threshold",
                )
            ],
            scored_at=now,
        ),
        created_at=now,
        idempotent=idempotent,
    )


@pytest.fixture(autouse=True)
def isolate_external_dependencies():
    """Prevent requests from opening the real database or connecting to Redis."""

    async def fake_session():
        yield object()

    old_override = app.dependency_overrides.get(get_session, _SENTINEL)
    app.dependency_overrides[get_session] = fake_session
    try:
        with patch("app.api.routes.transactions.get_redis", new_callable=AsyncMock) as fake_redis:
            fake_redis.return_value = AsyncMock()
            yield
    finally:
        if old_override is _SENTINEL:
            app.dependency_overrides.pop(get_session, None)
        else:
            app.dependency_overrides[get_session] = old_override


async def _request(method: str, url: str, **kwargs):
    """Send a request directly to FastAPI without starting its lifespan."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.request(method, url, **kwargs)


class TestSubmitTransactionEndpoint:
    @pytest.mark.asyncio
    async def test_valid_transaction_returns_200(self):
        with patch(
            "app.api.routes.transactions.TransactionService.submit",
            new_callable=AsyncMock,
            return_value=_response(),
        ) as submit:
            response = await _request("POST", _BASE, json=_VALID_PAYLOAD)

        assert response.status_code == 200
        body = response.json()
        assert body["transaction_id"] == "api-test-001"
        assert body["risk_assessment"]["fraud_score"] == 15.0
        assert body["risk_assessment"]["risk_level"] == "LOW"
        assert body["idempotent"] is False
        submit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_high_risk_transaction_contains_nested_assessment(self):
        payload = {**_VALID_PAYLOAD, "transaction_id": "api-test-002", "amount": 30000.00}
        with patch(
            "app.api.routes.transactions.TransactionService.submit",
            new_callable=AsyncMock,
            return_value=_response(
                transaction_id="api-test-002",
                amount=30000.00,
                fraud_score=85.0,
                risk_level=RiskLevel.CRITICAL,
                triggered=True,
            ),
        ):
            response = await _request("POST", _BASE, json=payload)

        assert response.status_code == 200
        assessment = response.json()["risk_assessment"]
        assert assessment["fraud_score"] == 85.0
        assert assessment["risk_level"] == "CRITICAL"
        assert assessment["rules_evaluated"][0]["triggered"] is True

    @pytest.mark.asyncio
    async def test_missing_required_field_returns_422(self):
        incomplete = {k: v for k, v in _VALID_PAYLOAD.items() if k != "amount"}
        response = await _request("POST", _BASE, json=incomplete)
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_negative_amount_returns_422(self):
        response = await _request("POST", _BASE, json={**_VALID_PAYLOAD, "amount": -1.00})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_transaction_type_returns_422(self):
        response = await _request(
            "POST", _BASE, json={**_VALID_PAYLOAD, "transaction_type": "INVALID_TYPE_123"}
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_repeated_transaction_can_return_idempotent_response(self):
        """Test routing of a service-provided result, not the service's DB logic."""
        first = _response(idempotent=False)
        repeated = _response(idempotent=True)
        with patch(
            "app.api.routes.transactions.TransactionService.submit",
            new_callable=AsyncMock,
            side_effect=[first, repeated],
        ) as submit:
            response1 = await _request("POST", _BASE, json=_VALID_PAYLOAD)
            response2 = await _request("POST", _BASE, json=_VALID_PAYLOAD)

        assert response1.status_code == 200
        assert response1.json()["idempotent"] is False
        assert response2.status_code == 200
        assert response2.json()["idempotent"] is True
        assert submit.await_count == 2

    @pytest.mark.asyncio
    async def test_response_time_header_present(self):
        with patch(
            "app.api.routes.transactions.TransactionService.submit",
            new_callable=AsyncMock,
            return_value=_response(),
        ):
            response = await _request("POST", _BASE, json=_VALID_PAYLOAD)

        assert response.status_code == 200
        assert "x-response-time" in response.headers


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self):
        response = await _request("GET", "/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
