"""Phase 8.1b — attach mode での WS 切断時の挙動テスト。

attach 中に WS が切断されたら ConnectionError を raise し、
ReplaySession.status が "errored" に遷移することを確認する。
"""

from __future__ import annotations

import asyncio
import os
import socket
import threading
from unittest.mock import patch

import orjson
import pytest
import websockets

from engine.replay_session import ReplaySession, _AttachClient, _ReplayStatus
from engine.schemas import SCHEMA_MAJOR, SCHEMA_MINOR


# ---------------------------------------------------------------------------
# ユニットテスト: _FakeClient で WS 切断を直接シミュレート
# ---------------------------------------------------------------------------


def test_disconnect_during_run_sets_status_errored(tmp_path):
    """attach 中に ConnectionError が発生すると status == 'errored' になること。

    _AttachClient.events() が ConnectionError を raise するケースを模した
    ユニットテスト。実際の WS 切断なしで状態遷移を確認する。
    """
    strategy_file = tmp_path / "strategy.py"
    strategy_file.write_text("# dummy\n", encoding="utf-8")

    class _DisconnectingClient:
        def send_command(self, cmd):
            pass

        def wait_for(self, *_args, **_kwargs):
            return {"event": "ReplayDataLoaded"}

        def events(self):
            raise ConnectionError("WS connection closed during run")

        def close(self):
            pass

    s = ReplaySession(force_mode="inprocess")
    s._entered = True
    s._mode = "attach"
    s._client = _DisconnectingClient()
    s._status = _ReplayStatus.IDLE

    s.load("1301.TSE", "2025-01-06", "2025-03-31", "Daily")

    with pytest.raises(ConnectionError):
        s.run(strategy_file=str(strategy_file), on_event=lambda e: None)

    assert s.status == "errored", f"status は 'errored' になるべき (got: {s.status!r})"


# ---------------------------------------------------------------------------
# 統合テスト: 実際の WS サーバーが Ready 後に切断するケース
# ---------------------------------------------------------------------------


def _spawn_disconnect_server():
    """Hello/Ready 後に接続を切断する mock server。Returns (port, stop_fn)."""
    server_ready = threading.Event()
    server_holder: list = []
    chosen_port: list[int] = []
    loop = asyncio.new_event_loop()

    async def _handler(ws):
        _ = await ws.recv()  # Hello
        ready = {
            "event": "Ready",
            "schema_major": SCHEMA_MAJOR,
            "schema_minor": SCHEMA_MINOR,
            "engine_version": "test",
            "engine_session_id": "00000000-0000-0000-0000-000000000000",
            "capabilities": {},
        }
        await ws.send(orjson.dumps(ready).decode())
        # 少し待ってから切断（handshake 後の disconnect を再現）
        await asyncio.sleep(0.1)
        await ws.close()

    async def _run():
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        chosen_port.append(port)
        srv = await websockets.serve(_handler, "127.0.0.1", port, compression=None)
        server_holder.append(srv)
        server_ready.set()
        try:
            await srv.wait_closed()
        except Exception:
            pass

    thread = threading.Thread(target=lambda: loop.run_until_complete(_run()), daemon=True)
    thread.start()
    server_ready.wait(timeout=5.0)

    def _stop():
        if server_holder:
            loop.call_soon_threadsafe(server_holder[0].close)
        thread.join(timeout=2.0)

    return chosen_port[0], _stop


def test_attach_ws_close_raises_connection_error():
    """WS が server 側から閉じられたら _AttachClient.events() が ConnectionError を raise する。"""
    import time as _time

    port, stop = _spawn_disconnect_server()
    try:
        client = _AttachClient(f"ws://127.0.0.1:{port}/", "disconnect-test-token", 3.0)
        client.handshake()

        start = _time.monotonic()
        with pytest.raises(ConnectionError):
            for _evt in client.events():
                pass
        elapsed = _time.monotonic() - start

        # 60 秒も待たないこと（H6 修正の確認）
        assert elapsed < 10.0, f"events() は迅速に終了するべき (took {elapsed:.2f}s)"
    finally:
        stop()


def test_attach_disconnect_via_fake_client_status_errored_not_stopped(tmp_path):
    """ConnectionError は STOPPED ではなく ERRORED に遷移することを確認。"""
    strategy_file = tmp_path / "dummy_strategy.py"
    strategy_file.write_text("# dummy\n", encoding="utf-8")

    events_received = []

    class _PartialStreamClient:
        def send_command(self, cmd):
            pass

        def wait_for(self, *_args, **_kwargs):
            return {"event": "ReplayDataLoaded"}

        def events(self):
            yield {"event": "KlineUpdate", "venue": "replay", "ticker": "1301.TSE"}
            # ここで突然切断（EngineStopped なしに ConnectionError）
            raise ConnectionError("unexpected WS close")

        def close(self):
            pass

    s = ReplaySession(force_mode="inprocess")
    s._entered = True
    s._mode = "attach"
    s._client = _PartialStreamClient()
    s._status = _ReplayStatus.IDLE

    s.load("1301.TSE", "2025-01-06", "2025-03-31", "Daily")

    with pytest.raises(ConnectionError):
        s.run(
            strategy_file=str(strategy_file),
            on_event=events_received.append,
        )

    assert s.status == "errored", f"不正切断は 'errored' になるべき (got: {s.status!r})"
    # 切断前に受信したイベントは on_event に届いていること
    assert any(e.get("event") == "KlineUpdate" for e in events_received), \
        "切断前のイベントは on_event に届くべき"
