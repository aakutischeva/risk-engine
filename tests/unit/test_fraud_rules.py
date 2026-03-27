"""Unit tests for individual fraud detection rules.

All tests are fully isolated: no database, no Redis, no network.
Redis-dependent rules receive either a mock or None.
"""

import pytest

from app.domain.enums import TransactionType
from app.domain.schemas.transaction import TransactionSubmit
from app.fraud.rules.amount import HighAmountRule
from app.fraud.rules.base import RuleContext
from app.fraud.rules.pattern import RoundAmountRule, SelfTransferRule
from app.fraud.rules.temporal import NightTimeRule


def _make_transaction(**kwargs) -> TransactionSubmit:
    defaults = {
        "transaction_id": "test-001",
        "amount": 100.00,
        "payer_key": "payer@test.com",
        "receiver_key": "receiver@test.com",
        "transaction_type": TransactionType.PIX,
    }
    defaults.update(kwargs)
    return TransactionSubmit(**defaults)


def _make_ctx(transaction: TransactionSubmit, hour: int = 14) -> RuleContext:
    return RuleContext(transaction=transaction, redis=None, current_hour=hour)


# ─── HighAmountRule ────────────────────────────────────────────────────────────


class TestHighAmountRule:
    rule = HighAmountRule()

    @pytest.mark.asyncio
    async def test_low_amount_scores_zero(self):
        tx = _make_transaction(amount=500.00)
        result = await self.rule.evaluate(_make_ctx(tx))
        assert result.score == 0.0
        assert result.triggered is False

    @pytest.mark.asyncio
    async def test_amount_at_high_threshold_triggers(self):
        tx = _make_transaction(amount=5_000.00)
        result = await self.rule.evaluate(_make_ctx(tx))
        assert result.triggered is True
        assert result.score >= 30.0

    @pytest.mark.asyncio
    async def test_critical_amount_scores_100(self):
        tx = _make_transaction(amount=25_000.00)
        result = await self.rule.evaluate(_make_ctx(tx))
        assert result.score == 100.0
        assert result.triggered is True

    @pytest.mark.asyncio
    async def test_score_interpolates_between_thresholds(self):
        # Midpoint between 5000 and 20000 should score between 30 and 80
        tx = _make_transaction(amount=12_500.00)
        result = await self.rule.evaluate(_make_ctx(tx))
        assert 30.0 < result.score < 80.0

    @pytest.mark.asyncio
    async def test_rule_name_is_correct(self):
        assert self.rule.name == "HighAmountRule"

    @pytest.mark.asyncio
    async def test_weight_is_above_one(self):
        assert self.rule.weight > 1.0


# ─── NightTimeRule ────────────────────────────────────────────────────────────


class TestNightTimeRule:
    rule = NightTimeRule()

    @pytest.mark.asyncio
    async def test_daytime_large_amount_scores_zero(self):
        tx = _make_transaction(amount=10_000.00)
        result = await self.rule.evaluate(_make_ctx(tx, hour=14))
        assert result.score == 0.0
        assert result.triggered is False

    @pytest.mark.asyncio
    async def test_nighttime_large_amount_triggers(self):
        tx = _make_transaction(amount=10_000.00)
        result = await self.rule.evaluate(_make_ctx(tx, hour=2))
        assert result.triggered is True
        assert result.score == 45.0

    @pytest.mark.asyncio
    async def test_nighttime_small_amount_does_not_trigger(self):
        tx = _make_transaction(amount=50.00)
        result = await self.rule.evaluate(_make_ctx(tx, hour=3))
        assert result.triggered is False
        assert result.score == 10.0

    @pytest.mark.asyncio
    async def test_boundary_hour_zero_large_amount_triggers(self):
        tx = _make_transaction(amount=6_000.00)
        result = await self.rule.evaluate(_make_ctx(tx, hour=0))
        assert result.triggered is True

    @pytest.mark.asyncio
    async def test_hour_after_window_does_not_trigger(self):
        tx = _make_transaction(amount=10_000.00)
        result = await self.rule.evaluate(_make_ctx(tx, hour=6))
        assert result.score == 0.0


# ─── RoundAmountRule ──────────────────────────────────────────────────────────


class TestRoundAmountRule:
    rule = RoundAmountRule()

    @pytest.mark.asyncio
    async def test_exact_round_amount_triggers(self):
        tx = _make_transaction(amount=5_000.00)
        result = await self.rule.evaluate(_make_ctx(tx))
        assert result.triggered is True
        assert result.score == 25.0

    @pytest.mark.asyncio
    async def test_non_round_amount_does_not_trigger(self):
        tx = _make_transaction(amount=1_234.56)
        result = await self.rule.evaluate(_make_ctx(tx))
        assert result.triggered is False
        assert result.score == 0.0

    @pytest.mark.asyncio
    async def test_small_round_amount_below_threshold_does_not_trigger(self):
        tx = _make_transaction(amount=100.00)
        result = await self.rule.evaluate(_make_ctx(tx))
        assert result.triggered is False

    @pytest.mark.asyncio
    async def test_exact_1000_triggers(self):
        tx = _make_transaction(amount=1_000.00)
        result = await self.rule.evaluate(_make_ctx(tx))
        assert result.triggered is True

    @pytest.mark.asyncio
    async def test_499_does_not_trigger(self):
        tx = _make_transaction(amount=499.00)
        result = await self.rule.evaluate(_make_ctx(tx))
        assert result.triggered is False


# ─── SelfTransferRule ─────────────────────────────────────────────────────────


class TestSelfTransferRule:
    rule = SelfTransferRule()

    @pytest.mark.asyncio
    async def test_identical_keys_trigger(self):
        tx = _make_transaction(payer_key="same@key.com", receiver_key="same@key.com")
        result = await self.rule.evaluate(_make_ctx(tx))
        assert result.triggered is True
        assert result.score == 60.0

    @pytest.mark.asyncio
    async def test_different_keys_do_not_trigger(self):
        tx = _make_transaction(payer_key="payer@key.com", receiver_key="receiver@key.com")
        result = await self.rule.evaluate(_make_ctx(tx))
        assert result.triggered is False
        assert result.score == 0.0

    @pytest.mark.asyncio
    async def test_case_insensitive_comparison(self):
        tx = _make_transaction(payer_key="USER@KEY.COM", receiver_key="user@key.com")
        result = await self.rule.evaluate(_make_ctx(tx))
        assert result.triggered is True

    @pytest.mark.asyncio
    async def test_whitespace_is_stripped(self):
        tx = _make_transaction(payer_key="  key@test.com  ", receiver_key="key@test.com")
        result = await self.rule.evaluate(_make_ctx(tx))
        assert result.triggered is True
