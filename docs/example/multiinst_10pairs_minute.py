"""10銘柄一括購読サンプル（分足版・メモリ負荷テスト用）。

T-AC-5 メモリテスト向けに 10 銘柄を同時リプレイする最小サンプルです。
schema_version: 2 と instruments リストで 10 銘柄を指定します。

起動:

    uv run python -m engine.replay_session run \
        --strategy docs/example/multiinst_10pairs_minute.py \
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


SCENARIO: Scenario = {
    "schema_version": 2,
    "instruments": [
        "1301.TSE",
        "7203.TSE",
        "6758.TSE",
        "9984.TSE",
        "8306.TSE",
        "7974.TSE",
        "4502.TSE",
        "6861.TSE",
        "8035.TSE",
        "9433.TSE",
    ],
    "start": "2025-01-06",
    "end": "2025-01-10",
    "granularity": "Minute",
    "initial_cash": 10_000_000,
}

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy


class MultiInstMinuteStrategy(Strategy):
    """10銘柄を等重購読し、各銘柄の初回バーで成行買いする最小戦略。"""

    def __init__(self, *, lot_size: int = 100) -> None:
        super().__init__(config=StrategyConfig(strategy_id="multiinst-10pairs-minute"))
        self.lot_size = int(lot_size)
        self._bought: dict[str, bool] = {}

    def on_start(self) -> None:
        instruments = SCENARIO["instruments"]
        for sym in instruments:
            instrument_id = InstrumentId.from_str(sym)
            instrument = self.cache.instrument(instrument_id)
            if instrument is None:
                self.log.error(f"Instrument not found: {sym}")
                continue
            bar_type = BarType.from_str(f"{sym}-1-MINUTE-LAST-EXTERNAL")
            self.subscribe_bars(bar_type)
            self._bought[sym] = False
        self.log.info(f"Subscribed {len(self._bought)} instruments")

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
