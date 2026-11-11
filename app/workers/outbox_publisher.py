"""Reliable outbox-to-RabbitMQ publisher (at-least-once delivery).

An outbox record is written in the API's PostgreSQL transaction. This worker
publishes it to a durable queue and marks it published only after the broker
confirms acceptance. Consumers MUST deduplicate using ``event_id`` because a
crash after publish confirmation but before DB commit can cause redelivery.
"""

import asyncio
import json
from datetime import UTC, datetime

import aio_pika
from aio_pika import DeliveryMode, ExchangeType, Message
from aio_pika.abc import AbstractExchange
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.infra.database.models import OutboxEvent


EXCHANGE_NAME = "risk.events"
ALERT_QUEUE_NAME = "risk.alerts"
TRANSACTION_ROUTING_KEY = "transaction.scored"


async def publish_event(exchange: AbstractExchange, event: OutboxEvent) -> None:
    """Publish one persistent event and wait for broker confirmation."""
    body = json.dumps(
        {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "aggregate_id": event.aggregate_id,
            "payload": event.payload,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    message = Message(
        body=body,
        message_id=event.event_id,
        correlation_id=event.aggregate_id,
        type=event.event_type,
        content_type="application/json",
        delivery_mode=DeliveryMode.PERSISTENT,
    )
    # channel(publisher_confirms=True, on_return_raises=True) is mandatory:
    # RabbitMQ can confirm an UNROUTABLE publish unless returns are checked.
    confirmation = await exchange.publish(
        message,
        routing_key=event.event_type,
        mandatory=True,
        timeout=15.0,
    )
    if not confirmation:
        raise RuntimeError("RabbitMQ did not confirm publication")


async def publish_pending_batch(
    session_factory: async_sessionmaker[AsyncSession],
    exchange: AbstractExchange,
    *,
    batch_size: int,
) -> int:
    """Publish pending rows, recording success/failure in PostgreSQL.

    SKIP LOCKED lets another worker process different rows. Locks remain held
    until confirmations and the database commit; no row is claimed twice
    concurrently. A crash after a broker confirmation may still cause a
    duplicate, so consumer-side idempotency is required.
    """
    delivered = 0
    async with session_factory() as session:
        async with session.begin():
            statement = (
                select(OutboxEvent)
                .where(OutboxEvent.published_at.is_(None))
                .order_by(OutboxEvent.created_at, OutboxEvent.event_id)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
            events = (await session.scalars(statement)).all()
            for event in events:
                try:
                    await publish_event(exchange, event)
                except Exception as exc:
                    event.attempts += 1
                    # Avoid persisting connection strings or broker credentials.
                    event.last_error = type(exc).__name__
                    logger.warning(
                        "Outbox publish failed | id={} attempt={} error={}",
                        event.event_id,
                        event.attempts,
                        type(exc).__name__,
                    )
                else:
                    event.attempts += 1
                    # DB DateTime column uses UTC without a timezone suffix.
                    event.published_at = datetime.now(UTC).replace(tzinfo=None)
                    event.last_error = None
                    delivered += 1
                    logger.info("Outbox published | id={}", event.event_id)
    return delivered


async def run_publisher() -> None:
    """Start the worker and reconnect after transient broker/DB failures."""
    if not settings.RABBITMQ_USER or not settings.RABBITMQ_PASSWORD:
        raise RuntimeError("RABBITMQ_USER and RABBITMQ_PASSWORD must be configured")

    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        while True:
            try:
                connection = await aio_pika.connect_robust(
                    host=settings.RABBITMQ_HOST,
                    port=settings.RABBITMQ_PORT,
                    login=settings.RABBITMQ_USER,
                    password=settings.RABBITMQ_PASSWORD,
                    virtualhost=settings.RABBITMQ_VHOST,
                    timeout=10.0,
                )
                async with connection:
                    channel = await connection.channel(
                        publisher_confirms=True,
                        on_return_raises=True,
                    )
                    exchange = await channel.declare_exchange(
                        EXCHANGE_NAME, ExchangeType.TOPIC, durable=True
                    )
                    # Pre-declare a durable queue so messages do not disappear
                    # before the dedicated Alert Service is deployed.
                    queue = await channel.declare_queue(ALERT_QUEUE_NAME, durable=True)
                    await queue.bind(exchange, routing_key=TRANSACTION_ROUTING_KEY)
                    logger.info(
                        "Outbox publisher ready | exchange={} queue={}",
                        EXCHANGE_NAME,
                        ALERT_QUEUE_NAME,
                    )

                    while True:
                        delivered = await publish_pending_batch(
                            sessions,
                            exchange,
                            batch_size=settings.OUTBOX_BATCH_SIZE,
                        )
                        if delivered < settings.OUTBOX_BATCH_SIZE:
                            await asyncio.sleep(settings.OUTBOX_POLL_INTERVAL_SECONDS)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Outbox publisher reconnecting after {}", type(exc).__name__)
                await asyncio.sleep(5)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run_publisher())
