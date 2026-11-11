"""No broker or database connection required for these publisher tests."""

import json
from datetime import UTC, datetime

import pytest
from aio_pika import DeliveryMode
from sqlalchemy.dialects import postgresql

from app.infra.database.models import OutboxEvent
from app.workers.outbox_publisher import publish_event, publish_pending_batch


def sample_event() -> OutboxEvent:
    return OutboxEvent(
        event_id="test-outbox-001",
        event_type="transaction.scored",
        aggregate_id="test-transaction-001",
        payload={"schema_version": 1, "risk_level": "HIGH"},
        created_at=datetime.now(UTC).replace(tzinfo=None),
        attempts=0,
        published_at=None,
        last_error=None,
    )


class FakeExchange:
    def __init__(self, confirmation=True, error=None):
        self.confirmation = confirmation
        self.error = error
        self.sent = []

    async def publish(self, message, *, routing_key, mandatory, timeout):
        self.sent.append((message, routing_key, mandatory, timeout))
        if self.error is not None:
            raise self.error
        return self.confirmation


class FakeResult:
    def __init__(self, events):
        self.events = events

    def all(self):
        return self.events


class FakeSession:
    def __init__(self, events):
        self.events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def begin(self):
        return self

    async def scalars(self, statement):
        # Ensure concurrent publishers do not select already-locked events.
        sql = str(statement.compile(dialect=postgresql.dialect()))
        assert "FOR UPDATE SKIP LOCKED" in sql
        return FakeResult([event for event in self.events if event.published_at is None])


class FakeSessionFactory:
    def __init__(self, events):
        self.events = events

    def __call__(self):
        return FakeSession(self.events)


@pytest.mark.asyncio
async def test_persistent_message_contains_event_id_and_uses_mandatory_routing():
    event = sample_event()
    exchange = FakeExchange()

    await publish_event(exchange, event)

    message, routing_key, mandatory, timeout = exchange.sent[0]
    assert routing_key == "transaction.scored"
    assert mandatory is True
    assert timeout > 0
    assert message.message_id == event.event_id
    assert message.delivery_mode == DeliveryMode.PERSISTENT
    assert json.loads(message.body) == {
        "event_id": event.event_id,
        "event_type": event.event_type,
        "aggregate_id": event.aggregate_id,
        "payload": event.payload,
    }


@pytest.mark.asyncio
async def test_missing_publisher_confirmation_is_failure():
    with pytest.raises(RuntimeError, match="did not confirm"):
        await publish_event(FakeExchange(confirmation=False), sample_event())


@pytest.mark.asyncio
async def test_failed_event_is_retried_and_marked_only_after_confirmation():
    event = sample_event()
    sessions = FakeSessionFactory([event])

    delivered = await publish_pending_batch(
        sessions, FakeExchange(error=ConnectionError("broker down")), batch_size=5
    )
    assert delivered == 0
    assert event.attempts == 1
    assert event.published_at is None
    assert event.last_error == "ConnectionError"

    exchange = FakeExchange()
    delivered = await publish_pending_batch(sessions, exchange, batch_size=5)
    assert delivered == 1
    assert event.attempts == 2
    assert event.published_at is not None
    assert event.last_error is None

    assert await publish_pending_batch(sessions, exchange, batch_size=5) == 0
    assert len(exchange.sent) == 1
