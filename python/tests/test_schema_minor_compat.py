"""SCHEMA_MINOR 差異許容のリグレッションガード（F6 / レビュー反映 2026-05-04 ラウンド1, H5）。

P5-scenario-in-strategy.md L120-121 が必須化する不変条件:
  - SCHEMA_MAJOR 不一致 → ハンドシェイク失敗（schema_mismatch で 1008 close）
  - SCHEMA_MINOR 差異   → WARN ログのみ、接続成立

`test_server_ws_compat.py` の handshake パターンを踏襲する。
"""

from __future__ import annotations

import asyncio
import socket
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest
import websockets

from engine.schemas import SCHEMA_MAJOR, SCHEMA_MINOR


@pytest.fixture
async def server_port():
    """Start a real DataEngineServer on a random port with all workers mocked."""
    from engine.server import DataEngineServer

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    mock_worker = MagicMock()
    mock_worker.prepare = AsyncMock(return_value=None)

    patches = [
        patch("engine.server.BinanceWorker", return_value=mock_worker),
        patch("engine.server.BybitWorker", return_value=mock_worker),
        patch("engine.server.HyperliquidWorker", return_value=mock_worker),
        patch("engine.server.MexcWorker", return_value=mock_worker),
        patch("engine.server.OkexWorker", return_value=mock_worker),
    ]
    for p in patches:
        p.start()

    server = DataEngineServer(port=port, token="schema-compat-token")
    task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.1)

    yield port, "schema-compat-token", server

    server.shutdown()
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
    for p in patches:
        p.stop()


async def _send_hello(
    port: int, token: str, *, schema_major: int, schema_minor: int
) -> tuple[websockets.ClientConnection, dict]:
    """Hello を送って最初の応答 frame を受け取る。Ready / EngineError 区別はテスト側で。"""
    ws = await websockets.connect(f"ws://127.0.0.1:{port}", compression=None)
    hello = {
        "op": "Hello",
        "schema_major": schema_major,
        "schema_minor": schema_minor,
        "client_version": "schema-compat-test",
        "token": token,
    }
    await ws.send(orjson.dumps(hello))
    raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
    msg = orjson.loads(raw)
    return ws, msg


@pytest.mark.asyncio
async def test_schema_major_mismatch_rejects_handshake(server_port):
    """SCHEMA_MAJOR 不一致 → EngineError(code=schema_mismatch) を返し接続を切る。"""
    port, token, _server = server_port

    ws, msg = await _send_hello(
        port, token, schema_major=SCHEMA_MAJOR + 1, schema_minor=SCHEMA_MINOR
    )
    assert msg.get("event") == "EngineError", f"Expected EngineError, got: {msg}"
    assert msg.get("code") == "schema_mismatch", f"Expected schema_mismatch: {msg}"
    # サーバーは 1008 で close する。次の recv は close を観測する。
    try:
        await asyncio.wait_for(ws.recv(), timeout=2.0)
    except (websockets.ConnectionClosed, asyncio.TimeoutError):
        pass
    await ws.close()


@pytest.mark.asyncio
async def test_schema_minor_lower_accepts_handshake(server_port):
    """SCHEMA_MINOR=0 でも SCHEMA_MAJOR 一致なら Ready を返し接続成立。"""
    port, token, _server = server_port

    ws, msg = await _send_hello(
        port, token, schema_major=SCHEMA_MAJOR, schema_minor=0
    )
    assert msg.get("event") == "Ready", f"Expected Ready, got: {msg}"
    assert msg.get("schema_major") == SCHEMA_MAJOR
    await ws.close()


@pytest.mark.asyncio
async def test_schema_minor_higher_accepts_handshake(server_port):
    """SCHEMA_MINOR=999（未来 minor）でも SCHEMA_MAJOR 一致なら Ready を返す。"""
    port, token, _server = server_port

    ws, msg = await _send_hello(
        port, token, schema_major=SCHEMA_MAJOR, schema_minor=999
    )
    assert msg.get("event") == "Ready", f"Expected Ready, got: {msg}"
    assert msg.get("schema_major") == SCHEMA_MAJOR
    await ws.close()
