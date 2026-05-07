"""C-GP4: LiveSession attach mode 本実装テスト。

attach mode で `LiveSession.login()` を呼ぶと engine に `RequestVenueLogin`
を送信し、`VenueReady` で is_logged_in=True、`VenueError` で例外 raise。

token / 引数の伝播ルール:
- `user_id` / `password` を明示すると attach mode では `ValueError`
  (wire 上に送れないため)。
- credentials なしで attach mode の login() を呼んだら engine に
  RequestVenueLogin を投げる (engine 側 GUI が credential を保持する想定)。
"""

from __future__ import annotations

import asyncio
import os
import socket
import threading
import time
import uuid
from typing import Any
from unittest.mock import patch

import orjson
import pytest

from engine.replay_session import LiveSession
from engine.schemas import SCHEMA_MAJOR, SCHEMA_MINOR

pytestmark = pytest.mark.skip(reason="WS transport removed in G3 — migrate to gRPC mock")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _MockEngineForLogin:
    """RequestVenueLogin を受けて VenueReady または VenueError を返す mock。"""

    def __init__(
        self,
        port: int,
        *,
        venue_response: dict | None = None,
    ) -> None:
        self.port = port
        # default success
        self.venue_response = venue_response or {
            "event": "VenueReady",
            "venue": "tachibana",
            "request_id": None,  # filled per-request
        }
        self.received: list[dict] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._server: Any = None
        self._ready = threading.Event()

    async def _handler(self, ws):
        try:
            hello_raw = await ws.recv()
            self.received.append(orjson.loads(hello_raw))
        except Exception:
            return

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
            async for raw in ws:
                msg = orjson.loads(raw)
                self.received.append(msg)
                if msg.get("op") == "RequestVenueLogin":
                    rid = msg.get("request_id")
                    resp = dict(self.venue_response)
                    resp["request_id"] = rid
                    await ws.send(orjson.dumps(resp).decode())
        except Exception:
            pass

    async def _run(self):
        import websockets
        self._server = await websockets.serve(
            self._handler, "127.0.0.1", self.port, compression=None
        )
        self._ready.set()
        try:
            await self._server.wait_closed()
        except Exception:
            pass

    def start(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=lambda: self._loop.run_until_complete(self._run()), daemon=True
        )
        self._thread.start()
        assert self._ready.wait(timeout=5.0)

    def stop(self):
        if self._server is not None and self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(self._server.close)
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=3.0)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_attach_login_sends_request_venue_login(monkeypatch):
    """attach mode で login() → engine が RequestVenueLogin を受信する。"""
    port = _free_port()
    server = _MockEngineForLogin(port)
    server.start()
    try:
        monkeypatch.setenv("FLOWSURFACE_ENGINE_TOKEN", "tok")
        with LiveSession(
            venue="tachibana",
            demo=True,
            force_mode="attach",
            attach_endpoint=f"ws://127.0.0.1:{port}/",
            attach_timeout_s=2.0,
        ) as s:
            assert s.mode == "attach"
            s.login()  # no creds → attach mode delegates to engine

        ops = [m.get("op") for m in server.received if isinstance(m, dict)]
        assert "RequestVenueLogin" in ops, (
            f"expected RequestVenueLogin in {ops!r}"
        )
        cmd = next(m for m in server.received if m.get("op") == "RequestVenueLogin")
        assert cmd.get("venue") == "tachibana"
    finally:
        server.stop()


def test_attach_login_blocks_explicit_credentials(monkeypatch):
    """attach mode で user_id/password を明示すると ValueError。"""
    port = _free_port()
    server = _MockEngineForLogin(port)
    server.start()
    try:
        monkeypatch.setenv("FLOWSURFACE_ENGINE_TOKEN", "tok")
        with LiveSession(
            venue="tachibana",
            demo=True,
            force_mode="attach",
            attach_endpoint=f"ws://127.0.0.1:{port}/",
            attach_timeout_s=2.0,
        ) as s:
            with pytest.raises(ValueError, match="user_id|password|credential"):
                s.login(user_id=f"u-{uuid.uuid4().hex[:6]}",
                        password=f"p-{uuid.uuid4().hex[:6]}")
    finally:
        server.stop()


def test_attach_login_returns_true_on_venue_ready(monkeypatch):
    """attach mode + VenueReady 受信で is_logged_in == True。"""
    port = _free_port()
    server = _MockEngineForLogin(port)
    server.start()
    try:
        monkeypatch.setenv("FLOWSURFACE_ENGINE_TOKEN", "tok")
        with LiveSession(
            venue="tachibana",
            demo=True,
            force_mode="attach",
            attach_endpoint=f"ws://127.0.0.1:{port}/",
            attach_timeout_s=2.0,
        ) as s:
            assert s.is_logged_in is False
            s.login()
            assert s.is_logged_in is True
    finally:
        server.stop()


def test_attach_login_raises_on_venue_error(monkeypatch):
    """attach mode + VenueError 受信で例外 raise。"""
    port = _free_port()
    server = _MockEngineForLogin(
        port,
        venue_response={
            "event": "VenueError",
            "venue": "tachibana",
            "request_id": None,
            "code": "login_failed",
            "message": "ID/パスワードが違います",
        },
    )
    server.start()
    try:
        monkeypatch.setenv("FLOWSURFACE_ENGINE_TOKEN", "tok")
        with LiveSession(
            venue="tachibana",
            demo=True,
            force_mode="attach",
            attach_endpoint=f"ws://127.0.0.1:{port}/",
            attach_timeout_s=2.0,
        ) as s:
            with pytest.raises(Exception) as ei:
                s.login()
            # The error must surface the engine-provided message verbatim
            # (or at least preserve the code).
            text = str(ei.value)
            assert "login_failed" in text or "パスワード" in text, text
            assert s.is_logged_in is False
    finally:
        server.stop()
