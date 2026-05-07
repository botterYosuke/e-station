"""KabuStation adapter 単体テスト（B1 / adapter-type-boundary Step 2）。"""

from __future__ import annotations

from datetime import timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from engine.exchanges.kabusapi_adapter import KABU_MAX_SYMBOLS, KabuStationAdapter
from engine.models import OrderBook, Trade


def _push_board_sample(
    *,
    symbol: str = "5401",
    current_price: float | None = 2479.0,
    current_price_time: str = "2020-07-22T15:00:00+09:00",
) -> dict:
    """OpenAPI `PushBoardSuccess` に相当するサンプル JSON。"""
    raw: dict = {
        "Symbol": symbol,
        "SymbolName": "新日鐵住金",
        "Exchange": 1,
        "ExchangeName": "東証１部",
        "CurrentPrice": current_price,
        "CurrentPriceTime": current_price_time,
        "BidPrice": 2479.0,
        "BidQty": 100,
        "AskPrice": 2480.0,
        "AskQty": 200,
        "Sell1": {"Price": 2480.0, "Qty": 200, "Time": "...", "Sign": "0101"},
        "Sell2": {"Price": 2481.0, "Qty": 300, "Time": "...", "Sign": "0101"},
        "Buy1": {"Price": 2479.0, "Qty": 100, "Time": "...", "Sign": "0101"},
        "Buy2": {"Price": 2478.0, "Qty": 400, "Time": "...", "Sign": "0101"},
        "VWAP": 2470.5,
        "TradingVolume": 12345600,
    }
    return raw


# ---------------------------------------------------------------------------
# constructor invariants
# ---------------------------------------------------------------------------


def test_constructor_accepts_up_to_50_symbols() -> None:
    KabuStationAdapter([f"S{i}" for i in range(KABU_MAX_SYMBOLS)])


def test_constructor_rejects_over_50_symbols() -> None:
    with pytest.raises(ValueError, match="50 銘柄"):
        KabuStationAdapter([f"S{i}" for i in range(KABU_MAX_SYMBOLS + 1)])


def test_constructor_rejects_duplicate_symbols() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        KabuStationAdapter(["7203", "7203"])


def test_constructor_preserves_order() -> None:
    adapter = KabuStationAdapter(["7203", "9984", "5401"])
    assert adapter.symbols == ("7203", "9984", "5401")


# ---------------------------------------------------------------------------
# parse_board
# ---------------------------------------------------------------------------


def test_parse_board_returns_order_book() -> None:
    adapter = KabuStationAdapter(["5401"])
    raw = _push_board_sample()
    ob = adapter.parse_board(raw)
    assert isinstance(ob, OrderBook)
    assert ob.symbol == "5401"
    # JST 15:00 -> UTC 06:00
    assert ob.timestamp.tzinfo is not None
    assert ob.timestamp.astimezone(timezone.utc).hour == 6
    # bids / asks: Buy1 -> Buy10 / Sell1 -> Sell10 順、欠損段は無視
    assert ob.bids[0] == (Decimal("2479.0"), Decimal("100"))
    assert ob.bids[1] == (Decimal("2478.0"), Decimal("400"))
    assert ob.asks[0] == (Decimal("2480.0"), Decimal("200"))
    assert ob.asks[1] == (Decimal("2481.0"), Decimal("300"))


def test_parse_board_decimal_precision_preserved() -> None:
    """Decimal が float 経由で精度を失わないこと。"""
    adapter = KabuStationAdapter(["5401"])
    raw = _push_board_sample()
    raw["Buy1"] = {"Price": "2479.555555555", "Qty": "0.123456789"}
    ob = adapter.parse_board(raw)
    assert ob.bids[0][0] == Decimal("2479.555555555")
    assert ob.bids[0][1] == Decimal("0.123456789")


