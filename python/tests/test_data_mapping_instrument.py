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


class TestInstrumentFactoryLotSizeResolution:
    """N1.2: InstrumentCache + lot_size_override の優先順位を検証する。"""

    def test_explicit_lot_size_kwarg_still_works(self):
        """旧 N0 互換: ``lot_size=100`` を渡せばそのまま使われる。"""
        from nautilus_trader.model.objects import Quantity
        inst = make_equity_instrument("7203", "TSE", lot_size=100)
        assert inst.lot_size == Quantity(100, precision=0)

    def test_lot_size_override_applied(self, tmp_path, monkeypatch):
        """``lot_size_override`` が cache より優先される。"""
        from engine.nautilus.instrument_cache import InstrumentCache
        from nautilus_trader.model.objects import Quantity

        # shared singleton を一時パスへ差し替え
        InstrumentCache.reset_shared_for_testing()
        monkeypatch.setattr(
            "engine.nautilus.instrument_cache._default_cache_path",
            lambda: tmp_path / "master.json",
        )
        try:
            inst = make_equity_instrument(
                "1301", "TSE", lot_size_override={"1301.TSE": 1}
            )
            assert inst.lot_size == Quantity(1, precision=0)
        finally:
            InstrumentCache.reset_shared_for_testing()

    def test_default_call_falls_back_to_100(self, tmp_path, monkeypatch):
        """引数省略時の cache miss → fallback=100（既存挙動の後方互換）。"""
        from engine.nautilus.instrument_cache import InstrumentCache
        from nautilus_trader.model.objects import Quantity

        InstrumentCache.reset_shared_for_testing()
        monkeypatch.setattr(
            "engine.nautilus.instrument_cache._default_cache_path",
            lambda: tmp_path / "master.json",
        )
        try:
            inst = make_equity_instrument("9999", "TSE")
            assert inst.lot_size == Quantity(100, precision=0)
        finally:
            InstrumentCache.reset_shared_for_testing()
