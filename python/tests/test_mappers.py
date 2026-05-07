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
    """真の指数表記 Decimal（1E-8 等）が wire で固定小数点表記になること。

    Decimal("1E-8") の str() は "1E-8" だが format(d, "f") は "0.00000001"。
    mappers._decimal_to_str が format(d, "f") を使っていることを確認する。
    """
    trade = Trade(
        symbol="x",
        timestamp=_utc(),
        price=Decimal("1E-8"),   # satoshi 相当の BTC 価格
        qty=Decimal("1E+8"),     # 大量 qty
        side="buy",
    )
    msg = trade_to_wire(trade)
    assert "E" not in msg.price and "e" not in msg.price, (
        f"price should be fixed-point, got {msg.price!r}. "
        "Fix: use format(d, 'f') in _decimal_to_str()."
    )
    assert "E" not in msg.qty and "e" not in msg.qty


# ---------------------------------------------------------------------------
# Wire compatibility: None フィールドの exclude_none 保証
# ---------------------------------------------------------------------------


def test_wire_dump_none_fields_excluded() -> None:
    """request_id=None / checksum=None が wire dict に含まれないこと。

    DepthSnapshotMsg の Optional フィールドが exclude_none=True で正しく除外され、
    Rust 側 ParseDict が予期しないフィールドを受信しないことを pin する。
    """
    ob = OrderBook(
        symbol="x",
        timestamp=_utc(),
        bids=[],
        asks=[],
        stream_session_id="s",
        sequence_id=1,
    )
    # request_id を渡さない（None）
    msg = order_book_to_wire(ob, venue="v", ticker="x", market="spot")
    dump = msg.model_dump(exclude_none=True)
    assert "request_id" not in dump, (
        "request_id=None should be excluded by exclude_none=True. "
        "Check kabu_push_pipeline.py uses model_dump(exclude_none=True)."
    )


# ---------------------------------------------------------------------------
# Wire compatibility: adapter model 直結禁止の Decimal 表現差
# ---------------------------------------------------------------------------


def test_adapter_model_direct_dump_vs_pipeline_decimal_representation() -> None:
    """adapter model .model_dump(mode="json") は指数表記を出し、mapper 経由は固定小数点。

    これは adapter model を wire に直結してはならない理由の実証テスト。
    mappers.py 経由 → _decimal_to_str(format(d, "f")) で固定小数点が保証される。
    adapter model 直接 → pydantic JSON シリアライザが Decimal("1E-8") を "1E-8" で出す。
    """
    ob = OrderBook(
        symbol="X",
        timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
        bids=[(Decimal("1E-8"), Decimal("1E+8"))],
        asks=[],
        stream_session_id="sess-1",
        sequence_id=1,
    )
    # adapter model 直接 dump (adapter → wire を bypass)
    raw_dump = ob.model_dump(mode="json")
    raw_bid_price = raw_dump["bids"][0][0]  # pydantic が tuple→list で出す

    # mapper 経由 (正規経路)
    wire = order_book_to_wire(ob, venue="v", ticker="X", market="spot")
    wire_dump = wire.model_dump(exclude_none=True)
    wire_bid_price = wire_dump["bids"][0]["price"]

    # 直接 dump は指数表記になる（これが禁止されている理由）
    assert "E" in raw_bid_price or "e" in raw_bid_price, (
        f"Expected raw dump to show exponent form for Decimal('1E-8'), got {raw_bid_price!r}"
    )
    # mapper 経由は固定小数点
    assert "E" not in wire_bid_price and "e" not in wire_bid_price, (
        f"Expected wire to show fixed-point, got {wire_bid_price!r}"
    )
