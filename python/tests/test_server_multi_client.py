"""Phase 8.1b B1 — multi-client broadcast tests.

Tests that DataEngineServer supports multiple concurrent clients:
- Events are broadcast to all connected clients.
- Commands from any client are processed (FCFS).
- MAX_CONNECTIONS limit is enforced with 1008 close code.
- ClientConnected / ClientDisconnected events are broadcast.
"""

from __future__ import annotations

import asyncio

import orjson
import pytest
import websockets

from engine.schemas import SCHEMA_MAJOR, SCHEMA_MINOR

pytestmark = pytest.mark.skip(reason="WS transport removed in G3 — covered by test_server_grpc_multi_client.py")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _wait_for_port(port: int, timeout: float = 3.0) -> None:
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
            await asyncio.sleep(0.02)


async def _connect_client(port: int, token: str) -> websockets.ClientConnection:
    """Connect and complete the Hello/Ready handshake."""
    ws = await websockets.connect(
        f"ws://127.0.0.1:{port}/",
        compression=None,
    )
    await ws.send(
        orjson.dumps(
            {
                "op": "Hello",
                "schema_major": SCHEMA_MAJOR,
                "schema_minor": SCHEMA_MINOR,
                "client_version": "test",
                "token": token,
                "mode": "replay",
            }
        ).decode()
    )
    ready = orjson.loads(await ws.recv())
    assert ready["event"] == "Ready", f"Expected Ready, got: {ready}"
    return ws


