"""issue #42 Phase 4: NautilusRunner.start_live() venue 分岐 unit tests.

統一決定: tachibana 専用だった ``start_live()`` を venue 引数で分岐:

- ``venue="tachibana"`` (default) → 既存 TachibanaLiveExecutionClient 経路
- ``venue="kabu_station"`` → 新 KabuStationLiveExecutionClient 経路
- ``venue="invalid"`` → ``EngineError{code:"venue_not_supported"}`` emit + abort

warm_up failure 経路 / silent failure 対策（EngineStopped 後付け emit）は
tachibana と同じく kabu_station にも適用される。
"""
from __future__ import annotations

import asyncio
import queue as _stdlib_queue
import threading

import pytest

from engine.nautilus.engine_runner import NautilusRunner


async def _async_sleep_forever() -> None:
    """Test-only helper: long-running async task that只 stop_event 経由でキャンセルされる。"""
    while True:
        await asyncio.sleep(0.01)


def _make_runner_args(on_event, *, venue: str = "kabu_station"):
    """start_live() に渡す引数 dict（kabu_station venue デフォルト）。"""
    return dict(
        instrument_id="9433.KabuStation Stock",
        strategy_file=None,
        strategy_init_kwargs=None,
        max_qty=100,
        max_notional_jpy=500_000,
        second_password="",  # kabu_station は第二暗証番号不要 (KabuTradePasswordHolder で別管理)
        session=object(),  # KabuStationVenue として後から差し替える
        fd_queue=_stdlib_queue.Queue(maxsize=10),
        ec_queue=_stdlib_queue.Queue(maxsize=10),
        on_event=on_event,
        stop_event=threading.Event(),
        strategy_id="test-kabu-live",
        venue=venue,
    )


class _FakeKabuExecClient:
    """warm_up True / close を実装した最小 fake。"""

    instances: list["_FakeKabuExecClient"] = []

    def __init__(self, *, warm_up_mode: str = "true", **_kwargs):
        self.warm_up_mode = warm_up_mode
        self.warm_up_called = False
        self.close_called = False
        self.id = "fake-kabu-exec"
        type(self).instances.append(self)

    async def warm_up(self) -> bool:
        self.warm_up_called = True
        if self.warm_up_mode == "raise":
            raise RuntimeError("simulated kabu warm_up failure")
        if self.warm_up_mode == "false":
            return False
        return True

    async def close(self) -> None:
        self.close_called = True


def _patch_kabu_dependencies(
    monkeypatch,
    *,
    warm_up_mode: str = "true",
    node_build_mode: str = "fail-assertion",
):
    """kabu_station 依存を最小 fake に差し替える。

    Args:
        warm_up_mode: "true" / "false" / "raise"
        node_build_mode: "fail-assertion" (デフォルト、warm_up failure 経路で node.build()
            に到達したら AssertionError) / "fail-runtime" (build で RuntimeError を投げ、
            warm_up 成功 → LiveStrategyReady emit → node_build_failed 経路を観測する用途)
    """
    monkeypatch.setattr(
        "engine.nautilus.engine_runner.is_market_open",
        lambda *_a, **_kw: True,
    )
    _FakeKabuExecClient.instances = []

    def _factory(*args, **kwargs):
        return _FakeKabuExecClient(warm_up_mode=warm_up_mode, **kwargs)

    monkeypatch.setattr(
        "engine.nautilus.clients.kabu_station.kabu_station_exec_client.KabuStationLiveExecutionClient",
        _factory,
    )

    class _FakeDataClient:
        def __init__(self, *_a, **_kw):
            self.id = "fake-kabu-data"

    class _FakeEventBridge:
        def __init__(self, *_a, **_kw):
            self._loop = None

    monkeypatch.setattr(
        "engine.nautilus.clients.kabu_station.kabu_station_data_client.KabuStationLiveDataClient",
        _FakeDataClient,
    )
    monkeypatch.setattr(
        "engine.nautilus.clients.kabu_station.kabu_station_event_bridge.KabuStationEventBridge",
        _FakeEventBridge,
    )

    # R4 R3-SILENT-5: engine_runner.py kabu 分岐は ``node.kernel`` が None なら
    # 早期 abort して EngineError(kernel_unavailable) を emit する。テスト経路は
    # kernel を mock で持たせて parent kwargs 組み立てを通す（factory 側で kwargs
    # は無害に投げ捨てられる）。
    # R8 HIGH-2 / R4 Group F: register_client は ``kernel.{data,exec}_engine``
    # 経由のみ（real TradingNode 準拠）。intermediate underscore 変数を持たず、
    # canonical な ``data_engine`` / ``exec_engine`` を class attr に直接代入する。
    class _FakeKernel:
        loop = None
        msgbus = None
        cache = None
        clock = None
        data_engine = type("DE", (), {"register_client": lambda *a, **k: None})()
        exec_engine = type("EE", (), {"register_client": lambda *a, **k: None})()

    class _FakeNode:
        def __init__(self, *_a, **_kw):
            self.kernel = _FakeKernel()

        def add_data(self, *_a, **_kw):
            pass

        def add_strategies(self, *_a, **_kw):
            pass

        def build(self):
            if node_build_mode == "fail-runtime":
                # 受け入れ基準: warm_up=true 経路で LiveStrategyReady emit が
                # 走った後、node.build() が失敗 → node_build_failed event。
                raise RuntimeError("simulated node.build() failure")
            if node_build_mode == "success":
                # R2 CRITICAL-1: warm_up=true + build()=成功 経路。bridge thread
                # 起動を観測する用途。
                return
            raise AssertionError(
                "node.build() should not be called when warm_up fails (kabu test path)"
            )

        async def run_async(self):
            # node が長く走るふりをして停止シグナルを待つ。stop_waiter 側で
            # FIRST_COMPLETED を解決させる設計（_asyncio.wait → cancel pending）。
            await _async_sleep_forever()

        async def stop_async(self):
            return None

        def stop(self):
            return None

    monkeypatch.setattr("nautilus_trader.live.node.TradingNode", _FakeNode)
    monkeypatch.setattr(
        "engine.nautilus.instrument_factory.make_equity_instrument",
        lambda *_a, **_kw: object(),
    )

    return _FakeKabuExecClient


