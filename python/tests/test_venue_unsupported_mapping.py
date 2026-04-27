"""D4-2: UnsupportedOrderError が Python dispatch 層（server._do_submit_order）で
Event::OrderRejected{reason_code="VENUE_UNSUPPORTED"} に写ることを確認。

UNSUPPORTED_IN_PHASE_O0 との違い:
  - UNSUPPORTED_IN_PHASE_O0: Rust HTTP dispatch 層の Phase O0 ガードが返す reason_code。
    server.py の check_phase_o0_order() で生成される（_do_submit_order の HTTP 送信前）。
  - VENUE_UNSUPPORTED: Python の tachibana_submit_order() 内で _envelope_to_wire() が
    UnsupportedOrderError を raise し、それを _do_submit_order の except 節が catch して
    outbox に積む reason_code。同じ HTTP 経路を通過するが、_envelope_to_wire() の写像段階
    で発生するため別経路である。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest
import websockets

from engine.schemas import SCHEMA_MAJOR, SCHEMA_MINOR


# ---------------------------------------------------------------------------
# Shared helpers (test_order_dispatch.py と同じパターン)
# ---------------------------------------------------------------------------


async def _connect(port: int, token: str) -> websockets.ClientConnection:
    ws = await websockets.connect(f"ws://127.0.0.1:{port}")
    await ws.send(
        orjson.dumps(
            {
                "op": "Hello",
                "schema_major": SCHEMA_MAJOR,
                "schema_minor": SCHEMA_MINOR,
                "client_version": "test",
                "token": token,
            }
        )
    )
    raw = await ws.recv()
    assert orjson.loads(raw)["event"] == "Ready"
    return ws


async def _recv_event(ws: websockets.ClientConnection, timeout: float = 3.0) -> dict:
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    return orjson.loads(raw)


def _make_mock_tachibana_worker():
    w = MagicMock()
    w.prepare = AsyncMock(return_value=None)
    w.capabilities = MagicMock(return_value={})
    return w


def _base_submit_order(request_id: str = "req-unsup-1") -> dict:
    return {
        "op": "SubmitOrder",
        "request_id": request_id,
        "venue": "tachibana",
        "order": {
            "client_order_id": "cid-unsup-001",
            "instrument_id": "7203.T/TSE",
            "order_side": "BUY",
            "order_type": "MARKET",
            "quantity": "100",
            "time_in_force": "DAY",
            "post_only": False,
            "reduce_only": False,
            "tags": ["cash_margin=cash"],
        },
    }


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def server(unused_tcp_port):
    from engine.server import DataEngineServer

    token = "test-tok-unsup"
    mock_tachibana = _make_mock_tachibana_worker()

    with (
        patch("engine.server.BinanceWorker", return_value=MagicMock(prepare=AsyncMock(), capabilities=MagicMock(return_value={}))),
        patch("engine.server.BybitWorker", return_value=MagicMock(prepare=AsyncMock(), capabilities=MagicMock(return_value={}))),
        patch("engine.server.HyperliquidWorker", return_value=MagicMock(prepare=AsyncMock(), capabilities=MagicMock(return_value={}))),
        patch("engine.server.MexcWorker", return_value=MagicMock(prepare=AsyncMock(), capabilities=MagicMock(return_value={}))),
        patch("engine.server.OkexWorker", return_value=MagicMock(prepare=AsyncMock(), capabilities=MagicMock(return_value={}))),
        patch("engine.server.TachibanaWorker", return_value=mock_tachibana),
    ):
        srv = DataEngineServer(port=unused_tcp_port, token=token)
        task = asyncio.create_task(srv.serve())
        await asyncio.sleep(0.05)
        yield unused_tcp_port, token, srv, mock_tachibana
        srv.shutdown()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


# ---------------------------------------------------------------------------
# Tests: UnsupportedOrderError → VENUE_UNSUPPORTED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unsupported_order_error_maps_to_venue_unsupported(server):
    """UnsupportedOrderError が _do_submit_order の except 節で
    OrderRejected{reason_code="VENUE_UNSUPPORTED"} に写ること（D4-2）。

    経路:
      _do_submit_order
        → tachibana_submit_order（mock で UnsupportedOrderError を raise）
        → except UnsupportedOrderError: outbox.append({"reason_code": "VENUE_UNSUPPORTED"})

    UNSUPPORTED_IN_PHASE_O0（check_phase_o0_order による Phase O0 ガード）とは
    別経路であることに注意：こちらは HTTP 送信前の写像段階で発生する。
    """
    from engine.exchanges.tachibana_orders import UnsupportedOrderError

    port, token, srv, __ = server
    ws = await _connect(port, token)

    # セッションと第二暗証番号を設定して Phase O0 ガードと第二暗証番号チェックを通過させる
    srv._tachibana_session = MagicMock()
    srv._tachibana_session.zyoutoeki_kazei_c = "1"
    srv._tachibana_session.url_request = MagicMock()
    srv._handle_set_second_password({"value": "test-pw"})

    # tachibana_submit_order が UnsupportedOrderError を raise するよう mock
    with patch(
        "engine.server.tachibana_submit_order",
        new=AsyncMock(side_effect=UnsupportedOrderError("unsupported order type for this venue")),
    ):
        await ws.send(orjson.dumps(_base_submit_order("req-venue-unsup")))

        # OrderSubmitted が先に発火される（nautilus 2段イベント）
        evt1 = await _recv_event(ws)
        assert evt1["event"] == "OrderSubmitted", (
            f"Expected OrderSubmitted first (nautilus 2-step), got {evt1!r}"
        )

        # 次に OrderRejected{VENUE_UNSUPPORTED} が来る
        evt2 = await _recv_event(ws)
        assert evt2["event"] == "OrderRejected", (
            f"Expected OrderRejected, got {evt2!r}"
        )
        assert evt2["reason_code"] == "VENUE_UNSUPPORTED", (
            f"Expected reason_code='VENUE_UNSUPPORTED' (from UnsupportedOrderError in _envelope_to_wire), "
            f"got {evt2['reason_code']!r}. "
            f"Note: UNSUPPORTED_IN_PHASE_O0 is a different path (check_phase_o0_order before HTTP dispatch)."
        )

    await ws.close()


@pytest.mark.asyncio
async def test_venue_unsupported_vs_unsupported_in_phase_o0_are_different_paths(server):
    """UNSUPPORTED_IN_PHASE_O0 と VENUE_UNSUPPORTED が別経路であることを確認。

    UNSUPPORTED_IN_PHASE_O0:
      check_phase_o0_order() による Phase O0 事前チェック。
      HTTP 送信より前、セッション確認より前に発生する。
      MARKET_IF_TOUCHED などの非サポート order_type で発生する。

    VENUE_UNSUPPORTED:
      tachibana_submit_order() 内の _envelope_to_wire() 写像失敗。
      セッション確認・第二暗証番号確認通過後、HTTP 送信直前に発生する。
      mock で UnsupportedOrderError を inject することでのみ再現可能。
    """
    port, token, srv, __ = server
    ws = await _connect(port, token)

    # UNSUPPORTED_IN_PHASE_O0 は check_phase_o0_order() が返す
    # セッション・第二暗証番号未設定でも Phase O0 ガードは通る（先に評価されるため）
    cmd = _base_submit_order("req-phase-o0")
    cmd["order"]["order_type"] = "MARKET_IF_TOUCHED"  # Phase O0 未サポート
    await ws.send(orjson.dumps(cmd))
    evt = await _recv_event(ws)

    assert evt["event"] == "OrderRejected"
    assert evt["reason_code"] == "UNSUPPORTED_IN_PHASE_O0", (
        "MARKET_IF_TOUCHED should be rejected by check_phase_o0_order() "
        "with reason_code='UNSUPPORTED_IN_PHASE_O0', not 'VENUE_UNSUPPORTED'"
    )

    await ws.close()
