"""TDD Red: Server dispatch integration tests."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest
import websockets

from engine.exchanges.base import WsNativeResyncTriggered
from engine.schemas import SCHEMA_MAJOR, SCHEMA_MINOR


# ---------------------------------------------------------------------------
# Helper: connect with handshake
# ---------------------------------------------------------------------------


async def _connect_and_handshake(port: int, token: str) -> websockets.ClientConnection:
    ws = await websockets.connect(f"ws://127.0.0.1:{port}")
    hello = {
        "op": "Hello",
        "schema_major": SCHEMA_MAJOR,
        "schema_minor": SCHEMA_MINOR,
        "client_version": "test",
        "token": token,
    }
    await ws.send(orjson.dumps(hello))
    raw = await ws.recv()
    msg = orjson.loads(raw)
    assert msg["event"] == "Ready", f"Expected Ready, got: {msg}"
    return ws


async def _wait_for_port(port: int, timeout: float = 2.0) -> None:
    """Poll until the TCP port is accepting connections."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            return
        except OSError:
            if loop.time() >= deadline:
                raise TimeoutError(f"port {port} not open after {timeout}s")
            await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def running_server(unused_tcp_port):
    """Start DataEngineServer on a random port, yield (port, token, mock_worker), then stop."""
    from engine.server import DataEngineServer

    token = "test-token-abc123"
    mock_worker = _make_mock_worker()

    noop_startup = AsyncMock(return_value=None)
    with patch("engine.server.BinanceWorker", return_value=mock_worker), \
         patch.object(DataEngineServer, "_startup_tachibana", noop_startup):
        server = DataEngineServer(port=unused_tcp_port, token=token)
        task = asyncio.create_task(server.serve())

        await _wait_for_port(unused_tcp_port)

        yield unused_tcp_port, token, mock_worker

        server.shutdown()
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass


def _make_mock_worker():
    worker = MagicMock()
    worker.prepare = AsyncMock(return_value=None)
    worker.list_tickers = AsyncMock(return_value=[{"symbol": "BTCUSDT"}])
    worker.fetch_klines = AsyncMock(return_value=[])
    worker.fetch_open_interest = AsyncMock(return_value=[])
    worker.fetch_depth_snapshot = AsyncMock(
        return_value={
            "last_update_id": 12345,
            "bids": [{"price": "67990.0", "qty": "1.5"}],
            "asks": [{"price": "68000.0", "qty": "0.5"}],
        }
    )
    worker.fetch_ticker_stats = AsyncMock(return_value={"mark_price": "68000.0", "daily_price_chg": "1.5"})
    worker.fetch_trades = AsyncMock(return_value=[
        {"ts_ms": 1700000000000, "price": "68000.0", "qty": "0.5", "side": "buy", "is_liquidation": False},
    ])
    worker.stream_trades = AsyncMock(return_value=None)
    worker.stream_depth = AsyncMock(return_value=None)
    worker.stream_kline = AsyncMock(return_value=None)
    return worker


@pytest.fixture
def unused_tcp_port():
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handshake_returns_ready(running_server):
    port, token, _ = running_server
    ws = await _connect_and_handshake(port, token)
    await ws.close()


@pytest.mark.asyncio
async def test_handshake_calls_worker_prepare_before_ready(running_server):
    """Spec §4.5 contract: workers must be warmed up before Ready is emitted."""
    port, token, mock_worker = running_server
    ws = await _connect_and_handshake(port, token)
    # By the time the client receives Ready, prepare() must already have been
    # awaited so the next op (e.g. ListTickers) can be served immediately.
    mock_worker.prepare.assert_awaited()
    await ws.close()


@pytest.mark.asyncio
async def test_wrong_token_disconnects(running_server):
    port, _, _mock = running_server
    ws = await websockets.connect(f"ws://127.0.0.1:{port}")
    bad_hello = {
        "op": "Hello",
        "schema_major": SCHEMA_MAJOR,
        "schema_minor": SCHEMA_MINOR,
        "client_version": "test",
        "token": "wrong-token",
    }
    await ws.send(orjson.dumps(bad_hello))
    raw = await ws.recv()
    msg = orjson.loads(raw)
    assert msg.get("event") == "EngineError"
    assert msg["code"] == "auth_failed"


