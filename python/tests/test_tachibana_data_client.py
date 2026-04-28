"""N2.0: TachibanaLiveDataClient / trade_dict_to_tick テスト"""

from __future__ import annotations

import pytest
from decimal import Decimal

from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.data import TradeTick

from engine.nautilus.clients.tachibana_data import trade_dict_to_tick


# ---------------------------------------------------------------------------
# trade_dict_to_tick
# ---------------------------------------------------------------------------


def _make_trade(
    price: str = "3775",
    qty: str = "100",
    side: str = "buy",
    ts_ms: int = 1_704_067_200_000,
) -> dict:
    return {"price": price, "qty": qty, "side": side, "ts_ms": ts_ms}


class TestTradeDictToTick:
    """FD frame trade dict → TradeTick 変換"""

    def test_buy_side_maps_to_buyer(self):
        trade = _make_trade(side="buy")
        tick = trade_dict_to_tick("7203.TSE", trade)
        assert tick.aggressor_side == AggressorSide.BUYER

    def test_sell_side_maps_to_seller(self):
        trade = _make_trade(side="sell")
        tick = trade_dict_to_tick("7203.TSE", trade)
        assert tick.aggressor_side == AggressorSide.SELLER

    def test_unknown_side_maps_to_no_aggressor(self):
        trade = _make_trade(side="unknown")
        tick = trade_dict_to_tick("7203.TSE", trade)
        assert tick.aggressor_side == AggressorSide.NO_AGGRESSOR

    def test_unrecognized_side_maps_to_no_aggressor(self):
        trade = _make_trade(side="ambiguous")
        tick = trade_dict_to_tick("7203.TSE", trade)
        assert tick.aggressor_side == AggressorSide.NO_AGGRESSOR

    def test_price_precision(self):
        trade = _make_trade(price="1234.5")
        tick = trade_dict_to_tick("7203.TSE", trade, price_precision=1)
        assert str(tick.price) == "1234.5"

    def test_size_precision_zero(self):
        trade = _make_trade(qty="200")
        tick = trade_dict_to_tick("7203.TSE", trade, size_precision=0)
        assert str(tick.size) == "200"

    def test_ts_event_is_ms_to_ns(self):
        ts_ms = 1_704_067_200_123
        trade = _make_trade(ts_ms=ts_ms)
        tick = trade_dict_to_tick("7203.TSE", trade)
        assert tick.ts_event == ts_ms * 1_000_000
        assert tick.ts_init == ts_ms * 1_000_000

    def test_trade_id_format(self):
        ts_ms = 1_704_067_200_000
        trade = _make_trade(ts_ms=ts_ms)
        tick = trade_dict_to_tick("7203.TSE", trade, seq=0)
        assert str(tick.trade_id) == f"L-{ts_ms}-0"

    def test_trade_id_seq_increments(self):
        ts_ms = 1_704_067_200_000
        trade = _make_trade(ts_ms=ts_ms)
        tick0 = trade_dict_to_tick("7203.TSE", trade, seq=0)
        tick1 = trade_dict_to_tick("7203.TSE", trade, seq=1)
        assert str(tick0.trade_id) == f"L-{ts_ms}-0"
        assert str(tick1.trade_id) == f"L-{ts_ms}-1"

    def test_instrument_id_parsed_correctly(self):
        trade = _make_trade()
        tick = trade_dict_to_tick("7203.TSE", trade)
        assert str(tick.instrument_id) == "7203.TSE"

    def test_returns_trade_tick_instance(self):
        trade = _make_trade()
        tick = trade_dict_to_tick("7203.TSE", trade)
        assert isinstance(tick, TradeTick)

    def test_integer_price_as_string(self):
        trade = _make_trade(price="3000")
        tick = trade_dict_to_tick("7203.TSE", trade)
        assert str(tick.price) == "3000.0"


# ---------------------------------------------------------------------------
# NO_AGGRESSOR 比率 sanity check
# ---------------------------------------------------------------------------


class TestNoAggressorRatio:
    """live の aggressor_side は全部 NO_AGGRESSOR にはならないはず（sanity）。"""

    def test_known_sides_are_distinguishable(self):
        trades = [
            _make_trade(side="buy"),
            _make_trade(side="sell"),
            _make_trade(side="buy"),
            _make_trade(side="unknown"),
        ]
        ticks = [trade_dict_to_tick("7203.TSE", t) for t in trades]
        buyer_count = sum(1 for t in ticks if t.aggressor_side == AggressorSide.BUYER)
        seller_count = sum(1 for t in ticks if t.aggressor_side == AggressorSide.SELLER)
        no_agg_count = sum(1 for t in ticks if t.aggressor_side == AggressorSide.NO_AGGRESSOR)
        assert buyer_count == 2
        assert seller_count == 1
        assert no_agg_count == 1
