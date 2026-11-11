"""Bounded Cache-Aside for individual transaction API responses.

This namespace never overlaps with Fraud Engine velocity counters. Redis is
optional: failures degrade to PostgreSQL reads and never fail the API request.
"""

import redis.asyncio as aioredis
from loguru import logger

from app.infra.cache.redis_client import get_redis

KEY_PREFIX = "transaction:detail:v1:"


def cache_key(transaction_id: str) -> str:
    return f"{KEY_PREFIX}{transaction_id}"


async def get_cached_json(redis: aioredis.Redis, transaction_id: str) -> str | None:
    try:
        return await redis.get(cache_key(transaction_id))
    except Exception as exc:
        logger.warning("Transaction cache read unavailable: {}", type(exc).__name__)
        return None


async def set_cached_json(
    redis: aioredis.Redis, transaction_id: str, value: str, ttl: int
) -> None:
    try:
        await redis.set(cache_key(transaction_id), value, ex=ttl)
    except Exception as exc:
        logger.warning("Transaction cache write unavailable: {}", type(exc).__name__)


async def delete_cached_json(redis: aioredis.Redis, transaction_id: str) -> None:
    try:
        await redis.delete(cache_key(transaction_id))
    except Exception as exc:
        logger.warning("Transaction cache eviction unavailable: {}", type(exc).__name__)


async def invalidate_after_commit(transaction_ids: set[str]) -> None:
    """Run only after a successful PostgreSQL commit; tolerate Redis outages."""
    if not transaction_ids:
        return
    try:
        redis = await get_redis()
        if redis is not None:
            await redis.delete(*(cache_key(tx_id) for tx_id in transaction_ids))
    except Exception as exc:
        # A committed transaction must not become HTTP 500 due to Redis.
        # The cache TTL bounds stale data if invalidation cannot complete.
        logger.warning("Post-commit cache invalidation failed: {}", type(exc).__name__)
