"""Application settings loaded from environment variables via pydantic-settings."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralised configuration for the Risk Engine API.

    All fields without defaults are required via environment variable or
    ``.env`` file. The application raises a validation error on startup if
    any required field is absent.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ── Application ───────────────────────────────────────────────────────────
    APP_NAME: str = "Risk Engine"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # ── PostgreSQL ────────────────────────────────────────────────────────────
    DATABASE_URL: str = Field(
        ...,
        description="Async PostgreSQL connection string (postgresql+asyncpg://...)",
    )

    # ── Redis ─────────────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"
    TRANSACTION_CACHE_TTL_SECONDS: int = Field(default=120, ge=1, le=3600)

    # ── RabbitMQ / Transactional Outbox ───────────────────────────────────────
    RABBITMQ_HOST: str = "rabbitmq"
    RABBITMQ_PORT: int = Field(default=5672, ge=1, le=65535)
    RABBITMQ_VHOST: str = "risk_engine"
    RABBITMQ_USER: str = ""
    RABBITMQ_PASSWORD: str = ""
    OUTBOX_BATCH_SIZE: int = Field(default=25, ge=1, le=100)
    OUTBOX_POLL_INTERVAL_SECONDS: float = Field(default=2.0, gt=0)

    # ── Fraud Engine ──────────────────────────────────────────────────────────
    # Sliding-window velocity check: max transactions per payer in the window
    VELOCITY_WINDOW_SECONDS: int = 300  # 5-minute window
    VELOCITY_MAX_TRANSACTIONS: int = 10  # max 10 tx in 5 min → HIGH risk
    VELOCITY_CRITICAL_TRANSACTIONS: int = 20  # 20+ tx in 5 min → CRITICAL

    # Amount thresholds (BRL)
    AMOUNT_HIGH_RISK_THRESHOLD: float = 5_000.00
    AMOUNT_CRITICAL_THRESHOLD: float = 20_000.00

    # Night-time window (local time hours, inclusive)
    NIGHTTIME_START_HOUR: int = 0
    NIGHTTIME_END_HOUR: int = 5

    # Webhook delivery for CRITICAL alerts (optional)
    ALERT_WEBHOOK_URL: str = ""
    ALERT_WEBHOOK_SECRET: str = ""


@lru_cache
def get_settings() -> Settings:
    """Return the singleton Settings instance, loaded once and cached.

    Returns:
        Application-wide settings object.
    """
    return Settings()


settings = get_settings()
