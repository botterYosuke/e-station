"""B3: Ready.capabilities exposes per-venue capability blocks (plan §T4 L548).

The Rust UI consumes ``capabilities.venue_capabilities[<venue>]`` to pre-disable
features the worker would reject (e.g. non-``"1d"`` timeframes for Tachibana).
The legacy flat keys must keep working for older clients that haven't been
recompiled against the structured helper.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest
import websockets

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
    """Start a real DataEngineServer (with TachibanaWorker registered)."""
    from engine.server import DataEngineServer

    token = "cap-test-token"
    # Patch every non-tachibana worker so init does no work; leave the
    # tachibana worker real so we exercise its capabilities() override.
    with (
        patch("engine.server.BinanceWorker", return_value=_make_dummy_worker()),
        patch("engine.server.BybitWorker", return_value=_make_dummy_worker()),
        patch("engine.server.HyperliquidWorker", return_value=_make_dummy_worker()),
        patch("engine.server.MexcWorker", return_value=_make_dummy_worker()),
        patch("engine.server.OkexWorker", return_value=_make_dummy_worker()),
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


async def _read_ready(port: int, token: str) -> dict:
    ws = await websockets.connect(f"ws://127.0.0.1:{port}")
    try:
        hello = {
            "op": "Hello",
            "schema_major": SCHEMA_MAJOR,
            "schema_minor": SCHEMA_MINOR,
            "client_version": "test",
            "token": token,
        }
        await ws.send(orjson.dumps(hello))
        raw = await ws.recv()
    finally:
        await ws.close()
    return orjson.loads(raw)


@pytest.mark.asyncio
async def test_ready_capabilities_includes_tachibana_supported_timeframes(
    running_server,
):
    port, token = running_server
    msg = await _read_ready(port, token)
    assert msg["event"] == "Ready"
    caps = msg["capabilities"]
    assert "venue_capabilities" in caps, caps
    assert caps["venue_capabilities"]["tachibana"]["supported_timeframes"] == ["1d"]


@pytest.mark.asyncio
async def test_ready_capabilities_does_not_break_legacy_keys(running_server):
    port, token = running_server
    msg = await _read_ready(port, token)
    caps = msg["capabilities"]
    # Older Rust clients still read these flat keys directly — preserve them.
    assert "supported_venues" in caps
    assert "supports_bulk_trades" in caps
    assert "supports_depth_binary" in caps
    assert "tachibana" in caps["supported_venues"]
