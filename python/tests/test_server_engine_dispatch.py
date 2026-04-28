"""Server IPC dispatch tests for StartEngine / StopEngine / LoadReplayData (N1.4).

`DataEngineServer._dispatch` の新規分岐を検証する。サーバ全体は起動せず、
最小モックで `_handle_*` ハンドラを直接呼ぶ。
"""

from __future__ import annotations

from collections import deque
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


class _ListOutbox:
    """_Outbox と duck-type 互換のテスト用スタブ (H5)。

    asyncio.Event.set は呼ばず、単純にキューとして動作する。
    """

    def __init__(self) -> None:
        self._q: deque = deque()

    def append(self, item: object) -> None:
        self._q.append(item)

    def popleft(self) -> object:
        return self._q.popleft()

    def __len__(self) -> int:
        return len(self._q)

    def __bool__(self) -> bool:
        return bool(self._q)

    def __iter__(self):
        return iter(list(self._q))


def _make_server(mode: str = "replay"):
    """Create a minimal DataEngineServer with mocked dependencies."""
    from engine.server import DataEngineServer

    with patch.object(DataEngineServer, "__init__", lambda self, **_: None):
        server = DataEngineServer()

    server._outbox = _ListOutbox()  # H5: duck-type スタブに差し替え
    server._mode = mode
    server._workers = {"tachibana": MagicMock()}
    server._tachibana_session = None
    server._tachibana_p_no_counter = MagicMock()
    server._session_holder = MagicMock()
    server._engine_tasks = {}  # N1.4 で使う
    return server


class TestLoadReplayDataDispatch:
    """LoadReplayData IPC が ReplayDataLoaded を outbox に流すこと。"""

    @pytest.mark.asyncio
    async def test_load_replay_data_emits_loaded_event(self) -> None:
        """LoadReplayData → ReplayDataLoaded(outbox)、件数は fixtures と一致。"""
        server = _make_server(mode="replay")
        msg = {
            "op": "LoadReplayData",
            "request_id": "req-load-1",
            "instrument_id": "1301.TSE",
            "start_date": "2024-01-04",
            "end_date": "2024-01-05",
            "granularity": "Trade",
        }
        await server._handle_load_replay_data(msg, base_dir=FIXTURES)

        events = [e for e in server._outbox if e.get("event") == "ReplayDataLoaded"]
        assert len(events) == 1
        assert events[0]["trades_loaded"] == 4
        assert events[0]["bars_loaded"] == 0
        assert events[0]["strategy_id"] == ""  # M7: 単独 LoadReplayData では空

    @pytest.mark.asyncio
    async def test_load_replay_data_rejected_in_live_mode(self) -> None:
        """M3: mode='live' では LoadReplayData を Error{mode_mismatch} で拒否し
        J-Quants ファイルを開かない（D8 起動時固定 / spec §3.2）。"""
        server = _make_server(mode="live")
        msg = {
            "op": "LoadReplayData",
            "request_id": "req-load-live",
            "instrument_id": "1301.TSE",
            "start_date": "2024-01-04",
            "end_date": "2024-01-05",
            "granularity": "Trade",
        }
        await server._handle_load_replay_data(msg, base_dir=FIXTURES)

        # ReplayDataLoaded は流れない
        assert not any(
            e.get("event") == "ReplayDataLoaded" for e in server._outbox
        )
        # Error{request_id, code="mode_mismatch"} が記録される
        errors = [e for e in server._outbox if e.get("event") == "Error"]
        assert len(errors) == 1
        assert errors[0]["request_id"] == "req-load-live"
        assert errors[0]["code"] == "mode_mismatch"


