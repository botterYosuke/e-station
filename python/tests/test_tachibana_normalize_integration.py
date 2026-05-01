"""Integration tests for Tachibana depth/trade price normalization wiring.

Covers the three integration gaps identified in the Phase C review:
1. stream_trades re-resolves tick from first trade price (not just depth)
2. ask-only depth snapshots still trigger tick re-resolution
3. _depth_polling_fallback updates tick before normalizing

These tests exercise the TachibanaWorker helper methods directly, since
full async stream integration requires a live session and WS infrastructure.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from engine.exchanges.tachibana import TachibanaWorker
from engine.exchanges.tachibana_master import YobineBand


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _band(kizun: str, tanka: str, decimals: int = 0) -> YobineBand:
    return YobineBand(
        kizun_price=Decimal(kizun),
        yobine_tanka=Decimal(tanka),
        decimals=decimals,
    )


def _worker_with_master() -> TachibanaWorker:
    """Return a TachibanaWorker with a representative TOPIX100 yobine table loaded."""
    worker = TachibanaWorker(cache_dir=Path("."), is_demo=True)
    worker._yobine_table = {
        "103": [
            _band("1000", "0.1", decimals=1),
            _band("3000", "0.5", decimals=1),
            _band("10000", "1.0", decimals=0),
            _band("30000", "5.0", decimals=0),
            _band("999999999", "50.0", decimals=0),
        ],
    }
    worker._master_records = {
        "CLMIssueSizyouMstKabu": [
            {"sIssueCode": "9984", "sYobineTaniNumber": "103"},
        ],
    }
    # Seed finest-tick fallback (as list_tickers does on startup)
    worker._ticker_min_ticksize["9984"] = Decimal("0.1")
    return worker


# ---------------------------------------------------------------------------
# Fix 1: stream_trades re-resolves tick from first trade price
# ---------------------------------------------------------------------------


def test_normalize_trade_price_uses_finest_tick_before_first_trade():
    """Before any trade arrives, finest-tick fallback (0.1) is used."""
    worker = _worker_with_master()
    # tick is 0.1 (finest) from list_tickers seed
    trade = {"price": "5379.7", "qty": "100", "side": "buy", "ts_ms": 0}
    result = worker._normalize_trade_price("9984", trade)
    assert Decimal(result["price"]) % Decimal("0.1") == 0
    # 5379.7 is already on 0.1 grid — but NOT on 1.0 grid
    assert Decimal(result["price"]) != Decimal("5380")


def test_update_min_ticksize_from_trade_price():
    """_update_min_ticksize_from_price with a 5379-yen price sets tick to 1.0."""
    worker = _worker_with_master()
    assert worker._ticker_min_ticksize["9984"] == Decimal("0.1")  # before

    worker._update_min_ticksize_from_price("9984", Decimal("5379"))
    assert worker._ticker_min_ticksize["9984"] == Decimal("1.0")  # after


def test_normalize_trade_price_after_tick_resolution():
    """After tick is resolved to 1.0, trade price 5379.7 rounds to 5380."""
    worker = _worker_with_master()
    worker._update_min_ticksize_from_price("9984", Decimal("5379"))

    trade = {"price": "5379.7", "qty": "100", "side": "buy", "ts_ms": 0}
    result = worker._normalize_trade_price("9984", trade)
    assert Decimal(result["price"]) == Decimal("5380")


# ---------------------------------------------------------------------------
# Fix 2: ask-only snapshots still trigger tick re-resolution
# ---------------------------------------------------------------------------


def test_try_update_min_ticksize_uses_bids_first():
    """When bids are present, use bids[0] for tick resolution."""
    worker = _worker_with_master()
    bids = [{"price": "5379", "qty": "6200"}]
    asks = [{"price": "99999", "qty": "100"}]  # cap-band price, should NOT be used
    worker._try_update_min_ticksize_from_levels("9984", bids, asks)
    # 5379 → band[3] → 1.0 yen
    assert worker._ticker_min_ticksize["9984"] == Decimal("1.0")


def test_try_update_min_ticksize_falls_back_to_asks_when_no_bids():
    """When bids is empty, asks[0] is used as fallback (Fix 2)."""
    worker = _worker_with_master()
    bids: list[dict] = []
    asks = [{"price": "5381", "qty": "2900"}]  # still in band[3]
    worker._try_update_min_ticksize_from_levels("9984", bids, asks)
    # 5381 → band[3] → 1.0 yen
    assert worker._ticker_min_ticksize["9984"] == Decimal("1.0")


def test_try_update_min_ticksize_no_op_when_both_empty():
    """Empty bids and asks leave the cached tick unchanged."""
    worker = _worker_with_master()
    original_tick = worker._ticker_min_ticksize["9984"]
    worker._try_update_min_ticksize_from_levels("9984", [], [])
    assert worker._ticker_min_ticksize["9984"] == original_tick


def test_normalize_depth_ask_only_uses_correct_tick():
    """Ask-only snapshot: tick is re-resolved from ask price, then normalization uses it."""
    worker = _worker_with_master()
    bids: list[dict] = []
    asks = [{"price": "5381.3", "qty": "2900"}]

    # Simulate what stream_depth does
    worker._try_update_min_ticksize_from_levels("9984", bids, asks)
    norm_bids, norm_asks = worker._normalize_depth_levels("9984", bids, asks)

    assert norm_bids == []
    assert len(norm_asks) == 1
    # 5381.3 rounded to 1.0-yen grid = 5381
    assert Decimal(norm_asks[0]["price"]) == Decimal("5381")


# ---------------------------------------------------------------------------
# Fix 3: _depth_polling_fallback updates tick before normalizing
# ---------------------------------------------------------------------------


def test_normalize_depth_levels_polling_scenario():
    """Simulate polling fallback: tick is updated from REST snapshot before normalize.

    This exercises the same code path as _depth_polling_fallback without
    starting an async event loop.
    """
    worker = _worker_with_master()
    # Initially finest-tick from startup
    assert worker._ticker_min_ticksize["9984"] == Decimal("0.1")

    poll_bids = [{"price": "5379", "qty": "6200"}]
    poll_asks = [{"price": "5380", "qty": "2900"}]

    # Simulate what _depth_polling_fallback does (Fix 3)
    worker._try_update_min_ticksize_from_levels("9984", poll_bids, poll_asks)
    norm_bids, norm_asks = worker._normalize_depth_levels("9984", poll_bids, poll_asks)

    # After tick update, normalization should use 1.0-yen grid
    assert worker._ticker_min_ticksize["9984"] == Decimal("1.0")
    assert Decimal(norm_bids[0]["price"]) % Decimal("1") == 0
    assert Decimal(norm_asks[0]["price"]) % Decimal("1") == 0


def test_polling_fallback_without_master_skips_update():
    """When no yobine table is loaded, tick update is skipped silently."""
    worker = TachibanaWorker(cache_dir=Path("."), is_demo=True)
    worker._ticker_min_ticksize["9984"] = Decimal("0.1")
    # yobine_table is empty — _update_min_ticksize_from_price is a no-op

    poll_bids = [{"price": "5379", "qty": "6200"}]
    worker._try_update_min_ticksize_from_levels("9984", poll_bids, [])

    # tick unchanged — no crash
    assert worker._ticker_min_ticksize["9984"] == Decimal("0.1")


def test_polling_fallback_unknown_ticker_skips_update():
    """ticker not in master records → no update, no crash."""
    worker = _worker_with_master()
    worker._try_update_min_ticksize_from_levels("0000", [{"price": "5379", "qty": "1"}], [])
    # "0000" was never in _ticker_min_ticksize — no entry added
    assert "0000" not in worker._ticker_min_ticksize


# ---------------------------------------------------------------------------
# Regression: normalize_depth_levels never produces zero or negative prices
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tick_str,prices", [
    ("0.1", ["151.9", "152.0", "151.8"]),
    ("0.5", ["2998.0", "2997.5", "2999.0"]),
    ("1.0", ["5379", "5378", "5380"]),
    ("5.0", ["15000", "14995", "15010"]),
])
def test_normalize_never_produces_nonpositive_price(tick_str: str, prices: list[str]):
    """All normalized prices are strictly positive regardless of tick size."""
    from engine.exchanges.normalize import normalize_depth_levels
    levels = [{"price": p, "qty": "100"} for p in prices]
    result = normalize_depth_levels(levels, Decimal(tick_str))
    for lv in result:
        price = Decimal(lv["price"])
        assert price > 0, f"price {price} is not positive"
        assert price % Decimal(tick_str) == 0, f"price {price} not on {tick_str} grid"
