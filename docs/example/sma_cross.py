"""SMA クロス ユーザー戦略サンプル（動作確認用）。

短期 SMA が長期 SMA を上抜けで成行買い、下抜けで成行売り（クローズ）する
古典戦略の最小実装。numpy / pandas は使わず ``collections.deque`` のみで
計算するため、追加依存は不要です。

起動:

    REPLAY_GRANULARITY=Daily \\
    REPLAY_START_DATE=2025-01-06 \\
    REPLAY_END_DATE=2025-03-31 \\
    bash scripts/run-replay-debug.sh docs/example/sma_cross.py

strategy_init_kwargs で初期化パラメータを上書きできます:

    {"instrument_id": "1301.TSE", "short": 3, "long": 5, "lot_size": 100}

デフォルトは Daily バー（1-DAY-LAST-EXTERNAL）を想定しています。
Minute バーで動かす場合は ``bar_type_str`` を指定してください:

    {"instrument_id": "1301.TSE", "bar_type_str": "1301.TSE-1-MINUTE-LAST-EXTERNAL"}

注意:
    - サンドボックスはありません。バグによる誤発注はユーザー責任です
    - 教育用の最小実装です。スリッページ・手数料・リスク管理は含みません
"""

from __future__ import annotations

from collections import deque
from typing import Deque

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy


class SmaCrossStrategy(Strategy):
    """短期 SMA × 長期 SMA のクロスでエントリー / クローズする最小戦略。

    デフォルトは Daily バー（short=3, long=5）で動作します。
    2025-01-06〜2025-03-31 の約 60 営業日で複数回シグナルが発生します。
    """

    def __init__(
        self,
        *,
        instrument_id: str = "1301.TSE",
        short: int = 3,
        long: int = 5,
        lot_size: int = 100,
        bar_type_str: str | None = None,
    ) -> None:
        super().__init__(config=StrategyConfig(strategy_id="sma-cross"))
        if short <= 0 or long <= 0:
            raise ValueError("short / long must be positive")
        if short >= long:
            raise ValueError(f"short ({short}) must be < long ({long})")
        self.instrument_id = InstrumentId.from_str(instrument_id)
        self.short = int(short)
        self.long = int(long)
        self.lot_size = int(lot_size)
        # Daily バーが engine のデフォルト（granularity="Daily"）
        self.bar_type_str = bar_type_str or f"{instrument_id}-1-DAY-LAST-EXTERNAL"
        self.closes: Deque[float] = deque(maxlen=self.long)
        self.position_side: OrderSide | None = None
        self._prev_short: float | None = None
        self._prev_long: float | None = None

    def on_start(self) -> None:
        instrument = self.cache.instrument(self.instrument_id)
        if instrument is None:
            self.log.error(f"Instrument not found: {self.instrument_id}")
            return
        self.subscribe_bars(BarType.from_str(self.bar_type_str))
        self.log.info(
            f"SmaCrossStrategy started: instrument={self.instrument_id} "
            f"short={self.short} long={self.long} bar_type={self.bar_type_str}"
        )

    def _sma(self, n: int) -> float | None:
        if len(self.closes) < n:
            return None
        total = 0.0
        for i in range(1, n + 1):
            total += self.closes[-i]
        return total / n

    def _submit_market(self, side: OrderSide) -> None:
        instrument = self.cache.instrument(self.instrument_id)
        if instrument is None:
            self.log.warning(f"instrument not in cache: {self.instrument_id}")
            return
        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=side,
            quantity=Quantity.from_int(self.lot_size),
            time_in_force=TimeInForce.DAY,
        )
        self.submit_order(order)

    def on_bar(self, bar: Bar) -> None:
        self.closes.append(float(bar.close))

        sma_s = self._sma(self.short)
        sma_l = self._sma(self.long)
        if sma_s is None or sma_l is None:
            return

        prev_s, prev_l = self._prev_short, self._prev_long
        self._prev_short, self._prev_long = sma_s, sma_l
        if prev_s is None or prev_l is None:
            return

        crossed_up = prev_s <= prev_l and sma_s > sma_l
        crossed_down = prev_s >= prev_l and sma_s < sma_l

        if crossed_up and self.position_side != OrderSide.BUY:
            self.log.info(f"BUY signal: sma_short={sma_s:.1f} crossed above sma_long={sma_l:.1f}")
            self._submit_market(OrderSide.BUY)
            self.position_side = OrderSide.BUY
        elif crossed_down and self.position_side == OrderSide.BUY:
            self.log.info(f"SELL signal: sma_short={sma_s:.1f} crossed below sma_long={sma_l:.1f}")
            self._submit_market(OrderSide.SELL)
            self.position_side = None