class TestStartEngineDispatch:
    """StartEngine IPC が EngineStarted → EngineStopped を outbox に流すこと。"""

    @pytest.mark.asyncio
    async def test_start_engine_replay_emits_started_and_stopped(self) -> None:
        """mode='replay' で StartEngine{engine='Backtest'} は受理され、
        EngineStarted → ReplayDataLoaded → EngineStopped を順に outbox に積む。"""
        server = _make_server(mode="replay")
        msg = {
            "op": "StartEngine",
            "request_id": "req-start-1",
            "engine": "Backtest",
            "strategy_id": "buy-and-hold",
            "config": {
                "instrument_id": "1301.TSE",
                "start_date": "2024-01-04",
                "end_date": "2024-01-05",
                "initial_cash": "1000000",
                "granularity": "Trade",
            },
        }
        await server._handle_start_engine(msg, base_dir=FIXTURES)

        events = [
            e for e in server._outbox
            if e.get("event") in ("EngineStarted", "ReplayDataLoaded", "EngineStopped")
        ]
        kinds = [e["event"] for e in events]
        assert "EngineStarted" in kinds
        assert "EngineStopped" in kinds
        # 順序: started が stopped より先
        assert kinds.index("EngineStarted") < kinds.index("EngineStopped")

    @pytest.mark.asyncio
    async def test_start_engine_live_mode_rejects_backtest(self) -> None:
        """mode='live' で StartEngine{engine='Backtest'} は EngineError or Error を返す。"""
        server = _make_server(mode="live")
        msg = {
            "op": "StartEngine",
            "request_id": "req-start-2",
            "engine": "Backtest",
            "strategy_id": "buy-and-hold",
            "config": {
                "instrument_id": "1301.TSE",
                "start_date": "2024-01-04",
                "end_date": "2024-01-05",
                "initial_cash": "1000000",
                "granularity": "Trade",
            },
        }
        await server._handle_start_engine(msg, base_dir=FIXTURES)

        # H4: Error{request_id, code} を確認する
        errors = [e for e in server._outbox if e.get("event") == "Error"]
        assert len(errors) == 1
        assert errors[0]["request_id"] == "req-start-2"
        assert errors[0]["code"] == "mode_mismatch"
        # H3: EngineError は送出されない（バリデーション失敗は Error のみ）
        assert not any(e.get("event") == "EngineError" for e in server._outbox)
        # EngineStarted は発火しない
        assert not any(e.get("event") == "EngineStarted" for e in server._outbox)


class TestStopEngineDispatch:
    """StopEngine IPC ハンドラの最小確認（走行中 task が無くても安全）。"""

    @pytest.mark.asyncio
    async def test_stop_engine_no_running_task_is_safe(self) -> None:
        """走行中タスクが無い状態で StopEngine を受けても raise しない。"""
        server = _make_server(mode="replay")
        msg = {
            "op": "StopEngine",
            "request_id": "req-stop-1",
            "strategy_id": "buy-and-hold",
        }
        # 例外を出さずに完了すること
        await server._handle_stop_engine(msg)

    @pytest.mark.asyncio
    async def test_stop_engine_running_runner_is_no_op(self) -> None:
        """H2: BacktestEngine.run() 走行中の StopEngine は dispose() を呼ばずに log のみ。"""
        import unittest.mock

        from engine.nautilus.engine_runner import NautilusRunner

        server = _make_server(mode="replay")
        runner = NautilusRunner()
        # 走行中フラグを立てて _engine が dispose されないことを確認
        runner._running = True
        engine_mock = MagicMock()
        runner._engine = engine_mock
        # M6: stop() が呼ばれることを spy する
        runner.stop = unittest.mock.MagicMock()
        server._engine_tasks["buy-and-hold"] = runner

        await server._handle_stop_engine(
            {"op": "StopEngine", "strategy_id": "buy-and-hold"}
        )
        # running 中は dispose() を絶対に呼ばない
        engine_mock.dispose.assert_not_called()
        # M6: stop() は 1 回呼ばれる
        runner.stop.assert_called_once()


