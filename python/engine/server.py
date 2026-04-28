"""WebSocket IPC server — loopback-only, single-client, token-authenticated."""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import time
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
    SecondPasswordInvalidError,
    SessionExpiredError,
    TachibanaError,
    UnreadNoticesError,
)
from engine.exchanges.tachibana_auth import (
    StartupLatch,
    TachibanaSession,
    TachibanaSessionHolder,
)
from engine.exchanges.tachibana_file_store import clear_session as tachibana_clear_session
from engine.exchanges.tachibana_login_flow import (
    LoginCancelled,
    startup_login as tachibana_startup_login,
    _MSG_LOGIN_FAILED,
)
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
        config_dir: Path | None = None,
        tachibana_is_demo: bool = True,
        wal_path: Path | None = None,
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
        if config_dir is None:
            config_dir = Path.home() / ".config" / "flowsurface" / "engine"
        self._config_dir = Path(config_dir)
        # B-3: WAL path for order audit log. Defaults to cache_dir / "tachibana_orders.jsonl".
        self._wal_path: Path = (
            Path(wal_path)
            if wal_path is not None
            else self._cache_dir / "tachibana_orders.jsonl"
        )
        self._current_conn: ServerConnection | None = None
        self._shutdown_event = asyncio.Event()
        self._outbox_event = asyncio.Event()
        self._engine_session_id: UUID = uuid.uuid4()
        self._handshake_lock = asyncio.Lock()

        # Tachibana p_no counter MUST be constructed before the worker dict
        # so the worker shares the same monotonic counter as
        # `validate_session_on_startup`. Two independent counters initialized
        # in the same Unix second emit identical p_no sequences and trigger
        # 立花 API error 6 (`p_no <= 前要求p_no`).
        self._tachibana_p_no_counter = PNoCounter()

        # Per-venue workers. The Tachibana worker is constructed at init time
        # with `session=None` so it sits dormant until `startup_login` completes
        # and `_apply_tachibana_session(...)` injects the post-login
        # `TachibanaSession`. This matches the lifecycle of the crypto workers
        # (init-time construction, lazy first-call HTTP) and avoids a special-
        # case "register-on-login" path in the dispatcher.
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
                p_no_counter=self._tachibana_p_no_counter,
            ),
        }

        # Active stream tasks keyed by (venue, ticker, market, stream_type, timeframe|None)
        self._streams: dict[tuple[str, str, str, str, str | None], _StreamHandle] = {}

        # Active fetch tasks (FetchKlines, RequestDepthSnapshot, etc.)
        self._fetch_tasks: set[asyncio.Task] = set()

        # Current proxy URL applied to workers. Initialized to None (no proxy).
        # _handle_set_proxy skips _cancel_all_streams when the URL is unchanged.
        self._proxy_url: str | None = None

        # ── Tachibana state (T3 / T-SC3) ─────────────────────────────
        # `self._tachibana_p_no_counter` is constructed earlier (above the
        # worker dict) so the TachibanaWorker can share it. See the comment
        # there for the p_no monotonicity rationale.
        self._tachibana_startup_latch = StartupLatch()
        self._tachibana_session: TachibanaSession | None = None
        # Guards startup_login against concurrent execution (double-start prevention).
        self._tachibana_login_inflight = asyncio.Lock()
        # Task handle for the background startup login (cancelled on disconnect).
        self._tachibana_startup_task: asyncio.Task | None = None

        # ── Order Phase state (T0.3) ──────────────────────────────────
        # 第二暗証番号: TachibanaSessionHolder でメモリ保持。
        # idle forget タイマー + lockout state を管理する（H-7: architecture.md §5.3）。
        self._session_holder = TachibanaSessionHolder()
        # Dev fast path: DEV_TACHIBANA_SECOND_PASSWORD が設定されており、かつ
        # dev_login_allowed=True のとき起動時に第二暗証番号を事前注入する。
        # E2E テスト / CI デモジョブ専用。iced modal は不要になる。
        # 値は絶対ログに出さない（C-M2）。
        if self._dev_tachibana_login_allowed:
            _dev_sp = os.environ.get("DEV_TACHIBANA_SECOND_PASSWORD", "")
            if _dev_sp:
                self._session_holder.set_password(_dev_sp)
                log.info(
                    "second_password pre-populated from DEV_TACHIBANA_SECOND_PASSWORD"
                    " (dev fast path — E2E/CI use only)"
                )

        # C-2: in-flight SubmitOrder カウンタ。ForgetSecondPassword 受信時のログ用。
        # asyncio 単一スレッドなので lock 不要。
        self._submit_order_inflight_count: int = 0

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
        startup_task: asyncio.Task | None = None
        try:
            await self._handshake(ws)
            # T-SC3: Python self-initiates Tachibana login after handshake.
            # The task runs concurrently with the recv/send loops.
            startup_task = asyncio.create_task(self._startup_tachibana())
            self._tachibana_startup_task = startup_task
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
            task_to_cancel = self._tachibana_startup_task
            if task_to_cancel is not None and not task_to_cancel.done():
                task_to_cancel.cancel()
                try:
                    await task_to_cancel
                except (asyncio.CancelledError, Exception):
                    pass
            self._tachibana_startup_task = None
            # Reset startup latch so the next reconnect can call
            # validate_session_on_startup again without the L6 guard firing.
            self._tachibana_startup_latch = StartupLatch()
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

        elif op == "RequestVenueLogin":
            # B-3: do NOT wrap in _spawn_fetch — cancelled tasks would emit
            # Error{code:cancelled} and leave VenueState stuck in LoginInFlight.
            # _startup_tachibana creates its own task internally for the long-
            # running login flow; the await here only blocks until the dispatch
            # logic (in-flight check, session clear) completes, not until login
            # finishes.
            await self._do_request_venue_login(msg)

        elif op == "SetSecondPassword":
            self._handle_set_second_password(msg)

        elif op == "ForgetSecondPassword":
            # C-2: 即時クリア（architecture.md §2.4 競合ポリシー）。
            # in-flight な SubmitOrder がある場合も待たずにクリアする。
            # 各 _do_submit_order は second_password をローカル変数に取得済みのため影響なし。
            inflight = self._submit_order_inflight_count
            self._session_holder.clear()
            if inflight > 0:
                log.info(
                    "ForgetSecondPassword: %d SubmitOrder(s) in-flight; "
                    "they will complete with already-captured second_password",
                    inflight,
                )
            else:
                log.info("ForgetSecondPassword received — clearing second_password from memory")

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

        elif op == "GetBuyingPower":
            self._spawn_fetch(
                self._do_get_buying_power(msg), msg.get("request_id")
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
        self._session_holder.set_password(value)

    async def _do_submit_order(self, msg: dict) -> None:
        # C-2: in-flight カウンタをインクリメント（architecture.md §2.4 競合ポリシー）。
        self._submit_order_inflight_count += 1
        try:
            await self._do_submit_order_inner(msg)
        finally:
            self._submit_order_inflight_count -= 1

    async def _do_submit_order_inner(self, msg: dict) -> None:
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

        # lockout チェック（H-8: SECOND_PASSWORD_INVALID 連続 max_retries 回で抑止）
        if self._session_holder.is_locked_out():
            self._outbox.append(
                {
                    "event": "OrderRejected",
                    "client_order_id": order.client_order_id,
                    "reason_code": "SECOND_PASSWORD_LOCKED",
                    "reason_text": "Second password is locked out due to repeated failures",
                    "ts_event_ms": 0,
                }
            )
            return

        # 第二暗証番号チェック（idle forget 適用済み）
        self._session_holder.touch()
        second_password = self._session_holder.get_password()
        if second_password is None:
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

        envelope = NautilusOrderEnvelope.model_validate(order.model_dump())
        # OrderSubmitted を先行して発火（nautilus 流の 2 段イベント）
        self._outbox.append(
            {
                "event": "OrderSubmitted",
                "client_order_id": order.client_order_id,
                "ts_event_ms": int(time.time() * 1000),
            }
        )
        # H-E: IPC SubmitOrder.order.request_key を submit_order に渡す。
        # Rust 側で計算した xxh3_64 ハッシュを WAL submit 行に書くことで、
        # 再起動後の OrderSessionState::load_from_wal() が冪等性マップを復元できる。
        ipc_request_key: int = raw_order.get("request_key", 0)

        # C-1: tachibana_submit_order の例外を適切な OrderRejected に写す
        try:
            result = await tachibana_submit_order(
                self._tachibana_session,
                second_password,
                envelope,
                p_no_counter=self._tachibana_p_no_counter,
                wal_path=self._wal_path,
                request_key=ipc_request_key,
            )
        except SessionExpiredError:
            # M-14: セッション期限切れ時は second_password もクリア
            self._session_holder.clear()
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
        except SecondPasswordInvalidError:
            self._session_holder.on_invalid()
            self._outbox.append(
                {
                    "event": "OrderRejected",
                    "client_order_id": order.client_order_id,
                    "reason_code": "SECOND_PASSWORD_INVALID",
                    "reason_text": "Second password is invalid",
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
        except OSError as exc:
            log.error(
                "_do_submit_order: WAL I/O error for cid=%s — %s",
                order.client_order_id,
                exc,
            )
            self._outbox.append(
                {
                    "event": "OrderRejected",
                    "client_order_id": order.client_order_id,
                    "reason_code": "INTERNAL_ERROR",
                    "reason_text": "WAL write failed; order not submitted",
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
        self._session_holder.on_submit_success()
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

        if self._session_holder.is_locked_out():
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": client_order_id,
                "reason_code": "SECOND_PASSWORD_LOCKED",
                "reason_text": "Second password is locked out due to repeated failures",
                "ts_event_ms": 0,
            })
            return

        self._session_holder.touch()
        second_password = self._session_holder.get_password()
        if second_password is None:
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
                second_password=second_password,
                client_order_id=client_order_id,
                venue_order_id=venue_order_id,
                change=change,
                p_no_counter=self._tachibana_p_no_counter,
            )
        except SessionExpiredError:
            self._session_holder.clear()
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": client_order_id,
                "reason_code": "SESSION_EXPIRED",
                "reason_text": "Session expired; please re-login",
                "ts_event_ms": int(time.time() * 1000),
            })
        except SecondPasswordInvalidError:
            self._session_holder.on_invalid()
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": client_order_id,
                "reason_code": "SECOND_PASSWORD_INVALID",
                "reason_text": "Second password is invalid",
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
        else:
            self._session_holder.on_submit_success()

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

        if self._session_holder.is_locked_out():
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": client_order_id,
                "reason_code": "SECOND_PASSWORD_LOCKED",
                "reason_text": "Second password is locked out due to repeated failures",
                "ts_event_ms": 0,
            })
            return

        self._session_holder.touch()
        second_password = self._session_holder.get_password()
        if second_password is None:
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
                second_password=second_password,
                client_order_id=client_order_id,
                venue_order_id=venue_order_id,
                p_no_counter=self._tachibana_p_no_counter,
            )
        except SessionExpiredError:
            self._session_holder.clear()
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": client_order_id,
                "reason_code": "SESSION_EXPIRED",
                "reason_text": "Session expired; please re-login",
                "ts_event_ms": int(time.time() * 1000),
            })
        except SecondPasswordInvalidError:
            self._session_holder.on_invalid()
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": client_order_id,
                "reason_code": "SECOND_PASSWORD_INVALID",
                "reason_text": "Second password is invalid",
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
        else:
            self._session_holder.on_submit_success()

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

        if self._session_holder.is_locked_out():
            self._outbox.append({
                "event": "Error",
                "request_id": req_id,
                "code": "SECOND_PASSWORD_LOCKED",
                "message": "Second password is locked out due to repeated failures",
            })
            return

        self._session_holder.touch()
        second_password = self._session_holder.get_password()
        if second_password is None:
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
            result = await tachibana_cancel_all_orders(
                session=self._tachibana_session,
                second_password=second_password,
                instrument_id=instrument_id,
                order_side=order_side,
                p_no_counter=self._tachibana_p_no_counter,
            )
        except SecondPasswordInvalidError:
            self._session_holder.on_invalid()
            self._outbox.append({
                "event": "Error",
                "request_id": req_id,
                "code": "SECOND_PASSWORD_INVALID",
                "message": "Second password is invalid",
            })
        except SessionExpiredError:
            self._session_holder.clear()
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
        else:
            self._session_holder.on_submit_success()
            if result.failed_count > 0:
                log.warning(
                    "cancel_all partial failure: canceled=%d failed=%d",
                    result.canceled_count,
                    result.failed_count,
                )
                self._outbox.append({
                    "event": "Error",
                    "request_id": req_id,
                    "code": "PARTIAL_CANCEL_FAILURE",
                    "message": f"canceled={result.canceled_count} failed={result.failed_count}",
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
            log.warning("_do_get_order_list: tachibana session not established — returning empty list")
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
            self._session_holder.clear()
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

    async def _do_get_buying_power(self, msg: dict) -> None:
        req_id = msg.get("request_id", "")
        venue = msg.get("venue", "")
        if venue not in self._workers:
            self._outbox.append({
                "event": "Error",
                "request_id": req_id,
                "code": "unknown_venue",
                "message": f"GetBuyingPower: unknown venue {venue!r}",
            })
            return

        if self._tachibana_session is None:
            log.warning("_do_get_buying_power: tachibana session not established (request_id=%s)", req_id)
            self._outbox.append({
                "event": "Error",
                "request_id": req_id,
                "code": "SESSION_NOT_ESTABLISHED",
                "message": "GetBuyingPower: tachibana session not established",
            })
            return

        try:
            cash_result = await tachibana_fetch_buying_power(
                session=self._tachibana_session,
                p_no_counter=self._tachibana_p_no_counter,
            )
            credit_result = await tachibana_fetch_credit_buying_power(
                session=self._tachibana_session,
                p_no_counter=self._tachibana_p_no_counter,
            )
        except SessionExpiredError:
            self._session_holder.clear()
            self._outbox.append({
                "event": "Error",
                "request_id": req_id,
                "code": "SESSION_EXPIRED",
                "message": "Session expired; please re-login",
            })
            return
        except Exception:
            log.exception("_do_get_buying_power: unexpected error")
            self._outbox.append({
                "event": "Error",
                "request_id": req_id,
                "code": "INTERNAL_ERROR",
                "message": "Internal error fetching buying power",
            })
            return

        ts_ms = int(time.time() * 1000)
        self._outbox.append({
            "event": "BuyingPowerUpdated",
            "request_id": req_id,
            "venue": venue,
            "cash_available": cash_result.available_amount,
            "cash_shortfall": cash_result.shortfall,
            "credit_available": credit_result.available_amount,
            "ts_ms": ts_ms,
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

    def _apply_tachibana_session(self, session: TachibanaSession) -> None:
        """Persist *session* to both server state and the TachibanaWorker.

        Must be called instead of bare ``self._tachibana_session = session``
        so the worker's ``_session`` field (checked by each API call before
        making network requests) is always kept in sync.  Previously the worker was never updated, which caused
        ``no_session`` errors on the first metadata fetch immediately after
        login (logged at 10:45:48 in the 2026-04-27 session).
        """
        self._tachibana_session = session
        self._workers["tachibana"].set_session(session)

    async def _startup_tachibana(self, request_id: str | None = None) -> None:
        """Drive Tachibana startup login (T-SC3).

        Called as a background task after handshake completes. Python
        self-initiates login after handshake completes.
        Emits VenueReady / VenueError / VenueLoginCancelled to outbox.
        """
        async with self._tachibana_login_inflight:
            try:
                session = await tachibana_startup_login(
                    self._config_dir,
                    self._cache_dir,
                    p_no_counter=self._tachibana_p_no_counter,
                    startup_latch=self._tachibana_startup_latch,
                    dev_login_allowed=self._dev_tachibana_login_allowed,
                )
            except LoginCancelled:
                log.info("tachibana startup: user cancelled login")
                self._emit({
                    "event": "VenueLoginCancelled",
                    "venue": "tachibana",
                    "request_id": request_id,
                })
                return
            except RuntimeError as exc:
                log.error(
                    "StartupLatch invariant violated (L6) — terminating engine: %s", exc
                )
                os.write(2, b"FATAL: StartupLatch invariant violated (L6)\n")
                os._exit(2)
            except UnreadNoticesError as exc:
                log.info("tachibana startup: unread notices: %s", exc)
                self._emit({
                    "event": "VenueError",
                    "venue": "tachibana",
                    "request_id": request_id,
                    "code": "unread_notices",
                    "message": str(exc),
                })
                return
            except (LoginError, TachibanaError, Exception) as exc:
                log.exception("_startup_tachibana: login failed: %s", exc)
                self._emit({
                    "event": "VenueError",
                    "venue": "tachibana",
                    "request_id": request_id,
                    "code": "login_failed",
                    "message": _MSG_LOGIN_FAILED,
                })
                return

            self._apply_tachibana_session(session)
            log.info("Tachibana session established successfully")
            self._emit({
                "event": "VenueReady",
                "venue": "tachibana",
                "request_id": request_id,
            })

    async def _do_request_venue_login(self, msg: dict) -> None:
        """`RequestVenueLogin` from the Rust UI — drive a fresh login."""
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

        if self._tachibana_login_inflight.locked():
            log.info("RequestVenueLogin: login already in-flight, re-emitting VenueLoginStarted")
            self._emit({"event": "VenueLoginStarted", "venue": "tachibana", "request_id": request_id})
            return

        self._tachibana_session = None
        self._workers["tachibana"].set_session(None)
        tachibana_clear_session(self._cache_dir)
        # B-3: spawn as a task so _dispatch_message returns immediately and the
        # recv loop is not blocked while the login dialog is open.  The task is
        # stored so the _handle finally block can cancel it on disconnect.
        task = asyncio.create_task(self._startup_tachibana(request_id=request_id))
        task.add_done_callback(
            lambda t: log.error(
                "_startup_tachibana task raised unexpectedly: %s", t.exception()
            ) if not t.cancelled() and t.exception() is not None else None
        )
        self._tachibana_startup_task = task

    async def _handle_set_proxy(self, msg: dict) -> None:
        proxy_url = msg.get("url")
        if proxy_url == self._proxy_url:
            return
        self._proxy_url = proxy_url

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
