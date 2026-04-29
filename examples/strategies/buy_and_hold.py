"""Buy-and-Hold ユーザー戦略サンプル（最小版）。

最初の Bar を受信した時点で 1 lot 成行買いし、以後はホールドするだけの
最小サンプルです。ユーザー戦略の書き方の出発点として使ってください。

起動:

    cargo run -- --mode replay --strategy-file examples/strategies/buy_and_hold.py

パラメータは N4.1 の ``init_kwargs`` 経由（HTTP body の
``strategy_init_kwargs`` JSON）で渡せます:

    {"instrument_id": "1301.TSE", "lot_size": 100}

注意:
    - サンドボックスはありません。バグによる誤発注はユーザー責任です。
      ``examples/strategies/README.md`` の「自己責任」項目を必ず読んでください
    - nautilus_trader 1.225.0 の API で動作確認しています
"""

from __future__ import annotations

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy


class BuyAndHoldStrategy(Strategy):
    """最初の Bar で 1 lot 成行買い、以後は何もしないシンプル戦略。"""

    def __init__(
        self,
        *,
        instrument_id: str,
        lot_size: int = 100,
        bar_type_str: str | None = None,
    ) -> None:
        super().__init__(config=StrategyConfig(strategy_id="buy-and-hold-example"))
        self.instrument_id = InstrumentId.from_str(instrument_id)
        self.lot_size = int(lot_size)
        # 既定: 1 分足 LAST EXTERNAL（replay の典型）
        self.bar_type_str = (
            bar_type_str or f"{instrument_id}-1-MINUTE-LAST-EXTERNAL"
        )
        self.bought = False

    def on_start(self) -> None:
        instrument = self.cache.instrument(self.instrument_id)
        if instrument is None:
            self.log.error(f"Instrument not found: {self.instrument_id}")
            return
        self.subscribe_bars(BarType.from_str(self.bar_type_str))

    def on_bar(self, bar: Bar) -> None:
        if self.bought:
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
        self.bought = True
