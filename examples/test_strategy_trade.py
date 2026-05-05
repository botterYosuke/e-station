"""バイアンドホールド戦略サンプル（歩み値版・動作確認用）。

`buy_and_hold.py` の Trade（歩み値）バリエーションです。granularity を
``"Trade"`` にし、Bar ではなく TradeTick を購読して最初の歩み値で成行買い、
以降は保有し続けます。GUI の TimeAndSales ペインに歩み値が流れる様子を
目視確認するのが目的です。

起動（headless / in-process）:

    uv run python -m engine.replay_session run \
        --strategy examples/buy_and_hold_trade.py \
        --instrument 1301.TSE --start 2025-01-06 --end 2025-01-06 \
        --granularity Trade --mode inprocess

GUI 付きで attach する場合は別ターミナルで先に `cargo run -- --mode replay`
を起動してから上記コマンドを `--mode auto`（または `attach`）で実行する。
詳しい手順は docs/wiki/backtest.md / examples/README.md を参照。

注意:
    - サンドボックスはありません。バグによる誤発注はユーザー責任です
    - 教育用の最小実装です。スリッページ・手数料・リスク管理は含みません
    - 歩み値は 1 営業日でも数千〜数万 tick になり Daily/Minute より重いため、
      初回確認はまず単日（start == end）で動かすことを推奨します
"""

from __future__ import annotations

from typing import TypedDict

from nautilus_trader.config import StrategyConfig


class Scenario(TypedDict):
    schema_version: int
    instrument: str
    start: str
    end: str
    granularity: str
    initial_cash: int


SCENARIO: Scenario = {
    "schema_version": 1,
    "instrument": "1301.TSE",
    "start": "2025-01-06",
    "end": "2025-01-06",
    "granularity": "Trade",
    "initial_cash": 1_000_000,
}
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy


class BuyAndHoldTradeStrategy(Strategy):
    """最初の歩み値で成行買いし、以降は保有し続ける最小戦略。"""

    def __init__(
        self,
        *,
        instrument_id: str = "1301.TSE",
        lot_size: int = 100,
    ) -> None:
        super().__init__(config=StrategyConfig(strategy_id="buy-and-hold-trade"))
        self.instrument_id = InstrumentId.from_str(instrument_id)
        self.lot_size = int(lot_size)
        self._bought = False

    def on_start(self) -> None:
        instrument = self.cache.instrument(self.instrument_id)
        if instrument is None:
            self.log.error(f"Instrument not found: {self.instrument_id}")
            return
        self.subscribe_trade_ticks(self.instrument_id)
        self.log.info(
            f"BuyAndHoldTradeStrategy started: instrument={self.instrument_id} "
            f"lot_size={self.lot_size}"
        )

    def on_trade_tick(self, tick: TradeTick) -> None:
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
        self.log.info(f"BUY: {self.lot_size} shares @ {tick.price}")