async def _drain_until(ws, predicate, timeout: float = 3.0):
    """Receive messages until predicate(msg) is True or timeout."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise TimeoutError("predicate not satisfied within timeout")
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        except asyncio.TimeoutError:
            raise TimeoutError("predicate not satisfied within timeout")
        msg = orjson.loads(raw)
        if predicate(msg):
            return msg


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def running_server(unused_tcp_port):
    from unittest.mock import AsyncMock, patch
    from engine.server import DataEngineServer

    token = "multi-client-test-token"

    noop_startup = AsyncMock(return_value=None)
    with patch.object(DataEngineServer, "_startup_tachibana", noop_startup):
        server = DataEngineServer(port=unused_tcp_port, token=token)
        task = asyncio.create_task(server.serve())

        await _wait_for_port(unused_tcp_port)

        yield unused_tcp_port, token

        server.shutdown()
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
            pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_clients_connect(running_server):
    """Two clients can connect simultaneously; both receive an event broadcast."""
    port, token = running_server
    ws1 = await _connect_client(port, token)
    ws2 = await _connect_client(port, token)

    try:
        # Both clients should receive the ClientConnected event for ws2.
        # ws1 may already have the message queued; ws2 received its own too.
        msg2_on_ws2 = await _drain_until(
            ws2, lambda m: m.get("event") == "ClientConnected", timeout=3.0
        )
        assert msg2_on_ws2["count"] >= 1

        # Send a Ping from ws1 — Pong goes only to ws1 (command response).
        await ws1.send(
            orjson.dumps({"op": "Ping", "request_id": "r1"}).decode()
        )
        pong = await _drain_until(
            ws1, lambda m: m.get("event") == "Pong", timeout=3.0
        )
        assert pong["request_id"] == "r1"
    finally:
        await ws1.close()
        await ws2.close()


@pytest.mark.asyncio
async def test_client_disconnect_does_not_affect_other(running_server):
    """After one client disconnects the remaining client still receives events."""
    port, token = running_server
    ws1 = await _connect_client(port, token)
    ws2 = await _connect_client(port, token)

    try:
        # Drain ClientConnected events from ws1.
        await _drain_until(
            ws1, lambda m: m.get("event") == "ClientConnected" and m.get("count", 0) == 2,
            timeout=3.0,
        )

        # Disconnect ws2.
        await ws2.close()

        # ws1 should receive ClientDisconnected.
        msg = await _drain_until(
            ws1, lambda m: m.get("event") == "ClientDisconnected", timeout=3.0
        )
        assert msg["count"] == 1  # only ws1 remains

        # ws1 is still functional — Ping should work.
        await ws1.send(
            orjson.dumps({"op": "Ping", "request_id": "r2"}).decode()
        )
        pong = await _drain_until(ws1, lambda m: m.get("event") == "Pong", timeout=3.0)
        assert pong["request_id"] == "r2"
    finally:
        await ws1.close()


@pytest.mark.asyncio
async def test_max_connections_reject(running_server):
    """The 5th connection is rejected with close code 1008."""
    from engine.server import MAX_CONNECTIONS

    port, token = running_server
    clients = []
    try:
        for _ in range(MAX_CONNECTIONS):
            ws = await _connect_client(port, token)
            clients.append(ws)

        # One more connection beyond the limit.
        ws_extra = await websockets.connect(
            f"ws://127.0.0.1:{port}/",
            compression=None,
        )
        try:
            # M-2 (silent): server now sends an EngineError(code="max_connections")
            # frame before closing with 1008. Drain that frame first; the next recv
            # must raise ConnectionClosedError(1008).
            first = await ws_extra.recv()
            payload = orjson.loads(first)
            assert payload.get("code") == "max_connections", payload
            with pytest.raises(websockets.exceptions.ConnectionClosedError) as exc_info:
                await ws_extra.recv()
            assert exc_info.value.rcvd.code == 1008
        finally:
            try:
                await ws_extra.close()
            except Exception:
                pass
    finally:
        for ws in clients:
            try:
                await ws.close()
            except Exception:
                pass


@pytest.mark.asyncio
async def test_client_connected_event_broadcast(running_server):
    """ClientConnected is broadcast to existing clients when a new one joins."""
    port, token = running_server
    ws1 = await _connect_client(port, token)

    # Drain ws1's own ClientConnected (count=1).
    await _drain_until(
        ws1,
        lambda m: m.get("event") == "ClientConnected" and m.get("count") == 1,
        timeout=3.0,
    )

    ws2 = await _connect_client(port, token)

    try:
        # ws1 should now receive ClientConnected with count=2.
        msg = await _drain_until(
            ws1,
            lambda m: m.get("event") == "ClientConnected" and m.get("count") == 2,
            timeout=3.0,
        )
        assert msg["count"] == 2
    finally:
        await ws1.close()
        await ws2.close()


@pytest.mark.asyncio
async def test_disconnect_does_not_kill_server_lifecycle_for_other_clients(running_server):
    """C1: When ws2 disconnects while ws1 is still connected, the server must
    NOT cancel server-lifecycle resources (Tachibana startup task, EVENT loop,
    startup latch, venue streams). These are shared across all clients;
    cleaning them up on every per-connection finally block destroyed live
    state for the surviving client. Only the last disconnect should clean up.
    """
    from unittest.mock import patch
    from engine.server import DataEngineServer

    port, token = running_server
    ws1 = await _connect_client(port, token)
    ws2 = await _connect_client(port, token)

    # Drain to a known state.
    await _drain_until(
        ws1,
        lambda m: m.get("event") == "ClientConnected" and m.get("count") == 2,
        timeout=3.0,
    )

    # Reach into the live server instance via the connection set. The
    # `running_server` fixture builds a fresh DataEngineServer per test;
    # we discover it through the module-level registry by scanning for
    # the asyncio task. Simpler: capture the server via a class hook.
    # Instead, install spies on the server methods we care about *before*
    # disconnecting ws2, then verify they were NOT called.
    # We look up the server via the only running asyncio task that is
    # serving the port.
    # Simpler approach: spy on _cancel_all_streams at the class level.
    cancel_all_called: list[bool] = []
    real_cancel = DataEngineServer._cancel_all_streams

    async def spy_cancel(self):
        cancel_all_called.append(True)
        return await real_cancel(self)

    try:
        with patch.object(DataEngineServer, "_cancel_all_streams", spy_cancel):
            # Disconnect ws2; ws1 still connected → cleanup must NOT run.
            await ws2.close()

            # Wait for ws1 to observe the ClientDisconnected so we know the
            # finally block has executed.
            msg = await _drain_until(
                ws1,
                lambda m: m.get("event") == "ClientDisconnected",
                timeout=3.0,
            )
            assert msg["count"] == 1

            # ws1 still functional.
            await ws1.send(
                orjson.dumps({"op": "Ping", "request_id": "rA"}).decode()
            )
            pong = await _drain_until(
                ws1, lambda m: m.get("event") == "Pong", timeout=3.0
            )
            assert pong["request_id"] == "rA"

            # The critical assertion: server-lifecycle cleanup did NOT run
            # while ws1 was still connected.
            assert not cancel_all_called, (
                "_cancel_all_streams was called on per-connection disconnect "
                "even though another client was still connected"
            )
    finally:
        await ws1.close()


@pytest.mark.asyncio
async def test_send_loop_survives_send_failure(running_server):
    """H8: ws.send が ConnectionClosed を raise しても他 client の send_loop は継続する。"""
    port, token = running_server
    ws1 = await _connect_client(port, token)
    ws2 = await _connect_client(port, token)

    try:
        # ws1 が両 client が接続済みであることを観測する
        await _drain_until(
            ws1,
            lambda m: m.get("event") == "ClientConnected" and m.get("count") == 2,
            timeout=3.0,
        )

        # ws2 を強制的に切断（abort）— サーバー側 send が失敗するシナリオ
        # close() でクライアントを止め、サーバー側 send が次に走ったとき
        # ConnectionClosed を踏むようにする。
        await ws2.close()

        # ws1 へ broadcast (ClientDisconnected) が届くこと
        await _drain_until(
            ws1,
            lambda m: m.get("event") == "ClientDisconnected",
            timeout=3.0,
        )

        # ws1 はまだ機能すること（Ping/Pong）
        await ws1.send(orjson.dumps({"op": "Ping", "request_id": "h8"}).decode())
        pong = await _drain_until(
            ws1, lambda m: m.get("event") == "Pong", timeout=3.0
        )
        assert pong["request_id"] == "h8"
    finally:
        await ws1.close()


@pytest.mark.asyncio
async def test_client_disconnected_event_broadcast(running_server):
    """ClientDisconnected is broadcast to remaining clients when one disconnects."""
    port, token = running_server
    ws1 = await _connect_client(port, token)
    ws2 = await _connect_client(port, token)

    try:
        # Wait until both are connected (ws1 sees count=2).
        await _drain_until(
            ws1,
            lambda m: m.get("event") == "ClientConnected" and m.get("count") == 2,
            timeout=3.0,
        )

        await ws2.close()

        # ws1 should receive ClientDisconnected with count=1.
        msg = await _drain_until(
            ws1,
            lambda m: m.get("event") == "ClientDisconnected",
            timeout=3.0,
        )
        assert msg["count"] == 1
    finally:
        await ws1.close()
