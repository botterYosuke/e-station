"""BuyAndHold サンプル戦略 (N0.4 / N1.4 拡張)

最初の入力 (Bar or TradeTick) で lot_size 分だけ成行買いし、以後はホールドし続ける。
backtest の決定論性テスト・smoke テスト・replay E2E の入力として使う。

N1.4 拡張: ``subscribe_kind`` (``"bar"`` | ``"trade"``) で購読対象を切替。
``"bar"`` 時は ``bar_type`` (``"-1-DAY-MID-EXTERNAL"`` 等) を指定。
"""

from __future__ import annotations

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType, TradeTick
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.strategy import Strategy


class BuyAndHoldStrategy(Strategy):
    """最初のバー / 最初の trade tick で 1 lot 成行買い、以後は放置するシンプル戦略。"""

    def __init__(
        self,
        instrument_id: InstrumentId,
        *,
        subscribe_kind: str = "bar",
        bar_type_str: str | None = None,
    ) -> None:
        super().__init__(config=StrategyConfig(strategy_id="buy-and-hold-001"))
        self.instrument_id = instrument_id
        self.subscribe_kind = subscribe_kind
        # N0 互換: 既定 "-1-DAY-MID-EXTERNAL" を維持。N1.4 replay (bars) は
        # ``"-1-MINUTE-LAST-EXTERNAL"`` などを呼出側から指定する。
        self.bar_type_str = bar_type_str or f"{instrument_id}-1-DAY-MID-EXTERNAL"
        self.bar_count = 0
        self.tick_count = 0
        self.bought = False

    def on_start(self) -> None:
        instrument = self.cache.instrument(self.instrument_id)
        if instrument is None:
            self.log.error(f"Instrument not found: {self.instrument_id}")
            return
        if self.subscribe_kind == "trade":
            self.subscribe_trade_ticks(self.instrument_id)
        else:
            self.subscribe_bars(BarType.from_str(self.bar_type_str))

    def _maybe_buy(self) -> None:
        if self.bought:
            return
        instrument = self.cache.instrument(self.instrument_id)
        if instrument is None:
            self.log.warning(
                "instrument not found in cache: %s", self.instrument_id
            )
            return
        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=OrderSide.BUY,
            quantity=instrument.lot_size,
            time_in_force=TimeInForce.DAY,
        )
        self.submit_order(order)
        self.bought = True

    def on_bar(self, bar: Bar) -> None:
        self.bar_count += 1
        self._maybe_buy()

    def on_trade_tick(self, tick: TradeTick) -> None:
        self.tick_count += 1
        self._maybe_buy()
