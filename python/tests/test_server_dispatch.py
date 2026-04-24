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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def running_server(unused_tcp_port):
    """Start DataEngineServer on a random port, yield (port, token, mock_worker), then stop."""
    from engine.server import DataEngineServer

    token = "test-token-abc123"
    mock_worker = _make_mock_worker()

    # Patch BinanceWorker so no real network calls happen
    with patch("engine.server.BinanceWorker", return_value=mock_worker):
        server = DataEngineServer(port=unused_tcp_port, token=token)
        task = asyncio.create_task(server.serve())

        await asyncio.sleep(0.1)  # let server start

        yield unused_tcp_port, token, mock_worker

        server.shutdown()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


def _make_mock_worker():
    worker = MagicMock()
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

    with patch("engine.server.BinanceWorker", return_value=mock_worker):
        server = DataEngineServer(port=unused_tcp_port, token=token)
        task = asyncio.create_task(server.serve())
        await asyncio.sleep(0.1)

        ws = await _connect_and_handshake(unused_tcp_port, token)

        await ws.send(orjson.dumps({
            "op": "RequestDepthSnapshot",
            "venue": "binance",
            "ticker": "BTCUSDT",
            "stream_session_id": "sess-1",
        }))

        # No Error event should arrive — timeout IS the success path.
        with pytest.raises(asyncio.TimeoutError):
            raw = await asyncio.wait_for(ws.recv(), timeout=0.2)
            msg = orjson.loads(raw)
            assert msg.get("event") != "Error", f"Unexpected Error event: {msg}"

        await ws.close()
        server.shutdown()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
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
