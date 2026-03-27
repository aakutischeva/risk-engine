"""Unit tests for the FraudEngine orchestration and scoring logic."""

import pytest

from app.domain.enums import RiskLevel, TransactionType
from app.domain.schemas.transaction import RuleResult, TransactionSubmit
from app.fraud.engine import FraudEngine, _classify, _weighted_average
from app.fraud.rules.base import FraudRule, RuleContext

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_transaction(**kwargs) -> TransactionSubmit:
    defaults = {
        "transaction_id": "engine-test-001",
        "amount": 100.00,
        "payer_key": "payer@test.com",
        "receiver_key": "receiver@test.com",
        "transaction_type": TransactionType.PIX,
    }
    defaults.update(kwargs)
    return TransactionSubmit(**defaults)


class _ConstantRule(FraudRule):
    """Test rule that always returns a fixed score."""

    def __init__(self, score: float, weight: float = 1.0, name: str = "ConstantRule") -> None:
        self._score = score
        self.weight = weight
        self.name = name

    async def evaluate(self, ctx: RuleContext) -> RuleResult:
        return RuleResult(
            rule=self.name,
            score=self._score,
            triggered=self._score > 0,
            reason=f"Constant score {self._score}",
        )


class _FailingRule(FraudRule):
    """Test rule that always raises an exception."""

    weight: float = 1.0
    name: str = "FailingRule"

    async def evaluate(self, ctx: RuleContext) -> RuleResult:
        raise RuntimeError("Simulated rule failure")


# ─── _classify ────────────────────────────────────────────────────────────────


class TestClassify:
    def test_score_below_30_is_low(self):
        assert _classify(0.0) == RiskLevel.LOW
        assert _classify(29.9) == RiskLevel.LOW

    def test_score_30_to_59_is_medium(self):
        assert _classify(30.0) == RiskLevel.MEDIUM
        assert _classify(59.9) == RiskLevel.MEDIUM

    def test_score_60_to_79_is_high(self):
        assert _classify(60.0) == RiskLevel.HIGH
        assert _classify(79.9) == RiskLevel.HIGH

    def test_score_80_plus_is_critical(self):
        assert _classify(80.0) == RiskLevel.CRITICAL
        assert _classify(100.0) == RiskLevel.CRITICAL


# ─── _weighted_average ────────────────────────────────────────────────────────


class TestWeightedAverage:
    def test_equal_weights_produces_arithmetic_mean(self):
        rules = [_ConstantRule(40.0), _ConstantRule(60.0)]
        results = [
            RuleResult(rule="r1", score=40.0, triggered=True, reason=""),
            RuleResult(rule="r2", score=60.0, triggered=True, reason=""),
        ]
        avg = _weighted_average(results, rules)
        assert avg == 50.0

    def test_higher_weight_dominates(self):
        rules = [_ConstantRule(100.0, weight=3.0), _ConstantRule(0.0, weight=1.0)]
        results = [
            RuleResult(rule="r1", score=100.0, triggered=True, reason=""),
            RuleResult(rule="r2", score=0.0, triggered=False, reason=""),
        ]
        avg = _weighted_average(results, rules)
        # 3/4 * 100 + 1/4 * 0 = 75
        assert avg == 75.0

    def test_empty_rules_returns_zero(self):
        assert _weighted_average([], []) == 0.0


# ─── FraudEngine ──────────────────────────────────────────────────────────────


class TestFraudEngine:
    @pytest.mark.asyncio
    async def test_low_risk_transaction_scores_low(self):
        engine = FraudEngine(rules=[_ConstantRule(10.0)])
        tx = _make_transaction(amount=50.00)
        result = await engine.score(tx, redis=None, current_hour=10)
        assert result.risk_level == RiskLevel.LOW
        assert result.fraud_score == 10.0

    @pytest.mark.asyncio
    async def test_critical_score_produces_critical_level(self):
        engine = FraudEngine(rules=[_ConstantRule(90.0)])
        tx = _make_transaction()
        result = await engine.score(tx, redis=None, current_hour=10)
        assert result.risk_level == RiskLevel.CRITICAL

    @pytest.mark.asyncio
    async def test_failing_rule_does_not_crash_engine(self):
        engine = FraudEngine(rules=[_FailingRule(), _ConstantRule(50.0)])
        tx = _make_transaction()
        result = await engine.score(tx, redis=None, current_hour=10)
        # FailingRule contributes 0, ConstantRule contributes 50 → avg 25 → LOW/MEDIUM
        assert result.fraud_score == 25.0
        assert result.risk_level == RiskLevel.LOW

    @pytest.mark.asyncio
    async def test_rules_evaluated_list_contains_all_rules(self):
        engine = FraudEngine(rules=[_ConstantRule(10.0, name="A"), _ConstantRule(20.0, name="B")])
        tx = _make_transaction()
        result = await engine.score(tx, redis=None, current_hour=10)
        rule_names = [r.rule for r in result.rules_evaluated]
        assert "A" in rule_names
        assert "B" in rule_names

    @pytest.mark.asyncio
    async def test_score_is_bounded_between_0_and_100(self):
        engine = FraudEngine(rules=[_ConstantRule(100.0)])
        tx = _make_transaction()
        result = await engine.score(tx, redis=None)
        assert 0.0 <= result.fraud_score <= 100.0

    @pytest.mark.asyncio
    async def test_real_default_rules_run_without_redis(self):
        engine = FraudEngine()
        tx = _make_transaction(amount=100.00)
        result = await engine.score(tx, redis=None, current_hour=14)
        assert result.fraud_score >= 0.0
        assert result.risk_level in list(RiskLevel)
