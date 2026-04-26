"""B3: TachibanaError / VenueCapabilityError → outbox Error mapping.

When a worker raises a typed `TachibanaError` (notably `VenueCapabilityError`
from `fetch_klines("5m")`), the dispatcher must surface the worker-side `code`
verbatim so the Rust UI can branch without parsing the message string.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest
import websockets

from engine.exchanges.tachibana import VenueCapabilityError
from engine.schemas import SCHEMA_MAJOR, SCHEMA_MINOR


@pytest.fixture
def unused_tcp_port():
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_dummy_worker():
    w = MagicMock()
    w.prepare = AsyncMock(return_value=None)
    return w


@pytest.fixture
async def running_server(unused_tcp_port, tmp_path):
    from engine.server import DataEngineServer

    token = "err-map-test"
    tachibana_mock = MagicMock()
    tachibana_mock.prepare = AsyncMock(return_value=None)
    tachibana_mock.capabilities = MagicMock(
        return_value={"supported_timeframes": ["1d"]}
    )
    tachibana_mock.fetch_klines = AsyncMock(
        side_effect=VenueCapabilityError(
            code="not_implemented",
            message="tachibana supports 1d only in Phase 1",
        )
    )

    with (
        patch("engine.server.BinanceWorker", return_value=_make_dummy_worker()),
        patch("engine.server.BybitWorker", return_value=_make_dummy_worker()),
        patch("engine.server.HyperliquidWorker", return_value=_make_dummy_worker()),
        patch("engine.server.MexcWorker", return_value=_make_dummy_worker()),
        patch("engine.server.OkexWorker", return_value=_make_dummy_worker()),
        patch("engine.server.TachibanaWorker", return_value=tachibana_mock),
    ):
        server = DataEngineServer(
            port=unused_tcp_port, token=token, cache_dir=tmp_path
        )
        task = asyncio.create_task(server.serve())
        await asyncio.sleep(0.1)
        try:
            yield unused_tcp_port, token
        finally:
            server.shutdown()
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


async def _connect(port: int, token: str) -> websockets.ClientConnection:
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
    assert msg["event"] == "Ready"
    return ws


@pytest.mark.asyncio
async def test_venue_capability_error_maps_to_not_implemented_outbox(running_server):
    port, token = running_server
    ws = await _connect(port, token)
    try:
        req_id = "req-cap-1"
        await ws.send(
            orjson.dumps(
                {
                    "op": "FetchKlines",
                    "request_id": req_id,
                    "venue": "tachibana",
                    "ticker": "7203",
                    "market": "stock",
                    "timeframe": "5m",
                    "limit": 100,
                }
            )
        )
        # Server may emit several events — find the matching Error.
        for _ in range(10):
            raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
            msg = orjson.loads(raw)
            if msg.get("event") == "Error" and msg.get("request_id") == req_id:
                assert msg["code"] == "not_implemented", msg
                assert "1d" in msg["message"]
                return
        pytest.fail("did not receive matching Error event for FetchKlines")
    finally:
        await ws.close()
