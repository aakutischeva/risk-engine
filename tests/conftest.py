"""Shared pytest fixtures for the risk-engine test suite."""

import pytest

from app.domain.enums import TransactionType
from app.domain.schemas.transaction import TransactionSubmit


@pytest.fixture
def sample_pix_transaction() -> TransactionSubmit:
    """Return a standard low-risk PIX transaction."""
    return TransactionSubmit(
        transaction_id="fixture-pix-001",
        amount=150.00,
        payer_key="alice@bank.com",
        receiver_key="bob@bank.com",
        transaction_type=TransactionType.PIX,
    )


@pytest.fixture
def high_amount_transaction() -> TransactionSubmit:
    """Return a transaction above the high-risk amount threshold."""
    return TransactionSubmit(
        transaction_id="fixture-high-001",
        amount=25_000.00,
        payer_key="alice@bank.com",
        receiver_key="merchant@bank.com",
        transaction_type=TransactionType.TED,
    )


@pytest.fixture
def self_transfer_transaction() -> TransactionSubmit:
    """Return a transaction where payer and receiver keys are identical."""
    return TransactionSubmit(
        transaction_id="fixture-self-001",
        amount=500.00,
        payer_key="user@bank.com",
        receiver_key="user@bank.com",
        transaction_type=TransactionType.PIX,
    )


@pytest.fixture
def round_amount_transaction() -> TransactionSubmit:
    """Return a transaction with a suspicious round amount."""
    return TransactionSubmit(
        transaction_id="fixture-round-001",
        amount=10_000.00,
        payer_key="alice@bank.com",
        receiver_key="bob@bank.com",
        transaction_type=TransactionType.PIX,
    )