@pytest.mark.asyncio
async def test_subscribe_trade_dispatches_to_worker(running_server):
    port, token, mock_worker = running_server

    ws = await _connect_and_handshake(port, token)

    subscribe_msg = {
        "op": "Subscribe",
        "venue": "binance",
        "ticker": "BTCUSDT",
        "stream": "trade",
    }
    await ws.send(orjson.dumps(subscribe_msg))

    # Let the dispatch coroutine run.
    await asyncio.sleep(0.05)

    mock_worker.stream_trades.assert_called_once()
    args = mock_worker.stream_trades.call_args.args
    # server calls: stream_trades(ticker, market, base_ssid, outbox, stop, on_ssid=...)
    assert args[0] == "BTCUSDT", f"Expected ticker BTCUSDT, got {args[0]}"

    await ws.close()


@pytest.mark.asyncio
async def test_request_depth_snapshot_ws_native_resync_no_error_event(unused_tcp_port):
    """WsNativeResyncTriggered must be swallowed; no Error event must be emitted."""
    from engine.server import DataEngineServer

    token = "test-token-resync"
    mock_worker = _make_mock_worker()
    mock_worker.fetch_depth_snapshot = AsyncMock(
        side_effect=WsNativeResyncTriggered("WS-native — reconnect triggered")
    )

    noop_startup = AsyncMock(return_value=None)
    with patch("engine.server.BinanceWorker", return_value=mock_worker), \
         patch.object(DataEngineServer, "_startup_tachibana", noop_startup):
        server = DataEngineServer(port=unused_tcp_port, token=token)
        task = asyncio.create_task(server.serve())
        await _wait_for_port(unused_tcp_port)

        ws = await _connect_and_handshake(unused_tcp_port, token)

        await ws.send(orjson.dumps({
            "op": "RequestDepthSnapshot",
            "venue": "binance",
            "ticker": "BTCUSDT",
            "stream_session_id": "sess-1",
        }))

        # Give dispatch time to complete, then assert no Error event was emitted.
        await asyncio.sleep(0.05)
        mock_worker.fetch_depth_snapshot.assert_awaited_once()
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=0.2)
            msg = orjson.loads(raw)
            pytest.fail(f"Unexpected event received: {msg}")
        except asyncio.TimeoutError:
            pass  # expected: WsNativeResyncTriggered was swallowed, no event emitted

        await ws.close()
        server.shutdown()
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=10.0)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass


@pytest.mark.asyncio
async def test_fetch_trades_returns_trades_fetched_event(running_server):
    """FetchTrades op dispatches to worker.fetch_trades and emits TradesFetched."""
    port, token, mock_worker = running_server

    ws = await _connect_and_handshake(port, token)

    req = {
        "op": "FetchTrades",
        "request_id": "req-trades-001",
        "venue": "binance",
        "ticker": "BTCUSDT",
        "market": "linear_perp",
        "start_ms": 1700000000000,
        "end_ms": 1700086400000,
    }
    await ws.send(orjson.dumps(req))

    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
    msg = orjson.loads(raw)
    assert msg.get("event") == "TradesFetched", f"Expected TradesFetched, got: {msg}"
    assert msg["request_id"] == "req-trades-001"
    assert msg["venue"] == "binance"
    assert msg["ticker"] == "BTCUSDT"
    assert isinstance(msg["trades"], list)
    assert len(msg["trades"]) == 1
    assert msg["trades"][0]["price"] == "68000.0"

    await ws.close()


@pytest.mark.asyncio
async def test_fetch_trades_unknown_venue_returns_error(running_server):
    """FetchTrades with an unknown venue returns an Error event."""
    port, token, _ = running_server

    ws = await _connect_and_handshake(port, token)

    req = {
        "op": "FetchTrades",
        "request_id": "req-trades-err",
        "venue": "unknown_venue",
        "ticker": "BTCUSDT",
        "start_ms": 1700000000000,
        "end_ms": 1700086400000,
    }
    await ws.send(orjson.dumps(req))

    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
    msg = orjson.loads(raw)
    assert msg.get("event") == "Error"
    assert msg["request_id"] == "req-trades-err"

    await ws.close()


