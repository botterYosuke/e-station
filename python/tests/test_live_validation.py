"""Phase 2 + Phase 8: D11 live validation テスト。

D11: 不正 initial_cash 文字列で replay StartEngine → invalid_config テスト。
"""

from __future__ import annotations

import queue as _stdlib_queue
from collections import deque
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class _ListOutbox:
    def __init__(self) -> None:
        self._q: deque = deque()

    def append(self, item: object) -> None:
        self._q.append(item)

    def send_to(self, ws: object, item: object) -> None:
        self._q.append(item)

    def popleft(self) -> object:
        return self._q.popleft()

    def count(self) -> int:
        return 0

    def __len__(self) -> int:
        return len(self._q)

    def __bool__(self) -> bool:
        return bool(self._q)

    def __iter__(self):
        return iter(list(self._q))


def _make_server(mode: str = "replay"):
    from engine.server import DataEngineServer, ReplayState, LiveState
    from decimal import Decimal
    from engine.nautilus.portfolio_view import PortfolioView

    with patch.object(DataEngineServer, "__init__", lambda self, **_: None):
        server = DataEngineServer()

    server._outbox = _ListOutbox()
    server._mode = mode
    server._workers = {"tachibana": MagicMock()}
    server._tachibana_session = None
    server._tachibana_p_no_counter = MagicMock()
    server._session_holder = MagicMock()
    server._engine_tasks = {}
    server._engine_stop_events = {}
    server._replay_speed_multiplier = 1
    server._replay_portfolio = PortfolioView(Decimal("1000000"))
    server._replay_strategy_id = ""
    server._cache_dir = Path("/tmp/test-engine-cache")
    server._replay_streaming_fills = []
    server._replay_state = ReplayState.LOADED  # replay の場合 LOADED が必要
    server._live_state = LiveState.DISCONNECTED
    server._event_task = None
    server._replay_session_epoch = 1  # LOADED なので epoch は 1
    server._live_fd_queue = _stdlib_queue.Queue(maxsize=10_000)
    server._live_ec_queue = _stdlib_queue.Queue(maxsize=1_000)
    return server


class TestLiveValidationD11:
    """D11: 不正な initial_cash 文字列で replay StartEngine → invalid_config。"""

    @pytest.mark.asyncio
    async def test_invalid_initial_cash_returns_invalid_config(self) -> None:
        """initial_cash に 'not-a-number' を渡すと Error{code='invalid_config'} が返る。

        かつ evt['message'] に 'initial_cash' が含まれること。
        """
        server = _make_server(mode="replay")

        msg = {
            "op": "StartEngine",
            "request_id": "req-d11-1",
            "engine": "Backtest",
            "strategy_id": "test-d11",
            "config": {
                "instrument_id": "1301.TSE",
                "start_date": "2024-01-04",
                "end_date": "2024-01-05",
                "initial_cash": "not-a-number",
                "granularity": "Daily",
                "strategy_file": "dummy.py",
            },
        }
        await server._handle_start_engine(msg)

        errors = [e for e in server._outbox if e.get("event") == "Error"]
        invalid_config_errors = [e for e in errors if e.get("code") == "invalid_config"]
        assert len(invalid_config_errors) >= 1, (
            f"Expected at least 1 Error{{code='invalid_config'}}, got {list(server._outbox)}"
        )
        evt = invalid_config_errors[0]
        assert evt["event"] == "Error"
        assert evt["code"] == "invalid_config"
        assert "initial_cash" in evt["message"], (
            f"Expected 'initial_cash' in message, got: {evt['message']!r}"
        )

    @pytest.mark.asyncio
    async def test_valid_initial_cash_no_error(self) -> None:
        """valid な initial_cash は invalid_config を返さない（エンジン実行が始まる）。

        NautilusRunner のモックを使って実際のエンジン起動をスキップする。
        """
        from engine.server import ReplayState

        server = _make_server(mode="replay")

        def fake_start_backtest_replay_streaming(**kwargs):
            on_event = kwargs.get("on_event")
            if on_event:
                on_event({"event": "EngineStarted", "strategy_id": kwargs.get("strategy_id", ""), "account_id": "replay", "ts_event_ms": 0})
                on_event({"event": "EngineStopped", "strategy_id": kwargs.get("strategy_id", ""), "final_equity": "1000000", "ts_event_ms": 0})
            return MagicMock(strategy_id="test-d11-valid", portfolio_fills=[])

        with patch("engine.nautilus.engine_runner.NautilusRunner.start_backtest_replay_streaming", fake_start_backtest_replay_streaming):
            msg = {
                "op": "StartEngine",
                "request_id": "req-d11-valid",
                "engine": "Backtest",
                "strategy_id": "test-d11-valid",
                "config": {
                    "instrument_id": "1301.TSE",
                    "start_date": "2024-01-04",
                    "end_date": "2024-01-05",
                    "initial_cash": "1000000",
                    "granularity": "Daily",
                    "strategy_file": "dummy.py",
                },
            }
            await server._handle_start_engine(msg)

        invalid_config_errors = [
            e for e in server._outbox
            if e.get("event") == "Error" and e.get("code") == "invalid_config"
        ]
        assert len(invalid_config_errors) == 0, (
            f"Expected no invalid_config errors, got {invalid_config_errors}"
        )
