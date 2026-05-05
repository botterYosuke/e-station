"""Phase 8.1b B2 — session ファイル経由の endpoint / token 解決テスト。

ReplaySession(force_mode="auto") が FLOWSURFACE_ENGINE_TOKEN や
明示 attach_endpoint 無しで、engine-session.json から port / token を
解決して attach mode になることを確認する。

_resolve_endpoint_and_token() の解決優先順位:
  1. 明示引数 (attach_endpoint)
  2. session ファイル  ← このファイルが検証する経路
  3. env のみ
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
from unittest.mock import patch

import orjson
import pytest
import websockets

from engine.replay_session import ReplaySession
from engine.schemas import SCHEMA_MAJOR, SCHEMA_MINOR


# ---------------------------------------------------------------------------
# Helper: minimal handshake-only WS server
# ---------------------------------------------------------------------------


def _spawn_ready_server(port: int):
    """指定ポートで Hello/Ready を処理して接続を維持する mock server を起動する。
    Returns (server_ready_event, stop_fn).
    """
    server_ready = threading.Event()
    server_holder: list = []
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
        try:
            async for _ in ws:
                pass
        except Exception:
            pass

    async def _run():
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

    return server_ready, _stop


# ---------------------------------------------------------------------------
# session ファイル経由で attach mode になる
# ---------------------------------------------------------------------------


def test_attach_via_session_file(tmp_path):
    """session ファイルに書かれた port / token で attach mode に入れること。

    FLOWSURFACE_ENGINE_TOKEN / 明示 attach_endpoint を設定せず、
    FLOWSURFACE_DATA_PATH だけを tmp_path に向けることで session ファイル
    経由の解決パスを検証する。
    """
    # 空きポートを見つける
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    TOKEN = "session-file-attach-token"

    _, stop_server = _spawn_ready_server(port)
    try:
        # session ファイルを tmp_path に書く（pid = current process = alive）
        session_file = tmp_path / "engine-session.json"
        session_data = {
            "port": port,
            "token": TOKEN,
            "pid": os.getpid(),
            "schema_major": SCHEMA_MAJOR,
            "started_at": "2026-05-03T00:00:00+00:00",
        }
        session_file.write_text(json.dumps(session_data), encoding="utf-8")

        # FLOWSURFACE_ENGINE_TOKEN を除去し、FLOWSURFACE_DATA_PATH のみ設定
        env_clean = {
            k: v
            for k, v in os.environ.items()
            if k not in ("FLOWSURFACE_ENGINE_TOKEN", "FLOWSURFACE_DATA_PATH")
        }
        env_clean["FLOWSURFACE_DATA_PATH"] = str(tmp_path)

        with patch.dict(os.environ, env_clean, clear=True):
            with ReplaySession(force_mode="auto", attach_timeout_s=3.0) as s:
                assert s.mode == "attach", \
                    f"session ファイル経由で attach mode になるべき (mode={s.mode!r})"
    finally:
        stop_server()


# ---------------------------------------------------------------------------
# session ファイルが stale pid の場合は in-process に fallback
# ---------------------------------------------------------------------------


def test_stale_session_file_falls_back_to_inprocess(tmp_path):
    """session ファイルの pid が dead なら in-process mode に fallback すること。"""
    session_file = tmp_path / "engine-session.json"
    session_data = {
        "port": 19876,
        "token": "stale-token",
        "pid": 999999999,  # dead pid
        "schema_major": SCHEMA_MAJOR,
        "started_at": "2026-05-03T00:00:00+00:00",
    }
    session_file.write_text(json.dumps(session_data), encoding="utf-8")

    env_clean = {
        k: v
        for k, v in os.environ.items()
        if k not in ("FLOWSURFACE_ENGINE_TOKEN", "FLOWSURFACE_DATA_PATH")
    }
    env_clean["FLOWSURFACE_DATA_PATH"] = str(tmp_path)

    with patch.dict(os.environ, env_clean, clear=True):
        with ReplaySession(force_mode="auto", attach_timeout_s=0.5) as s:
            assert s.mode == "inprocess", \
                "stale pid の session ファイルは in-process に fallback するべき"


# ---------------------------------------------------------------------------
# session ファイルが存在しない場合は in-process に fallback
# ---------------------------------------------------------------------------


def test_no_session_file_falls_back_to_inprocess(tmp_path):
    """session ファイルが無く FLOWSURFACE_ENGINE_TOKEN も無い場合は in-process mode。"""
    env_clean = {
        k: v
        for k, v in os.environ.items()
        if k not in ("FLOWSURFACE_ENGINE_TOKEN", "FLOWSURFACE_DATA_PATH")
    }
    env_clean["FLOWSURFACE_DATA_PATH"] = str(tmp_path)

    with patch.dict(os.environ, env_clean, clear=True):
        with ReplaySession(force_mode="auto") as s:
            assert s.mode == "inprocess"
