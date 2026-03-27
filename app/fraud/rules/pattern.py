"""Fraud rules based on behavioural patterns in the transaction data.

These rules analyse the transaction payload itself for suspicious patterns
that do not require external data lookups:

- ``RoundAmountRule``: Exactly round amounts (1000.00, 5000.00) are
  disproportionately common in mule transactions and money laundering.
- ``SelfTransferRule``: A payer key identical to the receiver key suggests
  account testing or internal fund movement prior to extraction.
"""

import math

from app.domain.schemas.transaction import RuleResult
from app.fraud.rules.base import FraudRule, RuleContext


class RoundAmountRule(FraudRule):
    """Penalise suspiciously round transaction amounts.

    Legitimate payments (bills, invoices, purchases) rarely land on exact
    round numbers.  Fraudulent transactions frequently use amounts like
    R$ 1 000, R$ 5 000 or R$ 10 000 — either because they are testing limits
    or because they are structured to avoid detection thresholds.

    The rule checks for amounts that are exact multiples of 100 and above
    R$ 500.  Small round amounts (R$ 100, R$ 200) are common for legitimate
    bill payments and are excluded.
    """

    weight: float = 0.8
    name: str = "RoundAmountRule"

    _MIN_SUSPICIOUS_AMOUNT: float = 500.0
    _ROUND_MULTIPLE: float = 100.0

    async def evaluate(self, ctx: RuleContext) -> RuleResult:
        amount = ctx.transaction.amount

        is_round = amount >= self._MIN_SUSPICIOUS_AMOUNT and math.isclose(
            amount % self._ROUND_MULTIPLE, 0.0, abs_tol=0.01
        )

        if is_round:
            score = 25.0
            triggered = True
            reason = (
                f"Amount R$ {amount:,.2f} is a suspicious exact multiple of "
                f"R$ {self._ROUND_MULTIPLE:.0f}."
            )
        else:
            score = 0.0
            triggered = False
            reason = f"Amount R$ {amount:,.2f} shows no suspicious rounding pattern."

        return RuleResult(rule=self.name, score=score, triggered=triggered, reason=reason)


class SelfTransferRule(FraudRule):
    """Flag transactions where payer and receiver keys are identical.

    A self-transfer can legitimately occur between own accounts, but it is
    also a common pattern in automated fraud scripts that probe accounts.
    This rule contributes a moderate score when detected.
    """

    weight: float = 1.2
    name: str = "SelfTransferRule"

    async def evaluate(self, ctx: RuleContext) -> RuleResult:
        payer = ctx.transaction.payer_key.strip().lower()
        receiver = ctx.transaction.receiver_key.strip().lower()

        if payer == receiver:
            score = 60.0
            triggered = True
            reason = (
                "Payer key and receiver key are identical "
                f"('{ctx.transaction.payer_key}'). Possible account probing."
            )
        else:
            score = 0.0
            triggered = False
            reason = "Payer and receiver keys are distinct — no self-transfer detected."

        return RuleResult(rule=self.name, score=score, triggered=triggered, reason=reason)