class TestStartEngineFailureRecovery:
    """H1: EngineStarted 後に run() が raise した場合 EngineStopped で補完されること。"""

    @pytest.mark.asyncio
    async def test_engine_started_then_failure_emits_stopped_and_error(self) -> None:
        """EngineStarted 送出後の例外で EngineStopped + EngineError の双方が outbox に積まれる。"""
        server = _make_server(mode="replay")
        msg = {
            "op": "StartEngine",
            "request_id": "req-fail-1",
            "engine": "Backtest",
            "strategy_id": "buy-and-hold",
            "config": {
                "instrument_id": "1301.TSE",
                "start_date": "2024-01-04",
                "end_date": "2024-01-05",
                "initial_cash": "1000000",
                "granularity": "Trade",
            },
        }

        # NautilusRunner.start_backtest_replay を mock して、EngineStarted を on_event 経由で
        # 送出した後に意図的に raise する
        def fake_start(*, on_event, strategy_id, **kw):
            on_event({
                "event": "EngineStarted",
                "strategy_id": strategy_id,
                "account_id": "TEST-ACCOUNT",
                "ts_event_ms": 1000,
            })
            raise RuntimeError("synthetic failure for test")

        with patch(
            "engine.nautilus.engine_runner.NautilusRunner.start_backtest_replay",
            side_effect=fake_start,
        ):
            await server._handle_start_engine(msg, base_dir=FIXTURES)

        kinds = [e.get("event") for e in server._outbox]
        assert "EngineStarted" in kinds
        assert "EngineStopped" in kinds
        assert "EngineError" in kinds
        # 順序: Started → Stopped → Error
        assert kinds.index("EngineStarted") < kinds.index("EngineStopped")
        assert kinds.index("EngineStopped") < kinds.index("EngineError")
        # final_equity は fallback の "0"
        stopped = next(e for e in server._outbox if e.get("event") == "EngineStopped")
        assert stopped["final_equity"] == "0"
        assert stopped["strategy_id"] == "buy-and-hold"
        # M5: EngineError に strategy_id が含まれる
        err = next(e for e in server._outbox if e.get("event") == "EngineError")
        assert err.get("strategy_id") == "buy-and-hold"
        # LOW-C: Error{request_id} イベントの確認
        err_events = [e for e in server._outbox if e.get("event") == "Error"]
        assert any(e.get("request_id") == "req-fail-1" for e in err_events)

    @pytest.mark.asyncio
    async def test_failure_before_engine_started_does_not_emit_stopped(self) -> None:
        """EngineStarted 未送出のうちに失敗した場合は EngineStopped を補完しない。"""
        server = _make_server(mode="replay")
        msg = {
            "op": "StartEngine",
            "request_id": "req-fail-2",
            "engine": "Backtest",
            "strategy_id": "buy-and-hold",
            "config": {
                "instrument_id": "1301.TSE",
                "start_date": "2024-01-04",
                "end_date": "2024-01-05",
                "initial_cash": "1000000",
                "granularity": "Trade",
            },
        }

        def fake_start(*, on_event, **kw):
            raise RuntimeError("failure before EngineStarted")

        with patch(
            "engine.nautilus.engine_runner.NautilusRunner.start_backtest_replay",
            side_effect=fake_start,
        ):
            await server._handle_start_engine(msg, base_dir=FIXTURES)

        kinds = [e.get("event") for e in server._outbox]
        assert "EngineStarted" not in kinds
        assert "EngineStopped" not in kinds  # 未 Start 状態では補完しない
        assert "EngineError" in kinds


