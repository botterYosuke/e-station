"""B3: EngineBusy state machine reject tests.

Replay state: Idle | Loaded | Running | Stopping
Live state:   Disconnected | Connecting | Connected

各 state guard が不適切な遷移時に EngineBusy event を broadcast することを検証する。
"""

from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


class _ListOutbox:
    """_Outbox と duck-type 互換のテスト用スタブ。"""

    def __init__(self) -> None:
        self._q: deque = deque()

    def append(self, item: object) -> None:
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
    """Create a minimal DataEngineServer with mocked dependencies."""
    from engine.server import DataEngineServer, ReplayState, LiveState

    with patch.object(DataEngineServer, "__init__", lambda self, **_: None):
        server = DataEngineServer()

    from decimal import Decimal
    from engine.nautilus.portfolio_view import PortfolioView

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
    # B3: state machine variables
    server._replay_state = ReplayState.IDLE
    server._live_state = LiveState.DISCONNECTED
    return server


def _busy_events(server) -> list[dict]:
    return [e for e in server._outbox if e.get("event") == "EngineBusy"]


# ---------------------------------------------------------------------------
# Replay state tests
# ---------------------------------------------------------------------------


class TestReplayStateBusy:
    """Replay state machine: inappropriate commands return EngineBusy."""

    @pytest.mark.asyncio
    async def test_load_busy_when_already_loaded(self) -> None:
        """Loaded 中の二度目 LoadReplayData が EngineBusy を返す。"""
        from engine.server import ReplayState

        server = _make_server(mode="replay")
        # 一度目は IDLE → LOADED に正常遷移させる
        server._replay_state = ReplayState.LOADED

        msg = {
            "op": "LoadReplayData",
            "request_id": "req-load-1",
            "instrument_id": "1301.TSE",
            "start_date": "2024-01-04",
            "end_date": "2024-01-05",
            "granularity": "Trade",
        }
        await server._handle_load_replay_data(msg, base_dir=FIXTURES)

        busy = _busy_events(server)
        assert len(busy) == 1, f"Expected 1 EngineBusy, got {busy}"
        assert busy[0]["attempted_command"] == "LoadReplayData"
        assert busy[0]["current_state"] == "LOADED"
        # state は変わっていないはず
        assert server._replay_state == ReplayState.LOADED

    @pytest.mark.asyncio
    async def test_start_engine_busy_when_idle(self) -> None:
        """Idle 中の StartEngine が EngineBusy を返す。"""
        from engine.server import ReplayState

        server = _make_server(mode="replay")
        # _replay_state は IDLE（デフォルト）

        msg = {
            "op": "StartEngine",
            "request_id": "req-start-1",
            "engine": "Backtest",
            "strategy_id": "test-strategy",
            "config": {
                "instrument_id": "1301.TSE",
                "start_date": "2024-01-04",
                "end_date": "2024-01-05",
                "initial_cash": "1000000",
                "granularity": "Daily",
                "strategy_file": "dummy.py",
            },
        }
        await server._handle_start_engine(msg, base_dir=FIXTURES)

        busy = _busy_events(server)
        assert len(busy) == 1, f"Expected 1 EngineBusy, got {busy}"
        assert busy[0]["attempted_command"] == "StartEngine"
        assert busy[0]["current_state"] == "IDLE"

    @pytest.mark.asyncio
    async def test_submit_order_busy_replay_idle(self) -> None:
        """Idle で SubmitOrder{venue='replay'} が EngineBusy を返す。"""
        from engine.server import ReplayState

        server = _make_server(mode="replay")
        # _replay_state は IDLE（デフォルト）

        msg = {
            "op": "SubmitOrder",
            "request_id": "req-submit-1",
            "venue": "replay",
            "order": {
                "client_order_id": "cid-001",
                "instrument_id": "1301.TSE",
                "order_side": "BUY",
                "order_type": "MARKET",
                "quantity": "100",
                "time_in_force": "DAY",
                "post_only": False,
                "reduce_only": False,
            },
        }
        await server._do_submit_order_inner(msg)

        busy = _busy_events(server)
        assert len(busy) == 1, f"Expected 1 EngineBusy, got {busy}"
        assert busy[0]["attempted_command"] == "SubmitOrder"
        assert busy[0]["current_state"] == "IDLE"

    @pytest.mark.asyncio
    async def test_set_replay_speed_busy_when_idle(self) -> None:
        """Idle で SetReplaySpeed が EngineBusy を返す。"""
        from engine.server import ReplayState

        server = _make_server(mode="replay")
        # _replay_state は IDLE（デフォルト）

        msg = {
            "op": "SetReplaySpeed",
            "request_id": "req-speed-1",
            "multiplier": 2,
        }
        ws_mock = MagicMock()
        await server._dispatch("SetReplaySpeed", msg, ws_mock)

        busy = _busy_events(server)
        assert len(busy) == 1, f"Expected 1 EngineBusy, got {busy}"
        assert busy[0]["attempted_command"] == "SetReplaySpeed"
        assert busy[0]["current_state"] == "IDLE"

    @pytest.mark.asyncio
    async def test_stop_engine_busy_when_idle(self) -> None:
        """Idle で StopEngine が EngineBusy を返す。"""
        from engine.server import ReplayState

        server = _make_server(mode="replay")
        # _replay_state は IDLE（デフォルト）

        msg = {
            "op": "StopEngine",
            "request_id": "req-stop-1",
            "strategy_id": "test-strategy",
        }
        await server._handle_stop_engine(msg)

        busy = _busy_events(server)
        assert len(busy) == 1, f"Expected 1 EngineBusy, got {busy}"
        assert busy[0]["attempted_command"] == "StopEngine"
        assert busy[0]["current_state"] == "IDLE"


