"""WebSocket IPC server — loopback-only, single-client, token-authenticated."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import orjson
import websockets
from websockets.server import ServerConnection

from flowsurface_data.schemas import (
    SCHEMA_MAJOR,
    SCHEMA_MINOR,
    EngineError,
    Hello,
    Ready,
    Shutdown,
)

log = logging.getLogger(__name__)

_ENGINE_VERSION = "0.1.0"


class DataEngineServer:
    def __init__(self, port: int, token: str) -> None:
        self._port = port
        self._token = token
        self._current_conn: ServerConnection | None = None
        self._shutdown_event = asyncio.Event()

    async def serve(self) -> None:
        async with websockets.serve(
            self._handle,
            "127.0.0.1",
            self._port,
            ping_interval=15,
            ping_timeout=30,
        ):
            log.info("Data engine listening on ws://127.0.0.1:%d", self._port)
            await self._shutdown_event.wait()

    async def _handle(self, ws: ServerConnection) -> None:
        # Supersede half-dead existing connection
        if self._current_conn is not None:
            try:
                await self._current_conn.send(
                    orjson.dumps({"event": "Error", "code": "superseded", "message": "new client connected"})
                )
                await self._current_conn.close()
            except Exception:
                pass

        self._current_conn = ws

        try:
            await self._handshake(ws)
            await self._dispatch_loop(ws)
        except websockets.exceptions.ConnectionClosed:
            log.info("Client disconnected")
        finally:
            if self._current_conn is ws:
                self._current_conn = None

    async def _handshake(self, ws: ServerConnection) -> None:
        raw = await ws.recv()
        msg = Hello.model_validate(orjson.loads(raw))

        if msg.token != self._token:
            await ws.send(
                orjson.dumps(
                    EngineError(code="auth_failed", message="token mismatch").model_dump()
                )
            )
            await ws.close()
            raise ValueError("auth_failed")

        if msg.schema_major != SCHEMA_MAJOR:
            await ws.send(
                orjson.dumps(
                    EngineError(
                        code="schema_mismatch",
                        message=f"expected major={SCHEMA_MAJOR}, got {msg.schema_major}",
                    ).model_dump()
                )
            )
            await ws.close()
            raise ValueError("schema_mismatch")

        import uuid

        ready = Ready(
            schema_major=SCHEMA_MAJOR,
            schema_minor=SCHEMA_MINOR,
            engine_version=_ENGINE_VERSION,
            engine_session_id=uuid.uuid4(),
            capabilities={"supported_venues": ["binance"]},
        )
        await ws.send(orjson.dumps(ready.model_dump(mode="json")))

    async def _dispatch_loop(self, ws: ServerConnection) -> None:
        async for raw in ws:
            msg: dict[str, Any] = orjson.loads(raw)
            op = msg.get("op")
            if op == "Shutdown":
                self._shutdown_event.set()
                break
            else:
                log.debug("Received op=%s (not yet handled)", op)
