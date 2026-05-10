"""KabuStationLiveDataClient — kabusapi PUSH 用 LiveDataClient adapter (issue #42 Phase 4)。

設計方針:
- 既存 ``engine.exchanges.kabusapi_register.RegisterSet`` を内部に保持し、subscribe /
  unsubscribe 経路で銘柄登録を行う。
- 50 銘柄 PUSH 上限（``RegisterSet.MAX``）に達したら、新規登録時に最古銘柄を evict し
  ``SubscriptionEvicted{symbol}`` IPC event を ``on_event`` callback 経由で emit する
  （``docs/specs/live-strategy.md §3.2-G`` 契約）。
- 既存 ``RegisterSet`` は満杯時に ``KabuRegisterFullError`` を投げる「明示的 reject」設計
  だが、本 adapter は live strategy 用に **暗黙 LRU evict + 通知** に切替える（spec §3.2-G）。
  対称性のため別インスタンスを保持し、既存 server.py 経路（明示 reject）と独立させる。
- 実 PUSH WebSocket 接続は ``server.py::_startup_kabu_station`` が管理する（Phase 4 minimal は
  「subscribe 経路と evict 通知契約」を確立することに専念）。

issue #42 R1 review R2-C (CRITICAL, H-1 punt 解消):
``LiveMarketDataClient`` (``nautilus_trader.live.data_client``) を継承する。
Nautilus の ``LiveDataEngine.register_client`` は Cython 親型 ``MarketDataClient``
互換を要求するため、未継承だと ``engine_runner.py::start_live`` の
``register_client(data_client)`` が ``node.build()`` 経由で type check に失敗する。
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from nautilus_trader.live.data_client import LiveMarketDataClient

from engine.exchanges.kabusapi_register import RegisterSet, MAX_SYMBOLS

log = logging.getLogger(__name__)


class KabuStationLiveDataClient(LiveMarketDataClient):
    """kabuステーション 向け LiveDataClient adapter。

    50 銘柄 PUSH 上限に達したら最古銘柄を evict し ``SubscriptionEvicted{symbol}``
    を IPC で emit する（spec §3.2-G）。

    issue #42 R1 review R2-C (CRITICAL, H-1 punt 解消):
    ``LiveMarketDataClient`` を継承する。Nautilus 親が要求する追加引数
    （``loop`` / ``client_id`` / ``venue`` / ``msgbus`` / ``cache`` / ``clock`` /
    ``instrument_provider``）は ``*args, **kwargs`` で吸収して ``super().__init__``
    に転送する。tachibana の ``TachibanaLiveDataClient`` と同じパターン。

    Args:
        *args: ``LiveMarketDataClient.__init__`` への positional 引数。
        on_event: IPC イベント callback。``SubscriptionEvicted`` を流す。
        exchange: kabu の Exchange code（既定 1=東証）。本 adapter は単一 exchange 想定。
        **kwargs: ``LiveMarketDataClient.__init__`` への keyword 引数。
    """

    def __init__(
        self,
        *args: Any,
        on_event: Callable[[dict], None] | None = None,
        exchange: int = 1,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._on_event = on_event if on_event is not None else (lambda _evt: None)
        self._exchange = exchange
        # spec §3.2-G: live strategy 用 RegisterSet は暗黙 LRU evict ＋ 通知方式。
        # on_evict callback で SubscriptionEvicted を emit する。
        self._register_set = RegisterSet(
            on_evict=self._on_evict,
            max_symbols=MAX_SYMBOLS,
        )

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------

    def subscribe(self, symbol: str) -> None:
        """銘柄を購読する。50 件超過時は最古銘柄を evict + SubscriptionEvicted emit。"""
        if self.is_subscribed(symbol):
            # 冪等: 既登録は LRU 位置だけ更新する
            self._register_set.touch(symbol, self._exchange)
            return

        # 上限到達 → evict 最古
        if len(self._register_set) >= MAX_SYMBOLS:
            self._register_set.evict_lru()

        # 既存 RegisterSet.register は満杯時に raise するが、上で evict 済みなので通る。
        self._register_set.register(symbol, self._exchange)

    def unsubscribe(self, symbol: str) -> bool:
        """銘柄の購読を解除する。"""
        return self._register_set.unregister(symbol, self._exchange)

    def is_subscribed(self, symbol: str) -> bool:
        """登録済みか確認する。"""
        return (symbol, self._exchange) in self._register_set

    def subscribed_symbols(self) -> list[str]:
        """登録中の全銘柄を LRU 順（古い順）で返す。"""
        return [s for s, _ex in self._register_set.all_symbols()]

    # ------------------------------------------------------------------
    # internal: SubscriptionEvicted emit hook
    # ------------------------------------------------------------------

    def _on_evict(self, symbol: str, exchange: int) -> None:
        """RegisterSet から evict された銘柄を IPC event として通知する（spec §3.2-G）。"""
        try:
            self._on_event(
                {
                    "event": "SubscriptionEvicted",
                    "venue": "kabu_station",
                    "symbol": symbol,
                    "exchange": exchange,
                }
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "KabuStationLiveDataClient: on_event callback raised for evict %s: %s",
                symbol,
                exc,
            )
