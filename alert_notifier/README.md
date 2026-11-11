# Alert Notification Service

This is a separate business service within the anti-fraud demonstration.
It owns its own PostgreSQL database and consumes `transaction.scored` events
from the durable RabbitMQ queue `risk.alerts`.

## Responsibilities

- Every valid event is recorded once in `processed_events` (primary key: `event_id`).
- Only `HIGH` and `CRITICAL` events create a `PENDING` row in `alert_notifications`.
- Existing Risk Engine `/alerts` endpoints and its `alerts` table are unchanged.
- A RabbitMQ delivery is acknowledged **after** the database commit succeeds.
- Invalid envelopes are stored in `risk.alerts.dead` before being acknowledged.
- Failed database writes cause the message to be requeued.

## Signed webhook delivery (stage 4)

The independent `alert-dispatcher` service finds `PENDING` and due `RETRY`
notifications in the Alert Notification Service's own PostgreSQL database.
It sends an authenticated HTTP POST containing event ID, transaction ID,
risk level, fraud score and triggered rule names. It deliberately does **not**
include payer or receiver keys.

- The payload is signed with HMAC-SHA256 in `X-Risk-Signature: sha256=...`.
- The receiver verifies the **exact body bytes** before parsing JSON.
- PostgreSQL `FOR UPDATE SKIP LOCKED` prevents concurrent dispatchers from
  claiming the same notification. HTTP calls have a five-second timeout.
- A 2xx HTTP response moves a work item to `SENT`, storing `sent_at`.
- Errors move the item to `RETRY`, with exponential backoff up to 60 seconds;
  attempts and the last error are kept for inspection. Retry is indefinite,
  so an outage does not discard pending notifications.
- Delivery is **at least once**. If a recipient accepts a message but the
  dispatcher crashes before its DB commit, the same event can be sent again.
  Real recipients must deduplicate `event_id` **durably**.

`notification-receiver` is a local teaching/demo recipient. It stores event
IDs in memory for duplicate detection **only while running**. It has no host
port and is not an external email/SMS/Telegram integration. Its messages are
visible via `docker compose logs notification-receiver`. Do not mistake an
in-memory mock for a production notification endpoint; production needs HTTPS,
durable receiver-side idempotency, and appropriate access controls.

### Configuration

In the host `.env`, add a unique, strong `NOTIFICATION_WEBHOOK_SECRET` (at least
32 characters; for example use `openssl rand -hex 32`). Do not publish it or
paste it into a chat. Compose passes the same secret to dispatcher and local
receiver. The webhook URL is confined to the private Docker network.

After applying the patch, run the Alert Notification Service migrations to
revision `alert002` **before** starting the new dispatcher. Existing `PENDING`
notifications are preserved and will be delivered on startup.

For a controlled outage demonstration, stop only the mock receiver **after**
both new services are up (`docker compose stop notification-receiver`), submit
a new HIGH transaction, and observe `RETRY`. Start the receiver again
(`docker compose start notification-receiver`); the dispatcher automatically
retries until the notification becomes `SENT`.

## Event contract

The RabbitMQ message body is JSON:

```json
{
  "event_id": "UUID",
  "event_type": "transaction.scored",
  "aggregate_id": "transaction ID",
  "payload": {
    "schema_version": 1,
    "transaction_id": "transaction ID",
    "fraud_score": 75.0,
    "risk_level": "HIGH",
    "rules_triggered": ["HighAmountRule"],
    "scored_at": "2026-11-04T10:48:38Z"
  }
}
```

This is **at-least-once** messaging, not exactly-once. The unique event ID
makes repeated deliveries idempotent in this service's database.

## Runtime environment

`ALERT_DB_HOST`, `ALERT_DB_PORT`, `ALERT_DB_USER`, `ALERT_DB_PASSWORD`,
`ALERT_DB_NAME`, `RABBITMQ_HOST`, `RABBITMQ_PORT`, `RABBITMQ_VHOST`,
`RABBITMQ_USER`, and `RABBITMQ_PASSWORD` are injected through Compose.
Do not save real passwords in source files.
