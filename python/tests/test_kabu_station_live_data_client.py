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
