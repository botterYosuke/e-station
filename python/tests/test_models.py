"""adapter boundary モデル単体テスト（A1 / adapter-type-boundary Step 1）。

確認項目:
- Decimal 精度（float 禁止、文字列入力での精度維持）
- side バリデーション
- timestamp の timezone-aware 強制
- frozen による immutability
- DepthDiff フィールド欠損時の ValidationError
- adapter model に wire 責務フィールドが含まれないこと
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from engine.models import DepthDiff, Instrument, OrderBook, Trade


JST = timezone(timedelta(hours=9))


def _utc(s: str = "2026-05-07T12:00:00+00:00") -> datetime:
    return datetime.fromisoformat(s)


# ---------------------------------------------------------------------------
# Instrument
# ---------------------------------------------------------------------------


def test_instrument_basic_construction() -> None:
    inst = Instrument(
        symbol="7203",
        display_name="Toyota",
        price_tick=Decimal("0.5"),
        lot_size=100,
        currency="JPY",
    )
    assert inst.price_tick == Decimal("0.5")
    assert inst.lot_size == 100


def test_instrument_price_tick_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Instrument(
            symbol="x",
            display_name="x",
            price_tick=Decimal("0"),
            lot_size=1,
            currency="JPY",
        )


def test_instrument_lot_size_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Instrument(
            symbol="x",
            display_name="x",
            price_tick=Decimal("1"),
            lot_size=0,
            currency="JPY",
        )


# ---------------------------------------------------------------------------
# OrderBook
# ---------------------------------------------------------------------------


def test_order_book_decimal_precision() -> None:
    ob = OrderBook(
        symbol="BTCUSDT",
        timestamp=_utc(),
        bids=[(Decimal("12345.67890123"), Decimal("0.0001"))],
        asks=[(Decimal("12345.67890124"), Decimal("0.0002"))],
    )
    assert ob.bids[0][0] == Decimal("12345.67890123")
    assert ob.asks[0][1] == Decimal("0.0002")


def test_order_book_naive_timestamp_rejected() -> None:
    naive = datetime(2026, 5, 7, 12, 0, 0)
    with pytest.raises(ValidationError):
        OrderBook(symbol="x", timestamp=naive, bids=[], asks=[])


def test_order_book_jst_to_utc_roundtrip() -> None:
    """JST 入力を UTC に変換しても保存値が等価であることを確認。"""
    jst_ts = datetime(2026, 5, 7, 21, 0, 0, tzinfo=JST)
    ob = OrderBook(
        symbol="7203",
        timestamp=jst_ts.astimezone(timezone.utc),
        bids=[],
        asks=[],
    )
    assert ob.timestamp == datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc)


def test_order_book_optional_session_fields() -> None:
    ob = OrderBook(
        symbol="x",
        timestamp=_utc(),
        bids=[],
        asks=[],
        stream_session_id="sess-1",
        sequence_id=42,
    )
    assert ob.stream_session_id == "sess-1"
    assert ob.sequence_id == 42


# ---------------------------------------------------------------------------
# DepthDiff
# ---------------------------------------------------------------------------


def test_depth_diff_required_fields() -> None:
    diff = DepthDiff(
        symbol="x",
        timestamp=_utc(),
        bids=[(Decimal("100"), Decimal("0"))],
        asks=[(Decimal("101"), Decimal("1"))],
        stream_session_id="sess-1",
        sequence_id=2,
        prev_sequence_id=1,
    )
    assert diff.sequence_id == 2
    assert diff.prev_sequence_id == 1
    assert diff.stream_session_id == "sess-1"


@pytest.mark.parametrize(
    "missing",
    ["stream_session_id", "sequence_id", "prev_sequence_id"],
)
def test_depth_diff_missing_required_field_raises(missing: str) -> None:
    payload: dict = dict(
        symbol="x",
        timestamp=_utc(),
        bids=[],
        asks=[],
        stream_session_id="s",
        sequence_id=1,
        prev_sequence_id=0,
    )
    payload.pop(missing)
    with pytest.raises(ValidationError):
        DepthDiff(**payload)


def test_depth_diff_naive_timestamp_rejected() -> None:
    with pytest.raises(ValidationError):
        DepthDiff(
            symbol="x",
            timestamp=datetime(2026, 5, 7, 12, 0, 0),
            bids=[],
            asks=[],
            stream_session_id="s",
            sequence_id=1,
            prev_sequence_id=0,
        )


# ---------------------------------------------------------------------------
# Trade
# ---------------------------------------------------------------------------


def test_trade_side_validation() -> None:
    Trade(
        symbol="x",
        timestamp=_utc(),
        price=Decimal("100"),
        qty=Decimal("1"),
        side="buy",
    )
    Trade(
        symbol="x",
        timestamp=_utc(),
        price=Decimal("100"),
        qty=Decimal("1"),
        side="sell",
    )
    Trade(
        symbol="x",
        timestamp=_utc(),
        price=Decimal("100"),
        qty=Decimal("1"),
        side="unknown",
    )

    with pytest.raises(ValidationError):
        Trade(
            symbol="x",
            timestamp=_utc(),
            price=Decimal("100"),
            qty=Decimal("1"),
            side="LONG",  # type: ignore[arg-type]
        )


def test_trade_negative_price_rejected() -> None:
    with pytest.raises(ValidationError):
        Trade(
            symbol="x",
            timestamp=_utc(),
            price=Decimal("-1"),
            qty=Decimal("1"),
            side="buy",
        )


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


def test_models_are_frozen() -> None:
    inst = Instrument(
        symbol="x",
        display_name="x",
        price_tick=Decimal("1"),
        lot_size=1,
        currency="JPY",
    )
    with pytest.raises(ValidationError):
        inst.symbol = "y"  # type: ignore[misc]

    ob = OrderBook(symbol="x", timestamp=_utc(), bids=[], asks=[])
    with pytest.raises(ValidationError):
        ob.symbol = "y"  # type: ignore[misc]

    trade = Trade(
        symbol="x",
        timestamp=_utc(),
        price=Decimal("100"),
        qty=Decimal("1"),
        side="buy",
    )
    with pytest.raises(ValidationError):
        trade.price = Decimal("0")  # type: ignore[misc]


# ---------------------------------------------------------------------------
# adapter model boundary: wire 責務フィールドが混入していないこと
# ---------------------------------------------------------------------------


_FORBIDDEN_WIRE_FIELDS = {"event", "venue", "market", "request_id"}


@pytest.mark.parametrize("model_cls", [Instrument, OrderBook, DepthDiff, Trade])
def test_adapter_models_have_no_wire_fields(model_cls) -> None:
    """adapter model のフィールド集合に wire 責務名が含まれないこと。

    Step 3 / C1 で wire 送出は mappers.py 経由を強制する設計上の不変条件。
    """
    fields = set(model_cls.model_fields.keys())
    leaked = fields & _FORBIDDEN_WIRE_FIELDS
    assert not leaked, (
        f"{model_cls.__name__} has wire fields {leaked}; "
        "wire responsibilities belong to schemas.py (mappers.py 経由で補充)"
    )
