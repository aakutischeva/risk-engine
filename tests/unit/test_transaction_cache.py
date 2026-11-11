"""Cache-Aside and post-commit invalidation, without a real Redis or PostgreSQL."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.infra.cache.transaction_cache as cache
import app.infra.database.connection as connection
from app.services.transaction_service import TransactionService


class FakeRedis:
    def __init__(self, broken=False):
        self.values = {}
        self.ttls = {}
        self.broken = broken
        self.gets = 0

    async def get(self, key):
        self.gets += 1
        if self.broken:
            raise ConnectionError("redis offline")
        return self.values.get(key)

    async def set(self, key, value, ex):
        if self.broken:
            raise ConnectionError("redis offline")
        self.values[key] = value
        self.ttls[key] = ex

    async def delete(self, *keys):
        if self.broken:
            raise ConnectionError("redis offline")
        for key in keys:
            self.values.pop(key, None)


def record_for_test(transaction_id="cached-tx-001"):
    return SimpleNamespace(
        transaction_id=transaction_id,
        amount=150.0,
        payer_key="payer@example.test",
        receiver_key="receiver@example.test",
        transaction_type="PIX",
        risk_details={
            "fraud_score": 5.0,
            "risk_level": "LOW",
            "rules_evaluated": [],
            "scored_at": datetime.now(UTC).isoformat(),
        },
        created_at=datetime.now(UTC).replace(tzinfo=None),
    )


def service_with_record(redis, record):
    service = TransactionService(session=SimpleNamespace(info={}), redis=redis)
    service._tx_repo.get_by_id = AsyncMock(return_value=record)
    return service


@pytest.mark.asyncio
async def test_first_read_misses_second_hits_cache():
    redis = FakeRedis()
    service = service_with_record(redis, record_for_test())

    first = await service.get_by_id("cached-tx-001")
    assert first.transaction_id == "cached-tx-001"
    assert service.cache_hit is False
    assert redis.ttls[cache.cache_key("cached-tx-001")] == 120

    second = await service.get_by_id("cached-tx-001")
    assert second.transaction_id == first.transaction_id
    assert second.risk_assessment.fraud_score == first.risk_assessment.fraud_score
    assert service.cache_hit is True
    service._tx_repo.get_by_id.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_cached_json_is_evicted_and_db_is_used():
    redis = FakeRedis()
    redis.values[cache.cache_key("cached-tx-001")] = "this-is-not-json"
    service = service_with_record(redis, record_for_test())

    response = await service.get_by_id("cached-tx-001")
    assert response.transaction_id == "cached-tx-001"
    assert service.cache_hit is False
    assert redis.values[cache.cache_key("cached-tx-001")] != "this-is-not-json"


@pytest.mark.asyncio
async def test_unavailable_redis_degrades_to_database_read():
    redis = FakeRedis(broken=True)
    service = service_with_record(redis, record_for_test())

    result = await service.get_by_id("cached-tx-001")
    assert result.transaction_id == "cached-tx-001"
    assert service.cache_hit is False
    service._tx_repo.get_by_id.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalidate_after_commit_deletes_only_transaction_namespace(monkeypatch):
    redis = FakeRedis()
    transaction_key = cache.cache_key("cached-tx-001")
    redis.values[transaction_key] = "stale"
    redis.values["velocity:payer"] = "keep"
    monkeypatch.setattr(cache, "get_redis", AsyncMock(return_value=redis))

    await cache.invalidate_after_commit({"cached-tx-001"})
    assert transaction_key not in redis.values
    assert redis.values["velocity:payer"] == "keep"


class FakeSession:
    def __init__(self, fails_commit=False):
        self.info = {}
        self.committed = False
        self.rolled_back = False
        self.fails_commit = fails_commit

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def commit(self):
        if self.fails_commit:
            raise RuntimeError("database unavailable")
        self.committed = True

    async def rollback(self):
        self.rolled_back = True


@pytest.mark.asyncio
async def test_invalidation_is_called_only_after_successful_commit(monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(connection, "AsyncSessionFactory", lambda: session)

    async def assert_committed_before_invalidation(transaction_ids):
        assert session.committed is True
        assert transaction_ids == {"cached-tx-001"}

    callback = AsyncMock(side_effect=assert_committed_before_invalidation)
    monkeypatch.setattr(connection, "invalidate_after_commit", callback)

    generator = connection.get_session()
    yielded = await anext(generator)
    yielded.info["transaction_cache_invalidate"] = {"cached-tx-001"}
    with pytest.raises(StopAsyncIteration):
        await anext(generator)
    callback.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_commit_never_invalidates(monkeypatch):
    session = FakeSession(fails_commit=True)
    monkeypatch.setattr(connection, "AsyncSessionFactory", lambda: session)
    callback = AsyncMock()
    monkeypatch.setattr(connection, "invalidate_after_commit", callback)

    generator = connection.get_session()
    yielded = await anext(generator)
    yielded.info["transaction_cache_invalidate"] = {"cached-tx-001"}
    with pytest.raises(RuntimeError, match="database unavailable"):
        await anext(generator)
    assert session.rolled_back is True
    callback.assert_not_awaited()
