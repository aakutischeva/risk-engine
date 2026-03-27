"""FastAPI application entry point for the Risk Engine."""

import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

from app.api.routes.alerts import router as alerts_router
from app.api.routes.dashboard import router as dashboard_router
from app.api.routes.transactions import router as transactions_router
from app.core.config import settings
from app.core.exceptions import (
    DuplicateTransactionError,
    RiskEngineError,
    TransactionNotFoundError,
)
from app.core.logging import configure_logging
from app.infra.cache.redis_client import close_redis, get_redis
from app.infra.database.connection import init_db

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    """Manage application startup and shutdown.

    On startup: initialise the database schema and Redis connection.
    On shutdown: close the Redis connection pool gracefully.

    Args:
        app: The FastAPI application instance.
    """
    logger.info("Starting {} v{}", settings.APP_NAME, settings.APP_VERSION)
    await init_db()
    await get_redis()
    logger.info("Startup complete — ready to score transactions.")
    yield
    logger.info("Shutting down...")
    await close_redis()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "Real-time transaction risk scoring engine. "
        "Evaluates each transaction against a configurable set of fraud detection "
        "rules — velocity checks, amount thresholds, temporal patterns and "
        "behavioural signals — and returns a composite risk score with full "
        "rule-level explainability."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_timing_middleware(request: Request, call_next: Any) -> Response:
    """Attach wall-clock request duration to every response header.

    Args:
        request: Incoming HTTP request.
        call_next: ASGI callable for downstream processing.

    Returns:
        The response with ``X-Response-Time`` header attached.
    """
    start = time.perf_counter()
    response: Response = await call_next(request)
    elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
    response.headers["X-Response-Time"] = f"{elapsed_ms}ms"
    return response


@app.exception_handler(RiskEngineError)
async def handle_domain_error(_request: Request, exc: RiskEngineError) -> JSONResponse:
    """Map domain exceptions to structured HTTP responses.

    Args:
        _request: Unused incoming request.
        exc: The raised domain exception.

    Returns:
        JSON error response with the appropriate HTTP status code.
    """
    if isinstance(exc, TransactionNotFoundError):
        status_code = 404
    elif isinstance(exc, DuplicateTransactionError):
        status_code = 409
    else:
        status_code = 400
    return JSONResponse(
        status_code=status_code,
        content={"detail": str(exc), "type": type(exc).__name__},
    )


app.include_router(transactions_router)
app.include_router(alerts_router)
app.include_router(dashboard_router)


@app.get("/health", tags=["System"], summary="Application health check")
async def health() -> dict[str, str]:
    """Return the operational status of the Risk Engine.

    Returns:
        Dictionary with ``status`` and ``version`` fields.
    """
    return {"status": "ok", "version": settings.APP_VERSION}
