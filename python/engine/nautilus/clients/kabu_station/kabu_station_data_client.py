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

import asyncio
import logging
from collections.abc import Awaitable, Callable
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
        register_cb: subscribe() 内で呼び出す async callback（issue #42 R2 CRITICAL-1）。
            server.py 側で PUT /register に流すために使う。None なら no-op で、
            従来の「内部 RegisterSet のみ更新」挙動と互換。
        **kwargs: ``LiveMarketDataClient.__init__`` への keyword 引数。
    """

    def __init__(
        self,
        *args: Any,
        on_event: Callable[[dict], None] | None = None,
        exchange: int = 1,
        register_cb: Callable[[str, int], Awaitable[None]] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._on_event = on_event if on_event is not None else (lambda _evt: None)
        self._exchange = exchange
        self._register_cb = register_cb
        # spec §3.2-G: live strategy 用 RegisterSet は暗黙 LRU evict ＋ 通知方式。
        # on_evict callback で SubscriptionEvicted を emit する。
        self._register_set = RegisterSet(
            on_evict=self._on_evict,
            max_symbols=MAX_SYMBOLS,
        )
        # issue #42 R2 CRITICAL-1: server.py 側 loop A → loop B bridge で
        # call_soon_threadsafe を打つために running loop を保持する。
        # _connect() で確定する（Nautilus 親が _connect() を呼ぶタイミング）。
        self._loop: asyncio.AbstractEventLoop | None = None
        # R3 H2: 同一 ts_ms 内の seq counter (instrument_id ごと)。
        # tachibana_data.py::TachibanaLiveDataClient._seq_per_ms と同実装。
        # trade_id="L-{ts_ms}-{seq}" の衝突 (Nautilus dedup) を避ける。
        self._seq_per_ms: dict[str, dict[int, int]] = {}

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------

    def subscribe(self, symbol: str) -> None:
        """銘柄を購読する。50 件超過時は最古銘柄を evict + SubscriptionEvicted emit。

        issue #42 R2 CRITICAL-1: register_cb が注入されていれば server 側 PUT /register
        への bridge として呼び出す（async）。呼出順序は **内部 RegisterSet 更新後**
        にすることで、subscribe() 同期 API の post-condition と整合させる。
        """
        if self.is_subscribed(symbol):
            # 冪等: 既登録は LRU 位置だけ更新する
            self._register_set.touch(symbol, self._exchange)
            # 冪等経路でも server 側 register が抜けるとフローが切れるので呼ぶ。
            self._invoke_register_cb(symbol)
            return

        # 上限到達 → evict 最古
        if len(self._register_set) >= MAX_SYMBOLS:
            self._register_set.evict_lru()

        # 既存 RegisterSet.register は満杯時に raise するが、上で evict 済みなので通る。
        self._register_set.register(symbol, self._exchange)
        self._invoke_register_cb(symbol)

    def _invoke_register_cb(self, symbol: str) -> None:
        """register_cb が None なら no-op。async cb を schedule する。

        subscribe() は同期 API のため、async callback は ensure_future で投入する。

        R3 H3: 旧実装は 3 段目 fallback で
        ``asyncio.get_event_loop().run_until_complete(coro)`` を呼んでいたが、
        Python 3.12+ では event loop 取得ポリシ変更 / nested-loop 規制で
        ``RuntimeError`` または deadlock になる経路がある。
        本実装では「running loop も self._loop も無い」場合は
        ``log.warning`` で silent skip する設計に統一する (subscribe() 自体は
        成功する = 内部 RegisterSet は更新される)。
        """
        if self._register_cb is None:
            return
        try:
            coro = self._register_cb(symbol, self._exchange)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "KabuStationLiveDataClient: register_cb factory raised for %s: %s",
                symbol,
                exc,
            )
            return
        try:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = self._loop
            if loop is not None and loop.is_running():
                asyncio.ensure_future(coro, loop=loop)
            else:
                # R3 H3: running loop も self._loop も無いケース。旧実装は
                # ``run_until_complete`` で同期実行を試みていたが Python 3.12+ で
                # RuntimeError / deadlock の温床。silent skip + warning 化する。
                # subscribe() の内部 RegisterSet 更新は先に終わっているため、
                # 後段で server.py 側 PUT /register が走れば物理経路は復旧する。
                log.warning(
                    "KabuStationLiveDataClient: register_cb dropped for %s: "
                    "no running loop (self._loop is None / not running)",
                    symbol,
                )
                # coroutine を実行しないので明示的に close して
                # RuntimeWarning ("coroutine was never awaited") を避ける。
                try:
                    coro.close()
                except Exception:  # noqa: BLE001
                    pass
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "KabuStationLiveDataClient: register_cb scheduling failed for %s: %s",
                symbol,
                exc,
            )

    def _feed_trade_dict_sync(self, instrument_id_str: str, trade: dict[str, Any]) -> None:
        """server.py loop A → loop B bridge で call_soon_threadsafe 経由で呼ばれる
        同期 entry point。trade dict → TradeTick → _handle_data。

        tachibana_data.py の同名 method と同じパターン（issue #42 R2 CRITICAL-1）。

        R3 H2: trade_dict_to_tick に同一 ts_ms 内 unique な seq を渡す
        (trade_id="L-{ts_ms}-{seq}" 衝突防止)。tachibana_data.py::feed_trade_dict
        と同じ `_next_seq` パターン。
        """
        # 遅延 import で循環依存と test の重い transitive を避ける。
        from engine.nautilus.clients.tachibana_data import trade_dict_to_tick

        try:
            ts_ms = int(trade["ts_ms"])
            seq = self._next_seq(instrument_id_str, ts_ms)
            tick = trade_dict_to_tick(instrument_id_str, trade, seq=seq)
        except Exception as exc:  # noqa: BLE001
            log.error(
                "_feed_trade_dict_sync: failed to convert trade dict for %s: %s "
                "(trade=%r)",
                instrument_id_str,
                exc,
                trade,
                exc_info=True,
            )
            return
        self._handle_data(tick)

    def _next_seq(self, instrument_id_str: str, ts_ms: int) -> int:
        """同一 instrument_id + ts_ms 内の連番を返す。

        tachibana_data.py::TachibanaLiveDataClient._next_seq と同実装。
        古い ts_ms entry は最新 1 件だけ保持して GC する。
        """
        counter = self._seq_per_ms.setdefault(instrument_id_str, {})
        seq = counter.get(ts_ms, 0)
        counter[ts_ms] = seq + 1
        # 古い ts_ms エントリを GC（直近 1 件だけ保持）
        if len(counter) > 1:
            oldest = min(counter)
            del counter[oldest]
        return seq

    async def _connect(self) -> None:
        """Nautilus 親が呼ぶ接続フック。running loop を保存する。

        issue #42 R2 CRITICAL-1: bridge thread から call_soon_threadsafe するために
        loop B（TradingNode が動く asyncio loop）を保持する必要がある。
        実 PUSH WebSocket 接続は server.py 層が管理するため no-op。
        """
        self._loop = asyncio.get_running_loop()
        try:
            self._set_connected(True)
        except Exception as exc:  # noqa: BLE001
            log.debug("KabuStationLiveDataClient: _set_connected failed: %s", exc)

    async def _disconnect(self) -> None:
        try:
            self._set_connected(False)
        except Exception as exc:  # noqa: BLE001
            log.debug("KabuStationLiveDataClient: _set_connected(False) failed: %s", exc)

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
