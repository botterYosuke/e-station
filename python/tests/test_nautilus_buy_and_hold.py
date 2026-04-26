"""Tests for BuyAndHold strategy (N0.4).

1 年分 BTC 日足を投入 → 最終 equity が初期資金より大きい（or NaN でない）ことを検証。
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from decimal import Decimal

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig
from nautilus_trader.model.currencies import JPY
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import Equity
from nautilus_trader.model.objects import Price, Quantity, Money

from engine.nautilus.data_loader import KlineRow, klines_to_bars
from engine.nautilus.strategies.buy_and_hold import BuyAndHoldStrategy

_BYPASS = LoggingConfig(bypass_logging=True)


def _make_equity(symbol: str = "7203", venue: str = "TSE") -> Equity:
    return Equity(
        instrument_id=InstrumentId(Symbol(symbol), Venue(venue)),
        raw_symbol=Symbol(symbol),
        currency=JPY,
        price_precision=1,
        price_increment=Price(Decimal("0.1"), precision=1),
        lot_size=Quantity(100, precision=0),
        isin=None,
        ts_event=0,
        ts_init=0,
    )


def _make_year_of_bars(instrument: Equity) -> list[Bar]:
    """2024 年 1 月〜12 月の日足データ（250 本）を生成する。"""
    klines: list[KlineRow] = []
    base = datetime(2024, 1, 4, tzinfo=timezone.utc)
    close = 2000.0
    for i in range(250):
        dt = base + timedelta(days=i)
        date_str = dt.strftime("%Y%m%d")
        close = max(1000.0, close + (10 if i % 2 == 0 else -5))  # 緩やかな上昇トレンド
        klines.append(
            KlineRow(
                date=date_str,
                open=str(close - 10),
                high=str(close + 20),
                low=str(close - 20),
                close=str(close),
                volume="1000",
            )
        )
    return klines_to_bars(str(instrument.id.symbol), str(instrument.id.venue), klines)


class TestBuyAndHoldStrategy:
    def setup_method(self):
        self.engine = BacktestEngine(
            config=BacktestEngineConfig(trader_id="TEST-BAH-001", logging=_BYPASS)
        )
        self.instrument = _make_equity()
        self.engine.add_venue(
            venue=Venue("TSE"),
            oms_type=OmsType.NETTING,
            account_type=AccountType.CASH,
            base_currency=JPY,
            starting_balances=[Money(1_000_000, JPY)],
        )
        self.engine.add_instrument(self.instrument)

    def teardown_method(self):
        self.engine.dispose()

    def test_strategy_processes_all_bars(self):
        bars = _make_year_of_bars(self.instrument)
        self.engine.add_data(bars)
        strategy = BuyAndHoldStrategy(instrument_id=self.instrument.id)
        self.engine.add_strategy(strategy)
        self.engine.run()
        assert strategy.bar_count > 0

    def test_final_equity_not_nan(self):
        bars = _make_year_of_bars(self.instrument)
        self.engine.add_data(bars)
        strategy = BuyAndHoldStrategy(instrument_id=self.instrument.id)
        self.engine.add_strategy(strategy)
        self.engine.run()

        # BacktestEngine.kernel.portfolio でアカウント残高を確認
        account = self.engine.kernel.portfolio.account(Venue("TSE"))
        balance = account.balance_total(JPY)
        assert balance is not None
        assert balance.as_decimal() > 0

    def test_bought_on_first_bar(self):
        """BuyAndHold は最初のバーで買いを入れる。"""
        bars = _make_year_of_bars(self.instrument)
        self.engine.add_data(bars)
        strategy = BuyAndHoldStrategy(instrument_id=self.instrument.id)
        self.engine.add_strategy(strategy)
        self.engine.run()
        assert strategy.bought is True
