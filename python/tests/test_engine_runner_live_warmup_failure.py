"""issue #42 Phase 1: NautilusRunner.start_live() の warm_up 失敗判定 + cleanup。

統一決定 #16 (R3-CM1 / R5-HIGH-2):

1. warm_up 失敗判定は **「例外」または「``False`` 戻り値」の OR**。
   - 旧実装は例外しか catch しておらず、`False` 戻り値を無視していた。
2. どちらの場合も:
   - ``EngineError{code:"warm_up_failed"}`` を emit
   - ``await exec_client.close()`` を必ず呼ぶ（HTTP session / WS subscription リーク防止）
   - return（``EngineStarted`` / ``LiveStrategyReady`` を emit しない）

これらのテストは `TachibanaLiveExecutionClient` を fake に差し替えて
warm_up の挙動だけ制御する。``node.build()`` には到達しないので
TradingNode の依存はモック不要。
"""

from __future__ import annotations

import asyncio
import queue as _stdlib_queue
import threading

import pytest

from engine.nautilus.engine_runner import NautilusRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_runner_args(on_event):
    return dict(
        instrument_id="8306.T",
        strategy_file=None,
        strategy_init_kwargs=None,
        max_qty=100,
        max_notional_jpy=500_000,
        second_password="test-second-pw",
        session=object(),
        fd_queue=_stdlib_queue.Queue(maxsize=10),
        ec_queue=_stdlib_queue.Queue(maxsize=10),
        on_event=on_event,
        stop_event=threading.Event(),
        strategy_id="test-live-strategy",
    )


class _FakeExecClient:
    """warm_up の挙動を制御できる fake `TachibanaLiveExecutionClient`."""

    instances: list["_FakeExecClient"] = []

    def __init__(
        self,
        *,
        warm_up_mode: str,  # "raise" | "false" | "true"
        **_kwargs,
    ) -> None:
        self.warm_up_mode = warm_up_mode
        self.close_called = False
        self.warm_up_called = False
        # Nautilus Trader の LiveExecutionClient 互換 attr の最低限ダミー化。
        # node._exec_engine.register_client が触る属性に備える。
        self.id = "fake-exec-client"
        type(self).instances.append(self)

    async def warm_up(self) -> bool:
        self.warm_up_called = True
        if self.warm_up_mode == "raise":
            raise RuntimeError("simulated warm_up failure")
        if self.warm_up_mode == "false":
            return False
        if self.warm_up_mode == "true":
            return True
        raise AssertionError(f"unknown warm_up_mode: {self.warm_up_mode!r}")

    async def close(self) -> None:
        self.close_called = True


