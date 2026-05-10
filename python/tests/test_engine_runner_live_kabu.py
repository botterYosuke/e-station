"""issue #42 Phase 4: NautilusRunner.start_live() venue 分岐 unit tests.

統一決定: tachibana 専用だった ``start_live()`` を venue 引数で分岐:

- ``venue="tachibana"`` (default) → 既存 TachibanaLiveExecutionClient 経路
- ``venue="kabu_station"`` → 新 KabuStationLiveExecutionClient 経路
- ``venue="invalid"`` → ``EngineError{code:"venue_not_supported"}`` emit + abort

warm_up failure 経路 / silent failure 対策（EngineStopped 後付け emit）は
tachibana と同じく kabu_station にも適用される。
"""
from __future__ import annotations

import queue as _stdlib_queue
import threading

import pytest

from engine.nautilus.engine_runner import NautilusRunner


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

    class _FakeNode:
        def __init__(self, *_a, **_kw):
            self._data_engine = type("DE", (), {"register_client": lambda *a, **k: None})()
            self._exec_engine = type("EE", (), {"register_client": lambda *a, **k: None})()

        def add_data(self, *_a, **_kw):
            pass

        def add_strategies(self, *_a, **_kw):
            pass

        def build(self):
            if node_build_mode == "fail-runtime":
                # 受け入れ基準: warm_up=true 経路で LiveStrategyReady emit が
                # 走った後、node.build() が失敗 → node_build_failed event。
                raise RuntimeError("simulated node.build() failure")
            raise AssertionError(
                "node.build() should not be called when warm_up fails (kabu test path)"
            )

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


class TestLiveBridgesNotStartedForKabuStation:
    """review-fix H-2: LiveDataBridge / LiveEcBridge は tachibana 専用なので
    venue=kabu_station では起動しない。

    起動すると ``data_client._loop.call_soon_threadsafe(...)`` で AttributeError が
    daemon thread 内で出て silent failure になる。venue gate が消える regression を
    pin するためソース文字列を inspect する。
    """

    def test_live_bridges_gated_by_tachibana_venue(self):
        import inspect
        from engine.nautilus import engine_runner as _er

        src = inspect.getsource(_er.NautilusRunner.start_live)
        # `_have_bridges` 起動条件に venue == "tachibana" gate があることを pin
        assert 'venue == "tachibana"' in src, (
            "LiveDataBridge / LiveEcBridge 起動は venue==tachibana で gate 必須"
        )


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
