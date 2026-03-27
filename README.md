# Risk Engine

[![CI](https://github.com/jmello04/risk-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/jmello04/risk-engine/actions/workflows/ci.yml)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)](https://fastapi.tiangolo.com)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Real-time transaction risk scoring engine for fraud detection. Evaluates each transaction against a configurable set of rules — amount thresholds, velocity checks, temporal patterns, and behavioural signals — and returns a composite fraud score with full rule-level explainability.

## Overview

Financial fraud detection is a latency-sensitive problem: a scoring decision that takes 500 ms is often too slow to block a fraudulent transaction before it clears. This engine is built to score a PIX/TED/DOC transaction in **under 30 ms** at the 95th percentile by:

- Running all fraud detection rules **concurrently** via `asyncio.gather`
- Caching velocity counters in **Redis** with a sliding window (ZADD/ZREMRANGEBYSCORE/ZCARD pipeline, single round-trip)
- Using **async SQLAlchemy 2.0** with the `asyncpg` driver for non-blocking persistence
- Failing open on infrastructure unavailability (Redis down → velocity rule returns 0, DB down → HTTP 503)

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  HTTP Layer  (FastAPI + Uvicorn)                                 │
│  POST /transactions  GET /alerts  GET /dashboard/stats          │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│  Service Layer                                                   │
│  TransactionService — idempotency check → score → persist       │
│  AlertService       — CRUD + status transitions                 │
└──────────┬───────────────────────────────┬──────────────────────┘
           │                               │
┌──────────▼──────────┐       ┌────────────▼────────────────────┐
│  Fraud Engine        │       │  Repository Layer               │
│                      │       │  TransactionRepository          │
│  asyncio.gather(     │       │  AlertRepository                │
│    HighAmountRule,   │       └────────────┬────────────────────┘
│    VelocityRule,     │                    │
│    NightTimeRule,    │       ┌────────────▼────────────────────┐
│    RoundAmountRule,  │       │  Infrastructure                 │
│    SelfTransferRule  │       │  PostgreSQL (asyncpg)           │
│  )                   │       │  Redis (sliding window)         │
│  → weighted average  │       └─────────────────────────────────┘
│  → risk level        │
└─────────────────────┘
```

## Fraud Detection Rules

| Rule | Weight | Logic |
|---|---|---|
| `HighAmountRule` | 1.5 | Linear interpolation 0→100 between R$5k and R$20k thresholds |
| `VelocityRule` | 2.0 | Sliding-window counter (5 min) via Redis pipeline; scores 50–100 |
| `NightTimeRule` | 1.0 | Transactions between 00h–05h above R$1k score 45; small amounts score 10 |
| `RoundAmountRule` | 0.8 | Exact multiples of R$100 above R$500 score 25 (structuring signal) |
| `SelfTransferRule` | 1.2 | Identical payer/receiver keys (normalised, case-insensitive) score 60 |

**Composite score** = weighted average of all rule scores
**Risk level** = LOW (0–29) / MEDIUM (30–59) / HIGH (60–79) / CRITICAL (80–100)
Transactions scored HIGH or CRITICAL automatically generate a fraud alert.

## Tech Stack

| Layer | Technology |
|---|---|
| Runtime | Python 3.12 |
| Web framework | FastAPI 0.115 + Uvicorn |
| Database | PostgreSQL 16 via async SQLAlchemy 2.0 + asyncpg |
| Cache | Redis 7 (hiredis) |
| Migrations | Alembic |
| Validation | Pydantic v2 |
| Logging | Loguru |
| Linting | Ruff |
| Tests | pytest + pytest-asyncio + httpx |
| Container | Docker (multi-stage) + Docker Compose |
| CI | GitHub Actions |

## Getting Started

### Prerequisites

- Docker and Docker Compose v2
- Python 3.12 (for local development without Docker)

### With Docker Compose

```bash
# 1. Clone and configure
git clone https://github.com/jmello04/risk-engine.git
cd risk-engine
cp .env.example .env
# Edit .env and set POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB

# 2. Start all services
docker compose up --build

# 3. Apply database migrations
docker compose exec api alembic upgrade head

# API is now available at http://localhost:8000
# Interactive docs: http://localhost:8000/docs
```

### Local Development

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env               # configure DATABASE_URL and REDIS_URL

alembic upgrade head
uvicorn app.main:app --reload
```

## API Reference

### Score a Transaction

```http
POST /transactions
Content-Type: application/json

{
  "transaction_id": "tx-20250101-0001",
  "amount": 12500.00,
  "payer_key": "alice@bank.com",
  "receiver_key": "bob@bank.com",
  "transaction_type": "PIX"
}
```

**Response 200**

```json
{
  "transaction_id": "tx-20250101-0001",
  "fraud_score": 52.3,
  "risk_level": "MEDIUM",
  "rules_evaluated": [
    {"rule": "HighAmountRule", "score": 49.3, "triggered": true, "reason": "Amount R$12500.00 interpolated to score 49.3"},
    {"rule": "VelocityRule",   "score": 0.0,  "triggered": false, "reason": "1 transaction(s) in window"},
    {"rule": "NightTimeRule",  "score": 0.0,  "triggered": false, "reason": "Daytime transaction"},
    {"rule": "RoundAmountRule","score": 0.0,  "triggered": false, "reason": "Amount is not a round number"},
    {"rule": "SelfTransferRule","score": 0.0, "triggered": false, "reason": "Payer and receiver keys differ"}
  ],
  "alert_id": null,
  "alert_status": null
}
```

| Status | Meaning |
|---|---|
| 200 | Transaction scored successfully |
| 409 | Duplicate `transaction_id` (idempotency guard) |
| 422 | Validation error (negative amount, unknown transaction type, etc.) |

### Other Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/alerts` | List alerts (filters: `status`, `risk_level`, pagination) |
| `GET` | `/alerts/{id}` | Get a single alert |
| `PATCH` | `/alerts/{id}` | Update alert status (OPEN → ACKNOWLEDGED → RESOLVED / FALSE_POSITIVE) |
| `GET` | `/dashboard/stats` | Aggregate metrics: totals, risk distribution, avg score |
| `GET` | `/health` | Liveness probe |
| `GET` | `/docs` | Swagger UI |

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | Yes | — | Async PostgreSQL DSN (`postgresql+asyncpg://...`) |
| `REDIS_URL` | No | `redis://localhost:6379/0` | Redis connection URL |
| `VELOCITY_WINDOW_SECONDS` | No | `300` | Sliding window duration for velocity checks |
| `VELOCITY_MAX_TRANSACTIONS` | No | `10` | Threshold for HIGH velocity score |
| `VELOCITY_CRITICAL_TRANSACTIONS` | No | `20` | Threshold for CRITICAL velocity score |
| `AMOUNT_HIGH_RISK_THRESHOLD` | No | `5000.0` | Amount (BRL) that begins scoring HIGH |
| `AMOUNT_CRITICAL_THRESHOLD` | No | `20000.0` | Amount (BRL) that scores 100 |
| `NIGHTTIME_START_HOUR` | No | `0` | Start of nighttime window (inclusive, 24h) |
| `NIGHTTIME_END_HOUR` | No | `5` | End of nighttime window (exclusive, 24h) |
| `LOG_LEVEL` | No | `INFO` | Loguru log level |

## Running Tests

```bash
# Unit tests only (no infrastructure needed)
pytest tests/unit/ -v

# Integration tests (requires running Postgres + Redis)
pytest tests/integration/ -v

# Full suite with coverage report
pytest --cov=app --cov-report=term-missing
```

## Project Structure

```
risk-engine/
├── app/
│   ├── api/routes/          # FastAPI routers (transactions, alerts, dashboard)
│   ├── core/                # Config, exceptions, logging
│   ├── domain/
│   │   ├── enums.py         # RiskLevel, TransactionType, AlertStatus
│   │   └── schemas/         # Pydantic request/response models
│   ├── fraud/
│   │   ├── engine.py        # FraudEngine — concurrent rule evaluation
│   │   └── rules/           # Individual fraud detection rules
│   ├── infra/
│   │   ├── cache/           # Redis client with graceful degradation
│   │   └── database/        # SQLAlchemy models, connection, repositories
│   └── services/            # TransactionService, AlertService
├── alembic/                 # Database migrations
├── tests/
│   ├── unit/                # Isolated rule and engine tests
│   └── integration/         # API endpoint tests
├── .github/workflows/ci.yml
├── docker-compose.yml
├── Dockerfile
└── pyproject.toml
```

## License

[MIT](LICENSE)