# ---------------------------------------------------------------------------
# venue=invalid → EngineError{venue_not_supported}
# ---------------------------------------------------------------------------


class TestInvalidVenueRejected:
    def test_invalid_venue_emits_engine_error_and_aborts(self, monkeypatch):
        # market_open は True にしておく（ここで弾かない経路を確認）
        monkeypatch.setattr(
            "engine.nautilus.engine_runner.is_market_open",
            lambda *_a, **_kw: True,
        )

        events: list[dict] = []
        runner = NautilusRunner()
        runner.start_live(**_make_runner_args(events.append, venue="invalid"))

        venue_errors = [
            e for e in events
            if e.get("event") == "EngineError" and e.get("code") == "venue_not_supported"
        ]
        assert len(venue_errors) == 1, (
            f"expected 1 EngineError(venue_not_supported), got events={events!r}"
        )
        # silent failure 対策: EngineStopped を emit して Rust 側 state machine を unstuck
        stopped = [e for e in events if e.get("event") == "EngineStopped"]
        assert len(stopped) == 1


# ---------------------------------------------------------------------------
# venue=kabu_station: warm_up 失敗経路（例外 OR False）
# ---------------------------------------------------------------------------


class TestKabuStationWarmUpFailure:
    def test_kabu_warm_up_exception_emits_warm_up_failed(self, monkeypatch):
        _patch_kabu_dependencies(monkeypatch, warm_up_mode="raise")

        events: list[dict] = []
        runner = NautilusRunner()
        runner.start_live(**_make_runner_args(events.append, venue="kabu_station"))

        warm_up_failed = [
            e for e in events
            if e.get("event") == "EngineError" and e.get("code") == "warm_up_failed"
        ]
        assert len(warm_up_failed) == 1
        # exec_client.close() called for resource leak guard
        assert _FakeKabuExecClient.instances
        assert _FakeKabuExecClient.instances[0].close_called

    def test_kabu_warm_up_returns_false_emits_warm_up_failed(self, monkeypatch):
        _patch_kabu_dependencies(monkeypatch, warm_up_mode="false")

        events: list[dict] = []
        runner = NautilusRunner()
        runner.start_live(**_make_runner_args(events.append, venue="kabu_station"))

        warm_up_failed = [
            e for e in events
            if e.get("event") == "EngineError" and e.get("code") == "warm_up_failed"
        ]
        assert len(warm_up_failed) == 1
        assert _FakeKabuExecClient.instances[0].close_called


# ---------------------------------------------------------------------------
# warm_up=true 経路で LiveStrategyReady の venue / instrument_id が正しく流れるか
# （review-fix M-2: 正の test pin 不足を補う）
# ---------------------------------------------------------------------------


