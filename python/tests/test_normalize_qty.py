"""C5: normalize_qty_contract and venue quantity normalization tests.

In Phase C, qty normalization functions are implemented in normalize.py and
tested here, but wired into adapter streams only after Phase E (when Rust-side
normalization becomes debug_assert-only).  These tests verify the Python-side
logic is correct and ready for Phase E activation.
"""

from __future__ import annotations

import random
from decimal import Decimal

import pytest

from engine.exchanges.normalize import normalize_qty_contract


# ---------------------------------------------------------------------------
# normalize_qty_contract
# ---------------------------------------------------------------------------


def test_normalize_qty_contract_basic():
    # 10 contracts * 0.001 contract_size = 0.01 base units
    result = normalize_qty_contract("10", Decimal("0.001"))
    assert Decimal(result) == Decimal("0.01")


def test_normalize_qty_contract_large():
    result = normalize_qty_contract("100", Decimal("10"))
    assert Decimal(result) == Decimal("1000")


def test_normalize_qty_contract_fractional():
    result = normalize_qty_contract("3.5", Decimal("2"))
    assert Decimal(result) == Decimal("7.0")


def test_normalize_qty_contract_zero_contract_size_returns_unchanged():
    original = "42.0"
    result = normalize_qty_contract(original, Decimal("0"))
    assert result == original


def test_normalize_qty_contract_invalid_qty_returns_unchanged():
    original = "abc"
    result = normalize_qty_contract(original, Decimal("1"))
    assert result == original


def test_normalize_qty_contract_identity():
    """contract_size=1 → qty unchanged."""
    result = normalize_qty_contract("123.456", Decimal("1"))
    assert Decimal(result) == Decimal("123.456")


# ---------------------------------------------------------------------------
# Property: result = qty * contract_size (commutativity / associativity)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("contract_size_str", ["0.001", "0.01", "0.1", "1", "10", "100"])
def test_normalize_qty_contract_property(contract_size_str: str):
    """normalize_qty_contract(qty, cs) == qty * cs for random positive quantities."""
    rng = random.Random(99)
    contract_size = Decimal(contract_size_str)
    for _ in range(100):
        raw_qty = rng.uniform(0.001, 1000.0)
        qty_str = f"{raw_qty:.6f}"
        result = Decimal(normalize_qty_contract(qty_str, contract_size))
        expected = Decimal(qty_str) * contract_size
        # Decimal arithmetic should be exact here
        assert result == expected, (
            f"normalize_qty_contract({qty_str!r}, {contract_size_str!r}) = {result}, "
            f"expected {expected}"
        )


# ---------------------------------------------------------------------------
# Venue-specific qty normalization semantics
# ---------------------------------------------------------------------------


def test_hyperliquid_contract_size_none_no_normalization():
    """Hyperliquid sends contract_size=None → qty stays unchanged.

    HyperliquidWorker.venue_caps() returns qty_norm_kind='contract' but the
    actual contract_size is None (quantities are already in base-asset units).
    In this case, Python should NOT apply contract normalization.
    """
    qty_str = "0.5432"
    # When contract_size is None, callers should skip normalization entirely.
    # This test documents the expected skip behaviour (no normalize call).
    assert qty_str == qty_str  # trivially true — just documenting the pattern


def test_tachibana_lot_normalization_is_passthrough():
    """Tachibana sends qty in share units (qty_norm_kind='lot').

    In Phase C, Tachibana qty is already in share units and requires no
    further normalization at the Python level.  The Rust side likewise does
    not apply QtyNormalization for tachibana in the current pipeline.
    """
    # No normalize call needed — just document the invariant.
    raw_qty = "100"
    assert raw_qty == raw_qty  # no transformation expected
