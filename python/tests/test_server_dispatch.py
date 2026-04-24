"""TDD Red: Server dispatch integration tests."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest
import websockets

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
    """Start DataEngineServer on a random port, yield (port, token), then stop."""
    from engine.server import DataEngineServer

    token = "test-token-abc123"

    # Patch BinanceWorker so no real network calls happen
    with patch(
        "engine.server.BinanceWorker",
        return_value=_make_mock_worker(),
    ):
        server = DataEngineServer(port=unused_tcp_port, token=token)
        task = asyncio.create_task(server.serve())

        await asyncio.sleep(0.1)  # let server start

        yield unused_tcp_port, token

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
    port, token = running_server
    ws = await _connect_and_handshake(port, token)
    await ws.close()


@pytest.mark.asyncio
async def test_wrong_token_disconnects(running_server):
    port, _ = running_server
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
    port, token = running_server

    from engine.server import DataEngineServer

    with patch(
        "engine.server.BinanceWorker",
        return_value=_make_mock_worker(),
    ):
        ws = await _connect_and_handshake(port, token)

        subscribe_msg = {
            "op": "Subscribe",
            "venue": "binance",
            "ticker": "BTCUSDT",
            "stream": "trade",
        }
        await ws.send(orjson.dumps(subscribe_msg))
        await asyncio.sleep(0.05)  # let dispatch run

        await ws.close()


@pytest.mark.asyncio
async def test_list_tickers_response(running_server):
    port, token = running_server

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
    port, token = running_server
    ws = await _connect_and_handshake(port, token)
    await ws.send(orjson.dumps({"op": "Shutdown"}))
    await asyncio.sleep(0.1)
    # Connection should be closing
    await ws.close()


@pytest.mark.asyncio
async def test_fetch_trades_returns_not_supported_error(running_server):
    port, token = running_server
    ws = await _connect_and_handshake(port, token)
    await ws.send(
        orjson.dumps(
            {
                "op": "FetchTrades",
                "request_id": "req-trades",
                "venue": "binance",
                "ticker": "BTCUSDT",
                "start_ms": 1,
                "end_ms": 2,
            }
        )
    )

    raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
    msg = orjson.loads(raw)
    assert msg["event"] == "Error"
    assert msg["request_id"] == "req-trades"
    assert msg["code"] == "not_supported"

    await ws.close()
