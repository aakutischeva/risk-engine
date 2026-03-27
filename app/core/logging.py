"""Structured logging configuration using loguru."""

import sys

from loguru import logger

from app.core.config import settings


def configure_logging() -> None:
    """Configure loguru with structured JSON output for production.

    In development (``DEBUG=true``) logs are emitted as coloured text to
    stderr.  In production they are emitted as single-line JSON records so
    that log aggregators (Datadog, Loki, CloudWatch) can parse them directly.
    """
    logger.remove()

    if settings.DEBUG:
        logger.add(
            sys.stderr,
            level=settings.LOG_LEVEL,
            format=(
                "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> — "
                "<level>{message}</level>"
            ),
            colorize=True,
        )
    else:
        logger.add(
            sys.stderr,
            level=settings.LOG_LEVEL,
            format=(
                '{{"time":"{time:YYYY-MM-DD HH:mm:ss.SSS}",'
                '"level":"{level}",'
                '"logger":"{name}",'
                '"message":"{message}"}}'
            ),
            serialize=False,
        )
