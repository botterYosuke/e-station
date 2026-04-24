"""WebSocket IPC server — loopback-only, single-client, token-authenticated."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any
from uuid import UUID

import orjson
import websockets
from websockets import ServerConnection

from data.exchanges.binance import BinanceWorker
from data.schemas import (
    SCHEMA_MAJOR,
    SCHEMA_MINOR,
    EngineError,
    Hello,
    Ready,
)

log = logging.getLogger(__name__)

_ENGINE_VERSION = "0.1.0"


class _Outbox(list[dict]):
    """List-like outbox that wakes the send loop whenever an event is appended."""

    def __init__(self, wake_send_loop: Any) -> None:
        super().__init__()
        self._wake_send_loop = wake_send_loop

    def append(self, item: dict) -> None:
        super().append(item)
        self._wake_send_loop()


# ---------------------------------------------------------------------------
# Active stream bookkeeping
# ---------------------------------------------------------------------------


class _StreamHandle:
    """Tracks a running stream task and its stop event."""

    def __init__(self, task: asyncio.Task, stop: asyncio.Event) -> None:
        self.task = task
        self.stop = stop

    async def cancel(self) -> None:
        self.stop.set()
        self.task.cancel()
        try:
            await self.task
        except (asyncio.CancelledError, Exception):
            pass


# ---------------------------------------------------------------------------
# DataEngineServer
# ---------------------------------------------------------------------------


class DataEngineServer:
    def __init__(self, port: int, token: str) -> None:
        self._port = port
        self._token = token
        self._current_conn: ServerConnection | None = None
        self._shutdown_event = asyncio.Event()
        self._outbox_event = asyncio.Event()
        self._engine_session_id: UUID = uuid.uuid4()

        # Per-venue workers (Phase 1: Binance only)
        self._workers: dict[str, BinanceWorker] = {
            "binance": BinanceWorker(),
        }

        # Active stream tasks keyed by (venue, ticker, stream)
        self._streams: dict[tuple[str, str, str], _StreamHandle] = {}

        # Shared outbox — server drains this and sends to client
        self._outbox = _Outbox(self._outbox_event.set)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        self._shutdown_event.set()
        self._outbox_event.set()

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

    # ------------------------------------------------------------------
    # Connection handling
    # ------------------------------------------------------------------

    async def _handle(self, ws: ServerConnection) -> None:
        try:
            await self._handshake(ws)
            await asyncio.gather(
                self._recv_loop(ws),
                self._send_loop(ws),
            )
        except websockets.exceptions.ConnectionClosed:
            log.info("Client disconnected")
        except Exception as exc:
            log.error("Connection error: %s", exc)
        finally:
            if self._current_conn is ws:
                self._current_conn = None
            await self._cancel_all_streams()

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

        # Token matches — supersede any half-dead connection
        if self._current_conn is not None:
            try:
                await self._current_conn.send(
                    orjson.dumps(
                        {"event": "Error", "code": "superseded", "message": "new client connected"}
                    )
                )
                await self._current_conn.close()
            except Exception:
                pass

        self._current_conn = ws

        ready = Ready(
            schema_major=SCHEMA_MAJOR,
            schema_minor=SCHEMA_MINOR,
            engine_version=_ENGINE_VERSION,
            engine_session_id=self._engine_session_id,
            capabilities={
                "supported_venues": list(self._workers.keys()),
                "supports_bulk_trades": False,
                "supports_depth_binary": False,
            },
        )
        await ws.send(orjson.dumps(ready.model_dump(mode="json")))

    # ------------------------------------------------------------------
    # Receive loop — decode ops and dispatch
    # ------------------------------------------------------------------

    async def _recv_loop(self, ws: ServerConnection) -> None:
        async for raw in ws:
            msg: dict[str, Any] = orjson.loads(raw)
            op = msg.get("op")
            try:
                await self._dispatch(op, msg, ws)
            except Exception as exc:
                log.error("Dispatch error op=%s: %s", op, exc)
                await self._send_error(ws, msg.get("request_id"), str(exc))

    # ------------------------------------------------------------------
    # Send loop — drains outbox and writes to client
    # ------------------------------------------------------------------

    async def _send_loop(self, ws: ServerConnection) -> None:
        while True:
            # Drain whatever is already in the outbox
            while self._outbox:
                event = self._outbox.pop(0)
                await ws.send(orjson.dumps(event))

            if self._shutdown_event.is_set():
                break

            # Wait for more events
            self._outbox_event.clear()
            if self._outbox:
                self._outbox_event.set()
                continue
            await self._outbox_event.wait()

    # ------------------------------------------------------------------
    # Op dispatcher
    # ------------------------------------------------------------------

    async def _dispatch(self, op: str | None, msg: dict, ws: ServerConnection) -> None:  # noqa: ARG002
        if op == "Shutdown":
            self._shutdown_event.set()
            self._outbox_event.set()

        elif op == "Subscribe":
            await self._handle_subscribe(msg)

        elif op == "Unsubscribe":
            await self._handle_unsubscribe(msg)

        elif op == "ListTickers":
            await self._handle_list_tickers(msg)

        elif op == "GetTickerMetadata":
            await self._handle_get_ticker_metadata(msg)

        elif op == "FetchKlines":
            await self._handle_fetch_klines(msg)

        elif op == "FetchTrades":
            await self._send_error(
                ws,
                msg.get("request_id"),
                "FetchTrades is not implemented in Phase 1",
                code="not_supported",
            )

        elif op == "FetchOpenInterest":
            await self._handle_fetch_oi(msg)

        elif op == "FetchTickerStats":
            await self._handle_fetch_ticker_stats(msg)

        elif op == "RequestDepthSnapshot":
            await self._handle_request_depth_snapshot(msg)

        elif op == "SetProxy":
            self._handle_set_proxy(msg)

        else:
            log.warning("Unhandled op=%s", op)
            await self._send_error(
                ws,
                msg.get("request_id"),
                f"Unsupported op: {op}",
                code="unsupported_op",
            )

    # ------------------------------------------------------------------
    # Subscribe / Unsubscribe
    # ------------------------------------------------------------------

    async def _handle_subscribe(self, msg: dict) -> None:
        venue = msg.get("venue", "")
        ticker = msg.get("ticker", "")
        stream = msg.get("stream", "")
        timeframe = msg.get("timeframe")

        worker = self._workers.get(venue)
        if worker is None:
            log.warning("Subscribe: unknown venue %s", venue)
            return

        key = (venue, ticker, stream)
        if key in self._streams:
            log.debug("Already subscribed to %s", key)
            return

        stop = asyncio.Event()
        ssid = f"{self._engine_session_id}:0"

        if stream == "trade":
            coro = worker.stream_trades(ticker, _market(ticker), ssid, self._outbox, stop)
        elif stream == "depth":
            coro = worker.stream_depth(ticker, _market(ticker), ssid, self._outbox, stop)
        elif stream == "kline" or stream.startswith("kline_"):
            tf = timeframe or (stream[len("kline_"):] if stream.startswith("kline_") else "1m")
            coro = worker.stream_kline(ticker, _market(ticker), tf, ssid, self._outbox, stop)
        else:
            log.warning("Unknown stream type: %s", stream)
            return

        task = asyncio.create_task(coro)
        self._streams[key] = _StreamHandle(task, stop)

        # Notify outbox consumer when stream emits
        task.add_done_callback(lambda _: self._outbox_event.set())

    async def _handle_unsubscribe(self, msg: dict) -> None:
        venue = msg.get("venue", "")
        ticker = msg.get("ticker", "")
        stream = msg.get("stream", "")
        key = (venue, ticker, stream)

        handle = self._streams.pop(key, None)
        if handle:
            await handle.cancel()

    # ------------------------------------------------------------------
    # Fetch operations (one-shot, run as background tasks)
    # ------------------------------------------------------------------

    async def _handle_list_tickers(self, msg: dict) -> None:
        req_id = msg.get("request_id", "")
        venue = msg.get("venue", "")
        worker = self._workers.get(venue)
        if worker is None:
            return

        async def _run() -> None:
            tickers = await worker.list_tickers(_default_market(venue))
            self._outbox.append(
                {
                    "event": "TickerInfo",
                    "request_id": req_id,
                    "venue": venue,
                    "tickers": tickers,
                }
            )

        asyncio.create_task(_run())

    async def _handle_get_ticker_metadata(self, msg: dict) -> None:
        req_id = msg.get("request_id", "")
        venue = msg.get("venue", "")
        ticker = msg.get("ticker", "")
        worker = self._workers.get(venue)
        if worker is None:
            return

        async def _run() -> None:
            tickers = await worker.list_tickers(_default_market(venue))
            meta = next((t for t in tickers if t["symbol"] == ticker), {})
            self._outbox.append(
                {
                    "event": "TickerInfo",
                    "request_id": req_id,
                    "venue": venue,
                    "tickers": [meta] if meta else [],
                }
            )

        asyncio.create_task(_run())

    async def _handle_fetch_klines(self, msg: dict) -> None:
        req_id = msg.get("request_id", "")
        venue = msg.get("venue", "")
        ticker = msg.get("ticker", "")
        timeframe = msg.get("timeframe", "1m")
        limit = msg.get("limit", 400)
        worker = self._workers.get(venue)
        if worker is None:
            return

        async def _run() -> None:
            klines = await worker.fetch_klines(ticker, _market(ticker), timeframe, limit=limit)
            self._outbox.append(
                {
                    "event": "Klines",
                    "request_id": req_id,
                    "venue": venue,
                    "ticker": ticker,
                    "timeframe": timeframe,
                    "klines": klines,
                }
            )

        asyncio.create_task(_run())

    async def _handle_fetch_oi(self, msg: dict) -> None:
        req_id = msg.get("request_id", "")
        venue = msg.get("venue", "")
        ticker = msg.get("ticker", "")
        timeframe = msg.get("timeframe", "1h")
        limit = msg.get("limit", 400)
        worker = self._workers.get(venue)
        if worker is None:
            return

        async def _run() -> None:
            oi = await worker.fetch_open_interest(ticker, _market(ticker), timeframe, limit=limit)
            self._outbox.append(
                {
                    "event": "OpenInterest",
                    "request_id": req_id,
                    "venue": venue,
                    "ticker": ticker,
                    "data": oi,
                }
            )

        asyncio.create_task(_run())

    async def _handle_fetch_ticker_stats(self, msg: dict) -> None:
        req_id = msg.get("request_id", "")
        venue = msg.get("venue", "")
        ticker = msg.get("ticker", "")
        worker = self._workers.get(venue)
        if worker is None:
            return

        async def _run() -> None:
            stats = await worker.fetch_ticker_stats(ticker, _market(ticker))
            self._outbox.append(
                {
                    "event": "TickerStats",
                    "request_id": req_id,
                    "venue": venue,
                    "ticker": ticker,
                    "stats": stats,
                }
            )

        asyncio.create_task(_run())

    async def _handle_request_depth_snapshot(self, msg: dict) -> None:
        venue = msg.get("venue", "")
        ticker = msg.get("ticker", "")
        worker = self._workers.get(venue)
        if worker is None:
            return

        async def _run() -> None:
            snap = await worker.fetch_depth_snapshot(ticker, _market(ticker))
            ssid = f"{self._engine_session_id}:snap"
            self._outbox.append(
                {
                    "event": "DepthSnapshot",
                    "venue": venue,
                    "ticker": ticker,
                    "stream_session_id": ssid,
                    "sequence_id": snap["last_update_id"],
                    "bids": snap["bids"],
                    "asks": snap["asks"],
                }
            )

        asyncio.create_task(_run())

    def _handle_set_proxy(self, msg: dict) -> None:
        proxy_url = msg.get("url")
        for worker in self._workers.values():
            worker._proxy = proxy_url
            worker._client = None  # force reconnect with new proxy

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _send_error(
        self,
        ws: ServerConnection,
        request_id: str | None,
        message: str,
        *,
        code: str = "dispatch_error",
    ) -> None:
        try:
            await ws.send(
                orjson.dumps(
                    {
                        "event": "Error",
                        "request_id": request_id,
                        "code": code,
                        "message": message,
                    }
                )
            )
        except Exception:
            pass

    async def _cancel_all_streams(self) -> None:
        for handle in list(self._streams.values()):
            await handle.cancel()
        self._streams.clear()


# ---------------------------------------------------------------------------
# Helpers for market routing
# ---------------------------------------------------------------------------


def _market(_ticker: str) -> str:
    """Infer market type from ticker symbol (Phase 1: all Binance = linear_perp)."""
    return "linear_perp"


def _default_market(venue: str) -> str:
    if venue == "binance":
        return "linear_perp"
    return "linear_perp"
