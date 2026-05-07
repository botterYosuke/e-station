"""M-TA6 / R2-H2: `Hello.mode` mismatch reject.

Split from test_review_fixes.py (Phase 8 R1 / Phase 5).
"""

from __future__ import annotations

import asyncio
import socket
import threading

import pytest


@pytest.mark.skip(
    reason="WS transport WebSocket mock removed — mode_mismatch for gRPC is covered by "
    "test_server_grpc_multi_client.py::test_mode_mismatch_second_client_returns_failed_precondition"
)
def test_handle_hello_mode_mismatch_rejects_second_client():
    """M-TA6: mode=live で接続済みの後、mode=replay で接続すると EngineError が返ること。

    このテストは WS transport 向けに書かれた仕様テストだが、
    gRPC 移行後は test_server_grpc_multi_client.py でカバーされる。
    """
    pass  # 実際のサーバー統合テストは test_server_grpc_multi_client.py で行う


# ---------------------------------------------------------------------------
# R2-H2: M-TA6 mode mismatch テスト（実際の DataEngineServer を使う）
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="WS transport removed in G3 — mode_mismatch covered by gRPC FAILED_PRECONDITION in test_grpc_smoke.py")
def test_handle_hello_mode_mismatch_with_real_server():
    """R2-H2 / M-TA6: 実際の DataEngineServer を使って mode mismatch を検証する。

    mode=replay で接続済みのサーバーに mode=live で接続すると
    EngineError{code="mode_mismatch"} が返ること。
    """
    import orjson
    from engine.schemas import SCHEMA_MAJOR, SCHEMA_MINOR
    from engine.replay_session import _AttachClient

    TOKEN = "test-token-mismatch"
    server_started = threading.Event()
    shutdown_holder: list = []
    chosen_port: list[int] = []

    async def _run_server():
        import websockets
        from engine.server import DataEngineServer
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        chosen_port.append(port)
        srv = DataEngineServer(
            token=TOKEN,
            port=port,
            dev_tachibana_login_allowed=False,
        )
        shutdown_holder.append(srv)
        # serve の中で listen が始まる前に set すると race になるため、
        # websockets.serve の context manager 内で set する
        stop_event = asyncio.Event()

        async def _serve():
            async with websockets.serve(
                srv._handle,
                "127.0.0.1",
                port,
                compression=None,
            ):
                server_started.set()
                await stop_event.wait()

        stop_event_holder.append(stop_event)
        await _serve()

    stop_event_holder: list = []
    loop = asyncio.new_event_loop()
    srv_thread = threading.Thread(
        target=lambda: loop.run_until_complete(_run_server()), daemon=True
    )
    srv_thread.start()
    server_started.wait(timeout=5.0)
    assert chosen_port, "server did not start"

    port = chosen_port[0]
    endpoint = f"ws://127.0.0.1:{port}/"

    try:
        # 1. mode=replay で最初の接続 → Ready を受け取る
        client1 = _AttachClient(endpoint, TOKEN, 5.0)
        client1.handshake()

        # 2. mode=live (不一致) で2番目の接続を試みる
        second_client_result: list[dict] = []
        second_done = threading.Event()

        async def _connect_second():
            import websockets
            try:
                async with websockets.connect(endpoint, compression=None, open_timeout=5.0) as ws:
                    hello = {
                        "op": "Hello",
                        "schema_major": SCHEMA_MAJOR,
                        "schema_minor": SCHEMA_MINOR,
                        "client_version": "test",
                        "token": TOKEN,
                        "mode": "live",  # サーバーは replay mode なのでミスマッチ
                    }
                    await ws.send(orjson.dumps(hello).decode())
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        msg = orjson.loads(raw)
                        second_client_result.append(msg)
                    except Exception:
                        pass
            except Exception:
                pass
            finally:
                second_done.set()

        second_loop = asyncio.new_event_loop()
        second_thread = threading.Thread(
            target=lambda: second_loop.run_until_complete(_connect_second()), daemon=True
        )
        second_thread.start()
        second_done.wait(timeout=8.0)
        second_thread.join(timeout=2.0)

        # mode mismatch の場合 EngineError が返ること
        assert second_client_result, \
            "second client received no response (expected EngineError for mode_mismatch)"
        msg = second_client_result[0]
        assert msg.get("event") in ("EngineError", "Error"), \
            f"expected EngineError for mode mismatch; got: {msg}"
        if msg.get("event") == "EngineError":
            assert msg.get("code") == "mode_mismatch", \
                f"expected code='mode_mismatch'; got: {msg}"

        client1.close()
    finally:
        # サーバーを停止する
        if stop_event_holder:
            loop.call_soon_threadsafe(stop_event_holder[0].set)
        srv_thread.join(timeout=3.0)