@pytest.mark.asyncio
async def test_list_tickers_response(running_server):
    port, token, _ = running_server

    ws = await _connect_and_handshake(port, token)

    req = {
        "op": "ListTickers",
        "request_id": "req-001",
        "venue": "binance",
    }
    await ws.send(orjson.dumps(req))

    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
    msg = orjson.loads(raw)
    assert msg.get("event") == "TickerInfo"
    assert msg["request_id"] == "req-001"
    assert isinstance(msg["tickers"], list)

    await ws.close()


@pytest.mark.asyncio
async def test_shutdown_op_stops_server(running_server):
    port, token, _ = running_server
    ws = await _connect_and_handshake(port, token)
    await ws.send(orjson.dumps({"op": "Shutdown"}))
    await asyncio.sleep(0.1)
    # Connection should be closing
    await ws.close()


@pytest.mark.asyncio
async def test_fetch_trades_now_supported_returns_trades_fetched(running_server):
    """Phase 4: FetchTrades is now implemented and returns TradesFetched (not Error)."""
    port, token, _ = running_server
    ws = await _connect_and_handshake(port, token)
    await ws.send(
        orjson.dumps(
            {
                "op": "FetchTrades",
                "request_id": "req-trades",
                "venue": "binance",
                "ticker": "BTCUSDT",
                "start_ms": 1700000000000,
                "end_ms": 1700086400000,
            }
        )
    )

    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
    msg = orjson.loads(raw)
    assert msg["event"] == "TradesFetched", f"Expected TradesFetched, got: {msg}"
    assert msg["request_id"] == "req-trades"

    await ws.close()


@pytest.mark.asyncio
async def test_subscribe_kline_multiple_timeframes_coexist(running_server):
    """Subscribing BTCUSDT 1m and 5m should create two independent streams."""
    port, token, mock_worker = running_server
    ws = await _connect_and_handshake(port, token)

    await ws.send(orjson.dumps({
        "op": "Subscribe", "venue": "binance", "ticker": "BTCUSDT",
        "stream": "kline", "timeframe": "1m",
    }))
    await asyncio.sleep(0.05)
    assert mock_worker.stream_kline.call_count == 1

    await ws.send(orjson.dumps({
        "op": "Subscribe", "venue": "binance", "ticker": "BTCUSDT",
        "stream": "kline", "timeframe": "5m",
    }))
    await asyncio.sleep(0.05)
    assert mock_worker.stream_kline.call_count == 2, (
        "5m subscribe should create a second stream, not overwrite the 1m stream"
    )

    await ws.send(orjson.dumps({
        "op": "Unsubscribe", "venue": "binance", "ticker": "BTCUSDT",
        "stream": "kline", "timeframe": "1m",
    }))
    await asyncio.sleep(0.05)
    assert mock_worker.stream_kline.call_count == 2

    await ws.close()


@pytest.mark.asyncio
async def test_fetch_klines_passes_start_end_ms_to_worker(running_server):
    """FetchKlines must forward start_ms and end_ms to the worker (regression for dropped fields)."""
    port, token, mock_worker = running_server
    ws = await _connect_and_handshake(port, token)

    req = {
        "op": "FetchKlines",
        "request_id": "req-kline-range",
        "venue": "binance",
        "ticker": "BTCUSDT",
        "timeframe": "1h",
        "limit": 100,
        "start_ms": 1_700_000_000_000,
        "end_ms": 1_700_086_400_000,
    }
    await ws.send(orjson.dumps(req))

    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
    msg = orjson.loads(raw)
    assert msg.get("event") == "Klines", f"Expected Klines, got: {msg}"

    _, call_kwargs = mock_worker.fetch_klines.call_args
    assert call_kwargs.get("start_ms") == 1_700_000_000_000, "start_ms was not forwarded"
    assert call_kwargs.get("end_ms") == 1_700_086_400_000, "end_ms was not forwarded"

    await ws.close()


