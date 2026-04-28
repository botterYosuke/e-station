"""立花 FD frame → nautilus TradeTick 変換 LiveDataClient (N2.0)

data-mapping.md §1.2 の写像仕様に従う。

- trade dict の ``side`` が ``"buy"`` → ``AggressorSide.BUYER``
- ``"sell"`` → ``AggressorSide.SELLER``
- ``"unknown"`` (曖昧) → ``AggressorSide.NO_AGGRESSOR``
- ``trade_id`` は ``f"L-{ts_ms}-{seq}"`` の連番文字列（live 専用プレフィックス L）

設計制約:
    このモジュールは nautilus 内部 API に直接依存する唯一の場所。
    tachibana_ws / tachibana_event 等には依存しない（循環回避）。
"""

from __future__ import annotations

import logging
import time as _time
from decimal import Decimal
from typing import Any

from nautilus_trader.live.data_client import LiveDataClient
from nautilus_trader.model.data import TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import InstrumentId, TradeId
from nautilus_trader.model.objects import Price, Quantity

log = logging.getLogger(__name__)

# side 文字列 → AggressorSide 写像（data-mapping.md §1.2）
_SIDE_MAP: dict[str, AggressorSide] = {
    "buy": AggressorSide.BUYER,
    "sell": AggressorSide.SELLER,
    "unknown": AggressorSide.NO_AGGRESSOR,
}


def trade_dict_to_tick(
    instrument_id_str: str,
    trade: dict[str, Any],
    *,
    price_precision: int = 1,
    size_precision: int = 0,
    seq: int = 0,
) -> TradeTick:
    """FdFrameProcessor の trade dict を nautilus TradeTick に変換する。

    Args:
        instrument_id_str: ``"7203.TSE"`` 形式の InstrumentId 文字列
        trade: FdFrameProcessor が返す trade dict。キー:
            - ``price`` (str): 約定価格
            - ``qty`` (str): 約定数量
            - ``side`` (str): ``"buy"`` / ``"sell"`` / ``"unknown"``
            - ``ts_ms`` (int): 約定時刻 UTC ミリ秒
        price_precision: Price の精度（デフォルト 1、呼値 0.1 円固定）
        size_precision: Quantity の精度（デフォルト 0、株数は整数）
        seq: 同一 ts_ms 内の連番（trade_id 生成用）

    Returns:
        nautilus TradeTick
    """
    ts_ms: int = int(trade["ts_ms"])
    ts_ns: int = ts_ms * 1_000_000

    price = Price(Decimal(str(trade["price"])), precision=price_precision)
    size = Quantity(Decimal(str(trade["qty"])), precision=size_precision)

    side_str = trade.get("side", "unknown")
    aggressor_side = _SIDE_MAP.get(side_str, AggressorSide.NO_AGGRESSOR)
    if side_str not in _SIDE_MAP:
        log.warning(
            "trade_dict_to_tick: unexpected side %r for %s, treating as NO_AGGRESSOR",
            side_str,
            instrument_id_str,
        )

    trade_id = TradeId(f"L-{ts_ms}-{seq}")
    instrument_id = InstrumentId.from_str(instrument_id_str)

    return TradeTick(
        instrument_id=instrument_id,
        price=price,
        size=size,
        aggressor_side=aggressor_side,
        trade_id=trade_id,
        ts_event=ts_ns,
        ts_init=ts_ns,
    )


class TachibanaLiveDataClient(LiveDataClient):
    """立花 EVENT WS から受け取った FD frame 由来の trade dict を TradeTick に変換し
    LiveDataEngine に渡す thin adapter。

    実際の WebSocket 接続・FdFrameProcessor 呼び出しは server.py 層が担う。
    本クラスは「変換 + 投入」の責務のみ持つ。

    Usage:
        ``feed_trade_dict(instrument_id, trade)`` を外部（server.py）から呼ぶと
        LiveDataEngine.process() に TradeTick が流れる。
    """

    # 同一 ts_ms 内の seq counter（instrument_id ごと）
    _seq_per_ms: dict[str, dict[int, int]]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._seq_per_ms = {}

    # ------------------------------------------------------------------
    # 公開 API（server.py から呼ぶ）
    # ------------------------------------------------------------------

    def feed_trade_dict(
        self,
        instrument_id_str: str,
        trade: dict[str, Any],
        *,
        price_precision: int = 1,
        size_precision: int = 0,
    ) -> None:
        """FdFrameProcessor の trade dict を TradeTick に変換して engine に流す。

        呼出は server.py 内の EVENT WS ループから行う。
        """
        ts_ms = int(trade["ts_ms"])
        seq = self._next_seq(instrument_id_str, ts_ms)

        try:
            tick = trade_dict_to_tick(
                instrument_id_str,
                trade,
                price_precision=price_precision,
                size_precision=size_precision,
                seq=seq,
            )
        except (KeyError, ValueError, Exception) as exc:
            log.error(
                "feed_trade_dict: failed to convert trade dict for %s: %s (trade=%r)",
                instrument_id_str,
                exc,
                trade,
                exc_info=True,
            )
            return

        self._handle_data(tick)

    # ------------------------------------------------------------------
    # LiveDataClient abstract methods
    # ------------------------------------------------------------------

    async def _connect(self) -> None:
        # EVENT WebSocket 接続は server.py 層が管理するため no-op。
        self._set_connected(True)
        log.info("TachibanaLiveDataClient connected")

    async def _disconnect(self) -> None:
        self._set_connected(False)
        log.info("TachibanaLiveDataClient disconnected")

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _next_seq(self, instrument_id_str: str, ts_ms: int) -> int:
        """同一 instrument_id + ts_ms 内の連番を返す。"""
        counter = self._seq_per_ms.setdefault(instrument_id_str, {})
        seq = counter.get(ts_ms, 0)
        counter[ts_ms] = seq + 1
        # 古い ts_ms エントリを GC（直近 1 件だけ保持）
        if len(counter) > 1:
            oldest = min(counter)
            del counter[oldest]
        return seq
