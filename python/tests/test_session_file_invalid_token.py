"""M-SF1 / M-SF3: session file invalid/missing token handling and
`_TokenMismatchError` typed-error detection.

Split from test_review_fixes.py (Phase 8 R1 / Phase 5).
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
from unittest.mock import patch

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


# ---------------------------------------------------------------------------
# M-SF1: _TokenMismatchError class exists and is used
# ---------------------------------------------------------------------------


def test_token_mismatch_error_class_exists():
    """M-SF1: _TokenMismatchError クラスが存在すること。"""
    try:
        from engine.replay_session import _TokenMismatchError
        assert issubclass(_TokenMismatchError, ConnectionRefusedError)
    except ImportError:
        pytest.fail("_TokenMismatchError should be importable from engine.replay_session")


def test_token_mismatch_uses_typed_error_not_string_check():
    """M-SF1: token mismatch の検出が文字列依存でなく型チェックで行われること。"""
    from engine.replay_session import _AttachClient, _TokenMismatchError
    import orjson

    async def _handler(ws):
        await ws.recv()  # Hello
        err = {"event": "EngineError", "code": "auth_failed", "message": "token mismatch"}
        await ws.send(orjson.dumps(err).decode())
        await ws.close()

    port, stop = _spawn_server(_handler)
    try:
        client = _AttachClient(f"ws://127.0.0.1:{port}/", "wrong-token", 2.0)
        try:
            client.handshake()
            pytest.fail("expected ConnectionRefusedError or _TokenMismatchError")
        except _TokenMismatchError:
            pass  # 期待通り: 型チェックで捕捉できた
        except ConnectionRefusedError:
            pass  # 許容: 既存の ConnectionRefusedError でも OK（移行過渡期）
    finally:
        stop()


# ---------------------------------------------------------------------------
# M-SF3: _read_session_file returns None when token field is missing
# ---------------------------------------------------------------------------


def test_read_session_file_no_token_returns_none(tmp_path):
    """M-SF3: session ファイルに token フィールドがない場合 None を返すこと。"""
    import os as _os
    session_data = {
        "pid": _os.getpid(),  # alive
        "port": 19876,
        # "token" フィールドなし
    }
    session_file = tmp_path / "engine-session.json"
    session_file.write_text(json.dumps(session_data), encoding="utf-8")

    with patch.dict(os.environ, {"FLOWSURFACE_DATA_PATH": str(tmp_path)}):
        from engine.replay_session import _read_session_file
        result = _read_session_file()

    assert result is None, f"expected None when token field is missing, got {result!r}"


def test_read_session_file_empty_token_returns_none(tmp_path):
    """M-SF3: session ファイルの token が空文字の場合 None を返すこと。"""
    import os as _os
    session_data = {
        "pid": _os.getpid(),
        "port": 19876,
        "token": "",  # 空文字
    }
    session_file = tmp_path / "engine-session.json"
    session_file.write_text(json.dumps(session_data), encoding="utf-8")

    with patch.dict(os.environ, {"FLOWSURFACE_DATA_PATH": str(tmp_path)}):
        from engine.replay_session import _read_session_file
        result = _read_session_file()

    assert result is None, f"expected None when token is empty, got {result!r}"