class TestSecondPasswordOptionalForKabu:
    """R3 M9: kabu_station 経路では second_password=None を許容する。
    tachibana 経路では None だと reject (type / runtime とも安全側に倒す)。
    """

    def test_kabu_warm_up_accepts_second_password_none(self, monkeypatch):
        _patch_kabu_dependencies(monkeypatch, warm_up_mode="false")  # 経路通過確認のみ
        events: list[dict] = []
        runner = NautilusRunner()
        args = _make_runner_args(events.append, venue="kabu_station")
        args["second_password"] = None
        # AttributeError / TypeError 等の生例外で落ちないこと (warm_up=false で
        # warm_up_failed 経路に到達して終了するはず)。
        runner.start_live(**args)
        # warm_up_failed event は出るが、本テストの主目的は「None で start_live が
        # 通過する」ことの pin。
        warm_up_failed = [
            e for e in events
            if e.get("event") == "EngineError" and e.get("code") == "warm_up_failed"
        ]
        assert warm_up_failed, (
            f"kabu_station venue + second_password=None で start_live が "
            f"AttributeError / TypeError を投げず warm_up 経路に到達すべき (R3 M9); "
            f"events={events!r}"
        )

    def test_tachibana_rejects_second_password_none(self):
        """tachibana 経路で second_password=None は EngineError で reject。"""
        events: list[dict] = []
        runner = NautilusRunner()
        args = _make_runner_args(events.append, venue="tachibana")
        args["second_password"] = None
        runner.start_live(**args)
        errors = [
            e for e in events
            if e.get("event") == "EngineError"
            and e.get("code")
            in ("invalid_config", "second_password_required", "warm_up_failed")
        ]
        assert errors, (
            f"tachibana venue + second_password=None は EngineError で reject "
            f"されるべき (R3 M9); events={events!r}"
        )


class TestLiveStrategyReadyVenuePropagation:
    """warm_up 成功 → LiveStrategyReady{venue:"kabu_station", ...} が emit されるか。

    venue 引数 → LiveStrategyReady.venue の伝搬を pin。Rust 側 ``auto_generate_live_panes``
    の冪等 key は ``(strategy_id, instrument_id, venue)`` 三つ組で、venue 取り違えは
    4 ペイン重複生成 / reconnect 経路の冪等性破壊につながる。

    node.build() を ``fail-runtime`` モードに切り替えて RuntimeError → node_build_failed
    経路を辿ることで、LiveStrategyReady emit 直後の sequence を観測する。
    """

    def test_kabu_warm_up_success_emits_live_strategy_ready_with_kabu_venue(
        self, monkeypatch
    ):
        _patch_kabu_dependencies(
            monkeypatch, warm_up_mode="true", node_build_mode="fail-runtime"
        )

        events: list[dict] = []
        runner = NautilusRunner()
        runner.start_live(**_make_runner_args(events.append, venue="kabu_station"))

        ready_events = [e for e in events if e.get("event") == "LiveStrategyReady"]
        assert len(ready_events) == 1, (
            f"expected 1 LiveStrategyReady, got events={events!r}"
        )
        # venue 取り違え regression を pin
        assert ready_events[0]["venue"] == "kabu_station", (
            f"LiveStrategyReady.venue must propagate venue arg, got {ready_events[0]!r}"
        )
        assert ready_events[0]["instrument_id"] == "9433.KabuStation Stock"
        assert ready_events[0]["strategy_id"] == "test-kabu-live"

        # EngineStarted も venue が account_id に流れる
        started_events = [e for e in events if e.get("event") == "EngineStarted"]
        assert len(started_events) == 1
        assert started_events[0]["account_id"] == "kabu_station"

        # build() 失敗 → node_build_failed が後続で出る（順序確認）
        build_failed = [
            e for e in events
            if e.get("event") == "EngineError" and e.get("code") == "node_build_failed"
        ]
        assert len(build_failed) == 1
        # LiveStrategyReady より node_build_failed が後（時系列順序の pin）
        ready_idx = events.index(ready_events[0])
        failed_idx = events.index(build_failed[0])
        assert ready_idx < failed_idx, (
            "LiveStrategyReady must precede node_build_failed (warm_up→Ready→build 順)"
        )

        # R8 HIGH-1: EngineStarted must be emitted BEFORE LiveStrategyReady.
        # Rust 側 src/handlers/replay.rs:LiveStarted arm が pending_strategy_id /
        # 60s warm_up timeout token をセットし、後続の LiveStrategyReady arm で
        # クリアする設計（test_live_session_cli_e2e.py:_EXPECTED_LIFECYCLE と一致）。
        # 順序が逆だと Ready で先にクリア → Started で再セットされ phantom
        # warm_up timeout が 60s 後に GUI に出る silent regression を起こす。
        started_idx = events.index(started_events[0])
        assert started_idx < ready_idx, (
            "EngineStarted must precede LiveStrategyReady "
            f"(Rust state machine契約 / _EXPECTED_LIFECYCLE), got events={events!r}"
        )


