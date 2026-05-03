"""Phase 8.1b: _AttachClient / ReplaySession attach mode のテスト。

attach mode の統合テスト（実際の engine server を起動）は J-Quants データが
必要なため @pytest.mark.live を付けて通常の CI からは除外する。

モックテスト（engine 不在・session ファイル解決・pid 判定）は live marker なしで動作する。
"""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

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
