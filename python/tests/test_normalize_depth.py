"""C1 / C2: normalize_depth / normalize_depth_levels price normalization tests.

Verifies that after normalization, every price is an exact multiple of
min_ticksize (no floating-point residual > 1e-9 relative to the tick).
"""

from __future__ import annotations

import random
from decimal import Decimal

import pytest

from engine.exchanges.normalize import (
    normalize_depth,
    normalize_depth_levels,
    normalize_kline,
    normalize_price,
    normalize_trade,
    normalize_trades_event,
)


# ---------------------------------------------------------------------------
# normalize_price
# ---------------------------------------------------------------------------


def test_normalize_price_exact_multiple_unchanged():
    assert normalize_price("5380", Decimal("5")) == "5380"
    assert normalize_price("151.9", Decimal("0.1")) == "151.9"


def test_normalize_price_rounds_down():
    # 5382 → nearest 5 multiple = 5380
    result = Decimal(normalize_price("5382", Decimal("5")))
    assert result == Decimal("5380")


def test_normalize_price_rounds_up():
    # 5383 → nearest 5 multiple = 5385
    result = Decimal(normalize_price("5383", Decimal("5")))
    assert result == Decimal("5385")


def test_normalize_price_half_rounds_up():
    # 5382.5 → ties round up → 5385
    result = Decimal(normalize_price("5382.5", Decimal("5")))
    assert result == Decimal("5385")


def test_normalize_price_zero_tick_returns_unchanged():
    assert normalize_price("5380", Decimal("0")) == "5380"


def test_normalize_price_invalid_string_returns_unchanged():
    assert normalize_price("NaN", Decimal("5")) == "NaN"
    assert normalize_price("", Decimal("5")) == ""
    assert normalize_price("abc", Decimal("1")) == "abc"


def test_normalize_price_fine_tick():
    # 151.93 → nearest 0.1 multiple = 151.9
    result = Decimal(normalize_price("151.93", Decimal("0.1")))
    assert result == Decimal("151.9")


def test_normalize_price_positive_result():
    """Phase C acceptance: prices must be positive after normalization."""
    prices = ["5379", "5378", "5380", "5381", "5375"]
    for p in prices:
        result = Decimal(normalize_price(p, Decimal("1")))
        assert result > 0, f"price {p} normalized to non-positive {result}"


# ---------------------------------------------------------------------------
# normalize_depth_levels
# ---------------------------------------------------------------------------


def test_normalize_depth_levels_all_on_grid():
    levels = [
        {"price": "5380.0", "qty": "100"},
        {"price": "5375.0", "qty": "200"},
        {"price": "5370.0", "qty": "50"},
    ]
    result = normalize_depth_levels(levels, Decimal("5"))
    for lv in result:
        price = Decimal(lv["price"])
        assert price % Decimal("5") == 0, f"price {price} not multiple of 5"


def test_normalize_depth_levels_preserves_qty():
    levels = [{"price": "5382", "qty": "999"}]
    result = normalize_depth_levels(levels, Decimal("5"))
    assert result[0]["qty"] == "999"


def test_normalize_depth_levels_empty():
    assert normalize_depth_levels([], Decimal("5")) == []


# ---------------------------------------------------------------------------
# normalize_depth
# ---------------------------------------------------------------------------


def test_normalize_depth_event_normalizes_both_sides():
    event = {
        "event": "DepthSnapshot",
        "venue": "tachibana",
        "bids": [{"price": "5379", "qty": "6200"}, {"price": "5378", "qty": "10600"}],
        "asks": [{"price": "5380", "qty": "2900"}, {"price": "5381", "qty": "4600"}],
    }
    result = normalize_depth(event, Decimal("1"))
    for lv in result["bids"] + result["asks"]:
        price = Decimal(lv["price"])
        assert price % Decimal("1") == 0
        assert price > 0


def test_normalize_depth_does_not_mutate_original():
    original_bid_price = "5382"
    event = {
        "bids": [{"price": original_bid_price, "qty": "100"}],
        "asks": [],
    }
    normalize_depth(event, Decimal("5"))
    assert event["bids"][0]["price"] == original_bid_price


