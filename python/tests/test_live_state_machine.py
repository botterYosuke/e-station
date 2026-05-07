"""Phase 2 + Phase 7: LiveState 拡張 + server.py dispatch テスト。

TDD RED フェーズ: これらのテストは実装前に失敗することが期待される。
"""

from __future__ import annotations

import asyncio
import queue as _stdlib_queue
import threading
from collections import deque
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _ListOutbox:
    """_Outbox と duck-type 互換のテスト用スタブ。"""

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


def _make_server(mode: str = "live"):
    """Create a minimal DataEngineServer with mocked dependencies."""
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
    server._replay_state = ReplayState.IDLE
    server._live_state = LiveState.DISCONNECTED
    server._event_task = None
    server._replay_session_epoch = 0
    # Phase 2: queue フィールド
    server._live_fd_queue = _stdlib_queue.Queue(maxsize=10_000)
    server._live_ec_queue = _stdlib_queue.Queue(maxsize=1_000)
    return server


def _events_of(server, kind: str) -> list[dict]:
    return [e for e in server._outbox if e.get("event") == kind]


# ---------------------------------------------------------------------------
# Phase 7: nautilus_capabilities
# ---------------------------------------------------------------------------


class TestNautilusCapabilities:
    """Phase 7: mode.py の nautilus_capabilities が live=True を返す。"""

    def test_live_true_in_live_mode(self) -> None:
        from engine.mode import nautilus_capabilities
        caps = nautilus_capabilities("live")
        assert caps["live"] is True, f"expected live=True, got {caps}"

    def test_backtest_still_true(self) -> None:
        from engine.mode import nautilus_capabilities
        caps = nautilus_capabilities("replay")
        assert caps["backtest"] is True


# ---------------------------------------------------------------------------
# Phase 2: LiveState TRADING / STOPPING が存在する
# ---------------------------------------------------------------------------


class TestLiveStateEnum:
    """LiveState に TRADING / STOPPING が追加されていること。"""

    def test_trading_exists(self) -> None:
        from engine.server import LiveState
        assert hasattr(LiveState, "TRADING"), "LiveState.TRADING が存在しない"

    def test_stopping_exists(self) -> None:
        from engine.server import LiveState
        assert hasattr(LiveState, "STOPPING"), "LiveState.STOPPING が存在しない"

    def test_trading_name(self) -> None:
        from engine.server import LiveState
        assert LiveState.TRADING.name == "TRADING"

    def test_stopping_name(self) -> None:
        from engine.server import LiveState
        assert LiveState.STOPPING.name == "STOPPING"


# ---------------------------------------------------------------------------
# Phase 2: DISCONNECTED で live StartEngine → EngineBusy
# ---------------------------------------------------------------------------


class TestStartEngineLiveDisconnected:
    """DISCONNECTED 状態で live StartEngine を送ると EngineBusy が返る。"""

    @pytest.mark.asyncio
    async def test_disconnected_returns_engine_busy(self) -> None:
        from engine.server import LiveState

        server = _make_server(mode="live")
        assert server._live_state == LiveState.DISCONNECTED

        msg = {
            "op": "StartEngine",
            "request_id": "req-live-1",
            "engine": "Live",
            "strategy_id": "live-test",
            "config": {
                "instrument_id": "8306.T",
                "max_qty": 100,
                "max_notional_jpy": 500_000,
                "strategy_file": "dummy.py",
            },
        }
        await server._handle_start_engine(msg)

        busy = _events_of(server, "EngineBusy")
        assert len(busy) == 1, f"Expected 1 EngineBusy, got {busy}"
        assert busy[0]["attempted_command"] == "StartEngine"
        assert busy[0]["current_state"] == "DISCONNECTED"


# ---------------------------------------------------------------------------
# Phase 2: max_qty=None で live StartEngine → invalid_config
# ---------------------------------------------------------------------------


class TestStartEngineLiveMaxQtyNone:
    """max_qty が None で live StartEngine → Error{code='invalid_config'} が返る。"""

    @pytest.mark.asyncio
    async def test_max_qty_none_returns_invalid_config(self) -> None:
        from engine.server import LiveState

        server = _make_server(mode="live")
        server._live_state = LiveState.CONNECTED  # CONNECTED 状態にする

        msg = {
            "op": "StartEngine",
            "request_id": "req-live-2",
            "engine": "Live",
            "strategy_id": "live-test",
            "config": {
                "instrument_id": "8306.T",
                "max_qty": None,
                "max_notional_jpy": 500_000,
                "strategy_file": "dummy.py",
            },
        }
        await server._handle_start_engine(msg)

        errors = _events_of(server, "Error")
        assert any(e.get("code") == "invalid_config" for e in errors), (
            f"Expected Error{{code='invalid_config'}}, got {errors}"
        )


# ---------------------------------------------------------------------------
# Phase 2: initial_cash=None で live StartEngine → live 分岐到達（replay の int() パースが走らない）
# ---------------------------------------------------------------------------


class TestStartEngineLiveInitialCashNone:
    """live モードでは initial_cash=None でも replay の int() パースが走らずに
    live 分岐へ正常到達すること。
    SessionHolder.get_password() が None を返す場合は SecondPasswordRequired が出る。
    """

    @pytest.mark.asyncio
    async def test_initial_cash_none_no_type_error(self) -> None:
        from engine.server import LiveState

        server = _make_server(mode="live")
        server._live_state = LiveState.CONNECTED
        # get_password() が None を返す
        server._session_holder.get_password.return_value = None

        msg = {
            "op": "StartEngine",
            "request_id": "req-live-3",
            "engine": "Live",
            "strategy_id": "live-test",
            "config": {
                "instrument_id": "8306.T",
                "max_qty": 100,
                "max_notional_jpy": 500_000,
                "strategy_file": "dummy.py",
                # initial_cash は省略 (None)
            },
        }
        # TypeError (int(None)) が出ずに SecondPasswordRequired が返ればOK
        await server._handle_start_engine(msg)

        # TypeError やクラッシュがなければテスト通過
        # SecondPasswordRequired が出るはず
        events = list(server._outbox)
        event_names = [e.get("event") for e in events]
        # Error{code='invalid_config'} または SecondPasswordRequired のどちらかが来るはず
        # invalid_config ではなく SecondPasswordRequired が来ることを確認
        assert "SecondPasswordRequired" in event_names, (
            f"Expected SecondPasswordRequired (not TypeError), got {event_names}"
        )


