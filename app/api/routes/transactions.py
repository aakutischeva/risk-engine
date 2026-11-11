"""API routes for transaction submission and retrieval."""

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    http_not_found,
)
from app.domain.enums import RiskLevel
from app.domain.schemas.transaction import (
    TransactionListResponse,
    TransactionResponse,
    TransactionSubmit,
)
from app.infra.cache.redis_client import get_redis
from app.infra.database.connection import get_session
from app.services.transaction_service import TransactionService

router = APIRouter(prefix="/transactions", tags=["Transactions"])


@router.post(
    "",
    response_model=TransactionResponse,
    status_code=status.HTTP_200_OK,
    summary="Submit a transaction for risk scoring",
    description=(
        "Evaluates the transaction against all fraud detection rules and returns "
        "a risk assessment. Idempotent: submitting the same `transaction_id` a "
        "second time returns the original result without re-scoring."
    ),
)
async def submit_transaction(
    payload: TransactionSubmit,
    session: AsyncSession = Depends(get_session),
) -> TransactionResponse:
    """Score a transaction and return the risk assessment.

    Args:
        payload: Validated transaction payload.
        session: Injected async database session.

    Returns:
        The transaction resource with the full risk assessment attached.
    """
    redis = await get_redis()
    service = TransactionService(session=session, redis=redis)
    return await service.submit(payload)


@router.get(
    "/{transaction_id}",
    response_model=TransactionResponse,
    summary="Retrieve a scored transaction by ID",
)
async def get_transaction(
    transaction_id: str,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> TransactionResponse:
    """Return a previously scored transaction.

    Args:
        transaction_id: The client-supplied unique identifier.
        session: Injected async database session.

    Raises:
        404: If the transaction does not exist.

    Returns:
        The transaction resource.
    """
    service = TransactionService(session=session, redis=await get_redis())
    result = await service.get_by_id(transaction_id)
    if result is None:
        raise http_not_found(f"Transaction '{transaction_id}' not found.")
    response.headers["X-Cache"] = "HIT" if getattr(service, "cache_hit", False) else "MISS"
    return result


@router.get(
    "",
    response_model=TransactionListResponse,
    summary="List processed transactions with optional filters",
)
async def list_transactions(
    risk_level: RiskLevel | None = Query(None, description="Filter by risk level."),
    payer_key: str | None = Query(None, description="Filter by payer key."),
    limit: int = Query(50, ge=1, le=100, description="Page size."),
    offset: int = Query(0, ge=0, description="Number of records to skip."),
    session: AsyncSession = Depends(get_session),
) -> TransactionListResponse:
    """Return a paginated list of processed transactions.

    Args:
        risk_level: Optional risk level filter.
        payer_key: Optional payer key filter.
        limit: Maximum number of records per page.
        offset: Pagination offset.
        session: Injected async database session.

    Returns:
        Paginated list with total count.
    """
    service = TransactionService(session=session)
    return await service.list_transactions(
        risk_level=risk_level,
        payer_key=payer_key,
        limit=limit,
        offset=offset,
    )
