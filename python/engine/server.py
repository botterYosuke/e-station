"""WebSocket IPC server — loopback-only, single-client, token-authenticated."""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import sys
import uuid
from collections import deque
from pathlib import Path
from typing import Any
from uuid import UUID

import orjson
import websockets
from websockets import ServerConnection

from engine.exchanges.base import WsNativeResyncTriggered
import httpx

from engine.exchanges.tachibana_helpers import (
    LoginError,
    PNoCounter,
    SessionExpiredError,
    TachibanaError,
    UnreadNoticesError,
)
from engine.exchanges.tachibana_auth import (
    StartupLatch,
    TachibanaSession,
    validate_session_on_startup,
)
from engine.exchanges.tachibana_login_flow import (
    run_login as tachibana_run_login,
    _MSG_LOGIN_FAILED,
)
from engine.exchanges.tachibana_url import EventUrl, MasterUrl, PriceUrl, RequestUrl
from engine.exchanges.binance import BinanceWorker
from engine.exchanges.bybit import BybitWorker
from engine.exchanges.hyperliquid import HyperliquidWorker
from engine.exchanges.mexc import MexcWorker
from engine.exchanges.okex import OkexWorker
from engine.exchanges.tachibana import TachibanaWorker
from engine.exchanges.tachibana_orders import (
    NautilusOrderEnvelope,
    UnsupportedOrderError,
    InsufficientFundsError,
    check_phase_o0_order,
    submit_order as tachibana_submit_order,
    modify_order as tachibana_modify_order,
    cancel_order as tachibana_cancel_order,
    cancel_all_orders as tachibana_cancel_all_orders,
    fetch_order_list as tachibana_fetch_order_list,
    fetch_buying_power as tachibana_fetch_buying_power,
    fetch_credit_buying_power as tachibana_fetch_credit_buying_power,
)
from engine.schemas import OrderListFilter as SchemaOrderListFilter, OrderModifyChange as SchemaOrderModifyChange
from engine.schemas import (
    SCHEMA_MAJOR,
    SCHEMA_MINOR,
    EngineError,
    Hello,
    Ready,
    SubmitOrderRequest,
)

log = logging.getLogger(__name__)

_ENGINE_VERSION = "0.1.0"


class _Outbox:
    """Deque-backed outbox that wakes the send loop whenever an event is appended."""

    def __init__(self, wake_send_loop: Any) -> None:
        self._q: deque[dict] = deque()
        self._wake_send_loop = wake_send_loop

    def append(self, item: dict) -> None:
        self._q.append(item)
        self._wake_send_loop()

    def popleft(self) -> dict:
        return self._q.popleft()

    def __len__(self) -> int:
        return len(self._q)

    def __bool__(self) -> bool:
        return bool(self._q)


# ---------------------------------------------------------------------------
# Active stream bookkeeping
# ---------------------------------------------------------------------------


class _StreamHandle:
    """Tracks a running stream task, its stop event, and the current ssid."""

    def __init__(self, stop: asyncio.Event) -> None:
        self.task: asyncio.Task | None = None
        self.stop = stop
        self.current_ssid: str | None = None

    async def cancel(self) -> None:
        self.stop.set()
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass


# ---------------------------------------------------------------------------
# DataEngineServer
# ---------------------------------------------------------------------------


