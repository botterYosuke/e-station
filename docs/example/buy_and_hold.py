"""バイアンドホールド戦略サンプル（動作確認・決定論性テスト用）。

最初のバーで成行買いし、その後は保有し続ける最小戦略。
numpy / pandas は使わず、追加依存は不要です。

起動:

    bash scripts/run-replay-debug.sh docs/example/buy_and_hold.py 1301.TSE 2025-01-06 2025-03-31

strategy_init_kwargs で初期化パラメータを上書きできます:

    {"instrument_id": "1301.TSE", "lot_size": 100}

注意:
    - サンドボックスはありません。バグによる誤発注はユーザー責任です
    - 教育用の最小実装です。スリッページ・手数料・リスク管理は含みません
"""

from __future__ import annotations

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy


class BuyAndHoldStrategy(Strategy):
    """最初のバーで成行買いし、以降は保有し続ける最小戦略。"""

    def __init__(
        self,
        *,
        instrument_id: str = "1301.TSE",
        lot_size: int = 100,
        bar_type_str: str | None = None,
    ) -> None:
        super().__init__(config=StrategyConfig(strategy_id="buy-and-hold"))
        self.instrument_id = InstrumentId.from_str(instrument_id)
        self.lot_size = int(lot_size)
        self.bar_type_str = bar_type_str or f"{instrument_id}-1-DAY-LAST-EXTERNAL"
        self._bought = False

    def on_start(self) -> None:
        instrument = self.cache.instrument(self.instrument_id)
        if instrument is None:
            self.log.error(f"Instrument not found: {self.instrument_id}")
            return
        self.subscribe_bars(BarType.from_str(self.bar_type_str))
        self.log.info(
            f"BuyAndHoldStrategy started: instrument={self.instrument_id} "
            f"lot_size={self.lot_size} bar_type={self.bar_type_str}"
        )

    def on_bar(self, bar: Bar) -> None:
        if self._bought:
            return
        instrument = self.cache.instrument(self.instrument_id)
        if instrument is None:
            self.log.warning(f"instrument not in cache: {self.instrument_id}")
            return
        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=OrderSide.BUY,
            quantity=Quantity.from_int(self.lot_size),
            time_in_force=TimeInForce.DAY,
        )
        self.submit_order(order)
        self._bought = True
        self.log.info(f"BUY: {self.lot_size} shares @ {bar.close}")
