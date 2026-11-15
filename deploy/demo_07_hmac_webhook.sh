#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(dirname "$0")/.."

MODE="${1:---check}"

case "$MODE" in
  --check|--execute|--full|--unit) ;;
  *) echo "Usage: $0 --check|--execute|--full|--unit"
     exit 2 ;;
esac

[[ $# -le 1 && $EUID -eq 0 ]] || {
  echo "Run with sudo bash"
  exit 1
}

[[ -z "${DOCKER_HOST:-}" &&
   -z "${DOCKER_CONTEXT:-}" ]] || {
  echo "[FAIL] Remote Docker not allowed"
  exit 1
}

[[ "$(docker context show)" == default ]] || exit 1

[[ "$(docker context inspect default \
  --format '{{.Endpoints.docker.Host}}')" == unix://* ]] || {
  echo "[FAIL] Docker must be local"
  exit 1
}

running() {
  [[ "$(docker inspect -f '{{.State.Running}}' \
    "$1" 2>/dev/null || true)" == true ]]
}

for c in \
  risk-engine-api \
  risk-engine-alert-postgres \
  risk-engine-outbox-publisher \
  risk-engine-alert-notifier \
  risk-engine-alert-dispatcher \
  risk-engine-notification-receiver \
  risk-engine-rabbitmq-haproxy; do

  running "$c" || {
    echo "[FAIL] Container not running: $c"
    exit 1
  }

  echo "[PASS] $c"
done

if [[ "$MODE" == "--unit" ]]; then
  if [[ -x .venv/bin/python ]]; then
    PY=.venv/bin/python
  else
    PY=python3
  fi

  "$PY" -m pytest -v \
    tests/unit/test_alert_delivery.py

  exit 0
fi

# Receiver is accessible inside the webhook network.

docker exec risk-engine-alert-dispatcher python -c '
import urllib.request

url = "http://notification-receiver:8080/health"

with urllib.request.urlopen(url, timeout=5) as r:
    assert r.status == 200

print("[PASS] Receiver /health")
'

if [[ "$MODE" == "--check" ]]; then
  echo "[PASS] DEMO 07 PREFLIGHT"
  echo "No data changed"
  exit 0
fi

[[ -t 0 ]] || {
  echo "[FAIL] Interactive terminal required"
  exit 1
}

echo

if [[ "$MODE" == "--full" ]]; then
  echo "FULL mode: up to 25 demo transactions"
  echo "will be created in PostgreSQL/Outbox."
else
  echo "EXECUTE mode: synthetic webhook data."
  echo "No PostgreSQL transactions will be created."
fi

read -r -p "Type HMAC to proceed: " ANSWER

[[ "$ANSWER" == HMAC ]] || {
  echo "Cancelled"
  exit 0
}

echo
echo "=== HMAC-SHA256 WEBHOOK TEST ==="

# Secret is accessed only inside Dispatcher container.
# It is never printed or copied to the host.

docker exec -i risk-engine-alert-dispatcher \
  python - <<'PY_HMAC'

import json
import os
import urllib.error
import urllib.request
import uuid

from alert_notifier.webhook import sign_payload

secret = os.environ["NOTIFICATION_WEBHOOK_SECRET"]

url = "http://notification-receiver:8080/webhook"

event = str(uuid.uuid4())

body = json.dumps({
    "schema_version": 1,
    "event_id": event,
    "transaction_id": "demo07-signed-" + event[:12],
    "risk_level": "HIGH",
    "fraud_score": 76.5,
    "rules_triggered": ["HighAmountRule"],
}, separators=(",", ":"), sort_keys=True).encode("utf-8")

signature = sign_payload(body, secret)

def send(data, signature_header=None):
    headers = {"Content-Type": "application/json"}

    if signature_header is not None:
        headers["X-Risk-Signature"] = signature_header

    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return (
                response.status,
                json.loads(response.read())["status"],
            )
    except urllib.error.HTTPError as exc:
        return (
            exc.code,
            json.loads(exc.read())["status"],
        )

def expect(label, got, wanted):
    assert got == wanted, (
        f"{label}: expected {wanted}, got {got}"
    )
    print(
        f"[PASS] {label}: {got[0]} {got[1]}",
        flush=True,
    )

expect(
    "Valid signature",
    send(body, signature),
    (200, "accepted"),
)

expect(
    "Duplicate delivery",
    send(body, signature),
    (200, "duplicate"),
)

expect(
    "Tampered body",
    send(body + b" ", signature),
    (401, "invalid_signature"),
)

expect(
    "Missing signature",
    send(body),
    (401, "invalid_signature"),
)

print("[PASS] HMAC-SHA256 and deduplication")

PY_HMAC

if [[ "$MODE" == "--execute" ]]; then
  echo
  echo "========================================="
  echo " DEMO 07 HMAC WEBHOOK SUCCESS"
  echo "========================================="
  exit 0
fi

echo
echo "=== FULL HIGH NOTIFICATION DELIVERY ==="

python3 - <<'PY_FULL'

import json
import subprocess
import time
import urllib.request
import uuid

from datetime import datetime, timezone

prefix = "demo07-" + uuid.uuid4().hex[:12]
payer = prefix + "@example.test"

started = datetime.now(timezone.utc).strftime(
    "%Y-%m-%dT%H:%M:%SZ"
)

def docker(*argv):
    p = subprocess.run(
        ["docker", *argv],
        text=True,
        capture_output=True,
        timeout=30,
    )

    if p.returncode != 0:
        raise RuntimeError(
            (p.stderr or p.stdout).strip()
        )

    if argv[0] == "logs":
        return (p.stdout + p.stderr).strip()

    return p.stdout.strip()

high_id = None

for i in range(1, 26):
    transaction_id = f"{prefix}-{i}"

    payload = {
        "transaction_id": transaction_id,
        "amount": 30000.0,
        "payer_key": payer,
        "receiver_key": payer,
        "transaction_type": "PIX",
    }

    req = urllib.request.Request(
        "http://127.0.0.1:8000/transactions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=20) as response:
        result = json.load(response)

    risk = result["risk_assessment"]["risk_level"]

    print(
        f"[{i:02d}] {transaction_id}: risk={risk}",
        flush=True,
    )

    if risk in ("HIGH", "CRITICAL"):
        high_id = transaction_id
        break

if not high_id:
    raise RuntimeError(
        "No HIGH/CRITICAL event within 25 attempts"
    )

print("[PASS] HIGH transaction:", high_id, flush=True)

query = (
    "SELECT status,attempts,event_id "
    "FROM alert_notifications "
    f"WHERE transaction_id='{high_id}';"
)

deadline = time.monotonic() + 120
sent = False

while time.monotonic() < deadline:
    result = docker(
        "exec",
        "risk-engine-alert-postgres",
        "psql",
        "-U", "alertsvc",
        "-d", "alertengine",
        "-AtF", "|",
        "-c", query,
    )

    if result:
        parts = result.splitlines()[0].split("|")

        if parts[0] == "SENT" and int(parts[1]) >= 1:
            sent = True

            print(
                f"[PASS] Alert DB: "
                f"status={parts[0]} "
                f"attempts={parts[1]} "
                f"event={parts[2]}",
                flush=True,
            )
            break

    time.sleep(2)

if not sent:
    raise RuntimeError(
        "No SENT notification after 120 seconds"
    )

# Check the same transaction in both service logs.

for container, phrase in (
    (
        "risk-engine-notification-receiver",
        "Webhook accepted",
    ),
    (
        "risk-engine-alert-dispatcher",
        "Webhook delivery confirmed",
    ),
):
    log = docker(
        "logs",
        "--since", started,
        container,
    )

    matched = any(
        phrase in line and high_id in line
        for line in log.splitlines()
    )

    if not matched:
        raise RuntimeError(
            f"No matching {phrase} in {container}"
        )

    print(
        f"[PASS] {container}: {phrase}",
        flush=True,
    )

print()
print("=========================================")
print(" DEMO 07 FULL SUCCESS")
print(" API -> Outbox -> RabbitMQ -> Notifier")
print(" -> Alert DB -> Dispatcher -> Receiver")
print(" HMAC-SHA256 VERIFIED")
print("=========================================")

PY_FULL
