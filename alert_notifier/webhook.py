"""Minimal signed JSON contract shared by dispatcher and demo receiver."""

import hashlib
import hmac
import json

from alert_notifier.models import AlertNotification


def encode_notification(notification: AlertNotification) -> bytes:
    """Return stable UTF-8 bytes to sign and transmit without sensitive payer data."""
    body = {
        "schema_version": 1,
        "event_id": notification.event_id,
        "transaction_id": notification.transaction_id,
        "risk_level": notification.risk_level,
        "fraud_score": notification.fraud_score,
        "rules_triggered": notification.rules_triggered,
    }
    return json.dumps(
        body, separators=(",", ":"), sort_keys=True, allow_nan=False
    ).encode("utf-8")


def sign_payload(body: bytes, secret: str) -> str:
    """HMAC authentication of exact request bytes, including event ID."""
    if len(secret) < 32:
        raise ValueError("Webhook secret must contain at least 32 characters")
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(body: bytes, signature: str, secret: str) -> bool:
    """Constant-time verification; unknown signatures never reach processing."""
    return hmac.compare_digest(sign_payload(body, secret), signature)
