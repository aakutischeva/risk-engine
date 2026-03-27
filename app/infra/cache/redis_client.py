"""Async Redis client singleton for the Risk Engine."""

import redis.asyncio as aioredis
from loguru import logger

from app.core.config import settings

_client: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis | None:
    """Return the shared async Redis client, creating it on first call.

    Returns None (and logs a warning) if the Redis connection cannot be
    established, allowing the application to degrade gracefully: rules that
    depend on Redis will skip their checks rather than blocking the request.

    Returns:
        A connected :class:`aioredis.Redis` instance, or None on failure.
    """
    global _client
    if _client is None:
        try:
            _client = aioredis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            await _client.ping()
            logger.info("Redis connection established at {}", settings.REDIS_URL)
        except Exception as exc:
            logger.warning("Redis unavailable — velocity checks will be skipped: {}", exc)
            _client = None
    return _client


async def close_redis() -> None:
    """Close the shared Redis connection gracefully.

    Called during application shutdown via the FastAPI lifespan handler.
    """
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
        logger.info("Redis connection closed.")
