"""バイアンドホールド戦略サンプル（分足版・動作確認用）。

`buy_and_hold.py` の Minute 足バリエーションです。granularity を ``"Minute"``
にし、bar_type を ``1-MINUTE-LAST-EXTERNAL`` で購読する点だけが違います。
最初の分足で成行買いし、その後は保有し続けます。

起動（headless / in-process）:

    uv run python -m engine.replay_session run \
        --strategy examples/buy_and_hold_minute.py \
        --instrument 1301.TSE --start 2025-01-06 --end 2025-01-10 \
        --granularity Minute --mode inprocess

GUI 付きで attach する場合は別ターミナルで先に `cargo run -- --mode replay`
を起動してから上記コマンドを `--mode auto`（または `attach`）で実行する。
詳しい手順は docs/wiki/backtest.md / examples/README.md を参照。

注意:
    - サンドボックスはありません。バグによる誤発注はユーザー責任です
    - 教育用の最小実装です。スリッページ・手数料・リスク管理は含みません
    - 1 週間（5 営業日）でおよそ 1,500 本の分足が流れます。データ範囲を
      広げる場合は実行時間とメモリに注意してください
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
    "end": "2025-01-10",
    "granularity": "Minute",
    "initial_cash": 1_000_000,
}
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy


class BuyAndHoldMinuteStrategy(Strategy):
    """最初の分足で成行買いし、以降は保有し続ける最小戦略。"""

    def __init__(
        self,
        *,
        instrument_id: str = "1301.TSE",
        lot_size: int = 100,
        bar_type_str: str | None = None,
    ) -> None:
        super().__init__(config=StrategyConfig(strategy_id="buy-and-hold-minute"))
        self.instrument_id = InstrumentId.from_str(instrument_id)
        self.lot_size = int(lot_size)
        self.bar_type_str = bar_type_str or f"{instrument_id}-1-MINUTE-LAST-EXTERNAL"
        self._bought = False

    def on_start(self) -> None:
        instrument = self.cache.instrument(self.instrument_id)
        if instrument is None:
            self.log.error(f"Instrument not found: {self.instrument_id}")
            return
        self.subscribe_bars(BarType.from_str(self.bar_type_str))
        self.log.info(
            f"BuyAndHoldMinuteStrategy started: instrument={self.instrument_id} "
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