class TestEngineStartedOrderingDuringWarmUp:
    """issue #42 R1 HIGH-1: ``EngineStarted`` を ``LiveStrategyWarmingUp`` より先に emit する。

    旧実装は ``ticker_task`` (5s 毎 LiveStrategyWarmingUp emit) を warm_up より前に start し、
    ``EngineStarted`` は warm_up 完了後に emit していた。warm_up が 5s を超えると ticker が
    先に LiveStrategyWarmingUp を emit してしまい、Rust 側 state machine
    (``src/handlers/replay.rs::ReplayMsg::LiveStarted`` arm が pending_strategy_id を set し
    ``LiveWarmingUp`` arm がそれと照合する設計) において先行 LiveStrategyWarmingUp が
    silent drop されていた（warm_up 進捗 banner / 60s timeout reset が機能しない）。

    user-spec lifecycle: ``EngineStarted → LiveStrategyWarmingUp → LiveStrategyReady``。
    """

    def test_engine_started_emitted_before_live_strategy_warming_up(self, monkeypatch):
        """warm_up が 5s 超かかる場合でも EngineStarted を先に emit する。

        ticker が 5s 毎に発火するため、warm_up を 6s 程度走らせると warm_up 完了前に
        LiveStrategyWarmingUp が emit される（旧実装はこちらが先になっていた）。
        本テストは新実装で EngineStarted が ticker 起動より前に出ることを pin する。
        """
        import time
        # 6s warm_up（ticker 1 回分以上）
        slow_factory_calls: list[float] = []

        class _SlowWarmUpExecClient(_FakeKabuExecClient):
            async def warm_up(self) -> bool:
                self.warm_up_called = True
                slow_factory_calls.append(time.monotonic())
                # 5s + 余裕。ticker が少なくとも 1 回 LiveStrategyWarmingUp を emit する。
                await asyncio.sleep(5.2)
                return True

        monkeypatch.setattr(
            "engine.nautilus.engine_runner.is_market_open",
            lambda *_a, **_kw: True,
        )
        _FakeKabuExecClient.instances = []

        def _factory(*_a, **kwargs):
            return _SlowWarmUpExecClient(warm_up_mode="true", **kwargs)

        monkeypatch.setattr(
            "engine.nautilus.clients.kabu_station.kabu_station_exec_client.KabuStationLiveExecutionClient",
            _factory,
        )

        class _FakeDataClient:
            def __init__(self, *_a, **_kw):
                self.id = "fake-kabu-data"

        class _FakeEventBridge:
            def __init__(self, *_a, **_kw):
                self._loop = None

        monkeypatch.setattr(
            "engine.nautilus.clients.kabu_station.kabu_station_data_client.KabuStationLiveDataClient",
            _FakeDataClient,
        )
        monkeypatch.setattr(
            "engine.nautilus.clients.kabu_station.kabu_station_event_bridge.KabuStationEventBridge",
            _FakeEventBridge,
        )

        class _FakeKernel:
            loop = None
            msgbus = None
            cache = None
            clock = None
            data_engine = type("DE", (), {"register_client": lambda *a, **k: None})()
            exec_engine = type("EE", (), {"register_client": lambda *a, **k: None})()

        class _FakeNode:
            def __init__(self, *_a, **_kw):
                self.kernel = _FakeKernel()

            def add_data(self, *_a, **_kw):
                pass

            def add_strategies(self, *_a, **_kw):
                pass

            def build(self):
                # warm_up → Started → Ready の後、build 失敗で cut-off。
                raise RuntimeError("test cut-off")

        monkeypatch.setattr("nautilus_trader.live.node.TradingNode", _FakeNode)
        monkeypatch.setattr(
            "engine.nautilus.instrument_factory.make_equity_instrument",
            lambda *_a, **_kw: object(),
        )

        events: list[dict] = []
        runner = NautilusRunner()
        runner.start_live(**_make_runner_args(events.append, venue="kabu_station"))

        # 順序検査: EngineStarted が LiveStrategyWarmingUp より先に出ていること。
        started_indices = [
            i for i, e in enumerate(events) if e.get("event") == "EngineStarted"
        ]
        warming_indices = [
            i for i, e in enumerate(events) if e.get("event") == "LiveStrategyWarmingUp"
        ]
        assert started_indices, (
            f"EngineStarted must be emitted (HIGH-1 contract); events={events!r}"
        )
        assert warming_indices, (
            "ticker must fire at least once during 5.2s warm_up "
            f"(test pre-condition); events={events!r}"
        )
        assert started_indices[0] < warming_indices[0], (
            "EngineStarted must precede LiveStrategyWarmingUp (R1 HIGH-1); "
            f"started_idx={started_indices[0]}, warming_idx={warming_indices[0]}, "
            f"events={events!r}"
        )

    def test_engine_started_emitted_before_live_strategy_warming_up_tachibana(
        self, monkeypatch
    ):
        """tachibana 経路でも同じ順序契約 (R1 HIGH-1 venue 対称性 pin)。"""
        import time
        from tests.test_engine_runner_live_warmup_failure import (
            _FakeExecClient,
            _patch_min_dependencies as _patch_tachibana_min,
        )

        class _SlowTachExecClient(_FakeExecClient):
            async def warm_up(self) -> bool:
                self.warm_up_called = True
                await asyncio.sleep(5.2)
                return True

        # market_open + 他の Tachibana 依存を patch
        monkeypatch.setattr(
            "engine.nautilus.engine_runner.is_market_open",
            lambda *_a, **_kw: True,
        )
        _FakeExecClient.instances = []

        def _factory(*_a, **kwargs):
            # warm_up_mode は base __init__ で必要。slow override する。
            return _SlowTachExecClient(warm_up_mode="true", **kwargs)

        monkeypatch.setattr(
            "engine.nautilus.clients.tachibana.TachibanaLiveExecutionClient",
            _factory,
        )

        class _FakeDataClient:
            def __init__(self, *_a, **_kw):
                self.id = "fake-data-client"

        class _FakeOrderIdMap:
            def __init__(self, *_a, **_kw):
                pass

        class _FakeEventBridge:
            def __init__(self, *_a, **_kw):
                self._loop = None

        monkeypatch.setattr(
            "engine.nautilus.clients.tachibana_data.TachibanaLiveDataClient",
            _FakeDataClient,
        )
        monkeypatch.setattr(
            "engine.nautilus.clients.tachibana_event_bridge.OrderIdMap",
            _FakeOrderIdMap,
        )
        monkeypatch.setattr(
            "engine.nautilus.clients.tachibana_event_bridge.TachibanaEventBridge",
            _FakeEventBridge,
        )

        class _FakeNode:
            def __init__(self, *_a, **_kw):
                class _Kernel:
                    loop = None
                    msgbus = None
                    cache = None
                    clock = None
                    data_engine = type("DE", (), {"register_client": lambda *a, **k: None})()
                    exec_engine = type("EE", (), {"register_client": lambda *a, **k: None})()
                self.kernel = _Kernel()

            def add_data(self, *_a, **_kw):
                pass

            def add_strategies(self, *_a, **_kw):
                pass

            def build(self):
                raise RuntimeError("test cut-off")

        monkeypatch.setattr("nautilus_trader.live.node.TradingNode", _FakeNode)
        monkeypatch.setattr(
            "engine.nautilus.instrument_factory.make_equity_instrument",
            lambda *_a, **_kw: object(),
        )

        events: list[dict] = []
        runner = NautilusRunner()
        # tachibana 既定の引数 dict（venue 省略 = tachibana）
        args = dict(
            instrument_id="8306.T",
            strategy_file=None,
            strategy_init_kwargs=None,
            max_qty=100,
            max_notional_jpy=500_000,
            second_password="test-pw",
            session=object(),
            fd_queue=_stdlib_queue.Queue(maxsize=10),
            ec_queue=_stdlib_queue.Queue(maxsize=10),
            on_event=events.append,
            stop_event=threading.Event(),
            strategy_id="test-tachibana-order",
        )
        runner.start_live(**args)

        started_indices = [
            i for i, e in enumerate(events) if e.get("event") == "EngineStarted"
        ]
        warming_indices = [
            i for i, e in enumerate(events) if e.get("event") == "LiveStrategyWarmingUp"
        ]
        assert started_indices, f"EngineStarted must be emitted; events={events!r}"
        assert warming_indices, f"ticker must fire; events={events!r}"
        assert started_indices[0] < warming_indices[0], (
            "EngineStarted must precede LiveStrategyWarmingUp on tachibana too "
            f"(R1 HIGH-1); events={events!r}"
        )


