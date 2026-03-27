"""Fraud detection engine: orchestrates rule evaluation and score aggregation.

The engine follows a simple pipeline:

1. Each registered :class:`FraudRule` is evaluated concurrently via
   ``asyncio.gather``.
2. Individual rule scores are combined into a single weighted average.
3. The composite score is mapped to a :class:`RiskLevel` label.
4. A :class:`RiskAssessment` is returned to the caller (service layer).

Adding a new rule requires only registering it in :data:`DEFAULT_RULES` —
no changes to routing, service or database layers.
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional

import redis.asyncio as aioredis
from loguru import logger

from app.domain.enums import RiskLevel
from app.domain.schemas.transaction import RiskAssessment, RuleResult, TransactionSubmit
from app.fraud.rules.amount import HighAmountRule
from app.fraud.rules.base import FraudRule, RuleContext
from app.fraud.rules.pattern import RoundAmountRule, SelfTransferRule
from app.fraud.rules.temporal import NightTimeRule
from app.fraud.rules.velocity import VelocityRule

# ── Rule registry ─────────────────────────────────────────────────────────────
# Order does not matter; all rules run concurrently.
DEFAULT_RULES: list[FraudRule] = [
    HighAmountRule(),
    VelocityRule(),
    NightTimeRule(),
    RoundAmountRule(),
    SelfTransferRule(),
]

# ── Risk level thresholds ─────────────────────────────────────────────────────
_THRESHOLDS: list[tuple[float, RiskLevel]] = [
    (80.0, RiskLevel.CRITICAL),
    (60.0, RiskLevel.HIGH),
    (30.0, RiskLevel.MEDIUM),
    (0.0,  RiskLevel.LOW),
]


def _classify(score: float) -> RiskLevel:
    """Map a composite fraud score to a qualitative risk level.

    Args:
        score: Weighted composite score in the range [0, 100].

    Returns:
        The corresponding :class:`RiskLevel` enum value.
    """
    for threshold, level in _THRESHOLDS:
        if score >= threshold:
            return level
    return RiskLevel.LOW


def _weighted_average(results: list[RuleResult], rules: list[FraudRule]) -> float:
    """Compute the weighted average score across all evaluated rules.

    Weights are normalised so that the final score always falls in [0, 100]
    regardless of the number or weights of registered rules.

    Args:
        results: Ordered list of rule results (same order as ``rules``).
        rules: Ordered list of rule instances providing the weights.

    Returns:
        Weighted composite score in the range [0, 100].
    """
    total_weight = sum(r.weight for r in rules)
    if total_weight == 0:
        return 0.0
    weighted_sum = sum(
        result.score * rule.weight
        for result, rule in zip(results, rules)
    )
    return round(weighted_sum / total_weight, 2)


class FraudEngine:
    """Stateless fraud detection engine.

    Instantiate once at application startup and reuse across requests.
    All state is held in Redis (velocity counters) and PostgreSQL
    (transaction history) — the engine itself carries no mutable state.

    Args:
        rules: List of :class:`FraudRule` instances to evaluate.
                Defaults to :data:`DEFAULT_RULES`.
    """

    def __init__(self, rules: Optional[list[FraudRule]] = None) -> None:
        self._rules: list[FraudRule] = rules if rules is not None else DEFAULT_RULES

    async def score(
        self,
        transaction: TransactionSubmit,
        redis: Optional[aioredis.Redis] = None,
        current_hour: Optional[int] = None,
    ) -> RiskAssessment:
        """Evaluate all rules against a transaction and return a risk assessment.

        All rule evaluations run concurrently via ``asyncio.gather``.  A rule
        that raises an unexpected exception is logged and contributes a score
        of 0 rather than propagating the error.

        Args:
            transaction: Transaction payload to score.
            redis: Async Redis client for velocity-based rules. Can be None
                   when running in environments without Redis (e.g. CI tests).
            current_hour: Hour of day (0–23) at evaluation time.  Defaults to
                          the actual current UTC hour when not provided.

        Returns:
            A fully populated :class:`RiskAssessment`.
        """
        if current_hour is None:
            current_hour = datetime.now(timezone.utc).hour

        ctx = RuleContext(
            transaction=transaction,
            redis=redis,
            current_hour=current_hour,
        )

        raw_results = await asyncio.gather(
            *[self._safe_evaluate(rule, ctx) for rule in self._rules],
            return_exceptions=False,
        )

        results: list[RuleResult] = list(raw_results)
        composite_score = _weighted_average(results, self._rules)
        risk_level = _classify(composite_score)

        triggered_rules = [r.rule for r in results if r.triggered]
        logger.info(
            "Transaction scored | id={} score={:.1f} level={} triggered={}",
            transaction.transaction_id,
            composite_score,
            risk_level.value,
            triggered_rules,
        )

        return RiskAssessment(
            fraud_score=composite_score,
            risk_level=risk_level,
            rules_evaluated=results,
            scored_at=datetime.now(timezone.utc),
        )

    async def _safe_evaluate(self, rule: FraudRule, ctx: RuleContext) -> RuleResult:
        """Wrap rule evaluation to prevent one failing rule from crashing the engine.

        Args:
            rule: The rule to evaluate.
            ctx: Evaluation context.

        Returns:
            The rule's result, or a zero-score result on unexpected error.
        """
        try:
            return await rule.evaluate(ctx)
        except Exception as exc:
            logger.error(f"Rule {rule.name} raised an unexpected error: {exc}")
            return RuleResult(
                rule=rule.name,
                score=0.0,
                triggered=False,
                reason=f"Rule evaluation failed: {exc}",
            )