@pytest.mark.asyncio
async def test_ping_returns_pong(running_server):
    """Ping op must return a Pong event with the same request_id (health check contract)."""
    port, token, _ = running_server
    ws = await _connect_and_handshake(port, token)

    await ws.send(orjson.dumps({"op": "Ping", "request_id": "health-check-1"}))

    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
    msg = orjson.loads(raw)
    assert msg.get("event") == "Pong", f"Expected Pong, got: {msg}"
    assert msg["request_id"] == "health-check-1"

    await ws.close()


# ---------------------------------------------------------------------------
# N3.C: SubmitOrder venue guard tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_order_hyperliquid_venue_returns_unsupported_order_venue(running_server):
    """venue="hyperliquid" の SubmitOrder は unsupported_order_venue エラーを返す。
    hyperliquid は _workers に登録されていても発注 IPC 経路はサポートしない。"""
    port, token, _ = running_server
    ws = await _connect_and_handshake(port, token)

    req = {
        "op": "SubmitOrder",
        "request_id": "req-hl-001",
        "venue": "hyperliquid",
        "order": {
            "client_order_id": "cid-hl-001",
            "symbol": "BTC-USDC",
            "side": "buy",
            "order_type": "market",
            "quantity": "0.01",
            "request_key": 0,
        },
    }
    await ws.send(orjson.dumps(req))

    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
    msg = orjson.loads(raw)
    assert msg.get("event") == "Error", f"Expected Error, got: {msg}"
    assert msg.get("code") == "unsupported_order_venue", (
        f"Expected code='unsupported_order_venue', got: {msg.get('code')!r}"
    )
    assert msg["request_id"] == "req-hl-001"

    await ws.close()


@pytest.mark.asyncio
async def test_submit_order_unknown_venue_returns_unsupported_order_venue(running_server):
    """venue="unknown_xyz" の SubmitOrder は unsupported_order_venue エラーを返す。"""
    port, token, _ = running_server
    ws = await _connect_and_handshake(port, token)

    req = {
        "op": "SubmitOrder",
        "request_id": "req-unk-001",
        "venue": "unknown_xyz",
        "order": {
            "client_order_id": "cid-unk-001",
            "symbol": "BTC-USDC",
            "side": "buy",
            "order_type": "market",
            "quantity": "0.01",
            "request_key": 0,
        },
    }
    await ws.send(orjson.dumps(req))

    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
    msg = orjson.loads(raw)
    assert msg.get("event") == "Error", f"Expected Error, got: {msg}"
    assert msg.get("code") == "unsupported_order_venue", (
        f"Expected code='unsupported_order_venue', got: {msg.get('code')!r}"
    )
    assert msg["request_id"] == "req-unk-001"

    await ws.close()


@pytest.mark.asyncio
async def test_submit_order_tachibana_venue_proceeds_to_tachibana_logic(running_server):
    """venue="tachibana" は unsupported_order_venue を返さず tachibana 固有の処理に進む。
    セッション未確立なので NOT_LOGGED_IN または SecondPasswordRequired が返る。"""
    port, token, _ = running_server
    ws = await _connect_and_handshake(port, token)

    req = {
        "op": "SubmitOrder",
        "request_id": "req-tac-001",
        "venue": "tachibana",
        "order": {
            "client_order_id": "cid-tac-001",
            "symbol": "7203",
            "side": "buy",
            "order_type": "market",
            "quantity": "1",
            "request_key": 0,
        },
    }
    await ws.send(orjson.dumps(req))

    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
    msg = orjson.loads(raw)
    # venue guard を通過したことを確認: unsupported_order_venue ではない
    assert msg.get("code") != "unsupported_order_venue", (
        "venue='tachibana' should not be rejected by venue guard"
    )
    # tachibana 固有のエラー（NOT_LOGGED_IN / SecondPasswordRequired / OrderRejected など）
    # が返ることで tachibana 経路に到達したことを確認する
    assert msg.get("event") in ("OrderRejected", "SecondPasswordRequired", "Error"), (
        f"Unexpected event for tachibana venue: {msg}"
    )

    await ws.close()


