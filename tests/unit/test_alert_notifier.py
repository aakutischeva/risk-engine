"""Alert consumer tests with fake broker/database objects (no external I/O)."""

import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

import alert_notifier.consumer as consumer
from alert_notifier.models import AlertNotification, ProcessedEvent
from alert_notifier.schemas import EventEnvelope


def event_json(risk_level="HIGH"):
    return {
        "event_id": str(uuid4()),
        "event_type": "transaction.scored",
        "aggregate_id": "tx-notifier-001",
        "payload": {
            "schema_version": 1,
            "transaction_id": "tx-notifier-001",
            "fraud_score": 85.0 if risk_level != "LOW" else 5.0,
            "risk_level": risk_level,
            "rules_triggered": ["HighAmountRule"] if risk_level != "LOW" else [],
            "scored_at": "2026-11-04T10:48:38Z",
        },
    }


class FakeSession:
    def __init__(self, storage, fail=False):
        self.storage = storage
        self.fail = fail

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def begin(self):
        return self

    async def get(self, model, event_id):
        if self.fail:
            raise ConnectionError("test database unavailable")
        for row in self.storage:
            if isinstance(row, model) and row.event_id == event_id:
                return row
        return None

    def add(self, row):
        self.storage.append(row)

    async def flush(self):
        return None


class FakeSessionFactory:
    def __init__(self, fail=False):
        self.storage = []
        self.fail = fail

    def __call__(self):
        return FakeSession(self.storage, self.fail)


class FakeIncomingMessage:
    def __init__(self, body):
        self.body = json.dumps(body).encode("utf-8")
        self.message_id = body.get("event_id")
        self.acks = 0
        self.nacks = []

    async def ack(self):
        self.acks += 1

    async def nack(self, *, requeue):
        self.nacks.append(requeue)


class FakeDeadExchange:
    def __init__(self, error=None, confirmation=True):
        self.error = error
        self.confirmation = confirmation
        self.sent = []

    async def publish(self, message, **kwargs):
        self.sent.append((message, kwargs))
        if self.error is not None:
            raise self.error
        return self.confirmation


@pytest.mark.asyncio
async def test_high_event_creates_one_notification_and_duplicate_is_ignored():
    payload = event_json("HIGH")
    event = EventEnvelope.model_validate(payload)
    sessions = FakeSessionFactory()

    assert await consumer.persist_event(sessions, event) is True
    assert await consumer.persist_event(sessions, event) is False

    ledger = [row for row in sessions.storage if isinstance(row, ProcessedEvent)]
    notifications = [row for row in sessions.storage if isinstance(row, AlertNotification)]
    assert len(ledger) == 1
    assert len(notifications) == 1
    assert notifications[0].status == "PENDING"
    assert notifications[0].risk_level == "HIGH"
    assert notifications[0].transaction_id == "tx-notifier-001"


@pytest.mark.asyncio
async def test_low_event_is_deduplicated_without_generating_notification():
    event = EventEnvelope.model_validate(event_json("LOW"))
    sessions = FakeSessionFactory()

    assert await consumer.persist_event(sessions, event) is True
    assert len(sessions.storage) == 1
    assert isinstance(sessions.storage[0], ProcessedEvent)


def test_mismatched_transaction_id_is_rejected_by_contract():
    payload = event_json()
    payload["aggregate_id"] = "different-transaction"
    with pytest.raises(ValidationError):
        EventEnvelope.model_validate(payload)


@pytest.mark.asyncio
async def test_invalid_event_goes_to_dead_queue_before_ack():
    payload = event_json()
    payload["payload"]["schema_version"] = 99
    message = FakeIncomingMessage(payload)
    dead = FakeDeadExchange()
    sessions = FakeSessionFactory()

    assert await consumer.handle_delivery(message, sessions, dead) == "quarantined"
    assert message.acks == 1
    assert message.nacks == []
    assert len(dead.sent) == 1
    assert dead.sent[0][1]["mandatory"] is True
    assert len(sessions.storage) == 0


@pytest.mark.asyncio
async def test_dead_queue_failure_requeues_without_ack(monkeypatch):
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(consumer.asyncio, "sleep", no_sleep)
    payload = event_json()
    payload["payload"]["schema_version"] = 99
    message = FakeIncomingMessage(payload)
    dead = FakeDeadExchange(error=ConnectionError("unavailable"))

    assert await consumer.handle_delivery(message, FakeSessionFactory(), dead) == "retry"
    assert message.acks == 0
    assert message.nacks == [True]


@pytest.mark.asyncio
async def test_database_failure_requeues_without_ack(monkeypatch):
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(consumer.asyncio, "sleep", no_sleep)
    message = FakeIncomingMessage(event_json())

    assert (
        await consumer.handle_delivery(message, FakeSessionFactory(fail=True), FakeDeadExchange())
        == "retry"
    )
    assert message.acks == 0
    assert message.nacks == [True]
