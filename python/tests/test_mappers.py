"""mapper 関数のテスト（C1 / adapter-type-boundary Step 3）。

確認項目:
- adapter model → wire DTO の必須フィールド保持
- gap recovery フィールド（stream_session_id / sequence_id / prev_sequence_id）の伝搬
- Decimal → str 変換（指数表記なし）
- timestamp → ts_ms 変換（UTC ベース）
- wire compatibility（model_dump で生成される JSON 形）
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from engine.mappers import (
    depth_diff_to_wire,
    order_book_to_wire,
    trade_to_wire,
    trades_to_wire,
)
from engine.models import DepthDiff, OrderBook, Trade
from engine.schemas import DepthDiffMsg, DepthSnapshotMsg, TradeMsg, Trades


def _utc(s: str = "2026-05-07T12:00:00+00:00") -> datetime:
    return datetime.fromisoformat(s)


# ---------------------------------------------------------------------------
# OrderBook → DepthSnapshotMsg
# ---------------------------------------------------------------------------


def test_order_book_to_wire_basic() -> None:
    ob = OrderBook(
        symbol="BTCUSDT",
        timestamp=_utc(),
        bids=[(Decimal("30000.5"), Decimal("1.5"))],
        asks=[(Decimal("30001.5"), Decimal("0.5"))],
        stream_session_id="sess-1",
        sequence_id=42,
    )
    msg = order_book_to_wire(ob, venue="binance", ticker="BTCUSDT", market="spot")
    assert isinstance(msg, DepthSnapshotMsg)
    assert msg.event == "DepthSnapshot"
    assert msg.venue == "binance"
    assert msg.ticker == "BTCUSDT"
    assert msg.market == "spot"
    assert msg.stream_session_id == "sess-1"
    assert msg.sequence_id == 42
    assert msg.bids[0].price == "30000.5"
    assert msg.bids[0].qty == "1.5"
    assert msg.asks[0].price == "30001.5"


def test_order_book_to_wire_missing_session_raises() -> None:
    ob = OrderBook(symbol="x", timestamp=_utc(), bids=[], asks=[])
    with pytest.raises(ValueError, match="stream_session_id"):
        order_book_to_wire(ob, venue="binance", ticker="x", market="spot")


def test_order_book_to_wire_missing_sequence_raises() -> None:
    ob = OrderBook(
        symbol="x",
        timestamp=_utc(),
        bids=[],
        asks=[],
        stream_session_id="sess-1",
    )
    with pytest.raises(ValueError, match="sequence_id"):
        order_book_to_wire(ob, venue="binance", ticker="x", market="spot")


def test_order_book_to_wire_request_id_propagates() -> None:
    ob = OrderBook(
        symbol="x",
        timestamp=_utc(),
        bids=[],
        asks=[],
        stream_session_id="sess-1",
        sequence_id=1,
    )
    msg = order_book_to_wire(
        ob, venue="binance", ticker="x", market="spot", request_id="req-1"
    )
    assert msg.request_id == "req-1"


# ---------------------------------------------------------------------------
# DepthDiff → DepthDiffMsg (gap recovery 不変条件)
# ---------------------------------------------------------------------------


def test_depth_diff_to_wire_preserves_gap_fields() -> None:
    diff = DepthDiff(
        symbol="x",
        timestamp=_utc(),
        bids=[(Decimal("100"), Decimal("0"))],  # qty=0 = 削除
        asks=[(Decimal("101"), Decimal("1"))],
        stream_session_id="sess-7",
        sequence_id=11,
        prev_sequence_id=10,
    )
    msg = depth_diff_to_wire(diff, venue="binance", ticker="x", market="spot")
    assert isinstance(msg, DepthDiffMsg)
    assert msg.stream_session_id == "sess-7"
    assert msg.sequence_id == 11
    assert msg.prev_sequence_id == 10
    assert msg.bids[0].qty == "0"  # 削除レベルの qty=0 を保持


# ---------------------------------------------------------------------------
# Trade → TradeMsg / list[Trade] → Trades
# ---------------------------------------------------------------------------


def test_trade_to_wire_ts_ms_conversion() -> None:
    trade = Trade(
        symbol="x",
        timestamp=datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc),
        price=Decimal("100.5"),
        qty=Decimal("0.001"),
        side="buy",
    )
    msg = trade_to_wire(trade)
    assert isinstance(msg, TradeMsg)
    assert msg.price == "100.5"
    assert msg.qty == "0.001"
    assert msg.side == "buy"
    # 2026-05-07T12:00:00Z = 1778155200000
    assert msg.ts_ms == 1778155200000


def test_trades_to_wire_batch() -> None:
    trades = [
        Trade(
            symbol="x",
            timestamp=_utc(),
            price=Decimal("100"),
            qty=Decimal("1"),
            side="buy",
        ),
        Trade(
            symbol="x",
            timestamp=_utc(),
            price=Decimal("101"),
            qty=Decimal("2"),
            side="sell",
        ),
    ]
    msg = trades_to_wire(
        trades, venue="binance", ticker="x", market="spot", stream_session_id="sess-1"
    )
    assert isinstance(msg, Trades)
    assert len(msg.trades) == 2
    assert msg.trades[0].side == "buy"
    assert msg.trades[1].side == "sell"


# ---------------------------------------------------------------------------
# Wire compatibility: JSON dump 形が schemas.py と整合
# ---------------------------------------------------------------------------


def test_wire_dump_no_decimal_in_json() -> None:
    """wire JSON に Decimal 型が混入せず、すべて str 化されていること。"""
    ob = OrderBook(
        symbol="x",
        timestamp=_utc(),
        bids=[(Decimal("30000.123456789"), Decimal("0.0001"))],
        asks=[],
        stream_session_id="s",
        sequence_id=1,
    )
    msg = order_book_to_wire(ob, venue="binance", ticker="x", market="spot")
    dump = msg.model_dump(mode="json")
    assert dump["bids"][0]["price"] == "30000.123456789"
    assert dump["bids"][0]["qty"] == "0.0001"
    # event / venue / market は wire 形式に含まれる
    assert dump["event"] == "DepthSnapshot"
    assert dump["venue"] == "binance"


def test_wire_dump_no_exponent_form() -> None:
    """大きい / 小さい Decimal が指数表記にならないこと（"1E-4" 等を回避）。"""
    trade = Trade(
        symbol="x",
        timestamp=_utc(),
        price=Decimal("0.0001"),
        qty=Decimal("100000000"),
        side="buy",
    )
    msg = trade_to_wire(trade)
    assert "E" not in msg.price
    assert "E" not in msg.qty