class TestStartEngineTimeoutRecovery:
    """HIGH-1: TimeoutError パスで started_marker=False でも EngineStopped が積まれること。"""

    @pytest.mark.asyncio
    async def test_timeout_with_started_marker_false_emits_stopped(self) -> None:
        """HIGH-1: TimeoutError 時は started_marker に依存せず常に EngineStopped を送出する。"""
        import asyncio
        server = _make_server(mode="replay")
        msg = {
            "op": "StartEngine",
            "request_id": "req-timeout-1",
            "engine": "Backtest",
            "strategy_id": "timeout-strategy",
            "config": {
                "instrument_id": "1301.TSE",
                "start_date": "2024-01-04",
                "end_date": "2024-01-05",
                "initial_cash": "1000000",
                "granularity": "Trade",
            },
        }

        # started_marker["sent"] が False のまま TimeoutError を発生させる
        # (EngineStarted を送出しないまま timeout)
        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            await server._handle_start_engine(msg, base_dir=FIXTURES)

        kinds = [e.get("event") for e in server._outbox]
        # started_marker=False でも EngineStopped が積まれる (HIGH-1)
        assert "EngineStopped" in kinds
        stopped = next(e for e in server._outbox if e.get("event") == "EngineStopped")
        assert stopped["strategy_id"] == "timeout-strategy"
        assert stopped["final_equity"] == "0"

    @pytest.mark.asyncio
    async def test_timeout_message_is_not_empty(self) -> None:
        """MEDIUM-1: TimeoutError の message が空文字にならない。"""
        import asyncio
        server = _make_server(mode="replay")
        msg = {
            "op": "StartEngine",
            "request_id": "req-timeout-msg",
            "engine": "Backtest",
            "strategy_id": "timeout-strategy-msg",
            "config": {
                "instrument_id": "1301.TSE",
                "start_date": "2024-01-04",
                "end_date": "2024-01-05",
                "initial_cash": "1000000",
                "granularity": "Trade",
            },
        }

        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            await server._handle_start_engine(msg, base_dir=FIXTURES)

        # EngineError と Error の message が空文字でないことを確認
        all_events = list(server._outbox)
        engine_errors = [e for e in all_events if e.get("event") == "EngineError"]
        errors = [e for e in all_events if e.get("event") == "Error"]
        assert engine_errors, "EngineError should be emitted on timeout"
        assert errors, "Error should be emitted on timeout"
        for e in engine_errors:
            assert e.get("message"), f"EngineError.message must not be empty, got: {e!r}"
        for e in errors:
            if e.get("code") == "timeout":
                assert e.get("message"), f"Error.message must not be empty, got: {e!r}"


class TestStartEngineMissingRequestId:
    """MEDIUM-2: request_id=None ガード。"""

    @pytest.mark.asyncio
    async def test_missing_request_id_returns_early(self) -> None:
        """MEDIUM-2: StartEngine で request_id が None/空の場合は早期 return する。"""
        server = _make_server(mode="replay")
        msg = {
            "op": "StartEngine",
            # request_id を意図的に省略 (None)
            "engine": "Backtest",
            "strategy_id": "no-request-id",
            "config": {
                "instrument_id": "1301.TSE",
                "start_date": "2024-01-04",
                "end_date": "2024-01-05",
                "initial_cash": "1000000",
                "granularity": "Trade",
            },
        }

        await server._handle_start_engine(msg, base_dir=FIXTURES)

        # 早期 return するので outbox に何も積まれない
        assert len(server._outbox) == 0


class TestStartEngineLowATasksCleanup:
    """LOW-A: initial_cash バリデーション失敗時に _engine_tasks に残骸が残らない。"""

    @pytest.mark.asyncio
    async def test_invalid_initial_cash_does_not_leak_engine_tasks(self) -> None:
        """LOW-A: initial_cash が不正な場合、_engine_tasks に strategy_id が残らない。"""
        server = _make_server(mode="replay")
        msg = {
            "op": "StartEngine",
            "request_id": "req-bad-cash",
            "engine": "Backtest",
            "strategy_id": "bad-cash-strategy",
            "config": {
                "instrument_id": "1301.TSE",
                "start_date": "2024-01-04",
                "end_date": "2024-01-05",
                "initial_cash": "not-a-number",  # 不正値
                "granularity": "Trade",
            },
        }

        await server._handle_start_engine(msg, base_dir=FIXTURES)

        # Error が積まれている
        errors = [e for e in server._outbox if e.get("event") == "Error"]
        assert any(e.get("code") == "invalid_config" for e in errors)
        # _engine_tasks に残骸が残っていない (LOW-A)
        assert "bad-cash-strategy" not in server._engine_tasks