class DataEngineServer:
    def __init__(
        self,
        port: int,
        token: str,
        *,
        dev_tachibana_login_allowed: bool = False,
        cache_dir: Path | None = None,
        tachibana_is_demo: bool = True,
    ) -> None:
        self._port = port
        self._token = token
        self._dev_tachibana_login_allowed = dev_tachibana_login_allowed
        # B3: cache_dir is plumbed from __main__.py (T4 stdin payload). It
        # falls back to a per-user default so dev mode (`uv run python -m
        # engine --port ... --token ...`) keeps working without an explicit
        # arg. The TachibanaWorker is the only worker that uses it (master
        # cache path) so a None here would crash worker construction below.
        if cache_dir is None:
            cache_dir = Path.home() / ".cache" / "flowsurface" / "engine"
        self._cache_dir = Path(cache_dir)
        self._current_conn: ServerConnection | None = None
        self._shutdown_event = asyncio.Event()
        self._outbox_event = asyncio.Event()
        self._engine_session_id: UUID = uuid.uuid4()
        self._handshake_lock = asyncio.Lock()

        # Per-venue workers. The Tachibana worker is constructed at init time
        # (option (a) in B3 §3) with `session=None` so it sits dormant until
        # `SetVenueCredentials` succeeds and `set_session(...)` injects the
        # post-login `TachibanaSession`. This matches the lifecycle of the
        # crypto workers (init-time construction, lazy first-call HTTP) and
        # avoids a special-case "register-on-login" path in the dispatcher.
        self._workers: dict[
            str,
            BinanceWorker
            | BybitWorker
            | HyperliquidWorker
            | MexcWorker
            | OkexWorker
            | TachibanaWorker,
        ] = {
            "binance": BinanceWorker(),
            "bybit": BybitWorker(),
            "hyperliquid": HyperliquidWorker(),
            "mexc": MexcWorker(),
            "okex": OkexWorker(),
            "tachibana": TachibanaWorker(
                cache_dir=self._cache_dir,
                is_demo=tachibana_is_demo,
            ),
        }

        # Active stream tasks keyed by (venue, ticker, market, stream_type, timeframe|None)
        self._streams: dict[tuple[str, str, str, str, str | None], _StreamHandle] = {}

        # Active fetch tasks (FetchKlines, RequestDepthSnapshot, etc.)
        self._fetch_tasks: set[asyncio.Task] = set()

        # ── Tachibana state (T3) ──────────────────────────────────────
        self._tachibana_p_no_counter = PNoCounter()
        self._tachibana_startup_latch = StartupLatch()
        self._tachibana_session: TachibanaSession | None = None
        # Mark a single SetVenueCredentials in-flight so that double
        # injections from a flaky client don't race the dialog flow.
        self._tachibana_login_inflight = asyncio.Lock()

        # ── Order Phase state (T0.3) ──────────────────────────────────
        # 第二暗証番号: メモリのみ保持。ログ・WAL には出さない（C-M2, C-R2-H2）。
        # `str | None` のまま保持し、呼び出し側で参照するだけにする。
        self._second_password: str | None = None

        # Monotonic counter to produce a fresh base ssid per subscribe
        self._stream_counter = 0

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
            compression=None,
        ):
            log.info("Data engine listening on ws://127.0.0.1:%d", self._port)
            await self._shutdown_event.wait()

    # ------------------------------------------------------------------
    # Connection handling
    # ------------------------------------------------------------------

    async def _handle(self, ws: ServerConnection) -> None:
        recv_task: asyncio.Task | None = None
        send_task: asyncio.Task | None = None
        try:
            await self._handshake(ws)
            recv_task = asyncio.create_task(self._recv_loop(ws))
            send_task = asyncio.create_task(self._send_loop(ws))
            done, pending = await asyncio.wait(
                {recv_task, send_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()
            for t in pending:
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
            for t in done:
                exc = t.exception()
                if exc and not isinstance(exc, websockets.exceptions.ConnectionClosed):
                    log.warning("Loop task error: %s", exc)
        except websockets.exceptions.ConnectionClosed:
            log.info("Client disconnected")
        except ValueError as exc:
            log.info("Handshake rejected: %s", exc)
        except Exception as exc:
            log.error("Connection error: %s", exc)
        finally:
            if self._current_conn is ws:
                self._current_conn = None
            await self._cancel_all_streams()

    async def _handshake(self, ws: ServerConnection) -> None:
        raw = await ws.recv()
        msg = Hello.model_validate(orjson.loads(raw))

        # Constant-time token comparison to defeat timing attacks.
        if not hmac.compare_digest(msg.token, self._token):
            await ws.send(
                orjson.dumps(
                    EngineError(code="auth_failed", message="token mismatch").model_dump()
                ).decode()
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
                ).decode()
            )
            await ws.close()
            raise ValueError("schema_mismatch")

        # Serialize the current-connection swap so concurrent handshakes
        # cannot both observe `self._current_conn is None`.
        async with self._handshake_lock:
            if self._current_conn is not None and self._current_conn is not ws:
                try:
                    await self._current_conn.send(
                        orjson.dumps(
                            {
                                "event": "Error",
                                "code": "superseded",
                                "message": "new client connected",
                            }
                        ).decode()
                    )
                    await self._current_conn.close()
                except Exception as exc:
                    log.warning("Failed to close superseded connection: %s", exc)
                # Cancel stream tasks from the old connection before handing
                # over to the new client so they don't become zombie tasks.
                await self._cancel_all_streams()
            self._current_conn = ws

        # Spec §4.5: warm every worker's HTTP client before announcing Ready,
        # so the client may issue list_tickers / fetch_ticker_stats / fetch_klines
        # immediately on the next event-loop tick without races against lazy
        # ClientSession construction. Capped at 20 s to bound cold-start latency.
        try:
            await asyncio.wait_for(
                asyncio.gather(*(w.prepare() for w in self._workers.values())),
                timeout=20.0,
            )
        except asyncio.TimeoutError:
            log.warning("Worker prepare() timed out after 20s; emitting Ready anyway")
        except Exception as exc:
            log.warning("Worker prepare() raised: %s; emitting Ready anyway", exc)

        # B3: aggregate per-venue capability dicts into a structured
        # `venue_capabilities` block. Workers that override
        # `capabilities()` to a non-empty dict get a venue entry; the
        # legacy flat keys (`supported_venues` / `supports_bulk_trades` /
        # `supports_depth_binary`) are retained verbatim so older Rust
        # clients that haven't been recompiled against the new helper
        # keep working (M1: avoid a one-shot schema bump that would
        # double-bind UI release with engine release).
        venue_caps: dict[str, dict] = {}
        for venue, worker in self._workers.items():
            try:
                caps = worker.capabilities()
            except Exception as exc:  # pragma: no cover — defensive
                log.warning(
                    "venue=%s capabilities() raised %s — omitting from Ready",
                    venue,
                    exc,
                )
                continue
            if caps:
                venue_caps[venue] = caps

        ready = Ready(
            schema_major=SCHEMA_MAJOR,
            schema_minor=SCHEMA_MINOR,
            engine_version=_ENGINE_VERSION,
            engine_session_id=self._engine_session_id,
            capabilities={
                "supported_venues": list(self._workers.keys()),
                "supports_bulk_trades": True,
                "supports_depth_binary": False,
                "venue_capabilities": venue_caps,
            },
        )
        await ws.send(orjson.dumps(ready.model_dump(mode="json")).decode())

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
            while self._outbox:
                event = self._outbox.popleft()
                await ws.send(orjson.dumps(event).decode())

            if self._shutdown_event.is_set():
                break

            self._outbox_event.clear()
            if self._outbox:
                continue
            await self._outbox_event.wait()

    # ------------------------------------------------------------------
    # Op dispatcher
    # ------------------------------------------------------------------

    async def _dispatch(self, op: str | None, msg: dict, ws: ServerConnection) -> None:
        if op == "Shutdown":
            self._shutdown_event.set()
            self._outbox_event.set()

        elif op == "Subscribe":
            await self._handle_subscribe(msg)

        elif op == "Unsubscribe":
            await self._handle_unsubscribe(msg)

        elif op == "ListTickers":
            self._spawn_fetch(self._do_list_tickers(msg), msg.get("request_id"))

        elif op == "GetTickerMetadata":
            self._spawn_fetch(self._do_get_ticker_metadata(msg), msg.get("request_id"))

        elif op == "FetchKlines":
            self._spawn_fetch(self._do_fetch_klines(msg), msg.get("request_id"))

        elif op == "FetchTrades":
            self._spawn_fetch(self._do_fetch_trades(msg), msg.get("request_id"))

        elif op == "FetchOpenInterest":
            self._spawn_fetch(self._do_fetch_oi(msg), msg.get("request_id"))

        elif op == "FetchTickerStats":
            self._spawn_fetch(self._do_fetch_ticker_stats(msg), msg.get("request_id"))

        elif op == "RequestDepthSnapshot":
            self._spawn_fetch(
                self._do_request_depth_snapshot(msg), msg.get("request_id")
            )

        elif op == "Ping":
            self._outbox.append(
                {"event": "Pong", "request_id": msg.get("request_id", "")}
            )

        elif op == "SetProxy":
            await self._handle_set_proxy(msg)

        elif op == "SetVenueCredentials":
            self._spawn_fetch(
                self._do_set_venue_credentials(msg), msg.get("request_id")
            )

        elif op == "RequestVenueLogin":
            self._spawn_fetch(
                self._do_request_venue_login(msg), msg.get("request_id")
            )

        elif op == "SetSecondPassword":
            self._handle_set_second_password(msg)

        elif op == "ForgetSecondPassword":
            self._second_password = None

        elif op == "SubmitOrder":
            self._spawn_fetch(
                self._do_submit_order(msg), msg.get("request_id")
            )

        elif op == "ModifyOrder":
            self._spawn_fetch(
                self._do_modify_order(msg), msg.get("request_id")
            )

        elif op == "CancelOrder":
            self._spawn_fetch(
                self._do_cancel_order(msg), msg.get("request_id")
            )

        elif op == "CancelAllOrders":
            self._spawn_fetch(
                self._do_cancel_all_orders(msg), msg.get("request_id")
            )

        elif op == "GetOrderList":
            self._spawn_fetch(
                self._do_get_order_list(msg), msg.get("request_id")
            )

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
            self._outbox.append(
                {
                    "event": "Error",
                    "request_id": None,
                    "code": "unknown_venue",
                    "message": f"Subscribe: unknown venue {venue!r}",
                }
            )
            self._outbox_event.set()
            return

        market = _market_from_msg(msg, venue)

        # Keys are always 5-tuples: (venue, ticker, market, stream_type, tf).
        # For non-kline streams tf=None; for klines tf is the timeframe string.
        tf: str | None = None
        if stream == "kline":
            tf = timeframe or "1m"
        elif stream.startswith("kline_"):
            # legacy: timeframe-suffixed stream names (e.g. "kline_5m")
            tf = timeframe or stream[len("kline_"):]
        key = (venue, ticker, market, "kline" if tf is not None else stream, tf)

        if key in self._streams:
            log.debug("Already subscribed to %s", key)
            return

        stop = asyncio.Event()
        self._stream_counter += 1
        base_ssid = f"{self._engine_session_id}:{self._stream_counter}"

        handle = _StreamHandle(stop=stop)

        def _on_ssid(new_ssid: str) -> None:
            handle.current_ssid = new_ssid
        if stream == "trade":
            coro = worker.stream_trades(
                ticker, market, base_ssid, self._outbox, stop, on_ssid=_on_ssid
            )
        elif stream == "depth":
            coro = worker.stream_depth(
                ticker, market, base_ssid, self._outbox, stop, on_ssid=_on_ssid
            )
        elif tf is not None:
            coro = worker.stream_kline(
                ticker,
                market,
                tf,
                base_ssid,
                self._outbox,
                stop,
                on_ssid=_on_ssid,
            )
        else:
            log.warning("Unknown stream type: %s", stream)
            self._outbox.append(
                {
                    "event": "Error",
                    "request_id": None,
                    "code": "unsupported_stream",
                    "message": f"Unknown stream type: {stream!r}",
                }
            )
            self._outbox_event.set()
            return

        task = asyncio.create_task(coro)
        handle.task = task
        self._streams[key] = handle

        def _on_done(t: asyncio.Task) -> None:
            self._outbox_event.set()
            self._streams.pop(key, None)
            if not t.cancelled():
                exc = t.exception()
                if exc is not None:
                    log.error("Stream task %s died unexpectedly: %s", key, exc)
                    self._outbox.append(
                        {
                            "event": "Error",
                            "request_id": None,
                            "code": "stream_error",
                            "message": f"Stream {key!r} terminated unexpectedly: {exc}",
                        }
                    )

        task.add_done_callback(_on_done)

    async def _handle_unsubscribe(self, msg: dict) -> None:
        venue = msg.get("venue", "")
        ticker = msg.get("ticker", "")
        stream = msg.get("stream", "")
        timeframe = msg.get("timeframe")
        market = _market_from_msg(msg, venue)

        tf: str | None = None
        if stream == "kline":
            tf = timeframe or "1m"
        elif stream.startswith("kline_"):
            # legacy: timeframe-suffixed stream names
            tf = timeframe or stream[len("kline_"):]
        key = (venue, ticker, market, "kline" if tf is not None else stream, tf)

        handle = self._streams.pop(key, None)
        if handle:
            await handle.cancel()

    # ------------------------------------------------------------------
    # Order Phase handlers (T0.3)
    # ------------------------------------------------------------------

    def _handle_set_second_password(self, msg: dict) -> None:
        # value は文字列として受け取り、メモリのみ保持。ログに出さない（C-M2）。
        # R2-MEDIUM: 空文字列・空白のみは立花 API が reject するため設定しない。
        value = msg.get("value")
        if not isinstance(value, str) or not value.strip():
            log.warning("SetSecondPassword: value is empty or non-string — ignoring")
            return
        self._second_password = value

    async def _do_submit_order(self, msg: dict) -> None:
        req_id = msg.get("request_id", "")
        venue = msg.get("venue", "")
        raw_order = msg.get("order", {})

        if venue not in self._workers:
            self._outbox.append(
                {
                    "event": "Error",
                    "request_id": req_id,
                    "code": "unknown_venue",
                    "message": f"SubmitOrder: unknown venue {venue!r}",
                }
            )
            return

        # Parse order (deny_unknown_fields は Rust 側 DTO で保証済み。
        # Python 側は extra="forbid" で二重防御)
        try:
            order = SubmitOrderRequest.model_validate(raw_order)
        except Exception as exc:
            self._outbox.append(
                {
                    "event": "Error",
                    "request_id": req_id,
                    "code": "invalid_order",
                    "message": str(exc),
                }
            )
            return

        # Phase O0 制限チェック
        reason_code = check_phase_o0_order(order)
        if reason_code is not None:
            self._outbox.append(
                {
                    "event": "OrderRejected",
                    "client_order_id": order.client_order_id,
                    "reason_code": reason_code,
                    "reason_text": "Not supported in Phase O0",
                    "ts_event_ms": 0,
                }
            )
            return

        # 第二暗証番号チェック
        if self._second_password is None:
            self._outbox.append(
                {
                    "event": "SecondPasswordRequired",
                    "request_id": req_id,
                }
            )
            return

        # M-2: セッション未取得チェック
        if self._tachibana_session is None:
            self._outbox.append(
                {
                    "event": "OrderRejected",
                    "client_order_id": order.client_order_id,
                    "reason_code": "NOT_LOGGED_IN",
                    "reason_text": "Tachibana session is not established",
                    "ts_event_ms": 0,
                }
            )
            return

        # 発注処理（T0.4）
        import time

        envelope = NautilusOrderEnvelope.model_validate(raw_order)
        # OrderSubmitted を先行して発火（nautilus 流の 2 段イベント）
        self._outbox.append(
            {
                "event": "OrderSubmitted",
                "client_order_id": order.client_order_id,
                "ts_event_ms": int(time.time() * 1000),
            }
        )
        # C-1: tachibana_submit_order の例外を適切な OrderRejected に写す
        try:
            result = await tachibana_submit_order(
                self._tachibana_session,
                self._second_password,
                envelope,
                p_no_counter=self._tachibana_p_no_counter,
            )
        except SessionExpiredError:
            # M-14: セッション期限切れ時は second_password もクリア
            self._second_password = None
            self._outbox.append(
                {
                    "event": "OrderRejected",
                    "client_order_id": order.client_order_id,
                    "reason_code": "SESSION_EXPIRED",
                    "reason_text": "Session expired; please re-login",
                    "ts_event_ms": int(time.time() * 1000),
                }
            )
            return
        except InsufficientFundsError as exc:
            self._outbox.append(
                {
                    "event": "OrderRejected",
                    "client_order_id": order.client_order_id,
                    "reason_code": "INSUFFICIENT_FUNDS",
                    "reason_text": str(exc),
                    "ts_event_ms": int(time.time() * 1000),
                }
            )
            return
        except UnsupportedOrderError as exc:
            self._outbox.append(
                {
                    "event": "OrderRejected",
                    "client_order_id": order.client_order_id,
                    "reason_code": "VENUE_UNSUPPORTED",
                    "reason_text": str(exc),
                    "ts_event_ms": int(time.time() * 1000),
                }
            )
            return
        except TachibanaError:
            self._outbox.append(
                {
                    "event": "OrderRejected",
                    "client_order_id": order.client_order_id,
                    "reason_code": "VENUE_REJECTED",
                    "reason_text": "Venue rejected the order",
                    "ts_event_ms": int(time.time() * 1000),
                }
            )
            return
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError):
            self._outbox.append(
                {
                    "event": "OrderRejected",
                    "client_order_id": order.client_order_id,
                    "reason_code": "TRANSPORT_ERROR",
                    "reason_text": "HTTP connection failed",
                    "ts_event_ms": int(time.time() * 1000),
                }
            )
            return
        except Exception:
            log.exception("_do_submit_order: unexpected error for cid=%s", order.client_order_id)
            self._outbox.append(
                {
                    "event": "OrderRejected",
                    "client_order_id": order.client_order_id,
                    "reason_code": "INTERNAL_ERROR",
                    "reason_text": "Internal error during order submission",
                    "ts_event_ms": int(time.time() * 1000),
                }
            )
            return
        self._outbox.append(
            {
                "event": "OrderAccepted",
                "client_order_id": result.client_order_id,
                "venue_order_id": result.venue_order_id,
                "ts_event_ms": int(time.time() * 1000),
            }
        )

    async def _do_modify_order(self, msg: dict) -> None:
        import time

        req_id = msg.get("request_id", "")
        venue = msg.get("venue", "")
        client_order_id = msg.get("client_order_id", "")
        raw_change = msg.get("change", {})

        if venue not in self._workers:
            self._outbox.append({
                "event": "Error",
                "request_id": req_id,
                "code": "unknown_venue",
                "message": f"ModifyOrder: unknown venue {venue!r}",
            })
            return

        if self._second_password is None:
            self._outbox.append({
                "event": "SecondPasswordRequired",
                "request_id": req_id,
            })
            return

        if self._tachibana_session is None:
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": client_order_id,
                "reason_code": "NOT_LOGGED_IN",
                "reason_text": "Tachibana session is not established",
                "ts_event_ms": int(time.time() * 1000),
            })
            return

        try:
            change = SchemaOrderModifyChange.model_validate(raw_change)
        except Exception as exc:
            self._outbox.append({
                "event": "Error",
                "request_id": req_id,
                "code": "invalid_change",
                "message": str(exc),
            })
            return

        # Emit OrderPendingUpdate before calling the venue
        venue_order_id = msg.get("venue_order_id", "")
        ts_now = int(time.time() * 1000)
        self._outbox.append({
            "event": "OrderPendingUpdate",
            "client_order_id": client_order_id,
            "ts_event_ms": ts_now,
        })

        try:
            await tachibana_modify_order(
                session=self._tachibana_session,
                second_password=self._second_password,
                client_order_id=client_order_id,
                venue_order_id=venue_order_id,
                change=change,
                p_no_counter=self._tachibana_p_no_counter,
            )
        except SessionExpiredError:
            self._second_password = None
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": client_order_id,
                "reason_code": "SESSION_EXPIRED",
                "reason_text": "Session expired; please re-login",
                "ts_event_ms": int(time.time() * 1000),
            })
        except Exception:
            log.exception("_do_modify_order: unexpected error for cid=%s", client_order_id)
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": client_order_id,
                "reason_code": "INTERNAL_ERROR",
                "reason_text": "Internal error during order modification",
                "ts_event_ms": int(time.time() * 1000),
            })

    async def _do_cancel_order(self, msg: dict) -> None:
        import time

        req_id = msg.get("request_id", "")
        venue = msg.get("venue", "")
        client_order_id = msg.get("client_order_id", "")
        venue_order_id = msg.get("venue_order_id", "")

        if venue not in self._workers:
            self._outbox.append({
                "event": "Error",
                "request_id": req_id,
                "code": "unknown_venue",
                "message": f"CancelOrder: unknown venue {venue!r}",
            })
            return

        if self._second_password is None:
            self._outbox.append({
                "event": "SecondPasswordRequired",
                "request_id": req_id,
            })
            return

        if self._tachibana_session is None:
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": client_order_id,
                "reason_code": "NOT_LOGGED_IN",
                "reason_text": "Tachibana session is not established",
                "ts_event_ms": int(time.time() * 1000),
            })
            return

        # Emit OrderPendingCancel before calling the venue
        ts_now = int(time.time() * 1000)
        self._outbox.append({
            "event": "OrderPendingCancel",
            "client_order_id": client_order_id,
            "ts_event_ms": ts_now,
        })

        try:
            await tachibana_cancel_order(
                session=self._tachibana_session,
                second_password=self._second_password,
                client_order_id=client_order_id,
                venue_order_id=venue_order_id,
                p_no_counter=self._tachibana_p_no_counter,
            )
        except SessionExpiredError:
            self._second_password = None
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": client_order_id,
                "reason_code": "SESSION_EXPIRED",
                "reason_text": "Session expired; please re-login",
                "ts_event_ms": int(time.time() * 1000),
            })
        except Exception:
            log.exception("_do_cancel_order: unexpected error for cid=%s", client_order_id)
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": client_order_id,
                "reason_code": "INTERNAL_ERROR",
                "reason_text": "Internal error during order cancellation",
                "ts_event_ms": int(time.time() * 1000),
            })

    async def _do_cancel_all_orders(self, msg: dict) -> None:
        req_id = msg.get("request_id", "")
        venue = msg.get("venue", "")
        instrument_id = msg.get("instrument_id")
        order_side = msg.get("order_side")

        if venue not in self._workers:
            self._outbox.append({
                "event": "Error",
                "request_id": req_id,
                "code": "unknown_venue",
                "message": f"CancelAllOrders: unknown venue {venue!r}",
            })
            return

        if self._second_password is None:
            self._outbox.append({
                "event": "SecondPasswordRequired",
                "request_id": req_id,
            })
            return

        if self._tachibana_session is None:
            self._outbox.append({
                "event": "Error",
                "request_id": req_id,
                "code": "NOT_LOGGED_IN",
                "message": "Tachibana session is not established",
            })
            return

        try:
            await tachibana_cancel_all_orders(
                session=self._tachibana_session,
                second_password=self._second_password,
                instrument_id=instrument_id,
                order_side=order_side,
                p_no_counter=self._tachibana_p_no_counter,
            )
        except SessionExpiredError:
            self._second_password = None
            self._outbox.append({
                "event": "Error",
                "request_id": req_id,
                "code": "SESSION_EXPIRED",
                "message": "Session expired; please re-login",
            })
        except Exception:
            log.exception("_do_cancel_all_orders: unexpected error")
            self._outbox.append({
                "event": "Error",
                "request_id": req_id,
                "code": "INTERNAL_ERROR",
                "message": "Internal error during cancel-all",
            })

    async def _do_get_order_list(self, msg: dict) -> None:
        req_id = msg.get("request_id", "")
        venue = msg.get("venue", "")
        raw_filter = msg.get("filter", {})

        if venue not in self._workers:
            self._outbox.append({
                "event": "Error",
                "request_id": req_id,
                "code": "unknown_venue",
                "message": f"GetOrderList: unknown venue {venue!r}",
            })
            return

        if self._tachibana_session is None:
            self._outbox.append({
                "event": "OrderListUpdated",
                "request_id": req_id,
                "orders": [],
            })
            return

        try:
            filter_ = SchemaOrderListFilter.model_validate(raw_filter)
            records = await tachibana_fetch_order_list(
                session=self._tachibana_session,
                filter=filter_,
                p_no_counter=self._tachibana_p_no_counter,
            )
        except SessionExpiredError:
            self._second_password = None
            self._outbox.append({
                "event": "Error",
                "request_id": req_id,
                "code": "SESSION_EXPIRED",
                "message": "Session expired; please re-login",
            })
            return
        except Exception:
            log.exception("_do_get_order_list: unexpected error")
            self._outbox.append({
                "event": "Error",
                "request_id": req_id,
                "code": "INTERNAL_ERROR",
                "message": "Internal error fetching order list",
            })
            return

        orders_json = [
            {
                "client_order_id": r.client_order_id,
                "venue_order_id": r.venue_order_id,
                "instrument_id": r.instrument_id,
                "order_side": r.order_side,
                "order_type": r.order_type,
                "quantity": r.quantity,
                "filled_qty": r.filled_qty,
                "leaves_qty": r.leaves_qty,
                "price": r.price,
                "trigger_price": r.trigger_price,
                "time_in_force": r.time_in_force,
                "expire_time_ns": r.expire_time_ns,
                "status": r.status,
                "ts_event_ms": r.ts_event_ms,
            }
            for r in records
        ]
        self._outbox.append({
            "event": "OrderListUpdated",
            "request_id": req_id,
            "orders": orders_json,
        })

    # ------------------------------------------------------------------
    # Fetch operation helpers (each is an async coroutine producing one event)
    # ------------------------------------------------------------------

    def _spawn_fetch(self, coro: Any, request_id: str | None) -> None:
        """Spawn a fetch coroutine, converting exceptions into Error events."""

        async def _run() -> None:
            try:
                await coro
            except asyncio.CancelledError:
                self._outbox.append(
                    {
                        "event": "Error",
                        "request_id": request_id,
                        "code": "cancelled",
                        "message": "request interrupted (proxy change or disconnect)",
                    }
                )
                raise
            except WsNativeResyncTriggered:
                # _do_request_depth_snapshot handles this internally; re-raise so
                # it is never silently converted to fetch_failed if the inner
                # handler is ever removed.
                raise
            except ValueError as exc:
                log.warning("Fetch bad request (request_id=%s): %s", request_id, exc)
                self._outbox.append(
                    {
                        "event": "Error",
                        "request_id": request_id,
                        "code": "not_found",
                        "message": str(exc),
                    }
                )
            except NotImplementedError as exc:
                log.warning("Fetch not implemented (request_id=%s): %s", request_id, exc)
                self._outbox.append(
                    {
                        "event": "Error",
                        "request_id": request_id,
                        "code": "not_implemented",
                        "message": str(exc),
                    }
                )
            except TachibanaError as exc:
                # B3 plan §T4 L548: surface the worker-side error code
                # (e.g. `"not_implemented"` from VenueCapabilityError) as-is
                # so the Rust UI can branch on `code` rather than
                # string-matching the message.
                log.warning(
                    "Fetch tachibana error (request_id=%s code=%s): %s",
                    request_id,
                    exc.code,
                    exc.message,
                )
                self._outbox.append(
                    {
                        "event": "Error",
                        "request_id": request_id,
                        "code": exc.code,
                        "message": exc.message,
                    }
                )
            except Exception as exc:
                log.warning("Fetch failed (request_id=%s): %s", request_id, exc)
                self._outbox.append(
                    {
                        "event": "Error",
                        "request_id": request_id,
                        "code": "fetch_failed",
                        "message": str(exc),
                    }
                )

        task = asyncio.create_task(_run())
        self._fetch_tasks.add(task)
        task.add_done_callback(self._fetch_tasks.discard)

    async def _do_list_tickers(self, msg: dict) -> None:
        req_id = msg.get("request_id", "")
        venue = msg.get("venue", "")
        market = _market_from_msg(msg, venue)
        worker = self._workers.get(venue)
        if worker is None:
            raise ValueError(f"unknown venue: {venue}")
        tickers = await worker.list_tickers(market)
        self._outbox.append(
            {
                "event": "TickerInfo",
                "request_id": req_id,
                "venue": venue,
                "tickers": tickers,
            }
        )

    async def _do_get_ticker_metadata(self, msg: dict) -> None:
        req_id = msg.get("request_id", "")
        venue = msg.get("venue", "")
        ticker = msg.get("ticker", "")
        worker = self._workers.get(venue)
        if worker is None:
            raise ValueError(f"unknown venue: {venue}")
        tickers = await worker.list_tickers(_market_from_msg(msg, venue))
        meta = next((t for t in tickers if t["symbol"] == ticker), None)
        self._outbox.append(
            {
                "event": "TickerInfo",
                "request_id": req_id,
                "venue": venue,
                "tickers": [meta] if meta else [],
            }
        )

    async def _do_fetch_klines(self, msg: dict) -> None:
        req_id = msg.get("request_id", "")
        venue = msg.get("venue", "")
        ticker = msg.get("ticker", "")
        timeframe = msg.get("timeframe", "1m")
        raw_limit = msg.get("limit", 400)
        limit = raw_limit if isinstance(raw_limit, int) and 1 <= raw_limit <= 5000 else 400
        raw_start = msg.get("start_ms")
        raw_end = msg.get("end_ms")
        start_ms = int(raw_start) if raw_start is not None else None
        end_ms = int(raw_end) if raw_end is not None else None
        worker = self._workers.get(venue)
        if worker is None:
            raise ValueError(f"unknown venue: {venue}")
        klines = await worker.fetch_klines(
            ticker, _market_from_msg(msg, venue), timeframe, limit=limit,
            start_ms=start_ms, end_ms=end_ms,
        )
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

    async def _do_fetch_trades(self, msg: dict) -> None:
        req_id = msg.get("request_id", "")
        venue = msg.get("venue", "")
        ticker = msg.get("ticker", "")
        market = _market_from_msg(msg, venue)
        start_ms = int(msg.get("start_ms", 0))
        end_ms = int(msg.get("end_ms", 0))
        data_path_str: str | None = msg.get("data_path")
        data_path = Path(data_path_str) if data_path_str else None

        worker = self._workers.get(venue)
        if worker is None:
            raise ValueError(f"unknown venue: {venue}")

        trades = await worker.fetch_trades(
            ticker, market, start_ms, end_ms=end_ms, data_path=data_path
        )
        _CHUNK = 50_000
        if not trades:
            self._outbox.append(
                {
                    "event": "TradesFetched",
                    "request_id": req_id,
                    "venue": venue,
                    "ticker": ticker,
                    "trades": [],
                    "is_last": True,
                }
            )
        else:
            for i in range(0, len(trades), _CHUNK):
                self._outbox.append(
                    {
                        "event": "TradesFetched",
                        "request_id": req_id,
                        "venue": venue,
                        "ticker": ticker,
                        "trades": trades[i : i + _CHUNK],
                        "is_last": i + _CHUNK >= len(trades),
                    }
                )

    async def _do_fetch_oi(self, msg: dict) -> None:
        req_id = msg.get("request_id", "")
        venue = msg.get("venue", "")
        ticker = msg.get("ticker", "")
        timeframe = msg.get("timeframe", "1h")
        raw_limit = msg.get("limit", 400)
        limit = raw_limit if isinstance(raw_limit, int) and 1 <= raw_limit <= 5000 else 400
        raw_start = msg.get("start_ms")
        raw_end = msg.get("end_ms")
        start_ms = int(raw_start) if raw_start is not None else None
        end_ms = int(raw_end) if raw_end is not None else None
        worker = self._workers.get(venue)
        if worker is None:
            raise ValueError(f"unknown venue: {venue}")
        oi = await worker.fetch_open_interest(
            ticker, _market_from_msg(msg, venue), timeframe, limit=limit,
            start_ms=start_ms, end_ms=end_ms,
        )
        self._outbox.append(
            {
                "event": "OpenInterest",
                "request_id": req_id,
                "venue": venue,
                "ticker": ticker,
                "data": oi,
            }
        )

    async def _do_fetch_ticker_stats(self, msg: dict) -> None:
        req_id = msg.get("request_id", "")
        venue = msg.get("venue", "")
        ticker = msg.get("ticker", "")
        worker = self._workers.get(venue)
        if worker is None:
            raise ValueError(f"unknown venue: {venue}")
        stats = await worker.fetch_ticker_stats(ticker, _market_from_msg(msg, venue))
        self._outbox.append(
            {
                "event": "TickerStats",
                "request_id": req_id,
                "venue": venue,
                "ticker": ticker,
                "stats": stats,
            }
        )

    async def _do_request_depth_snapshot(self, msg: dict) -> None:
        """Fetch a fresh snapshot and emit it on the currently-active depth ssid.

        The snapshot must carry the stream_session_id that the live depth
        stream is using, otherwise Rust cannot splice it into the depth
        feed. If no depth stream is active for (venue, ticker), fall back
        to a client-supplied ssid if provided, else emit an Error.
        """
        req_id: str | None = msg.get("request_id")
        venue = msg.get("venue", "")
        ticker = msg.get("ticker", "")
        client_ssid: str | None = msg.get("stream_session_id")
        worker = self._workers.get(venue)
        if worker is None:
            raise ValueError(f"unknown venue: {venue}")

        market = _market_from_msg(msg, venue)
        handle = self._streams.get((venue, ticker, market, "depth", None))
        # Capture ssid before the await for early validation only.
        ssid_before = handle.current_ssid if handle is not None else None
        if ssid_before is None:
            ssid_before = client_ssid
        if ssid_before is None:
            raise ValueError(
                "RequestDepthSnapshot: no active depth stream and no stream_session_id provided"
            )

        try:
            snap = await worker.fetch_depth_snapshot(ticker, _market_from_msg(msg, venue))
        except WsNativeResyncTriggered:
            # WS-native venues (e.g. Bybit orderbook.200) cannot provide a compatible REST
            # snapshot. The worker signals its active WS stream to reconnect; Rust will
            # receive a fresh DepthSnapshot via the stream once the WS reconnects.
            log.info(
                "RequestDepthSnapshot: WS-native resync triggered for %s/%s — "
                "snapshot will arrive via stream reconnect",
                venue,
                ticker,
            )
            return

        # Re-read ssid AFTER the await — the stream may have reconnected during
        # the fetch and updated current_ssid to a new session.  Fall back to the
        # pre-await value only if the stream is no longer alive.
        live_handle = self._streams.get((venue, ticker, market, "depth", None))
        live_ssid = live_handle.current_ssid if live_handle is not None else None
        ssid = live_ssid or ssid_before

        payload: dict = {
            "event": "DepthSnapshot",
            "venue": venue,
            "ticker": ticker,
            "market": market,
            "stream_session_id": ssid,
            "sequence_id": snap["last_update_id"],
            "bids": snap["bids"],
            "asks": snap["asks"],
        }
        if req_id is not None:
            payload["request_id"] = req_id
        self._outbox.append(payload)

    # ------------------------------------------------------------------
    # Tachibana credentials / login (T3)
    # ------------------------------------------------------------------

    def _emit(self, event: dict) -> None:
        # MEDIUM-12: see implementation-plan.md round 6 group D for context.
        # `_Outbox.append` already wakes the send loop via the registered
        # `wake_send_loop` callback — no explicit `_outbox_event.set()` here.
        self._outbox.append(event)

    def _emit_many(self, events: list[dict]) -> None:
        for ev in events:
            self._outbox.append(ev)

    def _restore_session_from_payload(self, session_payload: dict) -> TachibanaSession:
        """Build a `TachibanaSession` from a wire dict (5 virtual URLs +
        expiry + tax bucket). The URLs arrive plain because the wire DTO
        has already done its `Zeroizing` round-trip on the Rust side; we
        wrap them back into the newtype-tagged form.

        MEDIUM-10 (ラウンド 6): the four HTTP virtual URLs must start
        with `https://` and `url_event_ws` must start with `wss://`.
        Without this gate, a corrupt keyring blob (or a misconfigured
        proxy intercepting the validate-session response) could route
        the next request through plaintext and leak the session
        cookie. Reject malformed payloads with `ValueError`; the
        caller's existing `(KeyError, TypeError, ValueError,
        AttributeError)` handler then emits `session_restore_failed`.
        """
        url_request = session_payload["url_request"]
        url_master = session_payload["url_master"]
        url_price = session_payload["url_price"]
        url_event = session_payload["url_event"]
        url_event_ws = session_payload["url_event_ws"]
        for label, value in (
            ("url_request", url_request),
            ("url_master", url_master),
            ("url_price", url_price),
            ("url_event", url_event),
        ):
            if not isinstance(value, str) or not value.startswith("https://"):
                raise ValueError(
                    f"session url {label!r} must start with https://"
                )
        if not isinstance(url_event_ws, str) or not url_event_ws.startswith(
            "wss://"
        ):
            raise ValueError("session url 'url_event_ws' must start with wss://")
        return TachibanaSession(
            url_request=RequestUrl(url_request),
            url_master=MasterUrl(url_master),
            url_price=PriceUrl(url_price),
            url_event=EventUrl(url_event),
            url_event_ws=url_event_ws,
            zyoutoeki_kazei_c=session_payload.get("zyoutoeki_kazei_c", ""),
            expires_at_ms=session_payload.get("expires_at_ms"),
        )

    async def _do_set_venue_credentials(self, msg: dict) -> None:
        """Handle `SetVenueCredentials` for the Tachibana venue.

        Behavior (architecture.md §6, T3):

        * If the payload carries an existing session, validate it once
          via `validate_session_on_startup`. Success → `VenueReady`,
          stale (`p_errno=2`) → drive a fresh login (env fast path or
          dialog) using the payload's `user_id`/`password` as fallback.
        * If the payload has no session, drive a login directly (this is
          the very-first-launch / keyring-empty path).
        """
        request_id = msg.get("request_id")
        payload = msg.get("payload") or {}
        if payload.get("venue") != "tachibana":
            log.warning(
                "SetVenueCredentials: unsupported venue=%r (only tachibana)",
                payload.get("venue"),
            )
            self._emit(
                {
                    "event": "VenueError",
                    "venue": payload.get("venue", ""),
                    "request_id": request_id,
                    "code": "unsupported_venue",
                    "message": "対応していない venue です",
                }
            )
            return

        async with self._tachibana_login_inflight:
            # Step 1: try restoring an existing session if present.
            session_payload = payload.get("session")
            if session_payload:
                try:
                    session = self._restore_session_from_payload(session_payload)
                except (KeyError, TypeError, ValueError, AttributeError) as exc:
                    log.error(
                        "SetVenueCredentials: malformed session payload: %s", exc
                    )
                    session = None
                else:
                    try:
                        await validate_session_on_startup(
                            session,
                            latch=self._tachibana_startup_latch,
                            p_no_counter=self._tachibana_p_no_counter,
                        )
                    except RuntimeError as exc:
                        # L6 — programmer bug: validate_session_on_startup
                        # was called more than once per process. Architecture
                        # spec dictates that this must terminate the process
                        # so the orchestrator (Rust ProcessManager) can
                        # restart it cleanly.
                        log.error(
                            "StartupLatch invariant violated (L6) — terminating engine: %s",
                            exc,
                        )
                        # Make sure the message reaches stderr before exit
                        # so the supervisor test (MEDIUM-D2-1) can grep it.
                        sys.stderr.write(
                            "FATAL: StartupLatch invariant violated (L6)\n"
                        )
                        sys.stderr.flush()
                        os._exit(2)
                    except UnreadNoticesError as exc:
                        # Spec: "ブラウザで未読通知を確認後に再ログインしてください".
                        # This is *not* an auto-recoverable failure — we
                        # must surface it to the user verbatim instead of
                        # spawning the login dialog (Findings #3).
                        # `UnreadNoticesError` inherits from `LoginError`
                        # so this branch must precede the LoginError catch.
                        log.info(
                            "tachibana startup validation surfaced unread notices: %s",
                            exc,
                        )
                        self._emit(
                            {
                                "event": "VenueError",
                                "venue": "tachibana",
                                "request_id": request_id,
                                "code": "unread_notices",
                                "message": str(exc),
                            }
                        )
                        return
                    except SessionExpiredError as exc:
                        log.info(
                            "tachibana session expired on startup, will re-login: %s",
                            exc,
                        )
                        session = None
                    except (LoginError, TachibanaError) as exc:
                        log.warning(
                            "tachibana startup validation failed (%s); falling through to login",
                            exc,
                        )
                        session = None
                    else:
                        self._tachibana_session = session
                        log.info("Tachibana session validated successfully")
                        self._emit(
                            {
                                "event": "VenueReady",
                                "venue": "tachibana",
                                "request_id": request_id,
                            }
                        )
                        return

            # Step 2: fresh login (env fast path → keyring-fallback creds → dialog).
            # Pass through any plaintext user_id / password / is_demo
            # carried by the SetVenueCredentials payload so a startup
            # re-login can re-use them silently before falling back to
            # the dialog (Findings #3 / docstring contract).
            fallback_user_id = payload.get("user_id") or None
            fallback_password = payload.get("password") or None
            fallback_is_demo = payload.get("is_demo")
            # H3 / M-14: any unexpected failure inside `run_login` must
            # surface as a typed VenueError so the UI banner can classify
            # it. Detail goes to the log; the user-facing message is the
            # fixed Japanese banner string. Pinned by
            # `python/tests/test_tachibana_login_unexpected_error.py`.
            try:
                try:
                    events = await tachibana_run_login(
                        request_id=request_id,
                        p_no_counter=self._tachibana_p_no_counter,
                        dev_login_allowed=self._dev_tachibana_login_allowed,
                        is_startup=True,
                        fallback_user_id=fallback_user_id,
                        fallback_password=fallback_password,
                        fallback_is_demo=(
                            bool(fallback_is_demo) if fallback_is_demo is not None else None
                        ),
                    )
                except Exception as exc:
                    # H3 / M-14: any unexpected failure inside `run_login`
                    # must surface as a typed VenueError so the UI banner
                    # can classify it. Detail goes to the log; the user-
                    # facing message is the fixed Japanese banner string.
                    # Pinned by `test_tachibana_login_unexpected_error.py`.
                    log.exception(
                        "SetVenueCredentials: tachibana_run_login raised: %s", exc
                    )
                    self._emit(
                        {
                            "event": "VenueError",
                            "venue": "tachibana",
                            "request_id": request_id,
                            "code": "login_failed",
                            "message": _MSG_LOGIN_FAILED,
                        }
                    )
                    return
            finally:
                # HIGH-7 (ラウンド 6): scrub credential-bearing locals on
                # **every** exit path (success and failure). Previous
                # placement only ran on the exception path; the success
                # path left `fallback_password` / `payload` / `msg`
                # bound on the frame, so a verbose-formatter traceback
                # captured later — e.g. on the `_restore_session_from_
                # payload` error branch below — would still render the
                # password from this enclosing frame's locals.
                fallback_password = None  # noqa: F841 — overwritten on purpose
                fallback_user_id = None  # noqa: F841
                fallback_is_demo = None  # noqa: F841
                payload = None  # noqa: F841
                msg = None  # noqa: F841
            # Capture the validated session so future ops can use it.
            # If the payload is malformed, the Rust keyring will receive
            # the new session via VenueCredentialsRefreshed but the Python
            # in-memory session will still be the stale one — the two
            # sides desynchronise silently. Log + emit VenueError instead
            # of swallowing the exception so the supervisor can react and
            # the user sees a concrete failure rather than a "looks-OK"
            # state that breaks on the next price request.
            restore_failed = False
            for ev in events:
                if ev.get("event") == "VenueCredentialsRefreshed":
                    try:
                        self._tachibana_session = self._restore_session_from_payload(
                            ev["session"]
                        )
                    except (KeyError, TypeError, ValueError, AttributeError) as exc:
                        log.error(
                            "VenueCredentialsRefreshed: malformed session payload (%s) — "
                            "Rust and Python will desynchronise; surfacing as VenueError",
                            exc,
                        )
                        restore_failed = True
            # HIGH-1 (ラウンド 7): when `restore_failed=True` we MUST NOT
            # emit `VenueReady` (or `VenueCredentialsRefreshed`) for this
            # `request_id` — the Rust `apply_after_handshake` wait loop
            # treats `VenueReady` as terminal completion of the request,
            # and any later `VenueError` would be silently dropped by
            # the continuation listener (which logs but does not act).
            # Filter the event list so only the failure surfaces.
            if restore_failed:
                events = [
                    ev
                    for ev in events
                    if ev.get("event") not in ("VenueReady", "VenueCredentialsRefreshed")
                ]
            self._emit_many(events)
            if restore_failed:
                self._emit(
                    {
                        "event": "VenueError",
                        "venue": "tachibana",
                        "request_id": request_id,
                        "code": "session_restore_failed",
                        "message": "セッション復元に失敗しました（Rust/Python 不整合の可能性）。再ログインしてください。",
                    }
                )

    async def _do_request_venue_login(self, msg: dict) -> None:
        """`RequestVenueLogin` from the Rust UI — drive a fresh login
        regardless of any stored session. This is the user-initiated path
        (sidebar button, banner action)."""
        request_id = msg.get("request_id")
        venue = msg.get("venue")
        if venue != "tachibana":
            log.warning("RequestVenueLogin: unsupported venue=%r", venue)
            self._emit(
                {
                    "event": "VenueError",
                    "venue": venue or "",
                    "request_id": request_id,
                    "code": "unsupported_venue",
                    "message": "対応していない venue です",
                }
            )
            return

        async with self._tachibana_login_inflight:
            try:
                events = await tachibana_run_login(
                    request_id=request_id,
                    p_no_counter=self._tachibana_p_no_counter,
                    dev_login_allowed=self._dev_tachibana_login_allowed,
                    is_startup=False,
                )
            except Exception as exc:
                # H3 / M-14: any unexpected failure inside `run_login`
                # (helper crash, asyncio cancel, etc.) used to bubble up
                # to `_spawn_fetch` and surface as a generic Error event.
                # Spec §6 says login failures must surface as a typed
                # VenueError so the UI banner can classify them. Detail
                # only goes to the log — the user-facing message is the
                # fixed Japanese banner string. Secrets are never in
                # `exc` (we never pass user_id/password into it), but
                # we still keep the message generic to avoid future
                # regressions.
                #
                # M-LOG ラウンド 5: this dispatcher does not bind any
                # plaintext credentials in its frame (it does not
                # accept fallback_*), so there is nothing to scrub
                # here. Sister handler `_do_set_venue_credentials`
                # *does* scrub — keep the symmetry comment so future
                # edits adding fallback creds remember to mirror it.
                log.exception(
                    "RequestVenueLogin: tachibana_run_login raised: %s", exc
                )
                self._emit(
                    {
                        "event": "VenueError",
                        "venue": "tachibana",
                        "request_id": request_id,
                        "code": "login_failed",
                        "message": _MSG_LOGIN_FAILED,
                    }
                )
                return

            restore_failed = False
            for ev in events:
                if ev.get("event") == "VenueCredentialsRefreshed":
                    # H1: malformed session payload used to be swallowed
                    # silently with `except (KeyError, TypeError): pass`.
                    # Spec contract: every login failure surfaces as a
                    # typed VenueError. Mirror `_do_set_venue_credentials`
                    # so a desync between Rust and Python is observable.
                    try:
                        self._tachibana_session = self._restore_session_from_payload(
                            ev["session"]
                        )
                    except (KeyError, TypeError, ValueError, AttributeError) as exc:
                        log.error(
                            "RequestVenueLogin: malformed VenueCredentialsRefreshed "
                            "session payload (%s) — surfacing as VenueError",
                            exc,
                        )
                        restore_failed = True
            # HIGH-1 (ラウンド 7): mirror `_do_set_venue_credentials` —
            # filter VenueReady / VenueCredentialsRefreshed when the
            # restore failed so Rust's wait loop does not see a terminal
            # success event under the same `request_id` followed by a
            # silent VenueError.
            if restore_failed:
                events = [
                    ev
                    for ev in events
                    if ev.get("event") not in ("VenueReady", "VenueCredentialsRefreshed")
                ]
            self._emit_many(events)
            if restore_failed:
                self._emit(
                    {
                        "event": "VenueError",
                        "venue": "tachibana",
                        "request_id": request_id,
                        "code": "session_restore_failed",
                        "message": "セッション復元に失敗しました（Rust/Python 不整合の可能性）。再ログインしてください。",
                    }
                )

    async def _handle_set_proxy(self, msg: dict) -> None:
        proxy_url = msg.get("url")
        for worker in self._workers.values():
            await worker.set_proxy(proxy_url)

        # Snapshot active subscriptions before cancelling so we can reopen
        # them through the new proxy.  Fetch HTTP clients are replaced lazily.
        active_subs = list(self._streams.keys())
        await self._cancel_all_streams()

        for venue, ticker, market, stream_type, tf in active_subs:
            await self._handle_subscribe(
                {
                    "venue": venue,
                    "ticker": ticker,
                    "stream": stream_type,
                    "timeframe": tf,
                    "market": market,
                }
            )

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
                ).decode()
            )
        except Exception as exc:
            log.debug("Failed to send error response: %s", exc)

    async def _cancel_all_streams(self) -> None:
        for handle in list(self._streams.values()):
            await handle.cancel()
        self._streams.clear()
        for task in list(self._fetch_tasks):
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._fetch_tasks.clear()


# ---------------------------------------------------------------------------
# Helpers for market routing
# ---------------------------------------------------------------------------


def _market_from_msg(msg: dict, venue: str) -> str:
    """Return the market kind sent by the Rust client, falling back to the venue default."""
    return msg.get("market") or _default_market(venue)


def _default_market(venue: str) -> str:
    return "linear_perp"