def test_parse_board_handles_missing_levels() -> None:
    """欠損段（Sell5 が None）はスキップ。"""
    adapter = KabuStationAdapter(["5401"])
    raw = _push_board_sample()
    raw["Sell3"] = None  # 欠損段（key は存在するが None）
    raw["Sell4"] = {"Price": None, "Qty": 100}  # Price 欠損
    raw["Sell5"] = {"Price": 2484.0, "Qty": 50}
    ob = adapter.parse_board(raw)
    asks_prices = [p for p, _ in ob.asks]
    assert Decimal("2484.0") in asks_prices
    # None / Price 欠損段は含まれないこと
    assert len(ob.asks) == 3  # Sell1, Sell2, Sell5


def test_parse_board_naive_timestamp_assumed_jst() -> None:
    adapter = KabuStationAdapter(["5401"])
    raw = _push_board_sample(current_price_time="2020-07-22T15:00:00")
    ob = adapter.parse_board(raw)
    # naive を JST と解釈 → UTC 06:00
    assert ob.timestamp.astimezone(timezone.utc).hour == 6


def test_parse_board_missing_timestamp_raises() -> None:
    adapter = KabuStationAdapter(["5401"])
    raw = _push_board_sample()
    raw["CurrentPriceTime"] = None
    with pytest.raises(ValueError, match="timestamp"):
        adapter.parse_board(raw)


# ---------------------------------------------------------------------------
# parse_execution
# ---------------------------------------------------------------------------


def test_parse_execution_returns_trade() -> None:
    adapter = KabuStationAdapter(["5401"])
    raw = _push_board_sample()
    trade = adapter.parse_execution(raw)
    assert isinstance(trade, Trade)
    assert trade.symbol == "5401"
    assert trade.price == Decimal("2479.0")
    # PUSH には side が無いため "unknown" で正規化する
    assert trade.side == "unknown"
    assert trade.qty == Decimal("0")
    assert trade.timestamp.tzinfo is not None


def test_parse_execution_missing_current_price_raises() -> None:
    adapter = KabuStationAdapter(["5401"])
    raw = _push_board_sample(current_price=None)
    with pytest.raises(ValueError, match="CurrentPrice"):
        adapter.parse_execution(raw)


# ---------------------------------------------------------------------------
# roundtrip: 生 JSON → モデル → 値が崩れないこと
# ---------------------------------------------------------------------------


def test_roundtrip_preserves_decimal_string_input() -> None:
    """JSON 由来の数値が string でも float でも同じ Decimal 値になる。"""
    adapter_str = KabuStationAdapter(["5401"])
    raw_str = _push_board_sample()
    raw_str["Sell1"] = {"Price": "2480.5", "Qty": "100"}

    adapter_float = KabuStationAdapter(["5401"])
    raw_float = _push_board_sample()
    raw_float["Sell1"] = {"Price": 2480.5, "Qty": 100}

    ob_str = adapter_str.parse_board(raw_str)
    ob_float = adapter_float.parse_board(raw_float)
    assert ob_str.asks[0] == ob_float.asks[0]


def test_parse_board_pydantic_validation_propagates() -> None:
    """OrderBook 構築時の pydantic バリデーション失敗が呼び元に伝搬する。"""
    adapter = KabuStationAdapter(["5401"])
    raw = _push_board_sample()
    raw["Symbol"] = ""  # str だが空 → 上位 schema 制約は無いので OK
    # naive timestamp ではなく、明示的に invalid になるパターンを確認するため
    # `_parse_jst_to_utc` が ValueError を raise するケースを使う。
    raw["CurrentPriceTime"] = ""
    with pytest.raises(ValueError):
        adapter.parse_board(raw)


def test_parse_board_missing_symbol_key_raises_key_error() -> None:
    """'Symbol' キー自体が欠損した raw dict では KeyError が送出される。

    server.py は事前に `raw.get("Symbol", "")` でガードするが、
    parse_board 自体は raw["Symbol"] を直接アクセスするため KeyError を出す。
    この動作を pin することで「Symbol 欠損を黙って空文字にする」修正を防ぐ。
    呼び出し元（_on_kabu_board_push）の except Exception が捕捉する設計。
    """
    adapter = KabuStationAdapter(["5401"])
    raw = _push_board_sample()
    del raw["Symbol"]
    with pytest.raises(KeyError):
        adapter.parse_board(raw)
