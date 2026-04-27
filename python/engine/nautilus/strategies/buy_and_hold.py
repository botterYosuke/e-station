"""BuyAndHold サンプル戦略 (N0.4)

最初のバーで lot_size 分だけ成行買いし、以後はホールドし続ける。
backtest の決定論性テスト・smoke テストの入力として使う。
"""

from __future__ import annotations

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy


class BuyAndHoldStrategy(Strategy):
    """最初のバーで 1 lot 成行買い、以後は放置するシンプル戦略。"""

    def __init__(self, instrument_id: InstrumentId) -> None:
        super().__init__(config=StrategyConfig(strategy_id="buy-and-hold-001"))
        self.instrument_id = instrument_id
        self.bar_count = 0
        self.bought = False

    def on_start(self) -> None:
        instrument = self.cache.instrument(self.instrument_id)
        if instrument is None:
            self.log.error(f"Instrument not found: {self.instrument_id}")
            return
        bar_type = BarType.from_str(
            f"{self.instrument_id}-1-DAY-MID-EXTERNAL"
        )
        self.subscribe_bars(bar_type)

    def on_bar(self, bar: Bar) -> None:
        self.bar_count += 1
        if self.bought:
            return

        instrument = self.cache.instrument(self.instrument_id)
        if instrument is None:
            self.log.warning("on_bar: instrument not found in cache: %s", self.instrument_id)
            return

        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=OrderSide.BUY,
            quantity=instrument.lot_size,
            time_in_force=TimeInForce.DAY,
        )
        self.submit_order(order)
        self.bought = True
