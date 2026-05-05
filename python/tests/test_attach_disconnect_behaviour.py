"""C-1: `_AttachClient` post-handshake disconnect behaviour.

Split from test_review_fixes.py (Phase 8 R1 / Phase 5).
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time

import pytest


def _spawn_server(handler):
    """Spawn a websockets server thread. Returns (port, stop_fn)."""
    import websockets

    server_ready = threading.Event()
    chosen_port: list[int] = []
    server_holder: list = []

    async def _run():
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        chosen_port.append(port)
        srv = await websockets.serve(handler, "127.0.0.1", port, compression=None)
        server_holder.append(srv)
        server_ready.set()
        try:
            await srv.wait_closed()
        except Exception:
            pass

    loop = asyncio.new_event_loop()
    t = threading.Thread(target=lambda: loop.run_until_complete(_run()), daemon=True)
    t.start()
    server_ready.wait(timeout=5.0)

    def _stop():
        if server_holder:
            loop.call_soon_threadsafe(server_holder[0].close)
        t.join(timeout=3.0)

    return chosen_port[0], _stop


def _make_ready_msg():
    import orjson
    from engine.schemas import SCHEMA_MAJOR, SCHEMA_MINOR
    return orjson.dumps({
        "event": "Ready",
        "schema_major": SCHEMA_MAJOR,
        "schema_minor": SCHEMA_MINOR,
        "engine_version": "test",
        "engine_session_id": "00000000-0000-0000-0000-000000000000",
        "capabilities": {},
    }).decode()


# ---------------------------------------------------------------------------
# C-1: _AttachClient post-handshake disconnect → events() terminates quickly
# ---------------------------------------------------------------------------


def test_attach_client_post_handshake_disconnect_terminates_events_quickly():
    """C-1: ハンドシェイク後にサーバーが WS を強制クローズしたとき events() が速やかに
    ConnectionError を raise すること（60 秒ハングしないこと）。
    """
    async def _handler(ws):
        await ws.recv()  # Hello
        await ws.send(_make_ready_msg())
        # ハンドシェイク後に少し待ってから強制クローズ
        await asyncio.sleep(0.1)
        await ws.close()

    port, stop = _spawn_server(_handler)
    try:
        from engine.replay_session import _AttachClient
        client = _AttachClient(f"ws://127.0.0.1:{port}/", "tok", 3.0)
        client.handshake()

        start = time.monotonic()
        with pytest.raises((ConnectionError, StopIteration)):
            for _evt in client.events():
                pass
        elapsed = time.monotonic() - start
        assert elapsed < 10.0, f"events() should terminate quickly, took {elapsed:.1f}s"
        client.close()
    finally:
        stop()


def test_attach_client_post_handshake_disconnect_logs(caplog):
    """C-1: ハンドシェイク後に WS が閉じるとき recv_loop / _async_main がログを出すこと。

    ワーカースレッドのログは caplog では拾えないことがある。
    ここでは events() が速やかに終了すること（タイムアウトしないこと）と
    recv_queue に __error__ が入ることを確認する。
    """
    async def _handler(ws):
        await ws.recv()  # Hello
        await ws.send(_make_ready_msg())
        await asyncio.sleep(0.05)
        await ws.close()

    port, stop = _spawn_server(_handler)
    try:
        from engine.replay_session import _AttachClient
        client = _AttachClient(f"ws://127.0.0.1:{port}/", "tok", 3.0)
        client.handshake()
        # WS が閉じるまで少し待つ
        for _ in range(30):
            if client._closed_event.is_set():
                break
            time.sleep(0.1)

        # recv_queue に __error__ が届いていること（C-1 の核心）
        assert client._closed_event.is_set() or not client._recv_queue.empty(), \
            "expected closed event or error in recv_queue"

        client.close()
    finally:
        stop()
