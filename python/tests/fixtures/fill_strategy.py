"""テスト用 fill 戦略。

最初の bar で BUY、2 番目の bar で SELL する最小実装。
テスト fixture データ（equities_bars_daily_202401.csv.gz 2 件）で
必ず 2 回の fill が発生することを保証する。
"""

from __future__ import annotations

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy


class FillTestStrategy(Strategy):
    """bar 1 で BUY、bar 2 で SELL する決定論的テスト戦略。

    Notes
    -----
    このテストは fixtures/equities_bars_daily_202401.csv.gz の
    bar 順序（2024-01-04 = bar 1, 2024-01-05 = bar 2）に依存する。
    fixture データを変更する場合は on_bar の発注ロジックも確認すること。
    """

    def __init__(
        self,
        *,
        instrument_id: str = "1301.TSE",
        lot_size: int = 100,
        strategy_id: str = "fill-test",
    ) -> None:
        super().__init__(config=StrategyConfig(strategy_id=strategy_id))
        self.instrument_id = InstrumentId.from_str(instrument_id)
        self.lot_size = int(lot_size)
        self.bar_count = 0

    def on_start(self) -> None:
        bar_type_str = f"{self.instrument_id}-1-DAY-LAST-EXTERNAL"
        self.subscribe_bars(BarType.from_str(bar_type_str))

    def on_bar(self, bar: Bar) -> None:  # noqa: ARG002
        self.bar_count += 1
        instrument = self.cache.instrument(self.instrument_id)
        if instrument is None:
            return

        if self.bar_count == 1:
            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.BUY,
                quantity=Quantity.from_int(self.lot_size),
                time_in_force=TimeInForce.DAY,
            )
            self.submit_order(order)
        elif self.bar_count == 2:
            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.SELL,
                quantity=Quantity.from_int(self.lot_size),
                time_in_force=TimeInForce.DAY,
            )
            self.submit_order(order)
