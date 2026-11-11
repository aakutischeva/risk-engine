"""Tests for VelocityRule against a disposable, loopback-only Redis container.

Set TEST_REDIS_URL=redis://127.0.0.1:16379/0 before running.
The fixture rejects any other host/port to protect the production Redis.
"""

import os
import time
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
import pytest_asyncio
import redis.asyncio as redis_async

from app.core.config import settings
from app.domain.enums import RiskLevel, TransactionType
from app.domain.schemas.transaction import TransactionSubmit
from app.fraud.engine import FraudEngine
from app.fraud.rules.base import RuleContext
from app.fraud.rules.velocity import VelocityRule


_PREFIX = "risk_engine:velocity:"


def _transaction(payer: str, transaction_id: str | None = None) -> TransactionSubmit:
    return TransactionSubmit(
        transaction_id=transaction_id or f"redis-test-{uuid4().hex}",
        amount=150.00,
        payer_key=payer,
        receiver_key="test-merchant@example.test",
        transaction_type=TransactionType.PIX,
    )


async def _evaluate(rule: VelocityRule, client, payer: str, transaction_id=None):
    return await rule.evaluate(
        RuleContext(
            transaction=_transaction(payer, transaction_id),
            redis=client,
            current_hour=12,
        )
    )


@pytest_asyncio.fixture
async def redis_state():
    redis_url = os.environ.get("TEST_REDIS_URL", "")
    if not redis_url:
        pytest.skip("TEST_REDIS_URL is not configured")

    parsed = urlsplit(redis_url)
    if (
        parsed.scheme != "redis"
        or parsed.hostname != "127.0.0.1"
        or parsed.port != 16379
        or parsed.path not in ("", "/0")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
    ):
        pytest.fail(
            "Refusing unsafe TEST_REDIS_URL: use only redis://127.0.0.1:16379/0"
        )

    client = redis_async.from_url(redis_url, decode_responses=True)
    payers: list[str] = []

    def new_payer() -> str:
        payer = f"velocity-{uuid4().hex}@example.test"
        payers.append(payer)
        return payer

    try:
        assert await client.ping(), "Isolated Redis did not respond to PING"
        yield client, new_payer
    finally:
        try:
            if payers:
                await client.delete(*(_PREFIX + payer for payer in payers))
        finally:
            await client.aclose()


def _thresholds() -> tuple[int, int]:
    high = settings.VELOCITY_MAX_TRANSACTIONS
    critical = settings.VELOCITY_CRITICAL_TRANSACTIONS
    assert 1 < high < critical, "Expected 1 < high velocity threshold < critical"
    return high, critical


@pytest.mark.asyncio
async def test_normal_high_and_critical_thresholds(redis_state):
    client, new_payer = redis_state
    payer = new_payer()
    rule = VelocityRule()
    high, critical = _thresholds()

    for count in range(1, critical + 1):
        result = await _evaluate(rule, client, payer)
        if count < high:
            assert result.score == 0.0
            assert result.triggered is False
        elif count == high:
            assert result.score == 50.0
            assert result.triggered is True
        elif count == critical:
            assert result.score == 100.0
            assert result.triggered is True

    assert await client.zcard(_PREFIX + payer) == critical


@pytest.mark.asyncio
async def test_different_payers_have_independent_counters(redis_state):
    client, new_payer = redis_state
    first_payer = new_payer()
    second_payer = new_payer()
    high, _ = _thresholds()
    rule = VelocityRule()

    for _ in range(high):
        result_first = await _evaluate(rule, client, first_payer)
    result_second = await _evaluate(rule, client, second_payer)

    assert result_first.triggered is True
    assert result_second.score == 0.0
    assert await client.zcard(_PREFIX + second_payer) == 1


@pytest.mark.asyncio
async def test_repeated_transaction_id_counts_once(redis_state):
    client, new_payer = redis_state
    payer = new_payer()
    transaction_id = f"repeat-{uuid4().hex}"
    rule = VelocityRule()

    for _ in range(3):
        result = await _evaluate(rule, client, payer, transaction_id)

    assert result.score == 0.0
    assert await client.zcard(_PREFIX + payer) == 1


@pytest.mark.asyncio
async def test_old_entries_removed_and_ttl_is_set(redis_state):
    client, new_payer = redis_state
    payer = new_payer()
    redis_key = _PREFIX + payer
    window = settings.VELOCITY_WINDOW_SECONDS
    await client.zadd(redis_key, {"old-transaction": time.time() - window - 10})

    result = await _evaluate(VelocityRule(), client, payer)

    assert result.score == 0.0
    assert await client.zcard(redis_key) == 1
    ttl = await client.ttl(redis_key)
    assert 0 < ttl <= window


@pytest.mark.asyncio
async def test_unavailable_redis_skips_velocity_rule():
    result = await _evaluate(
        VelocityRule(),
        client=None,
        payer=f"missing-redis-{uuid4().hex}@example.test",
    )
    assert result.score == 0.0
    assert result.triggered is False
    assert "Redis unavailable" in result.reason


@pytest.mark.asyncio
async def test_fraud_engine_uses_real_redis_velocity_score(redis_state):
    client, new_payer = redis_state
    payer = new_payer()
    _, critical = _thresholds()
    engine = FraudEngine(rules=[VelocityRule()])

    for _ in range(critical):
        assessment = await engine.score(
            _transaction(payer), redis=client, current_hour=12
        )

    assert assessment.fraud_score == 100.0
    assert assessment.risk_level == RiskLevel.CRITICAL
    assert assessment.rules_evaluated[0].triggered is True
