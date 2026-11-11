"""Separate PostgreSQL connection for the notification service."""

import os

from sqlalchemy.engine import URL


def database_url() -> URL:
    """Build a URL without exposing or manually URL-encoding the password."""
    return URL.create(
        "postgresql+asyncpg",
        username=os.environ["ALERT_DB_USER"],
        password=os.environ["ALERT_DB_PASSWORD"],
        host=os.environ.get("ALERT_DB_HOST", "alert-postgres"),
        port=int(os.environ.get("ALERT_DB_PORT", "5432")),
        database=os.environ["ALERT_DB_NAME"],
    )