def _patch_min_dependencies(monkeypatch, *, warm_up_mode: str) -> type[_FakeExecClient]:
    """warm_up 失敗テストに必要な最小限の依存を fake に差し替える。

    market_closed ガードは fail させない（=is_market_open=True を返す）。
    exec_client / data_client / event_bridge / TradingNode は fake で置換し、
    warm_up failure 経路で **node.build に到達しない**ことを検証する。
    """
    monkeypatch.setattr(
        "engine.nautilus.engine_runner.is_market_open",
        lambda *_args, **_kwargs: True,
    )

    # _FakeExecClient のインスタンス記録をクリア
    _FakeExecClient.instances = []

    def _factory(*args, **kwargs):
        return _FakeExecClient(warm_up_mode=warm_up_mode, **kwargs)

    monkeypatch.setattr(
        "engine.nautilus.clients.tachibana.TachibanaLiveExecutionClient",
        _factory,
    )

    # 他の依存もスタブ化する（warm_up failure 経路では到達しないが、
    # コンストラクタ呼び出しは exec_client より前に走る可能性があるため備え）。
    class _FakeDataClient:
        def __init__(self, *_a, **_kw) -> None:
            self.id = "fake-data-client"

    class _FakeOrderIdMap:
        def __init__(self, *_a, **_kw) -> None:
            pass

    class _FakeEventBridge:
        def __init__(self, *_a, **_kw) -> None:
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

    # TradingNode は build() に到達しないので最小スタブで十分。
    class _FakeNode:
        def __init__(self, *_a, **_kw) -> None:
            self._data_engine = type("DE", (), {"register_client": lambda *a, **k: None})()
            self._exec_engine = type("EE", (), {"register_client": lambda *a, **k: None})()

        def add_data(self, *_a, **_kw) -> None:
            pass

        def add_strategies(self, *_a, **_kw) -> None:
            pass

        def build(self) -> None:
            raise AssertionError(
                "node.build() must NOT be called when warm_up fails"
            )

    monkeypatch.setattr("nautilus_trader.live.node.TradingNode", _FakeNode)

    # instrument factory は無害なダミーを返す。
    monkeypatch.setattr(
        "engine.nautilus.instrument_factory.make_equity_instrument",
        lambda *_a, **_kw: object(),
    )

    return _FakeExecClient


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWarmUpFailureExceptionPath:
    """warm_up が **例外を raise** した場合。"""

    def test_warm_up_exception_emits_error_not_ready(self, monkeypatch) -> None:
        """例外時: EngineError{code:"warm_up_failed"} emit / LiveStrategyReady は出ない。"""
        _patch_min_dependencies(monkeypatch, warm_up_mode="raise")

        events: list[dict] = []
        runner = NautilusRunner()
        runner.start_live(**_make_runner_args(events.append))

        # EngineError{code:"warm_up_failed"} が **1 件** emit される
        warm_up_failed = [
            e for e in events
            if e.get("event") == "EngineError" and e.get("code") == "warm_up_failed"
        ]
        assert len(warm_up_failed) == 1, (
            f"expected 1 EngineError(code='warm_up_failed'), got events={events!r}"
        )

        # LiveStrategyReady は emit されない（warm_up と pair）
        ready = [e for e in events if e.get("event") == "LiveStrategyReady"]
        assert ready == [], (
            f"LiveStrategyReady must NOT be emitted when warm_up raises, got {ready!r}"
        )

        # EngineStarted も emit されない
        started = [e for e in events if e.get("event") == "EngineStarted"]
        assert started == [], (
            f"EngineStarted must NOT be emitted when warm_up fails, got {started!r}"
        )

        # silent failure 対策: EngineStopped は emit される（Rust 側 state machine を
        # unstuck するため。Rust は EngineStarted 無しの EngineStopped を no-op として扱う）。
        stopped = [e for e in events if e.get("event") == "EngineStopped"]
        assert len(stopped) == 1, (
            f"EngineStopped must follow EngineError(warm_up_failed) to unstuck Rust, "
            f"got events={events!r}"
        )


class TestWarmUpFailureFalseReturnPath:
    """warm_up が **`False` を返す** 場合（旧実装で見逃されていた経路）。"""

    def test_warm_up_returns_false_emits_error_not_ready(self, monkeypatch) -> None:
        """False 戻り値: EngineError{code:"warm_up_failed"} emit / Ready 不発。"""
        _patch_min_dependencies(monkeypatch, warm_up_mode="false")

        events: list[dict] = []
        runner = NautilusRunner()
        runner.start_live(**_make_runner_args(events.append))

        warm_up_failed = [
            e for e in events
            if e.get("event") == "EngineError" and e.get("code") == "warm_up_failed"
        ]
        assert len(warm_up_failed) == 1, (
            f"expected 1 EngineError(code='warm_up_failed') for False return, got events={events!r}"
        )

        ready = [e for e in events if e.get("event") == "LiveStrategyReady"]
        assert ready == [], (
            f"LiveStrategyReady must NOT be emitted when warm_up returns False, got {ready!r}"
        )

        # silent failure 対策: EngineStopped emit
        stopped = [e for e in events if e.get("event") == "EngineStopped"]
        assert len(stopped) == 1, (
            f"EngineStopped must follow EngineError(warm_up_failed) for False return, "
            f"got events={events!r}"
        )


