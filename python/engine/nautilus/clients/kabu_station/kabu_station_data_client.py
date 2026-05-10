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

LiveDataClient 互換 (``nautilus_trader.live.data_client.LiveDataClient``) の最小実装。
parent class を継承しない thin adapter として実装し、必要 minimal API のみ公開する。
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from engine.exchanges.kabusapi_register import RegisterSet, MAX_SYMBOLS

log = logging.getLogger(__name__)


class KabuStationLiveDataClient:
    """kabuステーション 向け LiveDataClient adapter。

    50 銘柄 PUSH 上限に達したら最古銘柄を evict し ``SubscriptionEvicted{symbol}``
    を IPC で emit する（spec §3.2-G）。

    Args:
        on_event: IPC イベント callback。``SubscriptionEvicted`` を流す。
        exchange: kabu の Exchange code（既定 1=東証）。本 adapter は単一 exchange 想定。
    """

    def __init__(
        self,
        *,
        on_event: Callable[[dict], None] | None = None,
        exchange: int = 1,
        **_kwargs: Any,
    ) -> None:
        self._on_event = on_event if on_event is not None else (lambda _evt: None)
        self._exchange = exchange
        self.id = "kabu_station-data"
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
