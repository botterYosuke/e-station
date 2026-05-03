"""Phase 8.1b: _AttachClient / ReplaySession attach mode のテスト。

attach mode の統合テスト（実際の engine server を起動）は J-Quants データが
必要なため @pytest.mark.live を付けて通常の CI からは除外する。

モックテスト（engine 不在・session ファイル解決・pid 判定）は live marker なしで動作する。
"""

from __future__ import annotations

import json
import os
import socket
from unittest.mock import patch

import pytest

from engine.replay_session import (
    ReplaySession,
    _resolve_session_file_path,
    _is_pid_alive,
    _read_session_file,
)


# ---------------------------------------------------------------------------
# test_attach_fallback_when_no_engine
# ---------------------------------------------------------------------------


def test_attach_fallback_when_no_engine():
    """engine 不在時に in-process に fallback すること（force_mode='auto'）。

    FLOWSURFACE_ENGINE_TOKEN が未設定の場合は probe を skip して in-process になる。
    token 設定あり・engine 不在の場合は handshake 失敗 → in-process fallback。
    """
    # 環境変数をクリアして probe が走らない状態にする
    env_without_token = {k: v for k, v in os.environ.items() if k != "FLOWSURFACE_ENGINE_TOKEN"}

    with patch.dict(os.environ, env_without_token, clear=True):
        # session ファイルが存在しない状態 + token 未設定 → inprocess に fallback
        with patch("engine.replay_session._read_session_file", return_value=None):
            with ReplaySession(force_mode="auto") as s:
                assert s.mode == "inprocess"


def test_attach_fallback_when_engine_unreachable():
    """engine token あり・engine 未起動の場合に in-process に fallback すること。"""
    # 使われていないポートを見つける
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        free_port = sock.getsockname()[1]
    # そのポートはすぐ閉じるので unreachable

    with patch.dict(os.environ, {"FLOWSURFACE_ENGINE_TOKEN": "dummy-token"}):
        with patch("engine.replay_session._read_session_file", return_value=None):
            # attach_endpoint を unreachable なポートに向けて attach が失敗することを確認
            with ReplaySession(
                force_mode="auto",
                attach_endpoint=f"ws://127.0.0.1:{free_port}/",
                attach_timeout_s=0.5,
            ) as s:
                assert s.mode == "inprocess"


# ---------------------------------------------------------------------------
# test_attach_force_raises_when_no_engine
# ---------------------------------------------------------------------------