def test_normalize_depth_preserves_other_fields():
    event = {
        "event": "DepthSnapshot",
        "venue": "tachibana",
        "ticker": "9984",
        "sequence_id": 42,
        "bids": [{"price": "5380", "qty": "100"}],
        "asks": [],
    }
    result = normalize_depth(event, Decimal("5"))
    assert result["event"] == "DepthSnapshot"
    assert result["venue"] == "tachibana"
    assert result["ticker"] == "9984"
    assert result["sequence_id"] == 42


# ---------------------------------------------------------------------------
# normalize_trade / normalize_trades_event
# ---------------------------------------------------------------------------


def test_normalize_trade_rounds_price():
    trade = {"price": "5382.3", "qty": "100", "side": "buy", "ts_ms": 0}
    result = normalize_trade(trade, Decimal("1"))
    assert Decimal(result["price"]) == Decimal("5382")
    assert result["qty"] == "100"


def test_normalize_trade_no_price_key():
    trade = {"qty": "100", "side": "buy"}
    result = normalize_trade(trade, Decimal("1"))
    assert result is trade  # unchanged reference


def test_normalize_trades_event():
    event = {
        "event": "Trades",
        "trades": [
            {"price": "5379.7", "qty": "100"},
            {"price": "5380.2", "qty": "200"},
        ],
    }
    result = normalize_trades_event(event, Decimal("1"))
    prices = [Decimal(t["price"]) for t in result["trades"]]
    assert prices == [Decimal("5380"), Decimal("5380")]


# ---------------------------------------------------------------------------
# normalize_kline
# ---------------------------------------------------------------------------


def test_normalize_kline_rounds_ohlc():
    event = {
        "event": "KlineUpdate",
        "kline": {
            "open": "5379.3",
            "high": "5382.7",
            "low": "5376.1",
            "close": "5380.0",
            "volume": "12345",
            "is_closed": False,
        },
    }
    result = normalize_kline(event, Decimal("1"))
    k = result["kline"]
    assert Decimal(k["open"]) == Decimal("5379")
    assert Decimal(k["high"]) == Decimal("5383")
    assert Decimal(k["low"]) == Decimal("5376")
    assert Decimal(k["close"]) == Decimal("5380")
    assert k["volume"] == "12345"


def test_normalize_kline_no_kline_key():
    event = {"event": "KlineUpdate"}
    result = normalize_kline(event, Decimal("1"))
    assert result == event


# ---------------------------------------------------------------------------
# Property-based: random prices are always on the tick grid after normalize
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("ticksize_str", ["0.1", "0.5", "1", "5", "10", "0.01"])
def test_normalize_price_property_on_grid(ticksize_str: str):
    """Random prices in [100, 10000] are exact tick multiples after normalization."""
    rng = random.Random(42)
    ticksize = Decimal(ticksize_str)
    for _ in range(200):
        raw = rng.uniform(100.0, 10000.0)
        price_str = f"{raw:.6f}"
        normalized = Decimal(normalize_price(price_str, ticksize))
        remainder = normalized % ticksize
        assert remainder == 0, (
            f"price {normalized} is not a multiple of {ticksize} "
            f"(raw={price_str}, remainder={remainder})"
        )
        assert normalized > 0, f"price normalized to non-positive: {normalized}"


# ---------------------------------------------------------------------------
# Tachibana "alternating zeros" acceptance scenario
# 5379 yen SoftBank + 5x TickMultiplier context
# ---------------------------------------------------------------------------


def test_softbank_5379_prices_positive_after_normalize():
    """Phase C acceptance: 5379 yen stock prices normalized with 1-yen tick are positive."""
    min_ticksize = Decimal("1")  # correct tick for 3000-10000 yen band (yobine_code='103')
    # Typical 10-level depth for SoftBank ~5379 yen
    bids = [
        {"price": str(5379 - i), "qty": str(1000 + i * 100)} for i in range(10)
    ]
    asks = [
        {"price": str(5380 + i), "qty": str(1000 + i * 100)} for i in range(10)
    ]
    event = {"bids": bids, "asks": asks}
    result = normalize_depth(event, min_ticksize)
    for lv in result["bids"] + result["asks"]:
        price = Decimal(lv["price"])
        assert price > 0, f"price {price} is not positive"
        assert price % min_ticksize == 0, f"price {price} not on 1-yen grid"
