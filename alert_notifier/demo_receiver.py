"""Local-only demonstration webhook receiver, not a production notification sink.

Authentication: HMAC-SHA256 of the raw body. Duplicate event IDs are accepted
idempotently while this process remains running. Its in-memory ledger is NOT
durable across restarts; an actual recipient must persist deduplication state.
"""

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from typing import Literal
from uuid import UUID

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from alert_notifier.webhook import verify_signature


class NotificationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    event_id: UUID
    transaction_id: str = Field(min_length=1, max_length=64)
    risk_level: Literal["HIGH", "CRITICAL"]
    fraud_score: float = Field(ge=0, le=100)
    rules_triggered: list[str]


class ReceiverState:
    def __init__(self) -> None:
        self.seen: set[str] = set()
        self.lock = Lock()


def accept_webhook(
    body: bytes, signature: str, secret: str, state: ReceiverState
) -> tuple[int, str]:
    """Validate signature and data, then deduplicate an authenticated event."""
    if not verify_signature(body, signature, secret):
        return HTTPStatus.UNAUTHORIZED, "invalid_signature"

    try:
        payload = NotificationBody.model_validate_json(body)
    except (ValidationError, ValueError):
        return HTTPStatus.BAD_REQUEST, "invalid_payload"

    event_id = str(payload.event_id)
    with state.lock:
        if event_id in state.seen:
            logger.info("Webhook duplicate ignored | event={}", event_id)
            return HTTPStatus.OK, "duplicate"
        state.seen.add(event_id)

    logger.info(
        "Webhook accepted | transaction={} level={} event={}",
        payload.transaction_id,
        payload.risk_level,
        event_id,
    )
    return HTTPStatus.OK, "accepted"


class Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, message: str) -> None:
        body = json.dumps({"status": message}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(HTTPStatus.OK, "ok")
        else:
            self._json(HTTPStatus.NOT_FOUND, "not_found")

    def do_POST(self) -> None:
        if self.path != "/webhook":
            self._json(HTTPStatus.NOT_FOUND, "not_found")
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0
        if not 0 < content_length <= 16384:
            self._json(HTTPStatus.BAD_REQUEST, "invalid_size")
            return
        body = self.rfile.read(content_length)
        code, outcome = accept_webhook(
            body,
            self.headers.get("X-Risk-Signature", ""),
            self.server.secret,  # type: ignore[attr-defined]
            self.server.state,  # type: ignore[attr-defined]
        )
        self._json(code, outcome)

    def log_message(self, _format: str, *_args: object) -> None:
        """Avoid logging raw HTTP paths and request bodies."""


def run_receiver() -> None:
    secret = os.environ["NOTIFICATION_WEBHOOK_SECRET"]
    if len(secret) < 32:
        raise ValueError("NOTIFICATION_WEBHOOK_SECRET is too short")
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    server.secret = secret  # type: ignore[attr-defined]
    server.state = ReceiverState()  # type: ignore[attr-defined]
    logger.info("Local webhook receiver ready | port=8080")
    server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    run_receiver()
