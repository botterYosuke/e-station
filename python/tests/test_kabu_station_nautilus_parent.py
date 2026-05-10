"""issue #42 R1 review R2-C: KabuStation* Nautilus 親クラス継承の pin。

R1-CRITICAL (H-1 punt の本 PR 解消):
``KabuStationLiveExecutionClient`` / ``KabuStationLiveDataClient`` は
Nautilus の親クラス（``LiveExecutionClient`` / ``LiveMarketDataClient``）を
継承しなければ、``engine_runner.py::start_live`` 内で
``node._exec_engine.register_client(exec_client)`` /
``node._data_engine.register_client(data_client)`` の Cython 親型 type check
（``ExecutionClient`` / ``MarketDataClient``）を通らず、``venue=="kabu_station"``
での live strategy 起動が ``node.build()`` で必ず失敗する。

本 test は:

1. **isinstance 契約**: 構築されたインスタンスが Nautilus 親クラスのインスタンスである
2. **register_client smoke**: 実 ``LiveExecutionEngine.register_client`` /
   ``LiveDataEngine.register_client`` を呼んで type check を通る

の 2 つを pin する。

Nautilus が install されていない環境では ``pytest.importorskip`` で skip する。
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

# Nautilus が無い環境では skip（CI 一部 / 開発機）
nautilus = pytest.importorskip("nautilus_trader")


# ---------------------------------------------------------------------------
# parent kwargs 組立 helper（real Nautilus instance を返す）
# ---------------------------------------------------------------------------


def _make_parent_kwargs():
    """Nautilus 親クラスが要求する必須引数を実インスタンスで揃える。

    ``MagicMock`` だと Cython 側の type check で ``TypeError`` になるため、
    実 ``LiveClock`` / ``MessageBus`` / ``Cache`` / ``InstrumentProvider`` を
    使う。共通化のため exec / data 両方分の super-set を返す（不要 key は
    呼び側が無視する）。
    """
    from nautilus_trader.cache.cache import Cache
    from nautilus_trader.common.component import LiveClock, MessageBus
    from nautilus_trader.common.providers import InstrumentProvider
    from nautilus_trader.model.enums import AccountType, OmsType
    from nautilus_trader.model.identifiers import ClientId, TraderId, Venue

    loop = asyncio.new_event_loop()
    clock = LiveClock()
    trader_id = TraderId("KABUSTATION-TEST")
    msgbus = MessageBus(trader_id=trader_id, clock=clock)
    cache = Cache()
    instrument_provider = InstrumentProvider()

    return dict(
        loop=loop,
        client_id=ClientId("KABUSTATION-001"),
        venue=Venue("TSE"),
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=None,
        instrument_provider=instrument_provider,
        msgbus=msgbus,
        cache=cache,
        clock=clock,
    )


def _make_kabu_venue_mock():
    """KabuStationVenue 互換 mock。"""
    venue = MagicMock()
    venue.fetch_orders = AsyncMock(return_value=[])
    venue.clear = MagicMock()
    return venue


# ---------------------------------------------------------------------------
# C1: KabuStationLiveExecutionClient inherits LiveExecutionClient
# ---------------------------------------------------------------------------


class TestKabuStationExecClientInheritsLiveExecutionClient:
    """``KabuStationLiveExecutionClient`` が ``LiveExecutionClient`` を継承していること。"""

    def test_kabu_station_exec_client_inherits_live_execution_client(self):
        from nautilus_trader.live.execution_client import LiveExecutionClient

        from engine.nautilus.clients.kabu_station.kabu_station_exec_client import (
            KabuStationLiveExecutionClient,
        )

        parent_kwargs = _make_parent_kwargs()
        client = KabuStationLiveExecutionClient(
            **parent_kwargs,
            kabu_venue=_make_kabu_venue_mock(),
            strategy_id="kabu-test",
            max_qty=100,
            max_notional_jpy=500_000,
        )
        assert isinstance(client, LiveExecutionClient), (
            "KabuStationLiveExecutionClient must inherit LiveExecutionClient "
            "(R1-CRITICAL: register_client type check)"
        )

    def test_kabu_station_exec_client_safety_guard_still_works(self):
        """親継承後も max_qty/max_notional_jpy 必須チェックが先に走る（既存契約維持）。"""
        from engine.nautilus.clients.kabu_station.kabu_station_exec_client import (
            KabuStationLiveExecutionClient,
        )

        parent_kwargs = _make_parent_kwargs()
        with pytest.raises(ValueError, match="max_qty"):
            KabuStationLiveExecutionClient(
                **parent_kwargs,
                kabu_venue=_make_kabu_venue_mock(),
                strategy_id="kabu-test",
                max_qty=None,
                max_notional_jpy=500_000,
            )


# ---------------------------------------------------------------------------
# C1-2: KabuStationLiveDataClient inherits LiveMarketDataClient
# ---------------------------------------------------------------------------


class TestKabuStationDataClientInheritsLiveMarketDataClient:
    """``KabuStationLiveDataClient`` が ``LiveMarketDataClient`` を継承していること。"""

    def test_kabu_station_data_client_inherits_live_market_data_client(self):
        from nautilus_trader.live.data_client import LiveMarketDataClient

        from engine.nautilus.clients.kabu_station.kabu_station_data_client import (
            KabuStationLiveDataClient,
        )

        parent_kwargs = _make_parent_kwargs()
        # data client は loop/client_id/venue/msgbus/cache/clock/instrument_provider が必要。
        # OmsType / AccountType / base_currency は exec only なので除外。
        data_kwargs = {
            k: v
            for k, v in parent_kwargs.items()
            if k in {
                "loop",
                "client_id",
                "venue",
                "msgbus",
                "cache",
                "clock",
                "instrument_provider",
            }
        }
        client = KabuStationLiveDataClient(
            **data_kwargs,
            on_event=lambda _evt: None,
        )
        assert isinstance(client, LiveMarketDataClient), (
            "KabuStationLiveDataClient must inherit LiveMarketDataClient "
            "(R1-CRITICAL: register_client type check)"
        )


# ---------------------------------------------------------------------------
# register_client smoke: 実 LiveExecutionEngine / LiveDataEngine が受け入れるか
# ---------------------------------------------------------------------------


@pytest.mark.live_demo_inprocess
class TestRegisterClientAcceptsKabuStationClients:
    """node.kernel 経路で kabu_station client が登録できること。

    実 ``TradingNode`` を構築して ``kernel.exec_engine.register_client`` /
    ``kernel.data_engine.register_client`` に kabu_station client を渡し、
    Cython parent type check を通ることを smoke test する。

    @live_demo_inprocess マーカー: TradingNode 起動コストのため CI 標準実行から
    除外する（``not live_demo_inprocess`` で deselect）。手動実行用。

    asyncio: mode=auto なので event_loop fixture を 関数引数で取れる。
    Nautilus の ``LiveClock`` 内部で ``asyncio.get_event_loop()`` を呼ぶため
    fixture loop を「現在の loop」として set してから TradingNode を構築する。
    """

    def _setup_loop_in_thread(self):
        """test 関数 thread に event_loop を bind してから loop を返す。"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop

    def test_register_client_accepts_kabu_station_exec_client(self):
        loop = self._setup_loop_in_thread()
        try:
            from nautilus_trader.config import CacheConfig, TradingNodeConfig
            from nautilus_trader.live.node import TradingNode

            from engine.nautilus.clients.kabu_station.kabu_station_exec_client import (
                KabuStationLiveExecutionClient,
            )

            config = TradingNodeConfig(
                trader_id="KABUSTATION-SMOKE",
                cache=CacheConfig(database=None),
            )
            node = TradingNode(config=config)
            kernel = node.kernel

            from nautilus_trader.common.providers import InstrumentProvider
            from nautilus_trader.model.enums import AccountType, OmsType
            from nautilus_trader.model.identifiers import ClientId, Venue

            client = KabuStationLiveExecutionClient(
                loop=kernel.loop,
                client_id=ClientId("KABUSTATION-001"),
                venue=Venue("TSE"),
                oms_type=OmsType.NETTING,
                account_type=AccountType.CASH,
                base_currency=None,
                instrument_provider=InstrumentProvider(),
                msgbus=kernel.msgbus,
                cache=kernel.cache,
                clock=kernel.clock,
                kabu_venue=_make_kabu_venue_mock(),
                strategy_id="kabu-smoke",
                max_qty=100,
                max_notional_jpy=500_000,
            )

            # 実 register_client（Cython parent type check）
            kernel.exec_engine.register_client(client)
        finally:
            loop.close()

    def test_register_client_accepts_kabu_station_data_client(self):
        loop = self._setup_loop_in_thread()
        try:
            from nautilus_trader.config import CacheConfig, TradingNodeConfig
            from nautilus_trader.live.node import TradingNode

            from engine.nautilus.clients.kabu_station.kabu_station_data_client import (
                KabuStationLiveDataClient,
            )

            config = TradingNodeConfig(
                trader_id="KABUSTATION-SMOKE-DATA",
                cache=CacheConfig(database=None),
            )
            node = TradingNode(config=config)
            kernel = node.kernel

            from nautilus_trader.common.providers import InstrumentProvider
            from nautilus_trader.model.identifiers import ClientId, Venue

            client = KabuStationLiveDataClient(
                loop=kernel.loop,
                client_id=ClientId("KABUSTATION-DATA-001"),
                venue=Venue("TSE"),
                msgbus=kernel.msgbus,
                cache=kernel.cache,
                clock=kernel.clock,
                instrument_provider=InstrumentProvider(),
                on_event=lambda _evt: None,
            )

            kernel.data_engine.register_client(client)
        finally:
            loop.close()
