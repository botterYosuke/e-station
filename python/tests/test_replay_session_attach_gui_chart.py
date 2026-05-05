"""Phase 8.1b 穴1リグレッション保護 — GUI client と helper の三者 broadcast テスト。

engine (DataEngineServer) + 模擬 GUI WS client + helper (_AttachClient) の三者で
event が両 client に届くことを assert する。

穴1 (hole 1): multi-client 実装前は helper が接続すると既存 GUI 接続が上書きされ
event が GUI に届かなくなっていた。このテストでその回帰を検出する。

NautilusRunner は起動しない（J-Quants データ不要）。DataEngineServer の
ClientConnected / ClientDisconnected broadcast のみで broadcast 機能を検証する。

設計: DataEngineServer と GUI client はサーバーの event loop で動かす。
     _AttachClient は専用スレッドで動かす。複数の asyncio.run() 共存を避ける。
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from unittest.mock import AsyncMock, patch

import orjson
import pytest
import websockets

from engine.replay_session import _AttachClient
from engine.schemas import SCHEMA_MAJOR, SCHEMA_MINOR


# ---------------------------------------------------------------------------
# Server / GUI helpers
# ---------------------------------------------------------------------------


def _start_engine_server(port: int, token: str):
    """DataEngineServer を背景スレッドで起動する。Returns (server_loop, stop_fn)."""
    loop = asyncio.new_event_loop()
    ready_event = threading.Event()
    server_ref: list = []
    stop_event = asyncio.Event()

    async def _run():
        from engine.server import DataEngineServer

        noop = AsyncMock(return_value=None)
        with patch.object(DataEngineServer, "_startup_tachibana", noop):
            server = DataEngineServer(port=port, token=token)
            server_ref.append(server)
            task = asyncio.create_task(server.serve())
            # ポートが開くまで待つ（raw TCP でチェックせずポーリング）
            deadline = loop.time() + 8.0
            while True:
                try:
                    _, w = await asyncio.open_connection("127.0.0.1", port)
                    w.close()
                    await w.wait_closed()
                    break
                except OSError:
                    if loop.time() >= deadline:
                        raise TimeoutError(f"port {port} not open after 8s")
                    await asyncio.sleep(0.02)
            ready_event.set()
            # stop_event が set されるまで待機
            await stop_event.wait()
            server.shutdown()
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=3.0)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass

    thread = threading.Thread(target=lambda: loop.run_until_complete(_run()), daemon=True)
    thread.start()
    ready_event.wait(timeout=10.0)

    def _stop():
        # stop_event をセットしてサーバーを終了させる
        loop.call_soon_threadsafe(stop_event.set)
        thread.join(timeout=5.0)

    return loop, _stop


def _start_gui_client(server_loop: asyncio.AbstractEventLoop, port: int, token: str,
                      collect_events: list, done_event: threading.Event):
    """GUI client のコルーチンを server_loop 上で起動し、Future を返す。

    server_loop で動かすことで複数 asyncio.run() の競合を防ぐ。
    done_event は Hello/Ready ハンドシェイク完了後に set される。
    """
    async def _gui_coro():
        ws = await websockets.connect(f"ws://127.0.0.1:{port}/", compression=None)
        await ws.send(
            orjson.dumps({
                "op": "Hello",
                "schema_major": SCHEMA_MAJOR,
                "schema_minor": SCHEMA_MINOR,
                "client_version": "gui-sim",
                "token": token,
                "mode": "replay",
            }).decode()
        )
        ready = orjson.loads(await ws.recv())
        assert ready["event"] == "Ready", f"Expected Ready, got {ready}"
        done_event.set()
        # イベントを収集する（ClientDisconnected または timeout で終了）
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=8.0)
                evt = orjson.loads(raw)
                collect_events.append(evt)
        except (asyncio.TimeoutError, Exception):
            pass
        finally:
            try:
                await ws.close()
            except Exception:
                pass

    future = asyncio.run_coroutine_threadsafe(_gui_coro(), server_loop)
    return future


# ---------------------------------------------------------------------------
# テスト
# ---------------------------------------------------------------------------


def test_gui_and_helper_both_receive_client_connected_broadcast():
    """穴1リグレッション: GUI client が接続中に helper が接続すると、
    GUI は ClientConnected(count>=2) broadcast を受信する。

    Before multi-client: helper の接続が GUI 接続を上書きして broadcast が届かなかった。
    After multi-client: 両者が共存し、GUI に helper の接続が broadcast される。
    """
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    token = "gui-chart-broadcast-token"
    server_loop, stop_server = _start_engine_server(port, token)

    gui_events: list = []
    gui_connected = threading.Event()

    # GUI client を server_loop 上で起動
    gui_future = _start_gui_client(server_loop, port, token, gui_events, gui_connected)
    assert gui_connected.wait(timeout=5.0), "GUI client がハンドシェイクを完了できなかった"

    try:
        # helper を接続
        helper = _AttachClient(f"ws://127.0.0.1:{port}/", token, 3.0)
        helper.handshake()

        try:
            # GUI が ClientConnected(count>=2) を受信するまで待つ
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if any(
                    e.get("event") == "ClientConnected" and e.get("count", 0) >= 2
                    for e in gui_events
                ):
                    break
                time.sleep(0.05)

            assert any(
                e.get("event") == "ClientConnected" and e.get("count", 0) >= 2
                for e in gui_events
            ), f"GUI は helper 接続時に ClientConnected(count>=2) を受信するべき: {gui_events}"

            # helper を切断すると GUI に ClientDisconnected が届く
            helper.close()

            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if any(e.get("event") == "ClientDisconnected" for e in gui_events):
                    break
                time.sleep(0.05)

            assert any(
                e.get("event") == "ClientDisconnected" for e in gui_events
            ), f"GUI は helper 切断時に ClientDisconnected を受信するべき: {gui_events}"

        finally:
            try:
                helper.close()
            except Exception:
                pass
    finally:
        stop_server()


def test_helper_receives_client_connected_for_gui():
    """helper が接続済みの状態で GUI が接続すると helper も ClientConnected を受信する。

    broadcast は全接続方向に対称であることを確認する。

    NOTE: server は Ready を送った後に ClientConnected(count=1) を helper に届ける。
    このため recv_queue には helper 自身の接続分の count=1 が入る可能性があり、
    GUI 接続分の count=2 が来るまで複数 ClientConnected を読み進める必要がある。
    """
    import queue as q_module

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    token = "gui-chart-symmetric-token"
    server_loop, stop_server = _start_engine_server(port, token)

    try:
        # helper を先に接続
        helper = _AttachClient(f"ws://127.0.0.1:{port}/", token, 3.0)
        helper.handshake()

        try:
            # GUI client を後から接続（server_loop で動かす）
            gui_events: list = []
            gui_connected = threading.Event()
            gui_future = _start_gui_client(server_loop, port, token, gui_events, gui_connected)
            assert gui_connected.wait(timeout=5.0), "GUI client がハンドシェイクを完了できなかった"

            # helper の recv_queue から ClientConnected(count>=2) を探す。
            # helper 自身の接続分 ClientConnected(count=1) が先に来ることがあるため、
            # count>=2 が見つかるまでキューを消費する。
            deadline = time.monotonic() + 5.0
            found = False
            skipped = []
            while time.monotonic() < deadline:
                try:
                    msg = helper._recv_queue.get(timeout=0.1)
                    if msg.get("event") == "ClientConnected" and msg.get("count", 0) >= 2:
                        found = True
                        break
                    skipped.append(msg)
                except q_module.Empty:
                    pass

            # 読み飛ばしたメッセージをキューに戻す
            for m in skipped:
                helper._recv_queue.put_nowait(m)

            assert found, \
                f"helper は GUI 接続時に ClientConnected(count>=2) を受信するべき" \
                f" (skipped={skipped})"
        finally:
            helper.close()
    finally:
        stop_server()


def test_gui_survives_helper_disconnect():
    """helper が切断しても GUI の接続は維持され、ClientDisconnected が届く。"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    token = "gui-chart-survive-token"
    server_loop, stop_server = _start_engine_server(port, token)

    gui_events: list = []
    gui_connected = threading.Event()

    gui_future = _start_gui_client(server_loop, port, token, gui_events, gui_connected)
    assert gui_connected.wait(timeout=5.0), "GUI client がハンドシェイクを完了できなかった"

    try:
        # helper 接続 → 即切断
        helper = _AttachClient(f"ws://127.0.0.1:{port}/", token, 3.0)
        helper.handshake()
        helper.close()

        # GUI は ClientDisconnected を受信する（接続が切れていれば届かない）
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if any(e.get("event") == "ClientDisconnected" for e in gui_events):
                break
            time.sleep(0.05)

        assert any(e.get("event") == "ClientDisconnected" for e in gui_events), \
            f"GUI は helper 切断時に ClientDisconnected を受信するべき: {gui_events}"

    finally:
        stop_server()
