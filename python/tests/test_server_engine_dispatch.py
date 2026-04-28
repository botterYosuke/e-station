"""Server IPC dispatch tests for StartEngine / StopEngine / LoadReplayData (N1.4).

`DataEngineServer._dispatch` の新規分岐を検証する。サーバ全体は起動せず、
最小モックで `_handle_*` ハンドラを直接呼ぶ。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def _make_server(mode: str = "replay"):
    """Create a minimal DataEngineServer with mocked dependencies."""
    from engine.server import DataEngineServer

    with patch.object(DataEngineServer, "__init__", lambda self, **_: None):
        server = DataEngineServer()

    server._outbox = []
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

        # 期待: 拒否されたことが outbox に Error/EngineError として記録される
        events = [e for e in server._outbox if e.get("event") in ("Error", "EngineError")]
        assert len(events) >= 1
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
        from engine.nautilus.engine_runner import NautilusRunner

        server = _make_server(mode="replay")
        runner = NautilusRunner()
        # 走行中フラグを立てて _engine が dispose されないことを確認
        runner._running = True
        engine_mock = MagicMock()
        runner._engine = engine_mock
        server._engine_tasks["buy-and-hold"] = runner

        await server._handle_stop_engine(
            {"op": "StopEngine", "strategy_id": "buy-and-hold"}
        )
        # running 中は dispose() を絶対に呼ばない
        engine_mock.dispose.assert_not_called()


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
