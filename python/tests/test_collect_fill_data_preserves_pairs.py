"""T6 / M-A: regression guard for _collect_fill_data pair preservation.

Bug: engine_runner.py line 195 used to call
    return sorted(timestamps), sorted(last_prices)
which sorts the two lists **independently**, destroying the correspondence
between the n-th timestamp and the n-th price.

Fix: sort by timestamp, then unzip — so prices[i] always belongs to
timestamps[i].

RED phase: this test must FAIL against the original code (independent sort).
GREEN phase: this test must PASS after the fix (pair-preserving sort).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from engine.nautilus.engine_runner import _collect_fill_data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_order(ts_last: int, avg_px: float, is_closed: bool = True) -> SimpleNamespace:
    """Lightweight stand-in for a NautilusTrader Order object."""
    return SimpleNamespace(ts_last=ts_last, avg_px=avg_px, is_closed=is_closed)


def _make_engine(orders: list) -> MagicMock:
    """Return a mock BacktestEngine whose cache returns *orders*."""
    engine = MagicMock()
    engine.kernel.cache.orders.return_value = orders
    return engine


# ---------------------------------------------------------------------------
# Core correctness: pair correspondence is preserved
# ---------------------------------------------------------------------------


def test_collect_fill_data_preserves_pairs_basic() -> None:
    """The price at index i must correspond to the timestamp at index i.

    Setup: two fills where the later fill has a *higher* timestamp but a
    *lower* price.  After independent sort the order would be:
        timestamps = [100, 200]  (ascending)
        prices     = ["50.0", "80.0"]  (ascending — but 50.0 belongs to ts=200!)

    The correct pair-preserving result is:
        timestamps = [100, 200]
        prices     = ["80.0", "50.0"]   ← price paired with its own timestamp
    """
    orders = [
        _make_order(ts_last=200, avg_px=50.0),   # later ts, lower price
        _make_order(ts_last=100, avg_px=80.0),   # earlier ts, higher price
    ]
    engine = _make_engine(orders)

    timestamps, prices = _collect_fill_data(engine)

    assert timestamps == [100, 200], f"timestamps not sorted: {timestamps}"
    # After pair-preserving sort: ts=100 → avg_px=80.0, ts=200 → avg_px=50.0
    assert prices == ["80.0", "50.0"], (
        f"prices are not paired with their timestamps.\n"
        f"  expected: ['80.0', '50.0']\n"
        f"  got:      {prices}\n"
        "Independent sort of prices destroys the ts↔price correspondence."
    )


def test_collect_fill_data_three_fills_pair_order() -> None:
    """Three fills: timestamps [300, 100, 200] → after sort ts[0]=100, price[0]=px@100."""
    orders = [
        _make_order(ts_last=300, avg_px=30.0),
        _make_order(ts_last=100, avg_px=10.0),
        _make_order(ts_last=200, avg_px=20.0),
    ]
    engine = _make_engine(orders)

    timestamps, prices = _collect_fill_data(engine)

    assert timestamps == [100, 200, 300]
    assert prices == ["10.0", "20.0", "30.0"], (
        f"pair correspondence broken: {list(zip(timestamps, prices))}"
    )


def test_collect_fill_data_empty() -> None:
    """Empty order list → ([], [])."""
    engine = _make_engine([])
    timestamps, prices = _collect_fill_data(engine)
    assert timestamps == []
    assert prices == []


def test_collect_fill_data_single_fill() -> None:
    """Single fill is trivially correct in both implementations — baseline."""
    orders = [_make_order(ts_last=42, avg_px=123.45)]
    engine = _make_engine(orders)
    timestamps, prices = _collect_fill_data(engine)
    assert timestamps == [42]
    assert prices == ["123.45"]


def test_collect_fill_data_skips_open_orders() -> None:
    """Open (is_closed=False) orders must be excluded."""
    orders = [
        _make_order(ts_last=100, avg_px=10.0, is_closed=False),
        _make_order(ts_last=200, avg_px=20.0, is_closed=True),
    ]
    engine = _make_engine(orders)
    timestamps, prices = _collect_fill_data(engine)
    assert timestamps == [200]
    assert prices == ["20.0"]


def test_collect_fill_data_recoverable_exception_returns_empty() -> None:
    """H-I: 想定可能な属性欠落 (AttributeError/KeyError/TypeError) は握って ([], [])。

    元実装は ``except Exception`` で全捕捉していたが、本物の不具合 (RuntimeError 等) を
    隠蔽しないよう想定可能な型のみに絞った。"""
    engine = MagicMock()
    engine.kernel.cache.orders.side_effect = AttributeError("cache attr missing")

    timestamps, prices = _collect_fill_data(engine)
    assert timestamps == []
    assert prices == []


def test_collect_fill_data_unexpected_exception_propagates() -> None:
    """H-I: 想定外の例外 (RuntimeError 等) は握り潰さず raise する。

    呼出側 (start_backtest_replay の except) で EngineStopped 補完 + EngineError 経由で
    Rust に通知される。"""
    engine = MagicMock()
    engine.kernel.cache.orders.side_effect = RuntimeError("cache unavailable")
    with pytest.raises(RuntimeError, match="cache unavailable"):
        _collect_fill_data(engine)