def test_attach_force_raises_when_no_engine():
    """force_mode='attach' で engine 不在時に ConnectionRefusedError が raise されること。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        free_port = sock.getsockname()[1]

    with patch.dict(os.environ, {"FLOWSURFACE_ENGINE_TOKEN": "dummy-token"}):
        with patch("engine.replay_session._read_session_file", return_value=None):
            with pytest.raises(ConnectionRefusedError):
                with ReplaySession(
                    force_mode="attach",
                    attach_endpoint=f"ws://127.0.0.1:{free_port}/",
                    attach_timeout_s=0.5,
                ) as s:
                    pass


def test_attach_force_raises_when_no_token():
    """force_mode='attach' で token/endpoint が見つからない時に ConnectionRefusedError。"""
    env_without_token = {k: v for k, v in os.environ.items() if k != "FLOWSURFACE_ENGINE_TOKEN"}
    with patch.dict(os.environ, env_without_token, clear=True):
        with patch("engine.replay_session._read_session_file", return_value=None):
            with pytest.raises(ConnectionRefusedError):
                with ReplaySession(force_mode="attach") as s:
                    pass


# ---------------------------------------------------------------------------
# test_session_file_resolve
# ---------------------------------------------------------------------------


def test_session_file_resolve_env_override(tmp_path):
    """FLOWSURFACE_DATA_PATH env で session ファイルパスが override されること。"""
    override_dir = tmp_path / "custom_data"
    override_dir.mkdir()

    with patch.dict(os.environ, {"FLOWSURFACE_DATA_PATH": str(override_dir)}):
        result = _resolve_session_file_path()

    assert result == override_dir / "engine-session.json"


def test_session_file_resolve_default():
    """FLOWSURFACE_DATA_PATH 未設定時に platformdirs ベースのパスになること。"""
    env_without_override = {
        k: v for k, v in os.environ.items() if k != "FLOWSURFACE_DATA_PATH"
    }
    with patch.dict(os.environ, env_without_override, clear=True):
        result = _resolve_session_file_path()

    # platformdirs ベースなので "flowsurface" と "engine-session.json" を含む
    assert "flowsurface" in str(result)
    assert result.name == "engine-session.json"


# ---------------------------------------------------------------------------
# test_is_pid_alive_dead_pid
# ---------------------------------------------------------------------------


def test_is_pid_alive_dead_pid():
    """存在しない pid で _is_pid_alive が False を返すこと。"""
    # pid=1 は通常存在するが、非常に大きい pid は存在しないはず
    # sys.maxsize より小さく OS が扱えない範囲の pid
    # プラットフォームにより範囲が異なるが 999999999 は通常存在しない
    dead_pid = 999999999
    result = _is_pid_alive(dead_pid)
    assert result is False


def test_is_pid_alive_current_process():
    """現在のプロセス pid で _is_pid_alive が True を返すこと。"""
    import os
    result = _is_pid_alive(os.getpid())
    assert result is True


# ---------------------------------------------------------------------------
# test_read_session_file — stale pid
# ---------------------------------------------------------------------------


def test_read_session_file_stale_pid(tmp_path):
    """session ファイルの pid が dead なら None を返すこと。"""
    session_data = {
        "pid": 999999999,  # dead pid
        "port": 19876,
        "token": "test-token",
    }
    session_file = tmp_path / "engine-session.json"
    session_file.write_text(json.dumps(session_data), encoding="utf-8")

    with patch.dict(os.environ, {"FLOWSURFACE_DATA_PATH": str(tmp_path)}):
        result = _read_session_file()

    assert result is None


def test_read_session_file_valid(tmp_path):
    """pid が alive なら session データを返すこと。"""
    import os as _os
    session_data = {
        "pid": _os.getpid(),  # current process = alive
        "port": 19876,
        "token": "test-token",
    }
    session_file = tmp_path / "engine-session.json"
    session_file.write_text(json.dumps(session_data), encoding="utf-8")

    with patch.dict(os.environ, {"FLOWSURFACE_DATA_PATH": str(tmp_path)}):
        result = _read_session_file()

    assert result is not None
    assert result["port"] == 19876


def test_read_session_file_missing(tmp_path):
    """session ファイルが存在しない場合に None を返すこと。"""
    with patch.dict(os.environ, {"FLOWSURFACE_DATA_PATH": str(tmp_path)}):
        result = _read_session_file()

    assert result is None


def test_read_session_file_invalid_json(tmp_path):
    """session ファイルが不正な JSON の場合に None を返すこと。"""
    session_file = tmp_path / "engine-session.json"
    session_file.write_text("not-valid-json{{{", encoding="utf-8")

    with patch.dict(os.environ, {"FLOWSURFACE_DATA_PATH": str(tmp_path)}):
        result = _read_session_file()

    assert result is None


# ---------------------------------------------------------------------------
# test_replay_session_inprocess_mode（モード選択の確認）
# ---------------------------------------------------------------------------


def test_replay_session_force_inprocess():
    """force_mode='inprocess' で常に inprocess になること。"""
    with ReplaySession(force_mode="inprocess") as s:
        assert s.mode == "inprocess"


def test_replay_session_reuse_raises():
    """同一 ReplaySession を 2 度 with に入れると RuntimeError になること。"""
    s = ReplaySession(force_mode="inprocess")
    with s:
        pass
    with pytest.raises(RuntimeError, match="再利用不可"):
        with s:
            pass


# ---------------------------------------------------------------------------
# attach mode with mock server
# ---------------------------------------------------------------------------


def test_attach_mode_with_mock_server():
    """mock WS サーバー経由で attach mode が成立し mode == 'attach' になること。"""
    import threading
    import asyncio

    import websockets
    import orjson
    from engine.schemas import SCHEMA_MAJOR, SCHEMA_MINOR

    ready_sent = threading.Event()
    server_ready = threading.Event()
    chosen_port: list[int] = []

    async def _handler(ws):
        # Hello を受信
        raw = await ws.recv()
        msg = orjson.loads(raw)
        # Ready を送信
        ready_msg = {
            "event": "Ready",
            "schema_major": SCHEMA_MAJOR,
            "schema_minor": SCHEMA_MINOR,
            "engine_version": "test",
            "engine_session_id": "00000000-0000-0000-0000-000000000000",
            "capabilities": {},
        }
        await ws.send(orjson.dumps(ready_msg).decode())
        ready_sent.set()
        # 接続を維持する（close まで待つ）
        try:
            async for _ in ws:
                pass
        except Exception:
            pass

    async def _run_server():
        # 空きポートを取得
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        chosen_port.append(port)
        server = await websockets.serve(_handler, "127.0.0.1", port, compression=None)
        server_ready.set()
        await asyncio.sleep(10)
        server.close()

    loop = asyncio.new_event_loop()
    server_thread = threading.Thread(
        target=lambda: loop.run_until_complete(_run_server()), daemon=True
    )
    server_thread.start()
    server_ready.wait(timeout=5.0)

    port = chosen_port[0]
    endpoint = f"ws://127.0.0.1:{port}/"

    with patch.dict(os.environ, {"FLOWSURFACE_ENGINE_TOKEN": "test-token"}):
        with patch("engine.replay_session._read_session_file", return_value=None):
            with ReplaySession(
                force_mode="attach",
                attach_endpoint=endpoint,
                attach_timeout_s=3.0,
            ) as s:
                assert s.mode == "attach"

    loop.call_soon_threadsafe(loop.stop)


# ---------------------------------------------------------------------------
# H13: portfolio updates in attach mode must accept {event: ReplayBuyingPower}
# ---------------------------------------------------------------------------


def test_attach_mode_portfolio_updated_via_event_key(tmp_path):
    """H13: attach 経路の WS は ``{"event": "ReplayBuyingPower", ...}`` を返す。
    in-process 経路の ``{"type": ...}`` と区別なく portfolio が更新されること。
    """
    from engine.replay_session import ReplaySession, _ReplayStatus

    strat = tmp_path / "strategy.py"
    strat.write_text("# dummy\n")

    class _FakeClient:
        def __init__(self):
            self.sent = []

        def send_command(self, cmd):
            self.sent.append(cmd)

        def wait_for(self, *_args, **_kwargs):
            return {"event": "ReplayDataLoaded"}

        def events(self):
            yield {
                "event": "ReplayBuyingPower",
                "cash": 950000,
                "equity": 1050000,
            }
            yield {"event": "EngineStopped"}

        def close(self):
            pass

    s = ReplaySession(force_mode="auto")
    s._entered = True
    s._mode = "attach"
    s._client = _FakeClient()
    s._status = _ReplayStatus.IDLE
    s.load("1301.TSE", "2025-01-06", "2025-03-31", "Daily")
    received = []
    s.run(strategy_file=str(strat), on_event=received.append)
    assert s.portfolio is not None
    assert s.portfolio.get("cash") == 950000
    assert s.portfolio.get("equity") == 1050000
    # EngineStopped で抜ける → STOPPED
    assert s.status == "stopped"


# ---------------------------------------------------------------------------
# Group 2 — attach mode 動作修正のテスト
# ---------------------------------------------------------------------------


def _spawn_handshake_server(handler):
    """Helper: spawn a websockets server thread with handler. Returns (port, loop, thread, stop)."""
    import asyncio as _asyncio
    import threading as _threading

    import websockets

    server_ready = _threading.Event()
    chosen_port: list[int] = []
    server_holder: list = []

    async def _run_server():
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        chosen_port.append(port)
        server = await websockets.serve(handler, "127.0.0.1", port, compression=None)
        server_holder.append(server)
        server_ready.set()
        try:
            await server.wait_closed()
        except Exception:
            pass

    loop = _asyncio.new_event_loop()
    thread = _threading.Thread(
        target=lambda: loop.run_until_complete(_run_server()), daemon=True
    )
    thread.start()
    server_ready.wait(timeout=5.0)

    def _stop():
        # server.close() を呼ぶと wait_closed() が解決し _run_server が return → loop が
        # 自然に止まる。loop.stop() を別途呼ぶと "Event loop stopped before Future
        # completed" 警告が出るので避ける。
        if server_holder:
            loop.call_soon_threadsafe(server_holder[0].close)
        # thread が終わるのを少し待つ（テスト終了時の警告を抑える）
        thread.join(timeout=2.0)

    return chosen_port[0], loop, thread, _stop


def _ready_handler():
    """Returns an async handler that completes Hello/Ready and stays open."""
    import orjson
    from engine.schemas import SCHEMA_MAJOR, SCHEMA_MINOR

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
        try:
            async for _msg in ws:
                pass
        except Exception:
            pass

    return _handler


# ---------------------------------------------------------------------------
# C4: close() idempotent / no exception on double-call
# ---------------------------------------------------------------------------


def test_attach_client_close_is_idempotent():
    """C4: _AttachClient.close() を二度呼んでも例外が出ず thread が死ぬこと。"""
    from engine.replay_session import _AttachClient

    port, loop, thread, stop = _spawn_handshake_server(_ready_handler())
    try:
        client = _AttachClient(f"ws://127.0.0.1:{port}/", "tok", 3.0)
        client.handshake()

        # 1 度目: 正常終了して thread 死ぬ
        client.close()
        assert client._thread is not None
        assert not client._thread.is_alive(), "thread should have terminated after close()"

        # 2 度目: 例外を投げないこと
        client.close()  # no raise
    finally:
        stop()


# ---------------------------------------------------------------------------
# C5: recv_loop logs WS closure
# ---------------------------------------------------------------------------


def test_attach_client_recv_loop_logs_on_close(caplog):
    """C5: WS が server 側から閉じられたとき recv_loop が情報レベルでログを出すこと。"""
    import logging
    import time as _time

    from engine.replay_session import _AttachClient

    import orjson
    from engine.schemas import SCHEMA_MAJOR, SCHEMA_MINOR

    async def _handler(ws):
        _ = await ws.recv()
        ready = {
            "event": "Ready",
            "schema_major": SCHEMA_MAJOR,
            "schema_minor": SCHEMA_MINOR,
            "engine_version": "test",
            "engine_session_id": "00000000-0000-0000-0000-000000000000",
            "capabilities": {},
        }
        await ws.send(orjson.dumps(ready).decode())
        # すぐ close する
        await ws.close()

    port, loop, thread, stop = _spawn_handshake_server(_handler)
    try:
        # caplog で root logger ごと INFO まで拾う（recv_loop はワーカースレッドで動く）。
        caplog.set_level(logging.INFO)
        client = _AttachClient(f"ws://127.0.0.1:{port}/", "tok", 3.0)
        client.handshake()
        # closed を検知するまで少し待つ
        for _ in range(30):
            if client._closed_event.is_set():
                break
            _time.sleep(0.1)
        client.close()

        # ログに WS closed か recv_loop terminated が出ていること
        msgs = [rec.message for rec in caplog.records]
        assert any(
            "WS closed" in m or "recv_loop terminated" in m for m in msgs
        ), f"expected close log; got: {msgs}"
    finally:
        stop()


# ---------------------------------------------------------------------------
# H5: token mismatch surfaced at error level + raise on force=attach
# ---------------------------------------------------------------------------


def test_attach_token_mismatch_logs_error_and_falls_back(caplog):
    """H5: token mismatch を auto mode で検出すると error レベルで surface し inprocess に fallback する。"""
    import logging

    import orjson

    async def _handler(ws):
        _ = await ws.recv()
        # auth_failed を返す
        err = {"event": "EngineError", "code": "auth_failed", "message": "token mismatch"}
        await ws.send(orjson.dumps(err).decode())
        await ws.close()

    port, loop, thread, stop = _spawn_handshake_server(_handler)
    try:
        with caplog.at_level(logging.ERROR, logger="engine.replay_session"):
            with patch.dict(os.environ, {"FLOWSURFACE_ENGINE_TOKEN": "wrong-token"}):
                with patch("engine.replay_session._read_session_file", return_value=None):
                    with ReplaySession(
                        force_mode="auto",
                        attach_endpoint=f"ws://127.0.0.1:{port}/",
                        attach_timeout_s=2.0,
                    ) as s:
                        # auto → fallback → inprocess
                        assert s.mode == "inprocess"

        # error log に "token mismatch" を含む行があること
        assert any(
            rec.levelno >= logging.ERROR and "token mismatch" in rec.message
            for rec in caplog.records
        ), f"expected error-level token mismatch log; got: {[(r.levelname, r.message) for r in caplog.records]}"
    finally:
        stop()


def test_attach_token_mismatch_raises_when_force_attach():
    """H5: force_mode='attach' のとき token mismatch は raise する。"""
    import orjson

    async def _handler(ws):
        _ = await ws.recv()
        err = {"event": "EngineError", "code": "auth_failed", "message": "token mismatch"}
        await ws.send(orjson.dumps(err).decode())
        await ws.close()

    port, loop, thread, stop = _spawn_handshake_server(_handler)
    try:
        with patch.dict(os.environ, {"FLOWSURFACE_ENGINE_TOKEN": "wrong-token"}):
            with patch("engine.replay_session._read_session_file", return_value=None):
                with pytest.raises(ConnectionRefusedError, match="token mismatch"):
                    with ReplaySession(
                        force_mode="attach",
                        attach_endpoint=f"ws://127.0.0.1:{port}/",
                        attach_timeout_s=2.0,
                    ):
                        pass
    finally:
        stop()


# ---------------------------------------------------------------------------
# H6: events() returns promptly when WS closes
# ---------------------------------------------------------------------------


def test_attach_client_events_terminates_on_ws_close():
    """H6: WS が hard-close したら events() は 60 秒待たずに ConnectionError を raise する。"""
    import time as _time

    from engine.replay_session import _AttachClient

    import orjson
    from engine.schemas import SCHEMA_MAJOR, SCHEMA_MINOR

    async def _handler(ws):
        _ = await ws.recv()
        ready = {
            "event": "Ready",
            "schema_major": SCHEMA_MAJOR,
            "schema_minor": SCHEMA_MINOR,
            "engine_version": "test",
            "engine_session_id": "00000000-0000-0000-0000-000000000000",
            "capabilities": {},
        }
        await ws.send(orjson.dumps(ready).decode())
        # しばらくしてから close
        import asyncio as _aio
        await _aio.sleep(0.2)
        await ws.close()

    port, loop, thread, stop = _spawn_handshake_server(_handler)
    try:
        client = _AttachClient(f"ws://127.0.0.1:{port}/", "tok", 3.0)
        client.handshake()

        start = _time.monotonic()
        with pytest.raises(ConnectionError):
            for _evt in client.events():
                pass
        elapsed = _time.monotonic() - start
        assert elapsed < 5.0, f"events() should terminate quickly, took {elapsed}s"
        client.close()
    finally:
        stop()


# ---------------------------------------------------------------------------
# H7: _probe_engine removed
# ---------------------------------------------------------------------------


def test_probe_engine_removed():
    """H7: _probe_engine は削除済み。dead code は残っていないこと。"""
    import engine.replay_session as mod

    assert not hasattr(mod, "_probe_engine"), "_probe_engine should be removed"


# ---------------------------------------------------------------------------
# H12: wait_for translates EngineBusy to BusyError
# ---------------------------------------------------------------------------


def test_attach_wait_for_translates_engine_busy_to_busy_error():
    """H12: wait_for() 中に EngineBusy が来たら BusyError に翻訳されること。"""
    from engine.replay_session import _AttachClient, BusyError

    import orjson
    from engine.schemas import SCHEMA_MAJOR, SCHEMA_MINOR

    async def _handler(ws):
        _ = await ws.recv()
        ready = {
            "event": "Ready",
            "schema_major": SCHEMA_MAJOR,
            "schema_minor": SCHEMA_MINOR,
            "engine_version": "test",
            "engine_session_id": "00000000-0000-0000-0000-000000000000",
            "capabilities": {},
        }
        await ws.send(orjson.dumps(ready).decode())
        # 何か command を受け取ったら EngineBusy を返す
        try:
            _cmd = await ws.recv()
            busy = {
                "event": "EngineBusy",
                "current_state": "Loaded",
                "attempted_command": "LoadReplayData",
                "reason": "already loaded",
            }
            await ws.send(orjson.dumps(busy).decode())
            async for _msg in ws:
                pass
        except Exception:
            pass

    port, loop, thread, stop = _spawn_handshake_server(_handler)
    try:
        client = _AttachClient(f"ws://127.0.0.1:{port}/", "tok", 3.0)
        client.handshake()
        # ダミー command を送信
        client.send_command({"op": "LoadReplayData"})
        with pytest.raises(BusyError):
            client.wait_for("ReplayDataLoaded", timeout_s=3.0)
        client.close()
    finally:
        stop()


# ---------------------------------------------------------------------------
# H14: handshake() failure cleans up thread
# ---------------------------------------------------------------------------


def test_attach_handshake_failure_cleans_up_thread():
    """H14: handshake() が失敗したら thread / loop はリークしないこと。"""
    from engine.replay_session import _AttachClient

    # 使われていないポートを見つけてすぐ閉じる → connect 失敗
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        free_port = sock.getsockname()[1]

    client = _AttachClient(f"ws://127.0.0.1:{free_port}/", "tok", 0.5)
    with pytest.raises(ConnectionRefusedError):
        client.handshake()

    # handshake() の try/finally で close() が呼ばれているはず
    assert client._thread is not None
    # close() で join 済み
    assert not client._thread.is_alive(), "thread should have terminated after handshake() failure"
