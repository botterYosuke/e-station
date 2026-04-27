"""M-4: data-mapping.md §2 の写像を検証するテスト (N0)

Instrument フィールドが spec の仮置き値と一致することを確認する。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from engine.nautilus.instrument_factory import make_equity_instrument


class TestDataMappingInstrument:
    def test_symbol_mapped_correctly(self):
        inst = make_equity_instrument("7203", "TSE")
        assert str(inst.id.symbol) == "7203"

    def test_venue_mapped_correctly(self):
        inst = make_equity_instrument("7203", "TSE")
        assert str(inst.id.venue) == "TSE"

    def test_price_precision_is_1(self):
        inst = make_equity_instrument("7203", "TSE")
        assert inst.price_precision == 1

    def test_price_increment_is_0_1(self):
        inst = make_equity_instrument("7203", "TSE")
        assert str(inst.price_increment) == "0.1"

    def test_lot_size_is_100(self):
        inst = make_equity_instrument("7203", "TSE")
        from nautilus_trader.model.objects import Quantity
        assert inst.lot_size == Quantity(100, precision=0)

    def test_currency_is_jpy(self):
        from nautilus_trader.model.currencies import JPY
        inst = make_equity_instrument("7203", "TSE")
        assert inst.quote_currency == JPY