class TestWarmUpFailureClosesExecClient:
    """warm_up 失敗時の cleanup: ``await exec_client.close()`` が呼ばれる。"""

    def test_warm_up_exception_closes_exec_client(self, monkeypatch) -> None:
        """例外経路でも close() が呼ばれる（HTTP session / WS リーク防止）。"""
        _patch_min_dependencies(monkeypatch, warm_up_mode="raise")

        events: list[dict] = []
        runner = NautilusRunner()
        runner.start_live(**_make_runner_args(events.append))

        # _FakeExecClient.instances にコンストラクトされたインスタンスが登録される
        assert len(_FakeExecClient.instances) == 1, (
            f"expected exactly 1 exec_client instance, got {_FakeExecClient.instances!r}"
        )
        exec_client = _FakeExecClient.instances[0]
        assert exec_client.warm_up_called, "warm_up should have been called"
        assert exec_client.close_called, (
            "close() must be called after warm_up exception (resource leak guard)"
        )

    def test_warm_up_returns_false_closes_exec_client(self, monkeypatch) -> None:
        """False 戻り値経路でも close() が呼ばれる。"""
        _patch_min_dependencies(monkeypatch, warm_up_mode="false")

        events: list[dict] = []
        runner = NautilusRunner()
        runner.start_live(**_make_runner_args(events.append))

        assert len(_FakeExecClient.instances) == 1
        exec_client = _FakeExecClient.instances[0]
        assert exec_client.close_called, (
            "close() must be called after warm_up returned False (resource leak guard)"
        )


# ---------------------------------------------------------------------------
# R2-A M10: warm_up が無いクライアントは silent skip しない（log.warning を残す）
# ---------------------------------------------------------------------------


class _FakeExecClientNoWarmUp:
    """``warm_up`` メソッドを持たない fake exec client。"""

    instances: list["_FakeExecClientNoWarmUp"] = []

    def __init__(self, **_kwargs) -> None:
        self.close_called = False
        self.id = "fake-exec-client-no-warmup"
        type(self).instances.append(self)

    async def close(self) -> None:
        self.close_called = True


def _patch_no_warmup_dependencies(monkeypatch) -> None:
    """warm_up を持たない client を返す patch。それ以降の経路は早めに失敗させたい
    ので make_equity_instrument を例外にし、ログ記録を観測する箇所までで止める。
    """
    monkeypatch.setattr(
        "engine.nautilus.engine_runner.is_market_open",
        lambda *_a, **_kw: True,
    )
    _FakeExecClientNoWarmUp.instances = []
    monkeypatch.setattr(
        "engine.nautilus.clients.tachibana.TachibanaLiveExecutionClient",
        lambda *a, **kw: _FakeExecClientNoWarmUp(**kw),
    )

    class _FakeDataClient:
        def __init__(self, *_a, **_kw) -> None:
            self.id = "fake-data-client"

    class _FakeOrderIdMap:
        def __init__(self, *_a, **_kw) -> None:
            pass

    class _FakeEventBridge:
        def __init__(self, *_a, **_kw) -> None:
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
        def __init__(self, *_a, **_kw) -> None:
            self._data_engine = type("DE", (), {"register_client": lambda *a, **k: None})()
            self._exec_engine = type("EE", (), {"register_client": lambda *a, **k: None})()

        def add_data(self, *_a, **_kw) -> None:
            pass

        def add_strategies(self, *_a, **_kw) -> None:
            pass

        def build(self) -> None:
            # warm_up skip 後 build は呼ばれる想定だが、本テストは log 観測が目的なので
            # ここで raise して以降の処理は cut-off する。
            raise RuntimeError("test cut-off after warm_up skip log observation")

    monkeypatch.setattr("nautilus_trader.live.node.TradingNode", _FakeNode)
    monkeypatch.setattr(
        "engine.nautilus.instrument_factory.make_equity_instrument",
        lambda *_a, **_kw: object(),
    )


class TestStartLiveLogsWarningWhenWarmUpMissing:
    """M10: warm_up メソッドが無い client → ``log.warning`` を残し、silent skip しない。"""

    def test_start_live_logs_warning_when_warm_up_missing(
        self, monkeypatch, caplog
    ) -> None:
        import logging
        _patch_no_warmup_dependencies(monkeypatch)

        events: list[dict] = []
        runner = NautilusRunner()
        with caplog.at_level(logging.WARNING, logger="engine.nautilus.engine_runner"):
            runner.start_live(**_make_runner_args(events.append))

        # warm_up が無い client であっても警告が出ている（silent skip しない）
        warmup_warnings = [
            r for r in caplog.records
            if r.levelname == "WARNING" and "no warm_up method" in r.getMessage()
        ]
        assert len(warmup_warnings) >= 1, (
            f"expected log.warning when warm_up missing, got records: "
            f"{[r.getMessage() for r in caplog.records]!r}"
        )
