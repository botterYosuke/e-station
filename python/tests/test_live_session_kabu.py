"""K7: INV-K7-E2E — kabu_station venue の IPC ライフサイクルテスト.

LiveSession(venue="kabu_station").login() が attach mode で
RequestVenueLogin{venue:"kabu_station"} を送信し、
VenueReady{venue:"kabu_station"} を受信すると is_logged_in == True になる。
"""
from __future__ import annotations

import asyncio
import socket
import threading
from typing import Any

import orjson
import pytest

from engine.replay_session import LiveSession
from engine.schemas import SCHEMA_MAJOR, SCHEMA_MINOR


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _MockEngine:
    """RequestVenueLogin を受けて指定 response を返す軽量 mock engine."""

    def __init__(self, port: int, *, venue_response: dict | None = None) -> None:
        self.port = port
        self.venue_response = venue_response or {
            "event": "VenueReady",
            "venue": "kabu_station",
            "request_id": None,
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


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.demo_kabu
def test_login_kabu_station_emits_venue_ready(monkeypatch):
    """INV-K7-E2E: LiveSession(venue="kabu_station").login() が VenueReady を受信する。"""
    port = _free_port()
    server = _MockEngine(port)
    server.start()
    try:
        monkeypatch.setenv("FLOWSURFACE_ENGINE_TOKEN", "tok")
        with LiveSession(
            venue="kabu_station",
            demo=True,
            force_mode="attach",
            attach_endpoint=f"ws://127.0.0.1:{port}/",
            attach_timeout_s=2.0,
        ) as s:
            assert s.mode == "attach"
            assert s.is_logged_in is False
            s.login()
            assert s.is_logged_in is True

        # engine が RequestVenueLogin{venue:"kabu_station"} を受信した
        ops = [m.get("op") for m in server.received if isinstance(m, dict)]
        assert "RequestVenueLogin" in ops
        cmd = next(m for m in server.received if m.get("op") == "RequestVenueLogin")
        assert cmd.get("venue") == "kabu_station"
    finally:
        server.stop()


@pytest.mark.demo_kabu
def test_login_kabu_station_raises_on_venue_error(monkeypatch):
    """attach mode + VenueError で ConnectionError が raise される。"""
    port = _free_port()
    server = _MockEngine(
        port,
        venue_response={
            "event": "VenueError",
            "venue": "kabu_station",
            "request_id": None,
            "code": "local_app_down",
            "message": "kabu local app not running",
        },
    )
    server.start()
    try:
        monkeypatch.setenv("FLOWSURFACE_ENGINE_TOKEN", "tok")
        with LiveSession(
            venue="kabu_station",
            demo=True,
            force_mode="attach",
            attach_endpoint=f"ws://127.0.0.1:{port}/",
            attach_timeout_s=2.0,
        ) as s:
            with pytest.raises(ConnectionError, match="local_app_down"):
                s.login()
    finally:
        server.stop()