# ---------------------------------------------------------------------------
# N3 H3: CancelOrder / ModifyOrder venue guard tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_order_hyperliquid_venue_returns_unsupported_order_venue(running_server):
    """venue="hyperliquid" の CancelOrder は unsupported_order_venue エラーを返す。"""
    port, token, _ = running_server
    ws = await _connect_and_handshake(port, token)

    req = {
        "op": "CancelOrder",
        "request_id": "req-cancel-hl-001",
        "venue": "hyperliquid",
        "client_order_id": "cid-hl-001",
        "venue_order_id": "99999",
    }
    await ws.send(orjson.dumps(req))

    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
    msg = orjson.loads(raw)
    assert msg.get("event") == "Error", f"Expected Error, got: {msg}"
    assert msg.get("code") == "unsupported_order_venue", (
        f"Expected code='unsupported_order_venue', got: {msg.get('code')!r}"
    )
    assert msg["request_id"] == "req-cancel-hl-001"

    await ws.close()


@pytest.mark.asyncio
async def test_cancel_order_unknown_venue_returns_unsupported_order_venue(running_server):
    """venue="unknown_xyz" の CancelOrder は unsupported_order_venue エラーを返す。"""
    port, token, _ = running_server
    ws = await _connect_and_handshake(port, token)

    req = {
        "op": "CancelOrder",
        "request_id": "req-cancel-unk-001",
        "venue": "unknown_xyz",
        "client_order_id": "cid-unk-001",
        "venue_order_id": "11111",
    }
    await ws.send(orjson.dumps(req))

    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
    msg = orjson.loads(raw)
    assert msg.get("event") == "Error", f"Expected Error, got: {msg}"
    assert msg.get("code") == "unsupported_order_venue", (
        f"Expected code='unsupported_order_venue', got: {msg.get('code')!r}"
    )
    assert msg["request_id"] == "req-cancel-unk-001"

    await ws.close()


@pytest.mark.asyncio
async def test_modify_order_hyperliquid_venue_returns_unsupported_order_venue(running_server):
    """venue="hyperliquid" の ModifyOrder は unsupported_order_venue エラーを返す。"""
    port, token, _ = running_server
    ws = await _connect_and_handshake(port, token)

    req = {
        "op": "ModifyOrder",
        "request_id": "req-modify-hl-001",
        "venue": "hyperliquid",
        "client_order_id": "cid-hl-001",
        "change": {"new_quantity": "0.2"},
    }
    await ws.send(orjson.dumps(req))

    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
    msg = orjson.loads(raw)
    assert msg.get("event") == "Error", f"Expected Error, got: {msg}"
    assert msg.get("code") == "unsupported_order_venue", (
        f"Expected code='unsupported_order_venue', got: {msg.get('code')!r}"
    )
    assert msg["request_id"] == "req-modify-hl-001"

    await ws.close()


@pytest.mark.asyncio
async def test_modify_order_unknown_venue_returns_unsupported_order_venue(running_server):
    """venue="unknown_xyz" の ModifyOrder は unsupported_order_venue エラーを返す。"""
    port, token, _ = running_server
    ws = await _connect_and_handshake(port, token)

    req = {
        "op": "ModifyOrder",
        "request_id": "req-modify-unk-001",
        "venue": "unknown_xyz",
        "client_order_id": "cid-unk-001",
        "change": {"new_quantity": "0.3"},
    }
    await ws.send(orjson.dumps(req))

    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
    msg = orjson.loads(raw)
    assert msg.get("event") == "Error", f"Expected Error, got: {msg}"
    assert msg.get("code") == "unsupported_order_venue", (
        f"Expected code='unsupported_order_venue', got: {msg.get('code')!r}"
    )
    assert msg["request_id"] == "req-modify-unk-001"

    await ws.close()