# ---------------------------------------------------------------------------
# Live state tests
# ---------------------------------------------------------------------------


class TestLiveStateBusy:
    """Live state machine: inappropriate commands return EngineBusy."""

    @pytest.mark.asyncio
    async def test_submit_order_busy_live_disconnected(self) -> None:
        """Disconnected で live SubmitOrder が EngineBusy を返す。"""
        from engine.server import LiveState

        server = _make_server(mode="live")
        # _live_state は DISCONNECTED（デフォルト）

        msg = {
            "op": "SubmitOrder",
            "request_id": "req-submit-live-1",
            "venue": "tachibana",
            "order": {
                "client_order_id": "cid-live-001",
                "instrument_id": "6758.TSE",
                "order_side": "BUY",
                "order_type": "MARKET",
                "quantity": "10",
                "time_in_force": "DAY",
                "post_only": False,
                "reduce_only": False,
            },
        }
        await server._do_submit_order_inner(msg)

        busy = _busy_events(server)
        assert len(busy) == 1, f"Expected 1 EngineBusy, got {busy}"
        assert busy[0]["attempted_command"] == "SubmitOrder"
        assert busy[0]["current_state"] == "DISCONNECTED"

    @pytest.mark.asyncio
    async def test_login_busy_when_connected(self) -> None:
        """Connected 中の RequestVenueLogin が EngineBusy を返す。"""
        from engine.server import LiveState

        server = _make_server(mode="live")
        server._live_state = LiveState.CONNECTED
        server._tachibana_login_inflight = asyncio.Lock()
        server._config_dir = Path("/tmp/test-config")

        msg = {
            "op": "RequestVenueLogin",
            "request_id": "req-login-1",
            "venue": "tachibana",
        }
        await server._do_request_venue_login(msg)

        busy = _busy_events(server)
        assert len(busy) == 1, f"Expected 1 EngineBusy, got {busy}"
        assert busy[0]["attempted_command"] == "RequestVenueLogin"
        assert busy[0]["current_state"] == "CONNECTED"

    @pytest.mark.asyncio
    async def test_login_connecting_returns_venue_login_started(self) -> None:
        """H-TA1: CONNECTING 中の RequestVenueLogin は EngineBusy でなく
        VenueLoginStarted を返すこと（ログイン中を通知する仕様）。"""
        from engine.server import LiveState

        server = _make_server(mode="live")
        server._live_state = LiveState.CONNECTING
        server._tachibana_login_inflight = asyncio.Lock()
        server._config_dir = Path("/tmp/test-config")

        msg = {
            "op": "RequestVenueLogin",
            "request_id": "req-login-2",
            "venue": "tachibana",
        }
        await server._do_request_venue_login(msg)

        # EngineBusy でなく VenueLoginStarted が返ること
        all_events = list(server._outbox)
        busy = [e for e in all_events if e.get("event") == "EngineBusy"]
        started = [e for e in all_events if e.get("event") == "VenueLoginStarted"]
        assert len(busy) == 0, f"EngineBusy should not be emitted for CONNECTING; got {busy}"
        assert len(started) == 1, f"Expected 1 VenueLoginStarted, got {started}"
        assert started[0]["request_id"] == "req-login-2"


# ---------------------------------------------------------------------------
# Happy-path sanity: LoadReplayData from IDLE does NOT emit EngineBusy
# ---------------------------------------------------------------------------


class TestReplayStateHappyPath:
    """State machine happy path: correct state allows commands through."""

    @pytest.mark.asyncio
    async def test_load_from_idle_no_busy(self) -> None:
        """IDLE からの LoadReplayData は EngineBusy を返さない（LOADED へ遷移）。"""
        from engine.server import ReplayState

        server = _make_server(mode="replay")
        assert server._replay_state == ReplayState.IDLE

        msg = {
            "op": "LoadReplayData",
            "request_id": "req-load-happy",
            "instrument_id": "1301.TSE",
            "start_date": "2024-01-04",
            "end_date": "2024-01-05",
            "granularity": "Trade",
        }
        await server._handle_load_replay_data(msg, base_dir=FIXTURES)

        busy = _busy_events(server)
        assert len(busy) == 0, f"Expected no EngineBusy, got {busy}"
        assert server._replay_state == ReplayState.LOADED

    @pytest.mark.asyncio
    async def test_live_state_transitions(self) -> None:
        """LiveState の名前が正しく EngineBusy に含まれる。"""
        from engine.server import LiveState

        server = _make_server(mode="live")
        server._live_state = LiveState.CONNECTED

        # CONNECTED 状態では DISCONNECTED が必要な RequestVenueLogin が reject される
        server._tachibana_login_inflight = asyncio.Lock()
        server._config_dir = Path("/tmp/test-config")

        msg = {
            "op": "RequestVenueLogin",
            "request_id": "req-state-check",
            "venue": "tachibana",
        }
        await server._do_request_venue_login(msg)

        busy = _busy_events(server)
        assert len(busy) == 1
        assert "CONNECTED" in busy[0]["current_state"]
        assert "DISCONNECTED" in busy[0]["reason"]
