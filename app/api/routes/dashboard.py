"""API route for real-time risk statistics dashboard."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import AlertStatus, RiskLevel
from app.infra.database.connection import get_session
from app.infra.database.models import AlertRecord, TransactionRecord

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


class RiskDistribution(BaseModel):
    LOW: int = 0
    MEDIUM: int = 0
    HIGH: int = 0
    CRITICAL: int = 0


class DashboardStats(BaseModel):
    """Real-time snapshot of risk engine activity."""

    total_transactions: int
    total_alerts: int
    open_alerts: int
    risk_distribution: RiskDistribution
    avg_fraud_score: float
    highest_score_today: float
    generated_at: datetime


@router.get(
    "/stats",
    response_model=DashboardStats,
    summary="Real-time risk engine statistics",
    description=(
        "Returns aggregate metrics computed directly from the database. "
        "Suitable for a monitoring dashboard."
    ),
)
async def get_stats(session: AsyncSession = Depends(get_session)) -> DashboardStats:
    """Compute and return real-time risk engine statistics.

    Args:
        session: Injected async database session.

    Returns:
        Aggregate statistics snapshot.
    """
    # Total transactions and avg score
    tx_stats = await session.execute(
        select(
            func.count(TransactionRecord.transaction_id).label("total"),
            func.coalesce(func.avg(TransactionRecord.fraud_score), 0.0).label("avg_score"),
        )
    )
    tx_row = tx_stats.one()
    total_transactions = int(tx_row.total)
    avg_score = round(float(tx_row.avg_score), 2)

    # Risk level distribution
    dist_result = await session.execute(
        select(TransactionRecord.risk_level, func.count().label("cnt"))
        .group_by(TransactionRecord.risk_level)
    )
    distribution = RiskDistribution()
    for row in dist_result:
        setattr(distribution, row.risk_level, row.cnt)

    # Alert counts
    alert_total = await session.execute(
        select(func.count(AlertRecord.alert_id))
    )
    open_alerts = await session.execute(
        select(func.count(AlertRecord.alert_id)).where(
            AlertRecord.status == AlertStatus.OPEN.value
        )
    )

    # Highest fraud score today
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )
    highest_today_result = await session.execute(
        select(func.coalesce(func.max(TransactionRecord.fraud_score), 0.0)).where(
            TransactionRecord.created_at >= today_start
        )
    )

    return DashboardStats(
        total_transactions=total_transactions,
        total_alerts=int(alert_total.scalar() or 0),
        open_alerts=int(open_alerts.scalar() or 0),
        risk_distribution=distribution,
        avg_fraud_score=avg_score,
        highest_score_today=round(float(highest_today_result.scalar() or 0.0), 2),
        generated_at=datetime.now(timezone.utc),
    )