# ---------------------------------------------------------------------------
# Phase 2: SessionHolder.get_password() が None → SecondPasswordRequired emit
# ---------------------------------------------------------------------------


class TestSecondPasswordRequired:
    """SessionHolder.get_password() が None を返す → SecondPasswordRequired が emit される。"""

    @pytest.mark.asyncio
    async def test_second_password_none_emits_required(self) -> None:
        from engine.server import LiveState

        server = _make_server(mode="live")
        server._live_state = LiveState.CONNECTED
        server._session_holder.get_password.return_value = None

        msg = {
            "op": "StartEngine",
            "request_id": "req-live-4",
            "engine": "Live",
            "strategy_id": "live-test",
            "config": {
                "instrument_id": "8306.T",
                "max_qty": 100,
                "max_notional_jpy": 500_000,
                "strategy_file": "dummy.py",
            },
        }
        await server._handle_start_engine(msg)

        spr = _events_of(server, "SecondPasswordRequired")
        assert len(spr) == 1, f"Expected 1 SecondPasswordRequired, got {list(server._outbox)}"
        assert spr[0]["request_id"] == "req-live-4"


# ---------------------------------------------------------------------------
# Phase 2: CONNECTED → TRADING → CONNECTED の正常遷移
# ---------------------------------------------------------------------------


class TestLiveStateTradingTransition:
    """CONNECTED → TRADING → finally で CONNECTED に戻ることを確認する。"""

    @pytest.mark.asyncio
    async def test_connected_to_trading_to_connected(self) -> None:
        from engine.server import LiveState

        server = _make_server(mode="live")
        server._live_state = LiveState.CONNECTED
        # get_password() が有効な値を返す
        server._session_holder.get_password.return_value = "test-password"

        # start_live をすぐ返る stub に差し替える
        states_seen: list[str] = []

        def fake_start_live(self_runner, **kwargs):
            states_seen.append(server._live_state.name)
            # _on_event_tracked を使って EngineStarted / EngineStopped を emit
            on_event = kwargs.get("on_event")
            if on_event:
                on_event({"event": "EngineStarted", "strategy_id": kwargs.get("strategy_id", ""), "account_id": "tachibana", "ts_event_ms": 0})
                on_event({"event": "EngineStopped", "strategy_id": kwargs.get("strategy_id", ""), "final_equity": "1000000", "ts_event_ms": 0})

        with patch("engine.nautilus.engine_runner.NautilusRunner.start_live", fake_start_live):
            msg = {
                "op": "StartEngine",
                "request_id": "req-live-5",
                "engine": "Live",
                "strategy_id": "live-test",
                "config": {
                    "instrument_id": "8306.T",
                    "max_qty": 100,
                    "max_notional_jpy": 500_000,
                    "strategy_file": "dummy.py",
                },
            }
            await server._handle_start_engine(msg)

        # start_live が呼ばれたとき TRADING 状態だった
        assert "TRADING" in states_seen, f"TRADING state not seen during start_live, got {states_seen}"
        # finally で CONNECTED に戻った
        assert server._live_state == LiveState.CONNECTED, (
            f"Expected CONNECTED after run, got {server._live_state}"
        )


# ---------------------------------------------------------------------------
# Phase 2: TRADING 中の StopEngine → STOPPING 遷移 + stop_event.set() 呼び出し
# ---------------------------------------------------------------------------


class TestStopEngineLive:
    """TRADING 中の StopEngine → STOPPING 遷移 + stop_event.set() が呼ばれる。"""

    @pytest.mark.asyncio
    async def test_stop_engine_from_trading(self) -> None:
        from engine.server import LiveState

        server = _make_server(mode="live")
        server._live_state = LiveState.TRADING

        # stop_event を事前に登録
        stop_event = threading.Event()
        runner_mock = MagicMock()
        strategy_id = "live-strategy-stop"
        server._engine_tasks[strategy_id] = runner_mock
        server._engine_stop_events[strategy_id] = stop_event

        msg = {
            "op": "StopEngine",
            "request_id": "req-stop-live-1",
            "strategy_id": strategy_id,
        }
        await server._handle_stop_engine(msg)

        # STOPPING 状態に遷移した
        assert server._live_state == LiveState.STOPPING, (
            f"Expected STOPPING, got {server._live_state}"
        )
        # stop_event.set() が呼ばれた
        assert stop_event.is_set(), "stop_event.set() が呼ばれなかった"

    @pytest.mark.asyncio
    async def test_stop_engine_from_connected_returns_busy(self) -> None:
        """CONNECTED 状態（TRADING でない）で StopEngine → EngineBusy。"""
        from engine.server import LiveState

        server = _make_server(mode="live")
        server._live_state = LiveState.CONNECTED

        msg = {
            "op": "StopEngine",
            "request_id": "req-stop-live-2",
            "strategy_id": "live-test",
        }
        await server._handle_stop_engine(msg)

        busy = _events_of(server, "EngineBusy")
        assert len(busy) == 1, f"Expected 1 EngineBusy, got {busy}"
        assert busy[0]["current_state"] == "CONNECTED"
