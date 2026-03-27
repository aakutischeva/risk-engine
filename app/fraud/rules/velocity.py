"""Fraud rule: detect abnormal transaction velocity using a Redis sliding window.

A sliding-window counter is maintained in Redis per payer key.  Every call
adds the current transaction to the sorted set (scored by Unix timestamp),
prunes entries that fall outside the configured window, and reads the count.

Using Redis sorted sets (ZADD / ZREMRANGEBYSCORE / ZCARD) makes the counter
both O(log N) and naturally expiring — no cron job or batch cleanup needed.
The key expires automatically once the last entry in the window ages out.
"""

import time

from loguru import logger

from app.core.config import settings
from app.domain.schemas.transaction import RuleResult
from app.fraud.rules.base import FraudRule, RuleContext

_REDIS_KEY_PREFIX = "risk_engine:velocity"


class VelocityRule(FraudRule):
    """Score based on how many transactions the payer sent in the recent window.

    Thresholds (configurable via environment):
    - < VELOCITY_MAX_TRANSACTIONS        → score 0   (normal)
    - VELOCITY_MAX_TRANSACTIONS          → score 50  (high)
    - ≥ VELOCITY_CRITICAL_TRANSACTIONS   → score 100 (critical)

    When Redis is unavailable the rule logs a warning and returns score 0
    rather than blocking the transaction — fail-open is intentional here.
    """

    weight: float = 2.0
    name: str = "VelocityRule"

    async def evaluate(self, ctx: RuleContext) -> RuleResult:
        if ctx.redis is None:
            logger.warning("VelocityRule: Redis unavailable — skipping velocity check.")
            return RuleResult(
                rule=self.name,
                score=0.0,
                triggered=False,
                reason="Velocity check skipped (Redis unavailable).",
            )

        window = settings.VELOCITY_WINDOW_SECONDS
        max_tx = settings.VELOCITY_MAX_TRANSACTIONS
        critical_tx = settings.VELOCITY_CRITICAL_TRANSACTIONS

        redis_key = f"{_REDIS_KEY_PREFIX}:{ctx.transaction.payer_key}"
        now_ts = time.time()
        window_start = now_ts - window

        try:
            pipe = ctx.redis.pipeline()
            # Add this transaction to the sorted set (score = timestamp)
            pipe.zadd(redis_key, {ctx.transaction.transaction_id: now_ts})
            # Remove entries that fell outside the sliding window
            pipe.zremrangebyscore(redis_key, 0, window_start)
            # Count remaining entries (transactions in the window)
            pipe.zcard(redis_key)
            # Reset TTL so the key eventually expires
            pipe.expire(redis_key, window)
            results = await pipe.execute()
            count = int(results[2])
        except Exception as exc:
            logger.warning(f"VelocityRule: Redis error — {exc}. Skipping velocity check.")
            return RuleResult(
                rule=self.name,
                score=0.0,
                triggered=False,
                reason=f"Velocity check skipped (Redis error: {exc}).",
            )

        if count >= critical_tx:
            score = 100.0
            triggered = True
            reason = (
                f"Payer sent {count} transactions in the last {window // 60} min "
                f"(critical threshold: {critical_tx})."
            )
        elif count >= max_tx:
            ratio = min((count - max_tx) / max(critical_tx - max_tx, 1), 1.0)
            score = round(50.0 + ratio * 50.0, 2)
            triggered = True
            reason = (
                f"Payer sent {count} transactions in the last {window // 60} min "
                f"(high threshold: {max_tx}, score: {score:.0f}/100)."
            )
        else:
            score = 0.0
            triggered = False
            reason = (
                f"Payer sent {count} transaction(s) in the last {window // 60} min "
                f"— within normal limits."
            )

        return RuleResult(rule=self.name, score=score, triggered=triggered, reason=reason)