@pytest.mark.asyncio
async def test_cancel_order_tachibana_venue_proceeds_to_tachibana_logic(running_server):
    """venue="tachibana" の CancelOrder は unsupported_order_venue を返さず tachibana 経路に進む。
    セッション未確立なので NOT_LOGGED_IN / SecondPasswordRequired などが返る。"""
    port, token, _ = running_server
    ws = await _connect_and_handshake(port, token)

    req = {
        "op": "CancelOrder",
        "request_id": "req-cancel-tac-001",
        "venue": "tachibana",
        "client_order_id": "cid-tac-001",
        "venue_order_id": "88888",
    }
    await ws.send(orjson.dumps(req))

    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
    msg = orjson.loads(raw)
    # venue guard を通過したことを確認: unsupported_order_venue ではない
    assert msg.get("code") != "unsupported_order_venue", (
        "venue='tachibana' should not be rejected by venue guard"
    )
    # tachibana 固有のエラーが返ることで tachibana 経路に到達したことを確認する
    assert msg.get("event") in ("OrderRejected", "SecondPasswordRequired", "OrderPendingCancel", "Error"), (
        f"Unexpected event for tachibana cancel: {msg}"
    )

    await ws.close()


@pytest.mark.asyncio
async def test_modify_order_tachibana_venue_proceeds_to_tachibana_logic(running_server):
    """venue="tachibana" の ModifyOrder は unsupported_order_venue を返さず tachibana 経路に進む。
    セッション未確立なので NOT_LOGGED_IN / SecondPasswordRequired などが返る。"""
    port, token, _ = running_server
    ws = await _connect_and_handshake(port, token)

    req = {
        "op": "ModifyOrder",
        "request_id": "req-modify-tac-001",
        "venue": "tachibana",
        "client_order_id": "cid-tac-001",
        "change": {"new_quantity": "2"},
    }
    await ws.send(orjson.dumps(req))

    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
    msg = orjson.loads(raw)
    # venue guard を通過したことを確認: unsupported_order_venue ではない
    assert msg.get("code") != "unsupported_order_venue", (
        "venue='tachibana' should not be rejected by venue guard"
    )
    # tachibana 固有のエラーが返ることで tachibana 経路に到達したことを確認する
    assert msg.get("event") in ("OrderRejected", "SecondPasswordRequired", "Error"), (
        f"Unexpected event for tachibana modify: {msg}"
    )

    await ws.close()


@pytest.mark.asyncio
async def test_submit_order_replay_venue_still_handled_separately(running_server):
    """venue="replay" は専用 REPLAY_NOT_IMPLEMENTED 分岐で処理される（N3.C 変更なし）。"""
    port, token, _ = running_server
    ws = await _connect_and_handshake(port, token)

    req = {
        "op": "SubmitOrder",
        "request_id": "req-replay-001",
        "venue": "replay",
        "order": {
            "client_order_id": "cid-replay-001",
            "symbol": "7203",
            "side": "buy",
            "order_type": "market",
            "quantity": "1",
            "request_key": 0,
        },
    }
    await ws.send(orjson.dumps(req))

    # OrderSubmitted が先に来る（M-7 R2: 対称化）
    raw1 = await asyncio.wait_for(ws.recv(), timeout=2.0)
    msg1 = orjson.loads(raw1)
    assert msg1.get("event") == "OrderSubmitted", (
        f"Expected OrderSubmitted first for replay, got: {msg1}"
    )

    raw2 = await asyncio.wait_for(ws.recv(), timeout=2.0)
    msg2 = orjson.loads(raw2)
    assert msg2.get("event") == "OrderRejected", f"Expected OrderRejected, got: {msg2}"
    assert msg2.get("reason_code") == "REPLAY_NOT_IMPLEMENTED", (
        f"Expected REPLAY_NOT_IMPLEMENTED, got: {msg2.get('reason_code')!r}"
    )

    await ws.close()


