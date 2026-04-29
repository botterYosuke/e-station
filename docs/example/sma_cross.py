"""SMA クロス ユーザー戦略サンプル。

短期 SMA が長期 SMA を上抜けで成行買い、下抜けで成行売り（クローズ）する
古典戦略の最小実装。numpy / pandas は使わず ``collections.deque`` のみで
計算するため、追加依存は不要です。

起動:

    cargo run -- --mode replay --strategy-file examples/strategies/sma_cross.py

パラメータは N4.1 の ``init_kwargs`` 経由で渡せます:

    {"instrument_id": "1301.TSE", "short": 5, "long": 20, "lot_size": 100}

注意:
    - サンドボックスはありません。バグによる誤発注はユーザー責任です
      （``examples/strategies/README.md`` 参照）
    - 教育用の最小実装です。スリッページ・手数料・リスク管理は含みません
    - nautilus_trader 1.225.0 の API で動作確認しています
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
    """短期 SMA × 長期 SMA のクロスでエントリー / クローズする最小戦略。"""

    def __init__(
        self,
        *,
        instrument_id: str,
        short: int = 5,
        long: int = 20,
        lot_size: int = 100,
        bar_type_str: str | None = None,
    ) -> None:
        super().__init__(config=StrategyConfig(strategy_id="sma-cross-example"))
        if short <= 0 or long <= 0:
            raise ValueError("short / long must be positive")
        if short >= long:
            raise ValueError(f"short ({short}) must be < long ({long})")
        self.instrument_id = InstrumentId.from_str(instrument_id)
        self.short = int(short)
        self.long = int(long)
        self.lot_size = int(lot_size)
        self.bar_type_str = (
            bar_type_str or f"{instrument_id}-1-MINUTE-LAST-EXTERNAL"
        )
        # 長い方の窓だけ保持すれば short も計算できる
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

    def _sma(self, n: int) -> float | None:
        if len(self.closes) < n:
            return None
        # deque は末尾が最新。直近 n 本の平均
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
        # Bar.close は Price オブジェクト。float へ変換して保持
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
            # ショート保有なら 1 lot 買戻し（クローズ）+ 新規買い、を簡略化して
            # 単純に成行買いを 1 発出す（最小サンプルのため）。
            self._submit_market(OrderSide.BUY)
            self.position_side = OrderSide.BUY
        elif crossed_down and self.position_side == OrderSide.BUY:
            # ロング保有時のみ売却（クローズ）。新規ショートはサンプルでは行わない
            self._submit_market(OrderSide.SELL)
            self.position_side = None