class TestLiveBridgesVenueDispatch:
    """issue #42 R2 CRITICAL-1: venue 別に bridge 種別を起動する。

    tachibana → LiveDataBridge / LiveEcBridge（既存）
    kabu_station → KabuLiveDataBridge / KabuLiveEcBridge（R2 新規）

    起動経路は engine_runner.start_live() 内の node.build() 後ブロック。
    旧版（review-fix H-2 時点）は ``venue == "tachibana"`` で gate していたため
    kabu live data が一切 flow しない silent failure になっていた。R2 で本配線。
    """

    def test_kabu_bridges_referenced_in_source(self):
        """kabu 経路で KabuLiveDataBridge / KabuLiveEcBridge を起動する分岐を pin。"""
        import inspect
        from engine.nautilus import engine_runner as _er

        src = inspect.getsource(_er.NautilusRunner.start_live)
        assert "KabuLiveDataBridge" in src, (
            "kabu_station 経路で KabuLiveDataBridge を起動する必要あり (R2 CRITICAL-1)"
        )
        assert "KabuLiveEcBridge" in src, (
            "kabu_station 経路で KabuLiveEcBridge を起動する必要あり (R2 CRITICAL-1)"
        )

    def test_kabu_live_data_bridge_started_for_kabu_venue(self, monkeypatch):
        """warm_up=true → node.build() 成功 → bridge thread 起動を観測する。

        bridge クラス自体を fake に差し替えて、Thread に渡される `target` が
        kabu bridge の `run` method であることを ID で確認する。
        """
        _patch_kabu_dependencies(monkeypatch, warm_up_mode="true", node_build_mode="success")

        # KabuLiveDataBridge / KabuLiveEcBridge を fake クラスに差し替え、
        # __init__ で記録 + run() で即終了する。
        instantiated_data: list = []
        instantiated_ec: list = []

        class _FakeKabuDataBridge:
            def __init__(self, *args, **kwargs):
                instantiated_data.append((args, kwargs))

            def run(self):
                return  # daemon thread が即終了

        class _FakeKabuEcBridge:
            def __init__(self, *args, **kwargs):
                instantiated_ec.append((args, kwargs))

            def run(self):
                return

        monkeypatch.setattr(
            "engine.nautilus.live_bridges.KabuLiveDataBridge", _FakeKabuDataBridge
        )
        monkeypatch.setattr(
            "engine.nautilus.live_bridges.KabuLiveEcBridge", _FakeKabuEcBridge
        )

        import threading as _threading
        events: list[dict] = []
        runner = NautilusRunner()
        stop_event = _threading.Event()
        stop_event.set()
        args = _make_runner_args(events.append, venue="kabu_station")
        args["stop_event"] = stop_event
        # Python 3.14: ``loop.create_task(loop.run_in_executor(...))`` は
        # TypeError を raise する（既存挙動・本 fix 範囲外）。bridge 構築は
        # その前に完了するため、TypeError は catch して bridges 生成だけ観測する。
        try:
            runner.start_live(**args)
        except TypeError as exc:
            # 想定済 TypeError 以外なら raise する
            if "coroutine was expected" not in str(exc):
                raise

        assert instantiated_data, (
            "KabuLiveDataBridge must be instantiated for venue=kabu_station (R2 CRITICAL-1)"
        )
        assert instantiated_ec, (
            "KabuLiveEcBridge must be instantiated for venue=kabu_station (R2 CRITICAL-1)"
        )

    def test_kabu_event_bridge_loop_injected_for_kabu_venue(self, monkeypatch):
        """kabu の event_bridge は call_soon_threadsafe で叩かれるため
        engine_runner で _loop を明示注入する必要がある。
        """
        _patch_kabu_dependencies(monkeypatch, warm_up_mode="true", node_build_mode="success")

        # KabuStationEventBridge factory を実体に置き換え、event_bridge._loop が
        # set されたことを観測する
        observed: list = []

        class _ObservingEventBridge:
            def __init__(self, *a, **kw):
                self._loop = None
                observed.append(self)

        monkeypatch.setattr(
            "engine.nautilus.clients.kabu_station.kabu_station_event_bridge.KabuStationEventBridge",
            _ObservingEventBridge,
        )

        # bridges も fake にして実 thread を spawn しない
        class _FakeBridge:
            def __init__(self, *_a, **_kw):
                pass

            def run(self):
                return

        monkeypatch.setattr(
            "engine.nautilus.live_bridges.KabuLiveDataBridge", _FakeBridge
        )
        monkeypatch.setattr(
            "engine.nautilus.live_bridges.KabuLiveEcBridge", _FakeBridge
        )

        import threading as _threading
        events: list[dict] = []
        runner = NautilusRunner()
        stop_event = _threading.Event()
        stop_event.set()
        args = _make_runner_args(events.append, venue="kabu_station")
        args["stop_event"] = stop_event
        try:
            runner.start_live(**args)
        except TypeError as exc:
            # Python 3.14: ``loop.create_task(Future)`` の TypeError（既存挙動）。
            if "coroutine was expected" not in str(exc):
                raise

        assert observed, "KabuStationEventBridge must be instantiated for kabu_station venue"
        # _loop が None ではなく injected event loop に set されている
        assert observed[0]._loop is not None, (
            "engine_runner must inject event_bridge._loop after node.build() (R2 CRITICAL-1)"
        )

    def test_kabu_live_ec_bridge_calls_process_order_record(self):
        """KabuLiveEcBridge.run() は queue から record を取って
        bridge.process_order_record() を loop B 上で呼ぶ（call_soon_threadsafe 経由）。
        """
        import queue
        import threading
        from unittest.mock import MagicMock
        from engine.nautilus.live_bridges import KabuLiveEcBridge

        # bridge mock — _loop / process_order_record を持つ
        fake_loop = MagicMock()
        fake_loop.call_soon_threadsafe = MagicMock(
            side_effect=lambda fn, *a: fn(*a)  # synchronous in test
        )
        fake_bridge = MagicMock()
        fake_bridge._loop = fake_loop
        fake_bridge.process_order_record = MagicMock()

        ec_queue: queue.Queue = queue.Queue()
        stop_event = threading.Event()
        ec_bridge = KabuLiveEcBridge(fake_bridge, ec_queue, stop_event)

        record = {"OrderID": "ORD-1", "State": 5, "Symbol": "9433"}
        ec_queue.put(record)

        # 1 iteration だけ run — stop_event を after one process で set する
        # bridge.run() は while not stop ループだが poll は 50ms。
        t = threading.Thread(target=ec_bridge.run, daemon=True)
        t.start()
        # 短時間待って record が処理されたら stop
        import time
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if fake_bridge.process_order_record.called:
                break
            time.sleep(0.05)
        stop_event.set()
        t.join(timeout=2.0)

        fake_bridge.process_order_record.assert_called_once_with(record)


