"""Phase 8 review-fix-loop R1 / Phase 1 — H-Type4 RED→GREEN tests.

GetBuyingPower / GetPositions / GetOrderList が Live::DISCONNECTED 中に
EngineBusy event を返すことを assert する。
"""

from __future__ import annotations

from collections import deque
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class _ListOutbox:
    def __init__(self) -> None:
        self._q: deque = deque()

    def append(self, item: object) -> None:
        self._q.append(item)

    def send_to(self, ws: object, item: object) -> None:
        # M-GP8 (Phase 8 R1 / Phase 2): unicast 経路の test stub。
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


def _make_server():
    from engine.server import DataEngineServer, LiveState, ReplayState
    from engine.nautilus.portfolio_view import PortfolioView

    with patch.object(DataEngineServer, "__init__", lambda self, **_: None):
        server = DataEngineServer()

    server._outbox = _ListOutbox()
    server._mode = "live"
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
    return server


def _busy(server) -> list[dict]:
    return [e for e in server._outbox if e.get("event") == "EngineBusy"]


@pytest.mark.asyncio
async def test_get_buying_power_busy_when_disconnected() -> None:
    server = _make_server()
    msg = {"op": "GetBuyingPower", "request_id": "r1", "venue": "tachibana"}
    await server._do_get_buying_power(msg)
    busy = _busy(server)
    assert len(busy) == 1
    assert busy[0]["attempted_command"] == "GetBuyingPower"
    assert busy[0]["current_state"] == "DISCONNECTED"


@pytest.mark.asyncio
async def test_get_positions_busy_when_disconnected() -> None:
    server = _make_server()
    msg = {"op": "GetPositions", "request_id": "r2", "venue": "tachibana"}
    await server._do_get_positions(msg)
    busy = _busy(server)
    assert len(busy) == 1
    assert busy[0]["attempted_command"] == "GetPositions"
    assert busy[0]["current_state"] == "DISCONNECTED"


@pytest.mark.asyncio
async def test_get_order_list_busy_when_disconnected() -> None:
    server = _make_server()
    msg = {
        "op": "GetOrderList",
        "request_id": "r3",
        "venue": "tachibana",
        "filter": {},
    }
    await server._do_get_order_list(msg)
    busy = _busy(server)
    assert len(busy) == 1
    assert busy[0]["attempted_command"] == "GetOrderList"
    assert busy[0]["current_state"] == "DISCONNECTED"


@pytest.mark.asyncio
async def test_get_order_list_replay_venue_skips_live_guard() -> None:
    """venue='replay' は live state guard を通らないこと (既存仕様の維持)。"""
    server = _make_server()
    msg = {
        "op": "GetOrderList",
        "request_id": "r4",
        "venue": "replay",
        "filter": {},
    }
    await server._do_get_order_list(msg)
    # EngineBusy は出ない (replay は LiveState 関係なく WAL から返す)
    assert not _busy(server)
