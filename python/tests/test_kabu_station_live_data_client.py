"""issue #42 Phase 4: KabuStationLiveDataClient unit tests.

テスト対象 (`python/engine/nautilus/clients/kabu_station/kabu_station_data_client.py`):

- ``subscribe(symbol)`` で内部 RegisterSet に登録される
- ``unsubscribe(symbol)`` で登録解除される
- 51 件目登録要求時に最古銘柄が evict + ``SubscriptionEvicted{symbol}`` event emit
  （docs/specs/live-strategy.md §3.2-G）

50 銘柄上限の数値ソースは ``engine.exchanges.kabusapi_register.RegisterSet.MAX``。

issue #42 R1 review R2-C (CRITICAL):
``KabuStationLiveDataClient`` は ``LiveMarketDataClient`` を継承するため
``__init__`` に Nautilus 親必須引数（``loop`` / ``client_id`` / ``venue`` /
``msgbus`` / ``cache`` / ``clock`` / ``instrument_provider``）を渡す。
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest


def _nautilus_data_parent_kwargs() -> dict:
    """Nautilus ``LiveMarketDataClient`` 親が要求する必須 kwargs を組み立てる。"""
    from nautilus_trader.cache.cache import Cache
    from nautilus_trader.common.component import LiveClock, MessageBus
    from nautilus_trader.common.providers import InstrumentProvider
    from nautilus_trader.model.identifiers import ClientId, TraderId, Venue

    asyncio.set_event_loop(asyncio.new_event_loop())
    clock = LiveClock()
    trader_id = TraderId("KABUSTATION-TEST-DATA")
    msgbus = MessageBus(trader_id=trader_id, clock=clock)
    cache = Cache()

    return dict(
        loop=asyncio.get_event_loop(),
        client_id=ClientId("KABUSTATION-DATA-001"),
        venue=Venue("TSE"),
        msgbus=msgbus,
        cache=cache,
        clock=clock,
        instrument_provider=InstrumentProvider(),
    )


def _make_data_client(events_sink: list):
    from engine.nautilus.clients.kabu_station.kabu_station_data_client import (
        KabuStationLiveDataClient,
    )

    client = KabuStationLiveDataClient(
        **_nautilus_data_parent_kwargs(),
        on_event=events_sink.append,
    )
    return client


class TestSubscribeUnsubscribeRoundTrip:
    def test_subscribe_then_unsubscribe(self):
        events: list[dict] = []
        client = _make_data_client(events)

        client.subscribe("9433")
        assert client.is_subscribed("9433") is True
        assert "9433" in client.subscribed_symbols()

        client.unsubscribe("9433")
        assert client.is_subscribed("9433") is False

    def test_subscribe_idempotent(self):
        events: list[dict] = []
        client = _make_data_client(events)

        client.subscribe("9433")
        client.subscribe("9433")
        assert client.subscribed_symbols() == ["9433"]


class TestPushSymbolLimitEviction:
    def test_51st_subscription_evicts_oldest_and_emits_event(self):
        """50 件登録 → 51 件目で最古銘柄を evict + SubscriptionEvicted event emit。"""
        events: list[dict] = []
        client = _make_data_client(events)

        # 50 件登録
        for i in range(1, 51):
            client.subscribe(f"sym{i:03d}")

        assert len(client.subscribed_symbols()) == 50

        # 51 件目 → 最古 (sym001) が evict される
        client.subscribe("sym051")

        # evicted event が emit されたか
        evicted = [e for e in events if e.get("event") == "SubscriptionEvicted"]
        assert len(evicted) == 1, f"expected 1 SubscriptionEvicted, got events={events!r}"
        assert evicted[0]["symbol"] == "sym001"

        # 新規登録は行われた
        assert client.is_subscribed("sym051")
        assert not client.is_subscribed("sym001")
        assert len(client.subscribed_symbols()) == 50

    def test_eviction_callback_invoked_with_symbol(self):
        """Custom on_evict callback も併用できる（既存 `RegisterSet.on_evict` 経由）。"""
        events: list[dict] = []
        client = _make_data_client(events)

        for i in range(1, 51):
            client.subscribe(f"sym{i:03d}")
        client.subscribe("sym051")

        # SubscriptionEvicted は IPC event として emit される（必須契約）
        evicted = [e for e in events if e.get("event") == "SubscriptionEvicted"]
        assert evicted, "must emit SubscriptionEvicted on evict"

    def test_evicted_event_validates_against_schema(self):
        """issue #42 R1 HIGH-2: emit された SubscriptionEvicted payload が
        ``engine.schemas.SubscriptionEvicted`` で round-trip validate できる。

        旧実装は schema 全層に variant が無く、server_grpc.py の
        ``_EVENT_TO_FIELD_AND_CLASS.get(event_name)`` が None を返すため
        silent drop されていた（spec §3.2-G 契約違反）。
        本 test は schema 層が wire 形と整合していることを pin する。
        """
        from engine.schemas import SubscriptionEvicted

        events: list[dict] = []
        client = _make_data_client(events)
        for i in range(1, 51):
            client.subscribe(f"sym{i:03d}")
        client.subscribe("sym051")

        evicted = [e for e in events if e.get("event") == "SubscriptionEvicted"]
        assert evicted, "must emit SubscriptionEvicted"
        # pydantic で round-trip validate（field 名 / 型の契約 pin）
        msg = SubscriptionEvicted.model_validate(evicted[0])
        assert msg.event == "SubscriptionEvicted"
        assert msg.venue == "kabu_station"
        assert msg.symbol == "sym001"
        assert msg.exchange >= 1


# ---------------------------------------------------------------------------
# R2 CRITICAL-1: register_cb 注入 + _feed_trade_dict_sync (PUSH 物理経路本配線)
# ---------------------------------------------------------------------------


class TestRegisterCallbackBridge:
    """R2 CRITICAL-1: subscribe() 内で外部の register_cb を呼べる。

    server.py 側で kabu PUT /register に flow させるため、subscribe(symbol) 経路に
    optional async callback を注入できる必要がある。callback は (symbol, exchange)
    を受け取る。None なら no-op で従来挙動と互換。
    """

    def test_subscribe_calls_register_cb_when_provided(self):
        from engine.nautilus.clients.kabu_station.kabu_station_data_client import (
            KabuStationLiveDataClient,
        )

        calls: list[tuple[str, int]] = []

        async def _cb(symbol: str, exchange: int) -> None:
            calls.append((symbol, exchange))

        events: list[dict] = []
        client = KabuStationLiveDataClient(
            **_nautilus_data_parent_kwargs(),
            on_event=events.append,
            register_cb=_cb,
        )

        # R3 H3: 旧 fallback (`run_until_complete`) は削除されたため、本テストは
        # 「subscribe() が走る前に、production 経路と同じく event loop が回って
        # いる」状況を再現する。production では Nautilus parent が `_connect()`
        # を呼び `self._loop` をセットする経路で、subscribe() 内 `_invoke_register_cb`
        # は `ensure_future(coro, loop=self._loop)` で schedule する。
        async def _runner():
            await client._connect()  # self._loop を running loop にセット
            client.subscribe("9433")
            # ensure_future で schedule された pending task を drain
            pending = [t for t in asyncio.all_tasks() if not t.done()]
            current = asyncio.current_task()
            if current is not None and current in pending:
                pending.remove(current)
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        asyncio.run(_runner())

        assert calls == [("9433", 1)], f"register_cb should be invoked, got calls={calls!r}"

    def test_subscribe_skips_register_cb_when_none(self):
        """register_cb=None なら no-op（callback 呼出なし、subscribe 自体は成功）。"""
        events: list[dict] = []
        client = _make_data_client(events)  # register_cb 省略

        # subscribe 自体は成功し、内部 RegisterSet に登録される
        client.subscribe("9433")
        assert client.is_subscribed("9433") is True

    def test_subscribe_does_not_panic_when_no_running_loop_and_loop_attr_none(self, caplog):
        """R3 H3: 旧実装の 3 段目 fallback (`run_until_complete`) は Python 3.12+
        で RuntimeError / deadlock を起こすため、no running loop + self._loop=None
        の場合は警告ログだけ出して silent skip する設計に変更する。
        """
        from engine.nautilus.clients.kabu_station.kabu_station_data_client import (
            KabuStationLiveDataClient,
        )
        import logging

        called: list[tuple[str, int]] = []

        async def _cb(symbol: str, exchange: int) -> None:
            called.append((symbol, exchange))

        # 親 ctor は asyncio.set_event_loop(asyncio.new_event_loop()) を内部で
        # 呼ぶが、本テストでは subscribe() 呼出時に「running loop が無い」状況を
        # 作る。helper の _nautilus_data_parent_kwargs は loop 引数を渡しているが、
        # 実際の subscribe 呼出時に running loop が無いことを再現する。
        client = KabuStationLiveDataClient(
            **_nautilus_data_parent_kwargs(),
            on_event=lambda _evt: None,
            register_cb=_cb,
        )
        # self._loop は _connect() でセットされるが今回は connect していない
        client._loop = None

        with caplog.at_level(logging.WARNING, logger="engine.nautilus.clients.kabu_station.kabu_station_data_client"):
            # panic / RuntimeError / deadlock を起こさないことが本テストの主目的。
            client.subscribe("9433")

        # subscribe 自体は成功する (内部 RegisterSet 更新)
        assert client.is_subscribed("9433") is True
        # callback は drop されるので呼ばれない (R3 H3 設計)
        assert called == [], (
            f"register_cb must be dropped (not invoked) when no loop; got {called!r}"
        )


class TestFeedTradeDictSync:
    """R2 CRITICAL-1: _feed_trade_dict_sync で trade dict → TradeTick → _handle_data。

    server.py 側の loop A から call_soon_threadsafe 経由で叩く同期 API。
    tachibana_data.py の同名 method と同じシグネチャ。
    """

    def test_feed_trade_dict_sync_converts_to_trade_tick(self, monkeypatch):
        """trade dict が TradeTick に変換され、_handle_data に渡される。"""
        events: list[dict] = []
        client = _make_data_client(events)

        captured: list = []

        # _handle_data を mock で観測（Cython 親側の _handle_data を override）
        monkeypatch.setattr(client, "_handle_data", lambda tick: captured.append(tick))

        trade = {
            "price": "1500.0",
            "qty": "100",
            "side": "buy",
            "ts_ms": 1_700_000_000_000,
        }
        client._feed_trade_dict_sync("9433.TSE", trade)

        assert len(captured) == 1, f"expected 1 TradeTick, got {len(captured)} (captured={captured!r})"
        # 軽い shape 検査（TradeTick instance）
        from nautilus_trader.model.data import TradeTick
        assert isinstance(captured[0], TradeTick)

    def test_feed_trade_dict_sync_assigns_unique_seq_per_ms(self, monkeypatch):
        """R3 H2: 同一 ts_ms 内で連続 trade を流したとき trade_id が衝突しない。

        旧実装は seq=default (0) で trade_dict_to_tick を呼んでいたため、
        同一 ms に複数 push されると trade_id="L-{ts_ms}-0" が重複し
        Nautilus 側で warning + dedup または order book 不整合を生んでいた。
        tachibana_data.py::feed_trade_dict と同じ _next_seq pattern を適用する。
        """
        events: list[dict] = []
        client = _make_data_client(events)

        captured: list = []
        monkeypatch.setattr(client, "_handle_data", lambda tick: captured.append(tick))

        ts = 1_700_000_000_000
        trade_a = {"price": "1500.0", "qty": "100", "side": "buy", "ts_ms": ts}
        trade_b = {"price": "1501.0", "qty": "100", "side": "sell", "ts_ms": ts}
        client._feed_trade_dict_sync("9433.TSE", trade_a)
        client._feed_trade_dict_sync("9433.TSE", trade_b)

        assert len(captured) == 2, f"expected 2 TradeTicks, got {captured!r}"
        # trade_id が異なる (seq=0 と seq=1 で識別される)
        ids = [str(tick.trade_id) for tick in captured]
        assert ids[0] != ids[1], (
            f"trade_ids must be unique within same ts_ms; got duplicate {ids!r}"
        )
        # 連番形式 (L-<ts>-<seq>) — 最終 segment が異なる
        assert ids[0].split("-")[-1] != ids[1].split("-")[-1], (
            f"seq segment must differ: ids={ids!r}"
        )