class TestWarmingUpTickerVenueText:
    """review-fix H-3: warming_up_ticker メッセージが venue 名を含むかの pin（regression 防止）。

    実 ticker は 5s 周期で発火するため tests では `_warming_up_ticker` の stages 文字列を
    inspect してハードコード "tachibana" 残存を弾く。具体的には source 全体を見て
    旧来の `connecting to tachibana` 直書き行が **存在しないこと** を assert する。
    """

    def test_warming_up_ticker_does_not_hardcode_tachibana(self):
        import inspect
        from engine.nautilus import engine_runner as _er

        src = inspect.getsource(_er)
        # 旧版に存在した hard-code 文字列が start_live 内から消えていることを pin する。
        assert '"connecting to tachibana"' not in src, (
            "_warming_up_ticker must not hardcode 'connecting to tachibana' "
            "(regression: kabu live 起動時に tachibana 文言が出る)"
        )


# ---------------------------------------------------------------------------
# tachibana 経路の後方互換: venue 省略 / venue="tachibana" は既存挙動
# ---------------------------------------------------------------------------


class TestRegisterClientUsesKernelPath:
    """R8 HIGH-2: ``node.kernel.{data,exec}_engine.register_client`` 経由で
    Nautilus 親型 type check を通すこと。

    旧実装は ``node._data_engine`` / ``node._exec_engine`` という underscore
    prefix の private 属性に直接 register_client していた。real ``TradingNode``
    は canonical な surface として ``kernel.data_engine`` / ``kernel.exec_engine``
    のみを expose しており、private 属性は内部実装次第で消失しうる。
    fake test 経路は両方 mock していたため silent に通っていたが、
    real ``TradingNode`` smoke (test_kabu_station_nautilus_parent.py の
    ``@pytest.mark.live_demo_inprocess`` 群) は ``kernel.*_engine.register_client``
    のみを使っており、production と test の経路が drift していた。

    本 test では underscore 経路を **わざと AttributeError を出すように** 設計し、
    kernel 経由のみ通る regression pin として固定する。
    """

    def test_register_client_called_via_kernel_not_private_attr(self, monkeypatch):
        from unittest.mock import MagicMock

        monkeypatch.setattr(
            "engine.nautilus.engine_runner.is_market_open",
            lambda *_a, **_kw: True,
        )
        _FakeKabuExecClient.instances = []

        def _exec_factory(*_a, **kwargs):
            return _FakeKabuExecClient(warm_up_mode="true", **kwargs)

        monkeypatch.setattr(
            "engine.nautilus.clients.kabu_station.kabu_station_exec_client.KabuStationLiveExecutionClient",
            _exec_factory,
        )

        class _FakeDataClient:
            def __init__(self, *_a, **_kw):
                self.id = "fake-kabu-data"

        class _FakeEventBridge:
            def __init__(self, *_a, **_kw):
                self._loop = None

        monkeypatch.setattr(
            "engine.nautilus.clients.kabu_station.kabu_station_data_client.KabuStationLiveDataClient",
            _FakeDataClient,
        )
        monkeypatch.setattr(
            "engine.nautilus.clients.kabu_station.kabu_station_event_bridge.KabuStationEventBridge",
            _FakeEventBridge,
        )

        # kernel surface のみを expose する fake. ``_data_engine`` / ``_exec_engine``
        # 属性は **わざと持たせない** ことで、production code が underscore 経路に
        # fallback したら AttributeError で即 fail する設計。
        kernel_exec_mock = MagicMock()
        kernel_data_mock = MagicMock()

        class _KernelOnlyFakeKernel:
            loop = None
            msgbus = None
            cache = None
            clock = None
            data_engine = kernel_data_mock
            exec_engine = kernel_exec_mock

        class _KernelOnlyFakeNode:
            def __init__(self, *_a, **_kw):
                self.kernel = _KernelOnlyFakeKernel()

            def add_data(self, *_a, **_kw):
                pass

            def add_strategies(self, *_a, **_kw):
                pass

            def build(self):
                raise RuntimeError("kernel-only smoke: stop here, no real build()")

        monkeypatch.setattr("nautilus_trader.live.node.TradingNode", _KernelOnlyFakeNode)
        monkeypatch.setattr(
            "engine.nautilus.instrument_factory.make_equity_instrument",
            lambda *_a, **_kw: object(),
        )

        events: list[dict] = []
        runner = NautilusRunner()
        runner.start_live(**_make_runner_args(events.append, venue="kabu_station"))

        # kernel 経由で register_client が **一度ずつ** 呼ばれたこと。
        kernel_exec_mock.register_client.assert_called_once()
        kernel_data_mock.register_client.assert_called_once()