# ---------------------------------------------------------------------------
# N3 R2-M2: replay venue の CancelOrder/ModifyOrder が unsupported_order_venue を返すこと
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_order_replay_venue_returns_unsupported_order_venue(running_server):
    """venue="replay" の CancelOrder は unsupported_order_venue エラーを返す（R2-M2）。
    replay 注文のキャンセルは N1.15 の UI ガードで事前に抑止されるが、
    IPC に届いた場合は venue ガードで拒否する。"""
    port, token, _ = running_server
    ws = await _connect_and_handshake(port, token)

    req = {
        "op": "CancelOrder",
        "request_id": "req-cancel-replay-001",
        "venue": "replay",
        "client_order_id": "cid-replay-001",
        "venue_order_id": "77777",
    }
    await ws.send(orjson.dumps(req))

    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
    msg = orjson.loads(raw)
    assert msg.get("event") == "Error", f"Expected Error, got: {msg}"
    assert msg.get("code") == "unsupported_order_venue", (
        f"Expected code='unsupported_order_venue', got: {msg.get('code')!r}"
    )
    assert msg["request_id"] == "req-cancel-replay-001"

    await ws.close()


@pytest.mark.asyncio
async def test_modify_order_replay_venue_returns_unsupported_order_venue(running_server):
    """venue="replay" の ModifyOrder は unsupported_order_venue エラーを返す（R2-M2）。
    replay 注文の訂正は N1.15 の UI ガードで事前に抑止されるが、
    IPC に届いた場合は venue ガードで拒否する。"""
    port, token, _ = running_server
    ws = await _connect_and_handshake(port, token)

    req = {
        "op": "ModifyOrder",
        "request_id": "req-modify-replay-001",
        "venue": "replay",
        "client_order_id": "cid-replay-001",
        "change": {"new_quantity": "2"},
    }
    await ws.send(orjson.dumps(req))

    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
    msg = orjson.loads(raw)
    assert msg.get("event") == "Error", f"Expected Error, got: {msg}"
    assert msg.get("code") == "unsupported_order_venue", (
        f"Expected code='unsupported_order_venue', got: {msg.get('code')!r}"
    )
    assert msg["request_id"] == "req-modify-replay-001"

    await ws.close()


# ---------------------------------------------------------------------------
# N3 R2-M3: CancelAllOrders venue guard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_all_orders_hyperliquid_venue_returns_unsupported_order_venue(running_server):
    """venue="hyperliquid" の CancelAllOrders は unsupported_order_venue エラーを返す（R2-M3）。"""
    port, token, _ = running_server
    ws = await _connect_and_handshake(port, token)

    req = {
        "op": "CancelAllOrders",
        "request_id": "req-cancelall-hl-001",
        "venue": "hyperliquid",
    }
    await ws.send(orjson.dumps(req))

    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
    msg = orjson.loads(raw)
    assert msg.get("event") == "Error", f"Expected Error, got: {msg}"
    assert msg.get("code") == "unsupported_order_venue", (
        f"Expected code='unsupported_order_venue', got: {msg.get('code')!r}"
    )
    assert msg["request_id"] == "req-cancelall-hl-001"

    await ws.close()


@pytest.mark.asyncio
async def test_cancel_all_orders_tachibana_venue_proceeds(running_server):
    """venue="tachibana" の CancelAllOrders は unsupported_order_venue を返さず tachibana 経路に進む（R2-M3）。
    セッション未確立なので NOT_LOGGED_IN / SecondPasswordRequired などが返る。"""
    port, token, _ = running_server
    ws = await _connect_and_handshake(port, token)

    req = {
        "op": "CancelAllOrders",
        "request_id": "req-cancelall-tac-001",
        "venue": "tachibana",
    }
    await ws.send(orjson.dumps(req))

    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
    msg = orjson.loads(raw)
    # unsupported_order_venue ではなく tachibana ロジックに進んでいることを確認
    assert msg.get("code") != "unsupported_order_venue", f"unexpected unsupported_order_venue: {msg}"
    # tachibana 固有のエラーが返ることで tachibana 経路に到達したことを確認する
    assert msg.get("event") in ("Error", "SecondPasswordRequired"), (
        f"Unexpected event for tachibana cancel-all: {msg}"
    )

    await ws.close()
