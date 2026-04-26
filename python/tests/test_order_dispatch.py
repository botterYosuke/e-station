"""TDD Red → Green: server.py が SubmitOrder / SetSecondPassword / ForgetSecondPassword を
受け取ったとき正しいイベントを outbox に積むことを確認する統合テスト。

テスト方針:
- 実際の DataEngineServer を起動し、WebSocket 経由でコマンドを送る
- TachibanaWorker は mock に差し替える（ネットワーク呼び出し排除）
- 各テストは独立した outbox イベントを確認する

対象 T0.3 受け入れ条件:
  - SubmitOrder / venue=unknown → Error イベント（unknown_venue）
  - SubmitOrder / venue=tachibana / UNSUPPORTED 条件 → OrderRejected (UNSUPPORTED_IN_PHASE_O0)
  - SubmitOrder / venue=tachibana / 第二暗証番号未設定 → SecondPasswordRequired
  - SetSecondPassword → 次の SubmitOrder で SecondPasswordRequired が出なくなる
  - ForgetSecondPassword → 次の SubmitOrder で再び SecondPasswordRequired が出る
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest
import websockets

from engine.schemas import SCHEMA_MAJOR, SCHEMA_MINOR


# ---------------------------------------------------------------------------
# Shared helpers
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


def _base_submit_order(request_id: str = "req-1") -> dict:
    return {
        "op": "SubmitOrder",
        "request_id": request_id,
        "venue": "tachibana",
        "order": {
            "client_order_id": "cid-test-001",
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

    token = "test-tok"
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
# Tests: SubmitOrder dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_order_unknown_venue_returns_error(server):
    """venue=unknown → Error{code=unknown_venue}"""
    port, token, _, __ = server
    ws = await _connect(port, token)
    cmd = _base_submit_order()
    cmd["venue"] = "unknown_exchange"
    await ws.send(orjson.dumps(cmd))
    evt = await _recv_event(ws)
    assert evt["event"] == "Error"
    assert evt.get("code") == "unknown_venue"
    await ws.close()


@pytest.mark.asyncio
async def test_submit_order_unsupported_type_returns_order_rejected(server):
    """LIMIT 注文 → OrderRejected{reason_code=UNSUPPORTED_IN_PHASE_O0}"""
    port, token, _, __ = server
    ws = await _connect(port, token)
    cmd = _base_submit_order()
    cmd["order"]["order_type"] = "LIMIT"
    cmd["order"]["price"] = "2000"
    await ws.send(orjson.dumps(cmd))
    evt = await _recv_event(ws)
    assert evt["event"] == "OrderRejected"
    assert evt["reason_code"] == "UNSUPPORTED_IN_PHASE_O0"
    assert evt["client_order_id"] == "cid-test-001"
    await ws.close()


@pytest.mark.asyncio
async def test_submit_order_sell_returns_order_rejected(server):
    """SELL 注文 → OrderRejected{reason_code=UNSUPPORTED_IN_PHASE_O0}"""
    port, token, _, __ = server
    ws = await _connect(port, token)
    cmd = _base_submit_order()
    cmd["order"]["order_side"] = "SELL"
    await ws.send(orjson.dumps(cmd))
    evt = await _recv_event(ws)
    assert evt["event"] == "OrderRejected"
    assert evt["reason_code"] == "UNSUPPORTED_IN_PHASE_O0"
    await ws.close()


@pytest.mark.asyncio
async def test_submit_order_no_second_password_returns_second_password_required(server):
    """第二暗証番号未設定 → SecondPasswordRequired が返る"""
    port, token, _, __ = server
    ws = await _connect(port, token)
    await ws.send(orjson.dumps(_base_submit_order()))
    evt = await _recv_event(ws)
    assert evt["event"] == "SecondPasswordRequired"
    assert "request_id" in evt
    await ws.close()


# ---------------------------------------------------------------------------
# Tests: SetSecondPassword / ForgetSecondPassword
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_second_password_then_submit_order_proceeds(server):
    """SetSecondPassword 後は SecondPasswordRequired が出ず OrderSubmitted が返る。

    Note: 実際の HTTP 送信は mock するので OrderSubmitted まで進む（HTTP mock は TBD）。
    現段階では「SecondPasswordRequired が出ないこと」を確認するだけでよい。
    """
    port, token, _, __ = server
    ws = await _connect(port, token)

    # まず第二暗証番号を設定
    set_pw = {
        "op": "SetSecondPassword",
        "request_id": "spw-1",
        "value": "test-password",
    }
    await ws.send(orjson.dumps(set_pw))
    # 何もイベントが返らない（ACK なし仕様）

    # 発注
    await ws.send(orjson.dumps(_base_submit_order("req-2")))
    evt = await _recv_event(ws)

    # SecondPasswordRequired ではなく OrderSubmitted（またはその後続イベント）が返るはず
    assert evt["event"] != "SecondPasswordRequired", (
        f"SetSecondPassword 後に SecondPasswordRequired が返った: {evt}"
    )
    await ws.close()


@pytest.mark.asyncio
async def test_forget_second_password_causes_second_password_required(server):
    """ForgetSecondPassword 後の発注は再び SecondPasswordRequired になる。"""
    port, token, _, __ = server
    ws = await _connect(port, token)

    # 設定 → 忘れる
    await ws.send(orjson.dumps({"op": "SetSecondPassword", "request_id": "spw-1", "value": "pw"}))
    await asyncio.sleep(0.01)
    await ws.send(orjson.dumps({"op": "ForgetSecondPassword"}))
    await asyncio.sleep(0.01)

    # 発注
    await ws.send(orjson.dumps(_base_submit_order("req-3")))
    evt = await _recv_event(ws)
    assert evt["event"] == "SecondPasswordRequired"
    await ws.close()
