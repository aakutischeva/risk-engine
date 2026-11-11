"""Real PostgreSQL integration tests using a dedicated disposable test container.

Safety: refuses to connect unless TEST_DATABASE_URL points to the exact local
test database on 127.0.0.1:15432. Never use the production DATABASE_URL here.

These tests exercise repository persistence, service idempotency and alerts.
The fraud scoring engine is replaced by a deterministic stub for service tests;
Redis and HTTP routing are outside the scope of this file.
"""

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.services.transaction_service as transaction_service_module
from app.domain.enums import RiskLevel, TransactionType
from app.domain.schemas.transaction import RiskAssessment, RuleResult, TransactionSubmit
from app.infra.database.models import AlertRecord, Base, OutboxEvent, TransactionRecord
from app.infra.database.repositories.transaction_repository import TransactionRepository
from app.services.transaction_service import TransactionService


def _new_payload(amount: float = 150.0) -> TransactionSubmit:
    return TransactionSubmit(
        transaction_id=f"integration-{uuid4().hex}",
        amount=amount,
        payer_key="integration-payer@example.test",
        receiver_key="integration-receiver@example.test",
        transaction_type=TransactionType.PIX,
    )


def _assessment(risk: RiskLevel = RiskLevel.LOW) -> RiskAssessment:
    high = risk in (RiskLevel.HIGH, RiskLevel.CRITICAL)
    return RiskAssessment(
        fraud_score=75.0 if high else 5.0,
        risk_level=risk,
        rules_evaluated=[
            RuleResult(
                rule="HighAmountRule",
                score=100.0 if high else 0.0,
                triggered=high,
                reason="Test-controlled assessment",
            )
        ],
        scored_at=datetime.now(UTC),
    )


class StubFraudEngine:
    def __init__(self, result: RiskAssessment):
        self.result = result
        self.calls = 0

    async def score(self, payload, redis=None):
        self.calls += 1
        return self.result


@pytest_asyncio.fixture
async def session_factory():
    database_url = os.environ.get("TEST_DATABASE_URL", "")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")

    url = make_url(database_url)
    # Intentionally strict: protect the running application database.
    if (
        url.drivername != "postgresql+asyncpg"
        or url.host != "127.0.0.1"
        or url.port != 15432
        or url.database != "riskengine_test"
        or url.username != "riskengine_test"
    ):
        pytest.fail(
            "Refusing unsafe TEST_DATABASE_URL. Use only the disposable "
            "riskengine_test database at 127.0.0.1:15432."
        )

    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_transaction_is_persisted_and_can_be_loaded(session_factory):
    payload = _new_payload()
    assessment = _assessment()

    async with session_factory() as session:
        saved = await TransactionRepository(session).create(payload, assessment)
        assert saved.transaction_id == payload.transaction_id
        await session.commit()

    async with session_factory() as session:
        loaded = await TransactionRepository(session).get_by_id(payload.transaction_id)
        assert loaded is not None
        assert loaded.amount == payload.amount
        assert loaded.risk_level == RiskLevel.LOW.value
        assert loaded.risk_details["fraud_score"] == assessment.fraud_score


@pytest.mark.asyncio
async def test_repeated_submit_does_not_insert_duplicate(session_factory, monkeypatch):
    fake_engine = StubFraudEngine(_assessment())
    monkeypatch.setattr(transaction_service_module, "_engine", fake_engine)
    payload = _new_payload()

    async with session_factory() as session:
        first = await TransactionService(session=session).submit(payload)
        await session.commit()

    async with session_factory() as session:
        second = await TransactionService(session=session).submit(payload)
        await session.commit()

    assert first.idempotent is False
    assert second.idempotent is True
    assert second.risk_assessment.fraud_score == first.risk_assessment.fraud_score
    assert fake_engine.calls == 1

    async with session_factory() as session:
        result = await session.execute(
            select(func.count())
            .select_from(TransactionRecord)
            .where(TransactionRecord.transaction_id == payload.transaction_id)
        )
        assert result.scalar_one() == 1


@pytest.mark.asyncio
async def test_high_risk_submit_creates_open_alert(session_factory, monkeypatch):
    fake_engine = StubFraudEngine(_assessment(RiskLevel.HIGH))
    monkeypatch.setattr(transaction_service_module, "_engine", fake_engine)
    payload = _new_payload(amount=30000.0)

    async with session_factory() as session:
        response = await TransactionService(session=session).submit(payload)
        await session.commit()

    assert response.risk_assessment.risk_level == RiskLevel.HIGH
    assert fake_engine.calls == 1

    async with session_factory() as session:
        result = await session.execute(
            select(AlertRecord).where(AlertRecord.transaction_id == payload.transaction_id)
        )
        alert = result.scalar_one()
        assert alert.status == "OPEN"
        assert alert.risk_level == "HIGH"
        assert alert.rules_triggered == ["HighAmountRule"]


@pytest.mark.asyncio
async def test_new_submit_creates_outbox_event(session_factory, monkeypatch):
    fake_engine = StubFraudEngine(_assessment(RiskLevel.HIGH))
    monkeypatch.setattr(transaction_service_module, "_engine", fake_engine)
    payload = _new_payload(amount=30000.0)

    async with session_factory() as session:
        await TransactionService(session=session).submit(payload)
        await session.commit()

    async with session_factory() as session:
        result = await session.execute(
            select(OutboxEvent).where(OutboxEvent.aggregate_id == payload.transaction_id)
        )
        event = result.scalar_one()
        assert event.event_type == "transaction.scored"
        assert event.payload["schema_version"] == 1
        assert event.payload["transaction_id"] == payload.transaction_id
        assert event.payload["risk_level"] == "HIGH"
        assert event.payload["rules_triggered"] == ["HighAmountRule"]
        assert event.published_at is None
        assert event.attempts == 0


@pytest.mark.asyncio
async def test_repeated_submit_does_not_duplicate_outbox_event(session_factory, monkeypatch):
    fake_engine = StubFraudEngine(_assessment())
    monkeypatch.setattr(transaction_service_module, "_engine", fake_engine)
    payload = _new_payload()

    async with session_factory() as session:
        await TransactionService(session=session).submit(payload)
        await session.commit()

    async with session_factory() as session:
        repeated = await TransactionService(session=session).submit(payload)
        await session.commit()

    assert repeated.idempotent is True
    assert fake_engine.calls == 1
    async with session_factory() as session:
        result = await session.execute(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.aggregate_id == payload.transaction_id)
        )
        assert result.scalar_one() == 1


@pytest.mark.asyncio
async def test_rollback_discards_outbox_event(session_factory, monkeypatch):
    fake_engine = StubFraudEngine(_assessment())
    monkeypatch.setattr(transaction_service_module, "_engine", fake_engine)
    payload = _new_payload()

    async with session_factory() as session:
        await TransactionService(session=session).submit(payload)
        await session.rollback()

    async with session_factory() as session:
        result = await session.execute(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.aggregate_id == payload.transaction_id)
        )
        assert result.scalar_one() == 0
