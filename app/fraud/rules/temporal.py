"""Fraud rule: penalise large transactions that occur during nighttime hours.

Fraudsters exploit reduced monitoring windows during off-hours.  This rule
adds a moderate risk score to transactions above a threshold that occur
between NIGHTTIME_START_HOUR and NIGHTTIME_END_HOUR (local time).  Small
transactions are exempt — the pattern is only meaningful for significant
amounts.
"""

from app.core.config import settings
from app.domain.schemas.transaction import RuleResult
from app.fraud.rules.base import FraudRule, RuleContext


class NightTimeRule(FraudRule):
    """Add risk score to large transactions occurring during nighttime hours.

    The rule only triggers when:
    1. The current hour is within the configured nighttime window, AND
    2. The transaction amount exceeds the high-risk threshold.

    Night-time high-value transactions score 45 — meaningful contribution
    but not enough to flag a transaction on their own, by design.
    """

    weight: float = 1.0
    name: str = "NightTimeRule"

    async def evaluate(self, ctx: RuleContext) -> RuleResult:
        start = settings.NIGHTTIME_START_HOUR
        end = settings.NIGHTTIME_END_HOUR
        threshold = settings.AMOUNT_HIGH_RISK_THRESHOLD

        is_nighttime = start <= ctx.current_hour <= end
        is_large = ctx.transaction.amount >= threshold

        if is_nighttime and is_large:
            score = 45.0
            triggered = True
            reason = (
                f"High-value transaction (R$ {ctx.transaction.amount:,.2f}) "
                f"submitted at hour {ctx.current_hour:02d}:xx - "
                f"outside normal operating hours ({start:02d}h-{end:02d}h)."
            )
        elif is_nighttime:
            score = 10.0
            triggered = False
            reason = (
                f"Transaction submitted at hour {ctx.current_hour:02d}:xx "
                f"(nighttime window). Amount below high-risk threshold — low impact."
            )
        else:
            score = 0.0
            triggered = False
            reason = f"Transaction submitted at hour {ctx.current_hour:02d}:xx — normal hours."

        return RuleResult(rule=self.name, score=score, triggered=triggered, reason=reason)
