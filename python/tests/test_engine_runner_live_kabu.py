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


def _patch_kabu_dependencies(monkeypatch, *, warm_up_mode: str = "true"):
    """kabu_station 依存を最小 fake に差し替える。"""
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
