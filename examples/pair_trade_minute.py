"""2銘柄ペアトレード戦略サンプル（分足版）。

複数銘柄リプレイの使い方を示す最小サンプルです。
schema_version: 2 と instruments リストを使って 2 銘柄を同時にリプレイします。

起動:

    uv run python -m engine.replay_session run \
        --strategy examples/pair_trade_minute.py \
        --mode inprocess

注意:
    - サンドボックスはありません。バグによる誤発注はユーザー責任です
    - 教育用の最小実装です。スリッページ・手数料・リスク管理は含みません
"""

from __future__ import annotations

from typing import TypedDict


class Scenario(TypedDict):
    schema_version: int
    instruments: list[str]
    start: str
    end: str
    granularity: str
    initial_cash: int


# schema_version: 2 で instruments リストを使う（複数銘柄対応）
SCENARIO: Scenario = {
    "schema_version": 2,
    "instruments": ["1301.TSE", "7203.TSE"],
    "start": "2025-01-06",
    "end": "2025-01-10",
    "granularity": "Minute",
    "initial_cash": 1_000_000,
}

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy


class PairTradeMinuteStrategy(Strategy):
    """2銘柄を購読し、銘柄ごとに初回バーで成行買いする最小戦略。"""

    INSTRUMENT_IDS = ["1301.TSE", "7203.TSE"]

    def __init__(self, *, lot_size: int = 100) -> None:
        super().__init__(config=StrategyConfig(strategy_id="pair-trade-minute"))
        self.lot_size = int(lot_size)
        # 銘柄ごとに購入済みフラグを管理する
        self._bought: dict[str, bool] = {sym: False for sym in self.INSTRUMENT_IDS}

    def on_start(self) -> None:
        # 2 銘柄分の分足を購読する
        for sym in self.INSTRUMENT_IDS:
            instrument_id = InstrumentId.from_str(sym)
            instrument = self.cache.instrument(instrument_id)
            if instrument is None:
                self.log.error(f"Instrument not found: {sym}")
                continue
            bar_type = BarType.from_str(f"{sym}-1-MINUTE-LAST-EXTERNAL")
            self.subscribe_bars(bar_type)
            self.log.info(f"Subscribed: {bar_type}")

    def on_bar(self, bar: Bar) -> None:
        # str(bar.bar_type.instrument_id) で "1301.TSE" 形式の完全な ID を取得する
        inst_str = str(bar.bar_type.instrument_id)

        if self._bought.get(inst_str, True):
            return

        instrument_id = InstrumentId.from_str(inst_str)
        instrument = self.cache.instrument(instrument_id)
        if instrument is None:
            self.log.warning(f"instrument not in cache: {inst_str}")
            return

        order = self.order_factory.market(
            instrument_id=instrument_id,
            order_side=OrderSide.BUY,
            quantity=Quantity.from_int(self.lot_size),
            time_in_force=TimeInForce.DAY,
        )
        self.submit_order(order)
        self._bought[inst_str] = True
        self.log.info(f"BUY {inst_str}: {self.lot_size} shares @ {bar.close}")
