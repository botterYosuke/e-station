"""WebSocket frame compatibility tests.

Ensures the server never negotiates permessage-deflate with clients, because
fastwebsockets (the Rust IPC client) sends Sec-WebSocket-Extensions in its
HTTP upgrade request but does not implement a decompressor.  When the server
agrees to compression it sends RSV1=1 frames that fastwebsockets rejects with
"Reserved bits are not zero", dropping the IPC connection on every startup.

Root cause: websockets.serve() defaults to compression="deflate".
Fix:        websockets.serve(..., compression=None) in server.py.

Regression test: removing compression=None from server.py causes
test_server_refuses_permessage_deflate to fail.
"""

from __future__ import annotations

import asyncio
import socket
from unittest.mock import AsyncMock, MagicMock, patch

import orjson
import pytest
import websockets

from engine.schemas import SCHEMA_MAJOR, SCHEMA_MINOR


# ── Fixture ───────────────────────────────────────────────────────────────────


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

    server = DataEngineServer(port=port, token="compat-token")
    task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.1)

    yield port, "compat-token", server

    server.shutdown()
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass
    for p in patches:
        p.stop()


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _handshake(port: int, token: str, **connect_kwargs) -> websockets.ClientConnection:
    ws = await websockets.connect(f"ws://127.0.0.1:{port}", **connect_kwargs)
    hello = {
        "op": "Hello",
        "schema_major": SCHEMA_MAJOR,
        "schema_minor": SCHEMA_MINOR,
        "client_version": "compat-test",
        "token": token,
    }
    await ws.send(orjson.dumps(hello))
    raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
    msg = orjson.loads(raw)
    assert msg["event"] == "Ready", f"Expected Ready, got: {msg}"
    return ws


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_server_refuses_permessage_deflate(server_port):
    """Server MUST NOT negotiate permessage-deflate even when the client offers it.

    fastwebsockets sends Sec-WebSocket-Extensions: permessage-deflate in its
    HTTP upgrade request but cannot decompress incoming frames.  If the server
    agrees, it starts sending RSV1=1 frames that fastwebsockets rejects with
    "Reserved bits are not zero", dropping the IPC connection on every startup.

    This test fails when compression=None is removed from websockets.serve().
    """
    port, token, _server = server_port

    # Connect with the default compression="deflate" — client offers permessage-deflate.
    ws = await _handshake(port, token)

    active_extensions = ws.protocol.extensions
    has_deflate = any(
        "deflate" in type(ext).__name__.lower() for ext in active_extensions
    )

    await ws.close()

    assert not has_deflate, (
        f"Server negotiated permessage-deflate: {active_extensions}.\n"
        "This enables RSV1=1 compressed frames that fastwebsockets rejects.\n"
        "Fix: add compression=None to websockets.serve() in engine/server.py."
    )


@pytest.mark.asyncio
async def test_ping_pong_survives_without_client_compression(server_port):
    """Full Ping/Pong round-trip works when the client opts out of compression.

    Simulates a minimal client (like fastwebsockets before it sends any extension
    headers): connects with compression=None, completes the Hello/Ready handshake,
    then verifies Ping is answered with Pong.  If the server were sending RSV1=1
    frames the recv() call would raise a websockets.ProtocolError.
    """
    port, token, _server = server_port

    ws = await _handshake(port, token, compression=None)

    await ws.send(orjson.dumps({"op": "Ping", "request_id": "compat-ping"}))
    raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
    msg = orjson.loads(raw)

    await ws.close()

    assert msg.get("event") == "Pong", f"Expected Pong, got: {msg}"
    assert msg.get("request_id") == "compat-ping"


@pytest.mark.asyncio
async def test_tachibana_startup_skipped_in_replay_mode():
    """In replay mode, _startup_tachibana() must NOT be called after handshake.

    Root cause: _startup_tachibana() was unconditionally scheduled as an
    asyncio task after every handshake.  In replay mode with dev credentials
    set, Tachibana auto-login succeeded → VenueReady → FetchTickerStats("__all__")
    → 278 KB response frame → fastwebsockets rejected it with "Reserved bits are
    not zero", dropping the IPC connection before /api/replay/start was processed.

    Fix: `if self._mode != "replay":` guard before
    `asyncio.create_task(self._startup_tachibana())` in server.py.

    This test FAILS when that guard is removed.
    """
    from engine.server import DataEngineServer

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    mock_worker = MagicMock()
    mock_worker.prepare = AsyncMock(return_value=None)
    mock_startup = AsyncMock()

    patches = [
        patch("engine.server.BinanceWorker", return_value=mock_worker),
        patch("engine.server.BybitWorker", return_value=mock_worker),
        patch("engine.server.HyperliquidWorker", return_value=mock_worker),
        patch("engine.server.MexcWorker", return_value=mock_worker),
        patch("engine.server.OkexWorker", return_value=mock_worker),
        patch.object(DataEngineServer, "_startup_tachibana", mock_startup),
    ]
    for p in patches:
        p.start()

    server = DataEngineServer(port=port, token="replay-guard-token")
    task = asyncio.create_task(server.serve())
    await asyncio.sleep(0.1)

    try:
        async with websockets.connect(
            f"ws://127.0.0.1:{port}/",
            compression=None,
        ) as ws:
            hello = {
                "op": "Hello",
                "schema_major": SCHEMA_MAJOR,
                "schema_minor": SCHEMA_MINOR,
                "client_version": "replay-guard-test",
                "token": "replay-guard-token",
                "mode": "replay",
            }
            await ws.send(orjson.dumps(hello))
            raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
            msg = orjson.loads(raw)
            assert msg["event"] == "Ready", f"Expected Ready, got: {msg}"
            await asyncio.sleep(0.1)
    finally:
        server.shutdown()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        for p in patches:
            p.stop()

    assert not mock_startup.called, (
        "_startup_tachibana() must not be called in replay mode.\n"
        "Fix: add `if self._mode != 'replay':` guard before\n"
        "`asyncio.create_task(self._startup_tachibana())` in server.py."
    )


@pytest.mark.asyncio
async def test_large_frame_payload_does_not_set_rsv1(server_port):
    """A single >300 KB outbox event must flow end-to-end without protocol error.

    Regression guard for 2026-04-30: a 278 KB FetchTickerStats response from
    Python broke fastwebsockets with "Reserved bits are not zero" and dropped
    the IPC connection.  The replay-side fix (skip `_startup_tachibana()` in
    replay mode) only avoids the *trigger*; if venue count grows and a live
    bulk-fetch response crosses the same threshold, the bug returns.

    This test pins the IPC pipeline contract that large single frames are
    transmissible: it injects a 320 KB synthetic event into the outbox and
    asserts the client receives it intact, with the same `compression=None`
    handshake fastwebsockets uses.  Companion to
    `test_server_refuses_permessage_deflate` (which pins that the server
    never agrees to RSV1=1 deflate even when offered).
    """
    port, token, server = server_port

    ws = await _handshake(port, token, compression=None)

    big_payload = "x" * 320_000  # 320 KB > the 278 KB bug threshold.
    server._outbox.append(
        {"event": "Pong", "request_id": "large-frame-probe", "_pad": big_payload}
    )

    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
    msg = orjson.loads(raw)

    await ws.close()

    assert msg.get("event") == "Pong", f"Expected Pong, got: {msg!r}"
    assert msg.get("request_id") == "large-frame-probe"
    assert len(msg.get("_pad", "")) == 320_000, "payload size mismatch"
