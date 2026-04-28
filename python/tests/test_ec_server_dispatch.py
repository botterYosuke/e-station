"""Phase O2: _on_ec_event がECイベントを正しいIPC outboxイベントに変換することを検証する。

対象: DataEngineServer._on_ec_event / _venue_to_client / _fill_cumulative
"""

from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from engine.exchanges.tachibana_event import OrderEcEvent
from engine.server import DataEngineServer


def _make_server() -> DataEngineServer:
    """テスト用の DataEngineServer（接続なし）を生成する。"""
    return DataEngineServer(
        port=29999,
        token="test-token",
        dev_tachibana_login_allowed=False,
    )


def _make_fill_event(
    venue_order_id: str = "V001",
    trade_id: str = "T001",
    last_qty: str = "100",
    last_price: str = "3500",
    leaves_qty: str = "0",
    ts_event_ms: int = 1700000000000,
    notification_type: str = "2",
) -> OrderEcEvent:
    return OrderEcEvent(
        venue_order_id=venue_order_id,
        trade_id=trade_id,
        notification_type=notification_type,
        last_price=last_price,
        last_qty=last_qty,
        leaves_qty=leaves_qty,
        ts_event_ms=ts_event_ms,
    )


# ---------------------------------------------------------------------------
# _on_ec_event: OrderFilled (NT=2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_ec_event_fill_emits_order_filled() -> None:
    """NT=2（約定）: OrderFilled が outbox に追加される。"""
    srv = _make_server()
    srv._venue_to_client["V001"] = "C001"

    ev = _make_fill_event(venue_order_id="V001", last_qty="100", last_price="3500", leaves_qty="0")
    await srv._on_ec_event("EC", ev)

    assert len(srv._outbox) == 1
    msg = list(srv._outbox._q)[0]
    assert msg["event"] == "OrderFilled"
    assert msg["client_order_id"] == "C001"
    assert msg["venue_order_id"] == "V001"
    assert msg["trade_id"] == "T001"
    assert msg["last_qty"] == "100"
    assert msg["last_price"] == "3500"
    assert msg["leaves_qty"] == "0"
    assert msg["cumulative_qty"] == "100"
    assert msg["ts_event_ms"] == 1700000000000


@pytest.mark.asyncio
async def test_on_ec_event_cumulative_qty_accumulates() -> None:
    """部分約定2回: cumulative_qty が加算される。"""
    srv = _make_server()
    srv._venue_to_client["V002"] = "C002"

    ev1 = _make_fill_event(venue_order_id="V002", trade_id="T001", last_qty="50", leaves_qty="50")
    await srv._on_ec_event("EC", ev1)

    ev2 = _make_fill_event(venue_order_id="V002", trade_id="T002", last_qty="50", leaves_qty="0")
    await srv._on_ec_event("EC", ev2)

    msgs = list(srv._outbox._q)
    assert len(msgs) == 2
    assert msgs[0]["cumulative_qty"] == "50"
    assert msgs[1]["cumulative_qty"] == "100"


# ---------------------------------------------------------------------------
# _on_ec_event: OrderCanceled (NT=3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_ec_event_cancel_emits_order_canceled() -> None:
    """NT=3（取消）: OrderCanceled が outbox に追加される。"""
    srv = _make_server()
    srv._venue_to_client["V003"] = "C003"

    ev = _make_fill_event(venue_order_id="V003", notification_type="3", ts_event_ms=1700000001000)
    await srv._on_ec_event("EC", ev)

    assert len(srv._outbox) == 1
    msg = list(srv._outbox._q)[0]
    assert msg["event"] == "OrderCanceled"
    assert msg["client_order_id"] == "C003"
    assert msg["venue_order_id"] == "V003"
    assert msg["ts_event_ms"] == 1700000001000


# ---------------------------------------------------------------------------
# _on_ec_event: OrderExpired (NT=4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_ec_event_expired_emits_order_expired() -> None:
    """NT=4（失効）: OrderExpired が outbox に追加される。"""
    srv = _make_server()
    srv._venue_to_client["V004"] = "C004"

    ev = _make_fill_event(venue_order_id="V004", notification_type="4", ts_event_ms=1700000002000)
    await srv._on_ec_event("EC", ev)

    assert len(srv._outbox) == 1
    msg = list(srv._outbox._q)[0]
    assert msg["event"] == "OrderExpired"
    assert msg["client_order_id"] == "C004"
    assert msg["venue_order_id"] == "V004"
    assert msg["ts_event_ms"] == 1700000002000


# ---------------------------------------------------------------------------
# _on_ec_event: 受付通知 (NT=1) は無視
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_ec_event_accepted_notification_ignored() -> None:
    """NT=1（受付）: outbox に何も追加されない（OrderAccepted は submit 時に処理済み）。"""
    srv = _make_server()
    srv._venue_to_client["V005"] = "C005"

    ev = _make_fill_event(venue_order_id="V005", notification_type="1")
    await srv._on_ec_event("EC", ev)

    assert len(srv._outbox) == 0


# ---------------------------------------------------------------------------
# _on_ec_event: 未知の venue_order_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_ec_event_unknown_venue_order_id_emits_nothing() -> None:
    """venue_to_client に存在しない venue_order_id は outbox に何も追加しない。"""
    srv = _make_server()
    # _venue_to_client に何も入れない

    ev = _make_fill_event(venue_order_id="UNKNOWN", notification_type="2")
    await srv._on_ec_event("EC", ev)

    assert len(srv._outbox) == 0


# ---------------------------------------------------------------------------
# _on_ec_event: FD フレームは無視
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_ec_event_fd_frame_ignored() -> None:
    """frame_type="FD" は outbox に何も追加しない。"""
    srv = _make_server()
    ev = _make_fill_event(notification_type="2")
    await srv._on_ec_event("FD", ev)

    assert len(srv._outbox) == 0


# ---------------------------------------------------------------------------
# _venue_to_client が _do_get_order_list で更新される
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_venue_to_client_populated_from_order_list() -> None:
    """_do_get_order_list 後に venue_order_id → client_order_id マップが更新される。"""
    from unittest.mock import AsyncMock, patch
    from dataclasses import dataclass
    from typing import Optional

    @dataclass
    class _FakeRecord:
        client_order_id: str
        venue_order_id: Optional[str]
        instrument_id: str = "7203.TSE"
        order_side: str = "BUY"
        order_type: str = "LIMIT"
        quantity: str = "100"
        filled_qty: str = "0"
        leaves_qty: str = "100"
        price: Optional[str] = "3500"
        trigger_price: Optional[str] = None
        time_in_force: str = "DAY"
        expire_time_ns: Optional[int] = None
        status: str = "ACCEPTED"
        ts_event_ms: int = 1700000000000

    srv = _make_server()
    records = [
        _FakeRecord(client_order_id="C010", venue_order_id="V010"),
        _FakeRecord(client_order_id="C011", venue_order_id="V011"),
        _FakeRecord(client_order_id="C012", venue_order_id=None),  # unknown → skip
    ]
    msg = {"request_id": "req-1", "venue": "tachibana"}

    with (
        patch.object(srv, "_tachibana_session", MagicMock()),
        patch("engine.server.tachibana_fetch_order_list", AsyncMock(return_value=records)),
    ):
        await srv._do_get_order_list(msg)

    assert srv._venue_to_client.get("V010") == "C010"
    assert srv._venue_to_client.get("V011") == "C011"
    assert "None" not in srv._venue_to_client
