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
async def test_submit_order_limit_proceeds_past_phase_guard(server):
    """Phase O3: LIMIT 注文は Phase O0 ガードを通過し、次の SecondPasswordRequired に到達する。

    Phase O0 では UNSUPPORTED_IN_PHASE_O0 で reject されていたが、
    Phase O3 では LIMIT が解禁されたため SecondPasswordRequired になる。
    """
    port, token, _, __ = server
    ws = await _connect(port, token)
    cmd = _base_submit_order()
    cmd["order"]["order_type"] = "LIMIT"
    cmd["order"]["price"] = "2000"
    await ws.send(orjson.dumps(cmd))
    evt = await _recv_event(ws)
    # Phase O3: LIMIT はガードを通過し、SecondPasswordRequired に到達する
    assert evt["event"] == "SecondPasswordRequired", (
        f"Phase O3: LIMIT 注文はガードを通過するため SecondPasswordRequired が期待されるが、"
        f"got {evt!r}"
    )
    await ws.close()


@pytest.mark.asyncio
async def test_submit_order_sell_proceeds_past_phase_guard(server):
    """Phase O3: SELL 注文は Phase O0 ガードを通過し、SecondPasswordRequired に到達する。

    Phase O0 では UNSUPPORTED_IN_PHASE_O0 で reject されていたが、
    Phase O3 では SELL が解禁された。
    """
    port, token, _, __ = server
    ws = await _connect(port, token)
    cmd = _base_submit_order()
    cmd["order"]["order_side"] = "SELL"
    await ws.send(orjson.dumps(cmd))
    evt = await _recv_event(ws)
    # Phase O3: SELL はガードを通過し SecondPasswordRequired に到達する
    assert evt["event"] == "SecondPasswordRequired", (
        f"Phase O3: SELL 注文はガードを通過するため SecondPasswordRequired が期待されるが、"
        f"got {evt!r}"
    )
    await ws.close()


@pytest.mark.asyncio
async def test_submit_order_unsupported_type_market_if_touched_rejected(server):
    """MARKET_IF_TOUCHED → OrderRejected{reason_code=UNSUPPORTED_IN_PHASE_O0} (立花未対応)"""
    port, token, _, __ = server
    ws = await _connect(port, token)
    cmd = _base_submit_order()
    cmd["order"]["order_type"] = "MARKET_IF_TOUCHED"
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


# ---------------------------------------------------------------------------
# M-1: SetSecondPassword — 空文字列でも _second_password が更新される
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_second_password_empty_string_does_not_update_state(server):
    """空文字列 value では _second_password が更新されないこと（R2-MEDIUM）。

    立花 API は空文字列の第二暗証番号を reject するため、空文字列を設定しても
    _second_password は None のままにして VENUE_REJECTED を防ぐ。
    """
    _, _, srv, __ = server
    # 初期状態は None
    assert srv._second_password is None
    # 空文字列を送信 → 無視されるべき
    srv._handle_set_second_password({"value": ""})
    assert srv._second_password is None, "_second_password must NOT be updated for empty string"

    # 空白のみの文字列も無視されること
    srv._handle_set_second_password({"value": "   "})
    assert srv._second_password is None, "_second_password must NOT be updated for whitespace-only"

    # 有効な文字列は設定されること
    srv._handle_set_second_password({"value": "valid-pw"})
    assert srv._second_password == "valid-pw", "_second_password must be updated for valid string"


# ---------------------------------------------------------------------------
# M-2: _do_submit_order — セッション未確立時に OrderRejected{NOT_LOGGED_IN} が返る
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_order_not_logged_in_returns_order_rejected(server):
    """セッション未確立時の SubmitOrder は OrderRejected{NOT_LOGGED_IN} を返す（M-2）。"""
    port, token, srv, __ = server
    ws = await _connect(port, token)

    # 第二暗証番号を設定する（セッションチェックより先）
    srv._handle_set_second_password({"value": "pw"})
    # セッションは None のまま（_tachibana_session は設定しない）

    await ws.send(orjson.dumps(_base_submit_order("req-not-logged")))
    evt = await _recv_event(ws)
    assert evt["event"] == "OrderRejected", f"expected OrderRejected, got {evt}"
    assert evt["reason_code"] == "NOT_LOGGED_IN"
    await ws.close()


# ---------------------------------------------------------------------------
# C-1: _do_submit_order — HTTP エラー時に OrderRejected が発火される
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_order_http_error_causes_order_rejected(server):
    """tachibana_submit_order が httpx.ConnectError を上げると OrderRejected{TRANSPORT_ERROR} が返る（C-1）。"""
    import httpx
    from unittest.mock import patch, AsyncMock

    port, token, srv, __ = server
    ws = await _connect(port, token)

    # セッションをダミーで設定
    from unittest.mock import MagicMock
    srv._tachibana_session = MagicMock()
    srv._tachibana_session.zyoutoeki_kazei_c = "1"
    srv._tachibana_session.url_request = MagicMock()
    srv._handle_set_second_password({"value": "pw"})

    with patch(
        "engine.server.tachibana_submit_order",
        new=AsyncMock(side_effect=httpx.ConnectError("connection refused")),
    ):
        await ws.send(orjson.dumps(_base_submit_order("req-transport-err")))
        # OrderSubmitted が先に来るはず
        evt1 = await _recv_event(ws)
        assert evt1["event"] == "OrderSubmitted", f"expected OrderSubmitted first, got {evt1}"
        # 次に OrderRejected が来るはず
        evt2 = await _recv_event(ws)
        assert evt2["event"] == "OrderRejected", f"expected OrderRejected, got {evt2}"
        assert evt2["reason_code"] == "TRANSPORT_ERROR"

    await ws.close()


@pytest.mark.asyncio
async def test_submit_order_session_expired_clears_second_password(server):
    """SessionExpiredError 時は second_password がクリアされ OrderRejected{SESSION_EXPIRED} が返る（C-1 + M-14）。"""
    from unittest.mock import patch, AsyncMock, MagicMock
    from engine.exchanges.tachibana_helpers import SessionExpiredError

    port, token, srv, __ = server
    ws = await _connect(port, token)

    srv._tachibana_session = MagicMock()
    srv._tachibana_session.zyoutoeki_kazei_c = "1"
    srv._tachibana_session.url_request = MagicMock()
    srv._handle_set_second_password({"value": "sentinel"})
    assert srv._second_password == "sentinel"

    with patch(
        "engine.server.tachibana_submit_order",
        new=AsyncMock(side_effect=SessionExpiredError("expired")),
    ):
        await ws.send(orjson.dumps(_base_submit_order("req-session-exp")))
        evt1 = await _recv_event(ws)
        assert evt1["event"] == "OrderSubmitted"
        evt2 = await _recv_event(ws)
        assert evt2["event"] == "OrderRejected"
        assert evt2["reason_code"] == "SESSION_EXPIRED"

    # second_password はクリアされているはず
    assert srv._second_password is None, "second_password must be cleared on SessionExpiredError"
    await ws.close()
