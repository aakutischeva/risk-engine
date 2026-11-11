"""Consume scored transactions and persist alert notification work items.

The Risk Engine remains the owner of its analyst-facing ``alerts`` table.
This separate service maintains an event-deduplication ledger and independent
PENDING notification records, without changing existing /alerts API routes.

RabbitMQ deliveries are ACKed only after the PostgreSQL transaction commits.
Duplicates are harmless because processed_events.event_id is a primary key.
Malformed messages are routed to a durable dead-letter queue before ACK.
"""

import asyncio
import os
from uuid import uuid4

import aio_pika
from aio_pika import DeliveryMode, ExchangeType, Message
from aio_pika.abc import AbstractExchange, AbstractIncomingMessage
from loguru import logger
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alert_notifier.database import database_url
from alert_notifier.models import AlertNotification, ProcessedEvent
from alert_notifier.schemas import EventEnvelope

EVENT_EXCHANGE = "risk.events"
ALERT_QUEUE = "risk.alerts"
TRANSACTION_ROUTING_KEY = "transaction.scored"
DEAD_EXCHANGE = "risk.events.dead"
DEAD_QUEUE = "risk.alerts.dead"


async def persist_event(
    sessions: async_sessionmaker[AsyncSession], event: EventEnvelope
) -> bool:
    """Commit a deduplication record and optionally a pending notification.

    Returns False if this event has already been processed. Database writes
    are atomic; failure raises and the RabbitMQ message must be requeued.
    """
    event_id = str(event.event_id)
    async with sessions() as session, session.begin():
        if await session.get(ProcessedEvent, event_id) is not None:
            return False

        session.add(
            ProcessedEvent(
                event_id=event_id,
                event_type=event.event_type,
                transaction_id=event.payload.transaction_id,
                risk_level=event.payload.risk_level,
            )
        )
        # Ensure the parent row exists before inserting the FK child.
        await session.flush()
        if event.requires_notification:
            session.add(
                AlertNotification(
                    event_id=event_id,
                    transaction_id=event.payload.transaction_id,
                    risk_level=event.payload.risk_level,
                    fraud_score=event.payload.fraud_score,
                    rules_triggered=event.payload.rules_triggered,
                    status="PENDING",
                )
            )
    return True


async def send_to_dead_queue(
    message: AbstractIncomingMessage, exchange: AbstractExchange, error: Exception
) -> None:
    """Keep malformed input available for later inspection and investigation."""
    confirmation = await exchange.publish(
        Message(
            body=message.body,
            message_id=message.message_id or str(uuid4()),
            delivery_mode=DeliveryMode.PERSISTENT,
            headers={"source_queue": ALERT_QUEUE, "error_class": type(error).__name__},
        ),
        routing_key=DEAD_QUEUE,
        mandatory=True,
        timeout=15.0,
    )
    if not confirmation:
        raise RuntimeError("Dead-letter publication was not confirmed")


async def handle_delivery(
    message: AbstractIncomingMessage,
    sessions: async_sessionmaker[AsyncSession],
    dead_exchange: AbstractExchange,
) -> str:
    """ACK after DB commit; retry DB failures; quarantine invalid messages."""
    try:
        event = EventEnvelope.model_validate_json(message.body)
    except (ValidationError, ValueError) as exc:
        try:
            await send_to_dead_queue(message, dead_exchange, exc)
        except Exception as publish_exc:
            logger.warning("Dead-letter routing failed: {}", type(publish_exc).__name__)
            await message.nack(requeue=True)
            await asyncio.sleep(2)
            return "retry"
        await message.ack()
        logger.warning("Malformed alert event quarantined: {}", type(exc).__name__)
        return "quarantined"

    try:
        created = await persist_event(sessions, event)
    except Exception as exc:
        logger.warning(
            "Alert DB processing failed | event={} error={}",
            str(event.event_id),
            type(exc).__name__,
        )
        await message.nack(requeue=True)
        await asyncio.sleep(2)
        return "retry"

    await message.ack()
    if not created:
        logger.info("Duplicate event ignored | event={}", str(event.event_id))
        return "duplicate"
    if event.requires_notification:
        logger.info(
            "Alert notification pending | transaction={} level={}",
            event.payload.transaction_id,
            event.payload.risk_level,
        )
        return "notification"
    logger.info("Normal transaction recorded | transaction={}", event.payload.transaction_id)
    return "processed"


async def run_consumer() -> None:
    """Reconnect automatically and process the durable risk.alerts queue."""
    engine = create_async_engine(database_url(), pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        while True:
            try:
                connection = await aio_pika.connect_robust(
                    host=os.environ.get("RABBITMQ_HOST", "rabbitmq"),
                    port=int(os.environ.get("RABBITMQ_PORT", "5672")),
                    login=os.environ["RABBITMQ_USER"],
                    password=os.environ["RABBITMQ_PASSWORD"],
                    virtualhost=os.environ.get("RABBITMQ_VHOST", "risk_engine"),
                    timeout=10.0,
                )
                async with connection:
                    channel = await connection.channel(
                        publisher_confirms=True, on_return_raises=True
                    )
                    await channel.set_qos(prefetch_count=5)

                    exchange = await channel.declare_exchange(
                        EVENT_EXCHANGE, ExchangeType.TOPIC, durable=True
                    )
                    queue = await channel.declare_queue(ALERT_QUEUE, durable=True)
                    await queue.bind(exchange, routing_key=TRANSACTION_ROUTING_KEY)

                    dead_exchange = await channel.declare_exchange(
                        DEAD_EXCHANGE, ExchangeType.DIRECT, durable=True
                    )
                    dead_queue = await channel.declare_queue(DEAD_QUEUE, durable=True)
                    await dead_queue.bind(dead_exchange, routing_key=DEAD_QUEUE)

                    logger.info("Alert notifier ready | queue={}", ALERT_QUEUE)
                    async with queue.iterator() as messages:
                        async for message in messages:
                            await handle_delivery(message, sessions, dead_exchange)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Alert notifier reconnecting after {}", type(exc).__name__)
                await asyncio.sleep(5)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run_consumer())
