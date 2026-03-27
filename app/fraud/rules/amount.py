"""Fraud rule: flag transactions that exceed configured amount thresholds."""

from app.core.config import settings
from app.domain.schemas.transaction import RuleResult
from app.fraud.rules.base import FraudRule, RuleContext


class HighAmountRule(FraudRule):
    """Assign risk based on the absolute transaction amount.

    The score scales linearly between ``AMOUNT_HIGH_RISK_THRESHOLD`` and
    ``AMOUNT_CRITICAL_THRESHOLD`` and is capped at 100 above the critical
    threshold.  Transactions below the high-risk threshold score 0.

    Example thresholds (configurable via environment):
    - R$ 0      - R$ 4 999.99 -> score 0   (no risk from amount alone)
    - R$ 5 000  - R$ 19 999   -> score 30-79 (proportional)
    - R$ 20 000+             → score 100
    """

    weight: float = 1.5
    name: str = "HighAmountRule"

    async def evaluate(self, ctx: RuleContext) -> RuleResult:
        amount = ctx.transaction.amount
        high = settings.AMOUNT_HIGH_RISK_THRESHOLD
        critical = settings.AMOUNT_CRITICAL_THRESHOLD

        if amount >= critical:
            score = 100.0
            triggered = True
            reason = f"Amount R$ {amount:,.2f} exceeds critical threshold R$ {critical:,.2f}."
        elif amount >= high:
            # Linear interpolation between high and critical thresholds
            ratio = (amount - high) / (critical - high)
            score = round(30.0 + ratio * 50.0, 2)
            triggered = True
            reason = (
                f"Amount R$ {amount:,.2f} exceeds high-risk threshold "
                f"R$ {high:,.2f} (score {score:.0f}/100)."
            )
        else:
            score = 0.0
            triggered = False
            reason = f"Amount R$ {amount:,.2f} is within normal range."

        return RuleResult(rule=self.name, score=score, triggered=triggered, reason=reason)
