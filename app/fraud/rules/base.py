"""Abstract base class for all fraud detection rules.

Each rule is a self-contained strategy that receives a transaction snapshot
and optional Redis context, computes a fraud score in the range [0, 100], and
returns a structured result.  The engine collects results from all registered
rules and combines them into a final weighted score.

Rules are deliberately stateless with respect to the database — the only
external dependency allowed is Redis (for velocity counters and caches).
This makes each rule independently testable without database fixtures.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

import redis.asyncio as aioredis

from app.domain.schemas.transaction import RuleResult, TransactionSubmit


@dataclass(frozen=True)
class RuleContext:
    """Immutable context passed to every rule during evaluation.

    Attributes:
        transaction: The transaction being evaluated.
        redis: Optional async Redis client. Rules that do not require Redis
               (e.g. amount threshold) can safely ignore this field.
        current_hour: Hour of the day (0-23) at evaluation time. Injected
                      rather than read from ``datetime.now()`` to keep rules
                      deterministic in tests.
    """

    transaction: TransactionSubmit
    redis: aioredis.Redis | None
    current_hour: int


class FraudRule(ABC):
    """Strategy interface for a single fraud detection rule.

    Subclasses must implement :meth:`evaluate` and declare :attr:`weight`
    so the engine can compute a weighted composite score across all rules.

    Attributes:
        weight: Relative importance of this rule in the composite score.
                A rule with ``weight=2.0`` contributes twice as much as one
                with ``weight=1.0``.  Weights across all rules are normalised
                by the engine before aggregation.
        name: Human-readable rule identifier used in API responses and logs.
    """

    weight: float = 1.0
    name: str = "unnamed_rule"

    @abstractmethod
    async def evaluate(self, ctx: RuleContext) -> RuleResult:
        """Score a transaction against this rule.

        Args:
            ctx: Immutable evaluation context containing the transaction,
                 an optional Redis client and the current hour of the day.

        Returns:
            A :class:`RuleResult` with the computed score (0-100),
            a triggered flag, and a human-readable reason string.
        """
        ...
