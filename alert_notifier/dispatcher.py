"""Deliver persisted alert notifications via a signed, retrying HTTP webhook.

One PostgreSQL row is locked via SKIP LOCKED for each network attempt. Holding
the lock across a short, bounded HTTP call avoids a separate crash-prone
"SENDING" lease. A crash after HTTP 2xx but before DB commit can redeliver;
receivers must therefore deduplicate by event_id (at-least-once delivery).
"""

import asyncio
import os
from datetime import timedelta

import httpx
from loguru import logger
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alert_notifier.database import database_url
from alert_notifier.models import AlertNotification, utc_now
from alert_notifier.webhook import encode_notification, sign_payload


def retry_delay(attempt: int) -> int:
    """Exponential retry schedule capped at 60 seconds (no lost work items)."""
    return min(2 ** min(attempt, 6), 60)


async def dispatch_next(
    sessions: async_sessionmaker[AsyncSession],
    client: httpx.AsyncClient,
    webhook_url: str,
    secret: str,
) -> bool:
    """Attempt one due notification; commit SENT or RETRY before returning.

    Returns False when no work is due. Multiple instances cooperate using
    PostgreSQL row locks. Errors in PostgreSQL propagate to the outer loop.
    """
    now = utc_now()
    async with sessions() as session:
        async with session.begin():
            result = await session.execute(
                select(AlertNotification)
                .where(
                    AlertNotification.status.in_(("PENDING", "RETRY")),
                    or_(
                        AlertNotification.next_attempt_at.is_(None),
                        AlertNotification.next_attempt_at <= now,
                    ),
                )
                .order_by(AlertNotification.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            notification = result.scalar_one_or_none()
            if notification is None:
                return False

            notification.attempts = int(notification.attempts or 0) + 1
            notification.last_attempt_at = now
            body = encode_notification(notification)
            headers = {
                "Content-Type": "application/json",
                "X-Risk-Signature": sign_payload(body, secret),
                "X-Event-ID": notification.event_id,
            }
            error = ""
            try:
                response = await client.post(webhook_url, content=body, headers=headers)
                if response.status_code < 200 or response.status_code >= 300:
                    error = f"HTTP {response.status_code}"
            except httpx.RequestError as exc:
                error = type(exc).__name__

            transaction_id = notification.transaction_id
            attempt = notification.attempts
            if error:
                notification.status = "RETRY"
                notification.next_attempt_at = utc_now() + timedelta(
                    seconds=retry_delay(attempt)
                )
                notification.last_error = error
            else:
                notification.status = "SENT"
                notification.sent_at = utc_now()
                notification.next_attempt_at = None
                notification.last_error = None

    # Only log success after the database transaction has committed.
    if error:
        logger.warning(
            "Webhook delivery retry scheduled | transaction={} attempt={} error={}",
            transaction_id,
            attempt,
            error,
        )
    else:
        logger.info(
            "Webhook delivery confirmed | transaction={} attempt={}",
            transaction_id,
            attempt,
        )
    return True


async def run_dispatcher() -> None:
    """Continuously send due work; retry after DB/HTTP outages."""
    webhook_url = os.environ["NOTIFICATION_WEBHOOK_URL"]
    secret = os.environ["NOTIFICATION_WEBHOOK_SECRET"]
    if not webhook_url.startswith(("http://", "https://")):
        raise ValueError("NOTIFICATION_WEBHOOK_URL must use HTTP(S)")
    if len(secret) < 32:
        raise ValueError("NOTIFICATION_WEBHOOK_SECRET is too short")

    engine = create_async_engine(database_url(), pool_pre_ping=True)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
            logger.info("Alert dispatcher ready")
            while True:
                try:
                    did_work = await dispatch_next(sessions, client, webhook_url, secret)
                    if not did_work:
                        await asyncio.sleep(1)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.error("Alert dispatcher retrying after {}", type(exc).__name__)
                    await asyncio.sleep(5)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run_dispatcher())
