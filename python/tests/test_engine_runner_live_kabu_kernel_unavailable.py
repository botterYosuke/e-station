"""issue #42 R4 R3-SILENT-5: kabu_station 経路で ``node.kernel`` 不在時の早期 abort。

R3 sanity sweep で発見された MEDIUM:
- kabu_station 分岐の ``_kernel is None`` フォールバックが空 ``_kabu_parent_kwargs``
  で client 構築を試み、必須引数不足で ``TypeError`` になる経路があった。
  ``_kernel is None`` のとき早期 abort して
  ``EngineError{kernel_unavailable}`` + ``EngineStopped`` を emit する。

silent failure 対策の test marker は他の kabu test (`test_engine_runner_live_kabu.py`)
と独立に書ける (different `_FakeNode` 流儀 — 本テストは ``kernel`` attr を持たない
特殊 FakeNode を渡して早期 abort 経路だけを観測する)。
"""
from __future__ import annotations

import queue as _stdlib_queue
import threading

from engine.nautilus.engine_runner import NautilusRunner


class _FakeKabuExecClient:
    """warm_up True / close 実装の最小 fake。"""

    instances: list["_FakeKabuExecClient"] = []

    def __init__(self, *_a, **_kw):
        self.warm_up_called = False
        self.close_called = False
        self.id = "fake-kabu-exec"
        type(self).instances.append(self)

    async def warm_up(self) -> bool:
        self.warm_up_called = True
        return True

    async def close(self) -> None:
        self.close_called = True


def _patch_kabu_deps_without_kernel(monkeypatch) -> None:
    """kabu_station 経路で ``node.kernel`` が None になる FakeNode をセット。"""
    monkeypatch.setattr(
        "engine.nautilus.engine_runner.is_market_open",
        lambda *_a, **_kw: True,
    )
    _FakeKabuExecClient.instances = []

    def _factory(*a, **kw):
        return _FakeKabuExecClient()

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

    class _FakeNodeNoKernel:
        """``kernel`` attr を持たない fake node。

        R6 silent-MEDIUM-1: R4 Group F の cleanup と整合させ、underscore prefix
        の ``_data_engine`` / ``_exec_engine`` 属性を意図的に **持たせない**。
        production が ``getattr(node, "kernel", None) is None`` で早期 abort
        するため、register_client 経路には到達しない。万一 production が
        underscore 属性に fallback したら ``AttributeError`` で即発覚する設計。
        """

        def __init__(self, *_a, **_kw):
            pass  # _data_engine / _exec_engine は意図的に未定義

        def add_data(self, *_a, **_kw):
            pass

        def add_strategies(self, *_a, **_kw):
            pass

        def build(self):
            raise AssertionError(
                "node.build() must NOT be called when kernel is unavailable "
                "(R3-SILENT-5: early abort path)"
            )

    monkeypatch.setattr("nautilus_trader.live.node.TradingNode", _FakeNodeNoKernel)
    monkeypatch.setattr(
        "engine.nautilus.instrument_factory.make_equity_instrument",
        lambda *_a, **_kw: object(),
    )


def _make_kabu_runner_args(on_event):
    return dict(
        instrument_id="9433.KabuStation Stock",
        strategy_file=None,
        strategy_init_kwargs=None,
        max_qty=100,
        max_notional_jpy=500_000,
        second_password="",
        session=object(),
        fd_queue=_stdlib_queue.Queue(maxsize=10),
        ec_queue=_stdlib_queue.Queue(maxsize=10),
        on_event=on_event,
        stop_event=threading.Event(),
        strategy_id="test-kabu-live",
        venue="kabu_station",
    )


class TestKernelUnavailableEmitsEngineError:
    """R3-SILENT-5: kabu_station 経路で ``node.kernel`` が None なら早期 abort。

    旧実装は ``_kernel is None`` のとき空 ``_kabu_parent_kwargs`` で client 構築を
    試み、必須引数不足で ``TypeError`` になる silent failure を抱えていた。
    本テストは「kernel 不在 → ``EngineError{kernel_unavailable}`` + ``EngineStopped``
    の 2 件 emit + ``node.build()`` 不到達」の不変条件を pin する。
    """

    def test_kernel_unavailable_emits_engine_error(self, monkeypatch) -> None:
        _patch_kabu_deps_without_kernel(monkeypatch)

        events: list[dict] = []
        runner = NautilusRunner()
        runner.start_live(**_make_kabu_runner_args(events.append))

        kernel_errors = [
            e for e in events
            if e.get("event") == "EngineError" and e.get("code") == "kernel_unavailable"
        ]
        assert len(kernel_errors) == 1, (
            f"expected exactly 1 EngineError(kernel_unavailable), got: {events!r}"
        )
        # silent failure 対策: EngineStopped を emit して Rust 側 state machine を unstuck
        stopped = [e for e in events if e.get("event") == "EngineStopped"]
        assert len(stopped) == 1, (
            f"expected EngineStopped to follow kernel_unavailable, got: {events!r}"
        )
        # 順序 pin: kernel_unavailable → EngineStopped
        ke_idx = events.index(kernel_errors[0])
        st_idx = events.index(stopped[0])
        assert ke_idx < st_idx
