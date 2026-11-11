"""Local fake-DB and fake-HTTP tests for signed webhook dispatch and retry."""

import json
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest

from alert_notifier.demo_receiver import ReceiverState, accept_webhook
from alert_notifier.dispatcher import dispatch_next, retry_delay
from alert_notifier.models import AlertNotification, utc_now
from alert_notifier.webhook import encode_notification, sign_payload, verify_signature


SECRET = "test-only-" + "X" * 48


def new_notification() -> AlertNotification:
    return AlertNotification(
        event_id=str(uuid4()),
        transaction_id="fraud-integration-test",
        risk_level="HIGH",
        fraud_score=75.5,
        rules_triggered=["VelocityRule", "SelfTransferRule"],
        status="PENDING",
        attempts=0,
        created_at=utc_now(),
    )


class FakeResult:
    def __init__(self, notification):
        self.notification = notification

    def scalar_one_or_none(self):
        return self.notification


class FakeSession:
    def __init__(self, notification):
        self.notification = notification

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def begin(self):
        return self

    async def execute(self, _query):
        due = (
            self.notification.status in ("PENDING", "RETRY")
            and (
                self.notification.next_attempt_at is None
                or self.notification.next_attempt_at <= utc_now()
            )
        )
        return FakeResult(self.notification if due else None)


class FakeSessions:
    def __init__(self, notification):
        self.notification = notification

    def __call__(self):
        return FakeSession(self.notification)


class FakeClient:
    def __init__(self, status=200, error=None):
        self.status = status
        self.error = error
        self.posts = []

    async def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        if self.error:
            raise self.error
        return type("Response", (), {"status_code": self.status})()


def test_hmac_authenticates_exact_bytes_and_receiver_deduplicates():
    notification = new_notification()
    body = encode_notification(notification)
    signature = sign_payload(body, SECRET)

    assert verify_signature(body, signature, SECRET)
    assert not verify_signature(body + b" ", signature, SECRET)

    state = ReceiverState()
    assert accept_webhook(body, signature, SECRET, state) == (200, "accepted")
    assert accept_webhook(body, signature, SECRET, state) == (200, "duplicate")
    assert accept_webhook(body + b" ", signature, SECRET, state) == (401, "invalid_signature")
    assert len(state.seen) == 1


@pytest.mark.asyncio
async def test_success_marks_sent_and_includes_hmac_signature():
    notification = new_notification()
    sessions = FakeSessions(notification)
    client = FakeClient()

    assert await dispatch_next(sessions, client, "http://receiver/webhook", SECRET)
    assert notification.status == "SENT"
    assert notification.attempts == 1
    assert notification.sent_at is not None
    assert notification.last_error is None
    assert notification.next_attempt_at is None

    url, kwargs = client.posts[0]
    assert url == "http://receiver/webhook"
    body = json.loads(kwargs["content"])
    assert body["event_id"] == notification.event_id
    assert body["transaction_id"] == notification.transaction_id
    assert "payer_key" not in body
    assert "receiver_key" not in body
    assert verify_signature(kwargs["content"], kwargs["headers"]["X-Risk-Signature"], SECRET)

    assert await dispatch_next(sessions, client, "http://receiver/webhook", SECRET) is False
    assert len(client.posts) == 1


@pytest.mark.asyncio
async def test_http_error_schedules_retry_then_succeeds():
    notification = new_notification()
    sessions = FakeSessions(notification)
    failing = FakeClient(status=503)

    assert await dispatch_next(sessions, failing, "http://receiver/webhook", SECRET)
    assert notification.status == "RETRY"
    assert notification.attempts == 1
    assert notification.last_error == "HTTP 503"
    assert notification.next_attempt_at > utc_now()
    assert await dispatch_next(sessions, failing, "http://receiver/webhook", SECRET) is False

    notification.next_attempt_at = utc_now() - timedelta(seconds=1)
    succeeding = FakeClient(status=200)
    assert await dispatch_next(sessions, succeeding, "http://receiver/webhook", SECRET)
    assert notification.status == "SENT"
    assert notification.attempts == 2
    assert notification.last_error is None


@pytest.mark.asyncio
async def test_network_failure_remains_retryable():
    notification = new_notification()
    transport_error = httpx.ConnectError("receiver unavailable")
    client = FakeClient(error=transport_error)

    assert await dispatch_next(
        FakeSessions(notification), client, "http://receiver/webhook", SECRET
    )
    assert notification.status == "RETRY"
    assert notification.attempts == 1
    assert notification.last_error == "ConnectError"


def test_retry_delay_is_bounded():
    assert retry_delay(1) == 2
    assert retry_delay(2) == 4
    assert retry_delay(5) == 32
    assert retry_delay(10) == 60