class TestTachibanaBackwardsCompat:
    def test_default_venue_is_tachibana(self, monkeypatch):
        """venue 引数省略時は tachibana 経路を維持する（後方互換）。"""
        monkeypatch.setattr(
            "engine.nautilus.engine_runner.is_market_open",
            lambda *_a, **_kw: True,
        )

        from tests.test_engine_runner_live_warmup_failure import (
            _patch_min_dependencies as _patch_tachibana,
        )

        _patch_tachibana(monkeypatch, warm_up_mode="raise")

        events: list[dict] = []
        runner = NautilusRunner()
        # venue 引数を省略 → tachibana にフォールバック
        args = dict(
            instrument_id="8306.T",
            strategy_file=None,
            strategy_init_kwargs=None,
            max_qty=100,
            max_notional_jpy=500_000,
            second_password="x",
            session=object(),
            fd_queue=_stdlib_queue.Queue(maxsize=10),
            ec_queue=_stdlib_queue.Queue(maxsize=10),
            on_event=events.append,
            stop_event=threading.Event(),
            strategy_id="test-tachibana",
        )
        runner.start_live(**args)

        warm_up_failed = [
            e for e in events
            if e.get("event") == "EngineError" and e.get("code") == "warm_up_failed"
        ]
        assert len(warm_up_failed) == 1
