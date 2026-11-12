# Risk Engine

[![CI](https://github.com/aakutischeva/risk-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/aakutischeva/risk-engine/actions/workflows/ci.yml)
![Python 3.12](https://img.shields.io/badge/Python-3.12-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Real-time transaction risk scoring engine for fraud detection, extended into a microservice-based system. It evaluates payments against configurable rules, stores explainable risk assessments, publishes transaction events reliably through RabbitMQ, and delivers HMAC-signed alerts through an independent notification service.

## Overview

Risk Engine processes PIX/TED/DOC and other supported payment types using five explainable fraud detection rules. The original scoring engine has been retained, while its infrastructure has been extended for asynchronous event delivery, separate persistence and network isolation:

- Running fraud detection rules concurrently with `asyncio.gather` and calculating a weighted composite score.
- Using Redis sliding-window velocity counters and Cache-Aside reads for transaction details (`X-Cache: MISS` / `HIT`).
- Persisting each transaction and its `transaction.scored` event atomically in PostgreSQL through **Transactional Outbox**.
- Publishing events to RabbitMQ (`risk.events` → `risk.alerts`) and consuming them in a separate Alert Notifier service.
- Recording processed events and pending notifications in an independent PostgreSQL database; delivering alerts through HMAC-SHA256-signed HTTP webhooks with retries and deduplication.
- Segmenting the deployment across five Docker bridge networks, with additional host-side `iptables` rules for the demonstration environment.

The HTTP API returns the risk assessment without waiting for the webhook delivery. The system is a **working educational prototype**, not a production-ready banking platform; no production latency or availability SLA is claimed.

This version is based on [jmello04/risk-engine](https://github.com/jmello04/risk-engine). The original Fraud Engine, rules and core API serve as the foundation; messaging, the notification pipeline, caching extensions and network security are project additions.

## Architecture

```text
┌───────────────────────────────────────────────────────────────────────────────┐
│ HTTP Layer (FastAPI + Uvicorn)                                                │
│ POST /transactions  GET /transactions/{id}  GET /alerts  GET /dashboard/stats  │
└─────────────────────────────────┬─────────────────────────────────────────────┘
                                  │
┌─────────────────────────────────▼─────────────────────────────────────────────┐
│ Service Layer                                                                 │
│ TransactionService: idempotency check → score → persist + outbox event        │
│ AlertService: alerts CRUD + status transitions                                │
└──────────────┬──────────────────────────────────────┬─────────────────────────┘
               │                                      │
┌──────────────▼─────────────────┐    ┌───────────────▼────────────────────────┐
│ Fraud Engine                   │    │ Infrastructure                        │
│ asyncio.gather(                │    │ PostgreSQL #1:                        │
│   HighAmountRule,              │    │   transactions / alerts / outbox_events│
│   VelocityRule,                │    │ Redis: velocity + Cache-Aside         │
│   NightTimeRule,               │    └───────────────┬────────────────────────┘
│   RoundAmountRule,             │                    │
│   SelfTransferRule             │          ┌─────────▼──────────┐
│ ) → weighted score → risk level │          │ Outbox Publisher   │
└────────────────────────────────┘          └─────────┬──────────┘
                                                     │ transaction.scored
                                           ┌─────────▼──────────┐
                                           │ RabbitMQ           │
                                           │ risk.events        │
                                           │ risk.alerts        │
                                           └─────────┬──────────┘
                                                     │
                                           ┌─────────▼──────────┐
                                           │ Alert Notifier     │
                                           └─────────┬──────────┘
                                                     │
                                           ┌─────────▼──────────┐
                                           │ PostgreSQL #2      │
                                           │ processed_events   │
                                           │ alert_notifications│
                                           └─────────┬──────────┘
                                                     │
                                           ┌─────────▼──────────┐
                                           │ Alert Dispatcher   │
                                           └─────────┬──────────┘
                                                     │ HTTP + HMAC-SHA256
                                           ┌─────────▼──────────┐
                                           │ Webhook Receiver   │
                                           └────────────────────┘
```

The main API also uses Redis for transaction reads and velocity checks. For each **new** transaction, the service writes the result and an outbox event in the same database transaction. `outbox-publisher` sends the event with publisher confirms; `alert-notifier` records it in `processed_events`, and high-risk notifications are processed by `alert-dispatcher`. The pipeline uses **at-least-once** event delivery, so consumers deduplicate by `event_id`.

Docker Compose defines **11 services**: `api`, `postgres`, `redis`, `migrate`, `outbox-publisher`, `rabbitmq`, `alert-notifier`, `alert-postgres`, `alert-migrate`, `alert-dispatcher`, and `notification-receiver`. The two migration services are one-shot jobs; the other nine are long-running containers.

The services run on five dedicated networks (`tx_db`, `tx_cache`, `events`, `alert_db`, `webhook`). Additional host firewall configuration is documented in [`tools/NETWORK_SEGMENTATION.md`](tools/NETWORK_SEGMENTATION.md).

## Fraud Detection Rules

| Rule | Weight | Logic |
|------|-------:|-------|
| `HighAmountRule` | 1.5 | Amount-based scoring between the configured R$5k and R$20k thresholds |
| `VelocityRule` | 2.0 | Per-payer Redis sliding-window counter (5 minutes by default) |
| `NightTimeRule` | 1.0 | Higher-risk transactions during configured nighttime hours |
| `RoundAmountRule` | 0.8 | Exact multiples of R$100 above R$500 |
| `SelfTransferRule` | 1.2 | Identical normalized payer and receiver identifiers |

**Composite score** = weighted average of the five rule scores, from 0 to 100. **Risk level** = `LOW` (0–29.99), `MEDIUM` (30–59.99), `HIGH` (60–79.99), `CRITICAL` (80–100).

`HIGH` and `CRITICAL` transactions generate fraud alerts in the main database and notification records in the alert service. A `transaction.scored` event is emitted for **every newly scored transaction**, regardless of risk level.

## Tech Stack

| Layer | Technology |
|-------|------------|
| Runtime | Python 3.12 |
| Web framework | FastAPI 0.115 + Uvicorn |
| Databases | Two PostgreSQL 16 instances; SQLAlchemy 2.0 + asyncpg |
| Cache | Redis 7 (hiredis), sliding-window counters and Cache-Aside |
| Message broker | RabbitMQ 4, aio-pika, Transactional Outbox |
| Migrations | Alembic (separate main and notification migrations) |
| Validation | Pydantic v2 |
| Webhook security | HMAC-SHA256, delivery retries, duplicate detection |
| Network security | Docker bridge networks, `iptables` (`DOCKER-USER`) |
| Logging | Loguru |
| Linting and formatting | Ruff |
| Tests | pytest + pytest-asyncio + httpx |
| Containers | Docker + Docker Compose v2 |
| CI | GitHub Actions |

## Getting Started

### Prerequisites

- Docker Engine and Docker Compose v2.
- Git.
- Python 3.12 for local development without Docker.
- Free local port `8000` (and optionally `15672` for RabbitMQ Management).

### With Docker Compose

```bash
# 1. Clone and configure
git clone https://github.com/aakutischeva/risk-engine.git
cd risk-engine
cp .env.example .env

# 2. Edit .env and supply values for ALL of these variables:
# POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
# RABBITMQ_USER, RABBITMQ_PASSWORD
# ALERT_POSTGRES_USER, ALERT_POSTGRES_PASSWORD, ALERT_POSTGRES_DB
# NOTIFICATION_WEBHOOK_SECRET

# 3. Start the whole stack (including one-shot migrations)
docker compose up -d --build

# 4. Verify containers and API
docker compose ps -a
curl -fsS http://127.0.0.1:8000/health
```

For clarity, the required service credentials in `.env` can be represented as follows (**replace every placeholder with a strong value**):

```dotenv
POSTGRES_USER=risk_engine
POSTGRES_PASSWORD=REPLACE_ME
POSTGRES_DB=risk_engine

RABBITMQ_USER=risk_engine
RABBITMQ_PASSWORD=REPLACE_ME

ALERT_POSTGRES_USER=alert_engine
ALERT_POSTGRES_PASSWORD=REPLACE_ME
ALERT_POSTGRES_DB=alert_notifications

NOTIFICATION_WEBHOOK_SECRET=REPLACE_WITH_RANDOM_SECRET
```

The repository's `.env.example` may need to be supplemented with the new variables shown above. Compose constructs the internal PostgreSQL connection strings and service addresses automatically. Never commit a populated `.env` file.

The `migrate` and `alert-migrate` containers should finish with exit code `0`. **Do not run the original manual `docker compose exec api alembic upgrade head` step:** migration jobs are now part of Compose startup.

- API: <http://127.0.0.1:8000>
- Interactive docs: <http://127.0.0.1:8000/docs>
- Health endpoint: <http://127.0.0.1:8000/health>
- RabbitMQ Management: <http://127.0.0.1:15672> (credentials from `.env`)

Only the API and RabbitMQ Management ports are bound to the host's loopback interface. The demo Webhook Receiver is available to the Dispatcher **inside** the `webhook` network and is not exposed on the host.

For troubleshooting:

```bash
docker compose logs --tail=50 api outbox-publisher rabbitmq
docker compose logs --tail=50 alert-notifier alert-dispatcher notification-receiver
```

Host firewall rules are **not installed by `docker compose up`**. The Ubuntu VM demo uses [`tools/risk_engine_firewall.sh`](tools/risk_engine_firewall.sh) and a separately configured systemd service. Review the script and host network configuration before applying it elsewhere.

### Local Development

```bash
python3.12 -m venv .venv
source .venv/bin/activate                 # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env                     # Configure DATABASE_URL and REDIS_URL
alembic upgrade head
uvicorn app.main:app --reload
```

Local API development requires reachable PostgreSQL and Redis. Running the API alone does **not** start the RabbitMQ publisher or notification services; use Docker Compose for the complete system.

## API Reference

### Score a Transaction

```http
POST /transactions
Content-Type: application/json
```

```json
{
  "transaction_id": "tx-demo-001",
  "amount": 1500.00,
  "payer_key": "alice@example.test",
  "receiver_key": "bob@example.test",
  "transaction_type": "PIX"
}
```

**Response 200** (example; scores and timestamps depend on the current rules and context):

```json
{
  "transaction_id": "tx-demo-001",
  "amount": 1500.0,
  "payer_key": "alice@example.test",
  "receiver_key": "bob@example.test",
  "transaction_type": "PIX",
  "risk_assessment": {
    "fraud_score": 3.08,
    "risk_level": "LOW",
    "rules_evaluated": [
      {"rule": "HighAmountRule", "score": 0.0, "triggered": false, "reason": "Amount is within normal range"},
      {"rule": "VelocityRule", "score": 0.0, "triggered": false, "reason": "Payer activity is within normal limits"},
      {"rule": "NightTimeRule", "score": 0.0, "triggered": false, "reason": "Normal hours"},
      {"rule": "RoundAmountRule", "score": 25.0, "triggered": true, "reason": "Amount is an exact multiple of R$ 100"},
      {"rule": "SelfTransferRule", "score": 0.0, "triggered": false, "reason": "Payer and receiver differ"}
    ],
    "scored_at": "2026-11-11T19:18:16.690016Z"
  },
  "created_at": "2026-11-11T19:18:16.690371",
  "idempotent": false
}
```

Repeat the POST with the same `transaction_id` to receive the original assessment with `"idempotent": true`. The original score and `scored_at` remain unchanged; the database retains one transaction record.

| Status | Meaning |
|--------|---------|
| `200` | Transaction scored or existing transaction returned idempotently |
| `404` | Requested transaction or alert not found |
| `422` | Request validation error |

Unlike the original version, a repeated `POST /transactions` is **not** documented as HTTP 409: the current implementation returns HTTP 200 with the saved result. `409` remains a possible domain error code, but is not the normal sequential duplicate-POST behavior.

### Other Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/transactions/{transaction_id}` | Retrieve assessment by ID (`X-Cache: MISS` / `HIT`) |
| `GET` | `/transactions` | List transactions (filters, pagination) |
| `GET` | `/alerts` | List fraud alerts (status/risk filters, pagination) |
| `GET` | `/alerts/{alert_id}` | Retrieve one alert |
| `PATCH` | `/alerts/{alert_id}` | Update alert status (OPEN → ACKNOWLEDGED → RESOLVED / FALSE_POSITIVE) |
| `GET` | `/dashboard/stats` | Aggregate metrics: totals, risk distribution, average score |
| `GET` | `/health` | Liveness endpoint |
| `GET` | `/docs` | Swagger UI |

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Yes (Compose) | — | Main PostgreSQL credentials and database |
| `ALERT_POSTGRES_USER`, `ALERT_POSTGRES_PASSWORD`, `ALERT_POSTGRES_DB` | Yes (Compose) | — | Independent alert PostgreSQL credentials and database |
| `RABBITMQ_USER`, `RABBITMQ_PASSWORD` | Yes (Compose) | — | RabbitMQ credentials (vhost `risk_engine`) |
| `NOTIFICATION_WEBHOOK_SECRET` | Yes (Compose) | — | Shared HMAC signing and verification secret |
| `DATABASE_URL` | Yes (local API) | — | Async PostgreSQL DSN (`postgresql+asyncpg://...`); overridden by Compose |
| `REDIS_URL` | No | `redis://localhost:6379/0` | Redis connection URL; overridden by Compose |
| `TRANSACTION_CACHE_TTL_SECONDS` | No | `120` | Transaction Cache-Aside TTL |
| `OUTBOX_BATCH_SIZE` | No | `25` | Outbox polling batch size |
| `OUTBOX_POLL_INTERVAL_SECONDS` | No | `2.0` | Publisher polling interval (seconds) |
| `VELOCITY_WINDOW_SECONDS` | No | `300` | Sliding-window duration (seconds) |
| `VELOCITY_MAX_TRANSACTIONS` | No | `10` | High-velocity threshold |
| `VELOCITY_CRITICAL_TRANSACTIONS` | No | `20` | Critical-velocity threshold |
| `AMOUNT_HIGH_RISK_THRESHOLD` | No | `5000.0` | Amount above which scoring increases (BRL) |
| `AMOUNT_CRITICAL_THRESHOLD` | No | `20000.0` | Amount giving a maximum amount-rule score (BRL) |
| `NIGHTTIME_START_HOUR` | No | `0` | Beginning of nighttime window |
| `NIGHTTIME_END_HOUR` | No | `5` | End of nighttime window |
| `LOG_LEVEL` | No | `INFO` | Logging verbosity |

Use `.env` for local secrets; do not place production credentials directly in the README or source files.

## Running Tests

```bash
# Unit tests (including Fraud Engine, Outbox, cache and signed webhooks)
python -m pytest tests/unit/ -v

# API integration tests (some isolated infrastructure tests may be skipped)
python -m pytest tests/integration/ -v

# Full test suite
python -m pytest -q

# Ruff lint and formatting checks (same checks used in CI)
python -m ruff check .
python -m ruff format --check .
```

`test_postgres_isolated.py` and `test_redis_velocity_isolated.py` have dedicated test-environment requirements; absent their corresponding test configuration, they may be skipped. Do not point isolated integration tests at the running application's production database.

The HMAC delivery suite also tests accepted signatures, duplicate deliveries, modified-payload rejection and retry behavior. The project was manually smoke-tested with a `HIGH` risk transaction (`fraud_score = 60.31`), an event recorded in `processed_events` and a delivered notification (`SENT`, one attempt).

## Project Structure

```text
risk-engine/
├── app/
│   ├── api/routes/                   # FastAPI routes: transactions, alerts, dashboard
│   ├── core/                         # Configuration, exceptions, logging
│   ├── domain/                       # Risk levels and request/response schemas
│   ├── fraud/
│   │   ├── engine.py                 # Concurrent rule evaluation and scoring
│   │   └── rules/                   # Five fraud detection rules
│   ├── infra/
│   │   ├── cache/                   # Redis client and transaction Cache-Aside
│   │   └── database/                # SQLAlchemy models and repositories
│   ├── services/                     # TransactionService and AlertService
│   └── workers/outbox_publisher.py   # Reliable PostgreSQL-to-RabbitMQ publisher
├── alert_notifier/
│   ├── consumer.py                   # RabbitMQ consumer and event deduplication
│   ├── dispatcher.py                 # Webhook delivery and retry scheduling
│   ├── demo_receiver.py              # Local demo webhook endpoint
│   ├── webhook.py                    # HMAC-SHA256 signing and verification
│   ├── database.py, models.py        # Alert database models and session setup
│   └── migrations/                   # Independent alert database migrations
├── alembic/                          # Main PostgreSQL migrations, including Outbox
├── tests/
│   ├── unit/                         # Rules, engine, cache, events and HMAC tests
│   └── integration/                  # API and isolated DB/Redis tests
├── tools/
│   ├── risk_engine_firewall.sh       # Host firewall rules
│   └── NETWORK_SEGMENTATION.md       # Network topology and validation
├── .github/workflows/ci.yml         # GitHub Actions
├── docker-compose.yml               # 11 services, five bridge networks
├── Dockerfile
├── requirements.txt
└── pyproject.toml
```

## License

[MIT License](LICENSE). This project is an adaptation of [jmello04/risk-engine](https://github.com/jmello04/risk-engine); the original license and attribution are retained. The additional microservice infrastructure, delivery pipeline, caching and network-hardening work is documented in this repository.
