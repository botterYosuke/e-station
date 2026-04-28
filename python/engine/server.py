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
from engine.exchanges.tachibana_event import OrderEcEvent, TachibanaEventClient
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
from engine.mode import (
    ModeMismatchError,
    UnknownEngineKindError,
    validate_start_engine,
)

log = logging.getLogger(__name__)

# C-M2: httpx/httpcore の INFO/DEBUG ログには立花 API の URL が含まれ、
# クエリパラメータ sSecondPassword が露出するため WARNING 以上に抑制する。
# setLevel(WARNING) に加えて addFilter も設定することで確実に抑制する。
def _make_min_level_filter(min_level: int) -> logging.Filter:
    class _Filter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            return record.levelno >= min_level
    return _Filter()

for _http_logger_name in ("httpx", "httpcore"):
    _l = logging.getLogger(_http_logger_name)
    _l.setLevel(logging.WARNING)
    _l.addFilter(_make_min_level_filter(logging.WARNING))

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

    def __iter__(self):
        return iter(list(self._q))


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
        # N1.13: 起動時固定 mode (`"live"` | `"replay"`).
        # Hello 受信時に上書きする。default は旧クライアント互換の "live"。
        self._mode: str = "live"

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

        # ── Phase O2: EVENT WebSocket EC 約定通知 ────────────────────────
        # venue_order_id → client_order_id 逆引きマップ（EC フレームには client_order_id がない）
        self._venue_to_client: dict[str, str] = {}
        # venue_order_id → 累積約定数量（int）。部分約定の積算に使う
        self._fill_cumulative: dict[str, int] = {}
        # EVENT WebSocket 受信クライアント（重複検知 seen set を保持）
        self._event_client = TachibanaEventClient()
        # EVENT 受信ループのバックグラウンドタスク
        self._event_task: asyncio.Task | None = None

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

        # N1.4: 走行中 NautilusRunner マッピング (strategy_id → runner)。
        # StartEngine で登録、StopEngine から参照、完了時に pop される。
        self._engine_tasks: dict[str, Any] = {}

        # N1.16: REPLAY 仮想ポートフォリオ（CLMZanKaiKanougaku を呼ばない純粋 Python 実装）。
        # LoadReplayData 受信時に initial_cash で reset() する。
        from decimal import Decimal as _Decimal
        from engine.nautilus.portfolio_view import PortfolioView as _PortfolioView
        self._replay_portfolio = _PortfolioView(_Decimal("1000000"))
        self._replay_strategy_id: str = ""

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
            # Phase O2: EVENT ループも停止する
            if self._event_task is not None and not self._event_task.done():
                self._event_task.cancel()
                try:
                    await self._event_task
                except (asyncio.CancelledError, Exception):
                    pass
            self._event_task = None
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

        # N1.13: capture mode from Hello so dispatch policies (mode helper) can read it.
        self._mode = msg.mode

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
                # N1.1: Phase N1 では BacktestEngine のみ実装、Live は N2 から。
                "nautilus": {"backtest": True, "live": False},
                # N1.13: クライアントから受け取った mode をエコーバック。
                # UI 側は capabilities["mode"] で正規化された値を読む。
                "mode": self._mode,
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

        elif op == "StartEngine":
            # N1.4: BacktestEngine 起動。replay モード必須。
            self._spawn_fetch(
                self._handle_start_engine(msg), msg.get("request_id")
            )

        elif op == "StopEngine":
            # N1.4: 走行中 BacktestEngine を停止する。
            self._spawn_fetch(
                self._handle_stop_engine(msg), msg.get("request_id")
            )

        elif op == "LoadReplayData":
            # N1.4: J-Quants データを事前ロードして件数だけ通知する。
            # StartEngine が config に同等情報を持つので、本コマンドは事前確認用途。
            self._spawn_fetch(
                self._handle_load_replay_data(msg), msg.get("request_id")
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

        # M-7: venue=="replay" は N1.5 で実装する仮想注文経路。N1.4 段階では
        # _workers に "replay" が登録されないため `unknown_venue` で reject されてしまうが、
        # 「未実装」の意図を Rust 側に明示するため専用 reason_code で OrderRejected を返す。
        #
        # M-7 (R2 review-fix R2): 通常の Tachibana 経路（Submitted → Accepted/Rejected）と
        # 対称化するため、OrderRejected の前に OrderSubmitted を emit する。これにより
        # Rust UI の submitting フラグが OrderSubmitted で reset され、続く OrderRejected の
        # toast 表示後に stuck しない。
        if venue == "replay":
            cid = (raw_order or {}).get("client_order_id", "") if isinstance(raw_order, dict) else ""
            ts_event_ms = int(time.time() * 1000)
            self._outbox.append(
                {
                    "event": "OrderSubmitted",
                    "client_order_id": cid,
                    "ts_event_ms": ts_event_ms,
                }
            )
            self._outbox.append(
                {
                    "event": "OrderRejected",
                    "client_order_id": cid,
                    "reason_code": "REPLAY_NOT_IMPLEMENTED",
                    "reason_text": "REPLAY 仮想注文は N1.5 で実装",
                    "ts_event_ms": ts_event_ms,
                }
            )
            return

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

        # 発注処理（T0.4） — `time` はモジュール冒頭で import 済み
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
        # Phase O2: EC フレームの venue_order_id → client_order_id 逆引きマップを更新
        if result.venue_order_id:
            self._venue_to_client[result.venue_order_id] = result.client_order_id

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

    async def _do_get_order_list_replay(self, msg: dict) -> None:
        """N1.15: GetOrderList{venue='replay'} — WAL から注文一覧を返す。

        tachibana_orders_replay.jsonl を読み、phase='submit' のエントリのみを
        OrderRecordWire 形式の dict に変換して OrderListUpdated として返す。
        WAL が存在しない場合は空リストを返す。
        """
        import json

        req_id = msg.get("request_id", "")
        wal_path = self._cache_dir / "tachibana_orders_replay.jsonl"

        orders = []
        try:
            if wal_path.exists():
                with open(wal_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if entry.get("phase") != "submit":
                            continue
                        qty = entry.get("quantity", "0")
                        orders.append({
                            "client_order_id": entry.get("client_order_id", ""),
                            "venue_order_id": "",
                            "instrument_id": entry.get("instrument_id", ""),
                            "order_side": entry.get("order_side", ""),
                            "order_type": entry.get("order_type", ""),
                            "quantity": qty,
                            "filled_qty": "0",
                            "leaves_qty": qty,
                            "price": entry.get("price"),
                            "trigger_price": None,
                            "time_in_force": "DAY",
                            "expire_time_ns": None,
                            "status": "SUBMITTED",
                            "ts_event_ms": entry.get("ts", 0),
                            "venue": "replay",
                        })
        except OSError as exc:
            log.error("_do_get_order_list_replay: failed to read WAL: %s", exc)
            self._outbox.append({
                "event": "Error",
                "request_id": req_id,
                "code": "order_list_read_failed",
                "message": str(exc),
            })
            return

        self._outbox.append({
            "event": "OrderListUpdated",
            "request_id": req_id,
            "orders": orders,
        })

    async def _do_get_order_list(self, msg: dict) -> None:
        req_id = msg.get("request_id", "")
        venue = msg.get("venue", "")
        raw_filter = msg.get("filter", {})

        # N1.15: replay venue は WAL から返す（_workers に登録されていないため先に分岐）
        if venue == "replay":
            return await self._do_get_order_list_replay(msg)

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

        # Phase O2: OrderListUpdated の応答で venue_order_id → client_order_id を補完する。
        for r in records:
            if r.venue_order_id and r.client_order_id:
                self._venue_to_client[r.venue_order_id] = r.client_order_id

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
                "venue": "tachibana",
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
        # N1.16: REPLAY モードでは CLMZanKaiKanougaku を呼ばない（D9.6 ガード）。
        if venue == "replay":
            await self._do_get_buying_power_replay(msg)
            return
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

    async def _do_get_buying_power_replay(self, msg: dict) -> None:
        """REPLAY 余力: CLMZanKaiKanougaku を呼ばずに PortfolioView から返す（D9.6 明示ガード）。

        H-E: ReplayBuyingPower は push event なので request_id を付与しない。
        Rust 側スキーマが extra="forbid" であるためフィールド追加は forbidden。
        """
        # CLMZanKaiKanougaku は呼ばない（D9.6 明示ガード）
        ipc_dict = self._replay_portfolio.to_ipc_dict(
            strategy_id=self._replay_strategy_id,
        )
        self._outbox.append(ipc_dict)

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
            # Phase O2: VenueReady 後に EVENT WebSocket 受信ループを起動する。
            # 既存タスクが残っていれば（再ログイン時）キャンセルして再起動する。
            if self._event_task is not None and not self._event_task.done():
                self._event_task.cancel()
            self._event_task = asyncio.create_task(
                self._run_event_loop(session.url_event_ws)
            )

    async def _run_event_loop(self, url: str) -> None:
        """EVENT WebSocket に接続して EC 約定通知の受信ループを実行する（Phase O2）。

        VenueReady 後にバックグラウンドタスクとして起動される。
        再接続は TachibanaEventClient.receive_loop() の reconnect_fn で処理する。
        """
        async def _reconnect() -> object:
            return await websockets.connect(url, compression=None)

        try:
            ws = await websockets.connect(url, compression=None)
            log.info("EVENT WebSocket 接続完了")
            await self._event_client.receive_loop(
                ws,
                self._on_ec_event,
                reconnect_fn=_reconnect,
            )
        except asyncio.CancelledError:
            log.info("EVENT WebSocket ループがキャンセルされました")
            raise
        except Exception as exc:
            log.error("EVENT WebSocket ループが終了しました: %s", exc)

    async def _on_ec_event(self, frame_type: str, event: object) -> None:
        """EC フレームを IPC イベントに変換して outbox に push する（Phase O2）。

        TachibanaEventClient.receive_loop() の on_event コールバック。
        notification_type:
            "1" = 受付（OrderAccepted は submit 時に処理済み → 無視）
            "2" = 約定 → OrderFilled
            "3" = 取消 → OrderCanceled
            "4" = 失効 → OrderExpired
        """
        if frame_type != "EC":
            return

        ec: OrderEcEvent = event  # type: ignore[assignment]
        client_order_id = self._venue_to_client.get(ec.venue_order_id)
        if not client_order_id:
            log.warning(
                "EC event: venue_order_id=%r の client_order_id が不明 (マップ未登録)",
                ec.venue_order_id,
            )
            return

        nt = ec.notification_type
        if nt == "1":
            return  # 受付通知は OrderAccepted で処理済み
        elif nt == "2":
            last_qty_int = int(ec.last_qty or "0")
            prev = self._fill_cumulative.get(ec.venue_order_id, 0)
            cumulative = prev + last_qty_int
            self._fill_cumulative[ec.venue_order_id] = cumulative
            self._outbox.append({
                "event": "OrderFilled",
                "client_order_id": client_order_id,
                "venue_order_id": ec.venue_order_id,
                "trade_id": ec.trade_id,
                "last_qty": ec.last_qty or "0",
                "last_price": ec.last_price or "0",
                "cumulative_qty": str(cumulative),
                "leaves_qty": ec.leaves_qty or "0",
                "ts_event_ms": ec.ts_event_ms,
            })
        elif nt == "3":
            self._outbox.append({
                "event": "OrderCanceled",
                "client_order_id": client_order_id,
                "venue_order_id": ec.venue_order_id,
                "ts_event_ms": ec.ts_event_ms,
            })
        elif nt == "4":
            self._outbox.append({
                "event": "OrderExpired",
                "client_order_id": client_order_id,
                "venue_order_id": ec.venue_order_id,
                "ts_event_ms": ec.ts_event_ms,
            })
        else:
            log.debug("EC event: 未知の notification_type=%r (無視)", nt)

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

    # ------------------------------------------------------------------
    # N1.4: nautilus engine dispatch (StartEngine / StopEngine / LoadReplayData)
    # ------------------------------------------------------------------

    async def _handle_load_replay_data(
        self, msg: dict, *, base_dir: Path | None = None
    ) -> None:
        """LoadReplayData IPC: J-Quants ファイルを件数確認だけして ReplayDataLoaded 送出。

        ``base_dir`` はテスト用 fixtures パス上書き。本番は ``None`` (= S:/j-quants)。

        M3: mode="live" では replay 系 IPC を受理しない（D8 / spec §3.2 起動時固定）。
        Error{request_id, code="mode_mismatch"} を返して即 return する。
        """
        instrument_id = msg.get("instrument_id", "")
        start_date = msg.get("start_date", "")
        end_date = msg.get("end_date", "")
        granularity = msg.get("granularity", "Trade")
        request_id = msg.get("request_id")

        if self._mode != "replay":
            self._outbox.append(
                {
                    "event": "Error",
                    "request_id": request_id,
                    "code": "mode_mismatch",
                    "message": (
                        f"LoadReplayData rejected: mode={self._mode!r} "
                        "(replay IPC only allowed in mode='replay')"
                    ),
                }
            )
            return

        from engine.nautilus.jquants_loader import (
            load_daily_bars,
            load_minute_bars,
            load_trades,
        )

        try:
            bars_loaded = 0
            trades_loaded = 0
            kwargs = {"base_dir": base_dir} if base_dir is not None else {}
            if granularity == "Trade":
                trades_loaded = sum(
                    1 for _ in load_trades(instrument_id, start_date, end_date, **kwargs)
                )
            elif granularity == "Minute":
                bars_loaded = sum(
                    1 for _ in load_minute_bars(instrument_id, start_date, end_date, **kwargs)
                )
            elif granularity == "Daily":
                bars_loaded = sum(
                    1 for _ in load_daily_bars(instrument_id, start_date, end_date, **kwargs)
                )
            else:
                raise ValueError(f"unknown granularity: {granularity!r}")
        except Exception as exc:
            log.error(
                "LoadReplayData failed: instrument_id=%r granularity=%r",
                instrument_id,
                granularity,
                exc_info=True,
            )
            self._outbox.append(
                {
                    "event": "Error",
                    "request_id": request_id,
                    "code": "load_replay_data_failed",
                    "message": str(exc),
                }
            )
            return

        # H-H: LoadReplayData 時にポートフォリオをリセット（strategy 確定前なので cash=0）。
        from decimal import Decimal as _Decimal
        self._replay_strategy_id = ""
        self._replay_portfolio.reset(_Decimal(0))

        self._outbox.append(
            {
                "event": "ReplayDataLoaded",
                # M-8 (R1b / schema 2.5): 単独 LoadReplayData は strategy 未起動のため
                # null を送る (旧 minor=4 では "" だった)。Rust 側は Option<String>::None
                # で受ける。strategy 経路 (start_backtest_replay) は別 emit で具体値。
                "strategy_id": None,
                "bars_loaded": bars_loaded,
                "trades_loaded": trades_loaded,
                "ts_event_ms": int(time.time() * 1000),
            }
        )

    async def _handle_start_engine(
        self, msg: dict, *, base_dir: Path | None = None
    ) -> None:
        """StartEngine IPC: BacktestEngine を起動して EngineStarted/EngineStopped を送出。

        mode と engine kind の不整合は ``validate_start_engine`` が ValueError を
        raise → Error{request_id, code='mode_mismatch'} 送出。

        実行は ``asyncio.to_thread`` で別 thread に逃がす。BacktestEngine.run() は
        同期実行で長時間 block するので、event loop を塞がないため。
        IPC イベントは別 thread から ``loop.call_soon_threadsafe(self._outbox.append, evt)``
        経由で main loop に戻して append する (C1)。``_Outbox.append`` 内の
        ``asyncio.Event.set`` が main loop 上で実行されることを保証する。

        H-G (R1b): main thread からのエラー append (validation 失敗 / race guard /
        parse 失敗 / TimeoutError / except) も同様に ``call_soon_threadsafe`` で
        統一する。混在させると worker thread が schedule 済の callback と main thread
        の直 append が逆順で観測される race が残る。
        ``_emit`` ローカル関数で単一窓口化する。

        例外時 (engine.run() が途中で raise) は ``EngineStarted`` 送出済みで
        ``EngineStopped`` が抜けるため、except で final_equity="0" の
        EngineStopped を補完送出する (H1)。
        """
        from engine.schemas import EngineError as EngineErrorModel
        from engine.nautilus.engine_runner import NautilusRunner

        engine_kind = msg.get("engine", "")
        strategy_id = msg.get("strategy_id", "")
        config = msg.get("config", {})
        request_id = msg.get("request_id")

        # MEDIUM-2: request_id が None/空の場合は防御的に早期 return する。
        # Rust 側の StartEngine は必ず request_id を付けるが、不正メッセージ対策。
        if not request_id:
            log.warning("StartEngine: missing request_id, ignoring")
            return

        # H-2 (R2 review-fix R2): R1b の H-G で main thread と worker thread の
        # append を ``call_soon_threadsafe`` 経由で統一したが、これは
        # ``_handle_start_engine`` が ``asyncio.CancelledError`` を受け取ったときに
        # scheduled callback が drain されず Error イベントが落ちる cancel-unsafe な
        # 経路を生む。main thread 経路 (validation 失敗 / race guard / parse 失敗 /
        # TimeoutError / except) は coroutine 内で実行されているため race がなく、
        # 直接 ``self._outbox.append(...)`` で安全に書ける。
        # worker thread 経路 (``asyncio.to_thread`` 内の ``_on_event``) のみ
        # ``call_soon_threadsafe`` で main loop に戻す。
        loop = asyncio.get_running_loop()

        def _emit_direct(evt: dict) -> None:
            """Main-thread (coroutine) 経路用。直接 outbox に append する。"""
            self._outbox.append(evt)

        def _emit_threadsafe(evt: dict) -> None:
            """Worker-thread (``asyncio.to_thread``) 経路用。``call_soon_threadsafe``
            で main loop に戻して append を schedule する。
            """
            loop.call_soon_threadsafe(self._outbox.append, evt)

        # H-2: 内部互換のため ``_emit`` 名は残す (main-thread 直 append としてセマンティク変更)。
        # 既存パスは _emit_direct と等価。
        _emit = _emit_direct

        # _drain は ``asyncio.to_thread`` 完了後の保険として継続する。
        # worker thread が schedule した callback を drain して呼び出し側が
        # outbox を直ちに観測できる semantics を保つ。
        async def _drain() -> None:
            await asyncio.sleep(0)

        try:
            validate_start_engine(self._mode, engine_kind)
        except ModeMismatchError as exc:
            # H3: バリデーション失敗は Error{request_id} のみ送出。
            # EngineError は接続レベル専用 (auth_failed / schema_mismatch) に限定する。
            _emit(
                {
                    "event": "Error",
                    "request_id": request_id,
                    "code": "mode_mismatch",
                    "message": str(exc),
                }
            )
            await _drain()
            return
        except UnknownEngineKindError as exc:
            # M-10: 不明な engine kind は別 code で返す
            _emit(
                {
                    "event": "Error",
                    "request_id": request_id,
                    "code": "unknown_engine_kind",
                    "message": str(exc),
                }
            )
            await _drain()
            return

        # M-14: 同一 strategy_id が既に走行中なら早期 reject (race guard)。
        # 連投で _engine_tasks を上書きすると、先行 runner ハンドルを失い StopEngine が
        # 効かなくなる。
        if strategy_id in self._engine_tasks:
            _emit(
                {
                    "event": "Error",
                    "request_id": request_id,
                    "code": "engine_already_running",
                    "message": f"engine for strategy_id={strategy_id!r} is already running",
                }
            )
            await _drain()
            return

        # M4: initial_cash を to_thread 前にパースし、parse 失敗は即 Error で返す。
        # LOW-A: バリデーション成功後に runner と _engine_tasks を登録することで、
        # parse 失敗時に残骸が _engine_tasks に残らない。
        try:
            initial_cash = int(config.get("initial_cash", "0"))
        except (ValueError, TypeError) as exc:
            _emit(
                {
                    "event": "Error",
                    "request_id": request_id,
                    "code": "invalid_config",
                    "message": f"initial_cash: {exc}",
                }
            )
            await _drain()
            return

        runner = NautilusRunner()
        # 走行中ハンドルを保持 (StopEngine で参照)。N1.4 は同時 1 戦略想定。
        self._engine_tasks[strategy_id] = runner

        # M-8 (R2 review-fix R2): StartEngine 受理直後に _replay_strategy_id を確定させる。
        # これ以降に GetBuyingPower(replay) が走った場合でも正しい strategy_id が
        # ReplayBuyingPower イベントに乗る。runner 完了後の result からの上書きはそのまま
        # 残す（fallback として最終的な値で上書きする想定）。
        if self._mode == "replay":
            self._replay_strategy_id = strategy_id

        # C1 / H-2 (R2 review-fix R2): worker thread からの append は
        # ``_emit_threadsafe`` (= ``call_soon_threadsafe``) を使う。main thread の
        # 直 append とは別経路だが、両者とも asyncio loop の単一スレッド上で順序付けられる。
        def _on_event(evt: dict) -> None:
            _emit_threadsafe(evt)

        # H1 補助: EngineStarted を送出したかどうかを worker thread から記録する。
        # 例外時に未送出なら EngineStopped 補完を抑制する。
        # ただし TimeoutError パスでは started_marker に依存しない (HIGH-1)。
        started_marker = {"sent": False}

        def _on_event_tracked(evt: dict) -> None:
            if evt.get("event") == "EngineStarted":
                started_marker["sent"] = True
            _on_event(evt)

        # C-1: result_holder で ReplayBacktestResult をキャプチャし、fills を portfolio に反映。
        result_holder: list = [None]

        def _run() -> None:
            result_holder[0] = runner.start_backtest_replay(
                strategy_id=strategy_id,
                instrument_id=config.get("instrument_id", ""),
                start_date=config.get("start_date", ""),
                end_date=config.get("end_date", ""),
                granularity=config.get("granularity", "Trade"),
                initial_cash=initial_cash,
                base_dir=base_dir,
                on_event=_on_event_tracked,
            )

        try:
            # H2: timeout=3600s でラップし、TimeoutError を code="timeout" で送出。
            await asyncio.wait_for(asyncio.to_thread(_run), timeout=3600.0)
            # C-1: 実行完了後に fills を PortfolioView に反映する。
            result = result_holder[0]
            if result is not None:
                from decimal import Decimal as _Decimal
                self._replay_strategy_id = result.strategy_id
                self._replay_portfolio.reset(_Decimal(initial_cash))
                for fill in result.portfolio_fills:
                    self._replay_portfolio.on_fill(
                        fill.instrument_id, fill.side, fill.qty, fill.price
                    )
        except asyncio.TimeoutError as exc:
            log.error(
                "StartEngine timed out: strategy_id=%r",
                strategy_id,
                exc_info=True,
            )
            # worker thread はキャンセルできないが stop() シグナルを送ってリソースを解放する。
            try:
                runner.stop()
            except Exception:
                pass
            # HIGH-1: timeout 後も worker thread は走り続けるため started_marker に依存しない。
            # Rust 側は EngineStarted なしの EngineStopped を no-op として扱う。
            _emit(
                {
                    "event": "EngineStopped",
                    "strategy_id": strategy_id,
                    "final_equity": "0",
                    "ts_event_ms": int(time.time() * 1000),
                }
            )
            # MEDIUM-1: str(asyncio.TimeoutError()) は空文字なので fallback メッセージを使う。
            timeout_msg = str(exc) or f"StartEngine timed out after 3600s: strategy_id={strategy_id!r}"
            _emit(
                EngineErrorModel(
                    code="timeout",
                    message=timeout_msg,
                    strategy_id=strategy_id,
                ).model_dump()
            )
            # H1: Error{request_id} で Rust の待機を解除する
            _emit(
                {
                    "event": "Error",
                    "request_id": request_id,
                    "code": "timeout",
                    "message": timeout_msg,
                }
            )
        except Exception as exc:
            # M3: exc_info=True を追加し strategy_id をコンテキストとして記録
            log.error("StartEngine failed: strategy_id=%r", strategy_id, exc_info=True)
            # H1: EngineStarted を送出済みで EngineStopped を未送出なら補完。
            # Rust 側 state machine が stuck しないようにする。
            if started_marker["sent"]:
                _emit(
                    {
                        "event": "EngineStopped",
                        "strategy_id": strategy_id,
                        "final_equity": "0",
                        "ts_event_ms": int(time.time() * 1000),
                    }
                )
            _emit(
                EngineErrorModel(
                    code="engine_run_failed",
                    message=str(exc),
                    strategy_id=strategy_id,
                ).model_dump()
            )
            # H1: Error{request_id} で Rust の 60 秒ハングを解消
            _emit(
                {
                    "event": "Error",
                    "request_id": request_id,
                    "code": "engine_run_failed",
                    "message": str(exc),
                }
            )
        finally:
            self._engine_tasks.pop(strategy_id, None)
            # M-8 (R2 review-fix R2): 走行終了時に _replay_strategy_id をリセット。
            # 次の GetBuyingPower(replay) は空 strategy_id を返し、UI 側で
            # 「未実行」を識別できるようにする。
            if self._mode == "replay" and self._replay_strategy_id == strategy_id:
                self._replay_strategy_id = ""
            # H-G (R1b): 関数 return 前に scheduled callback を drain して呼び出し側
            # (テスト含む) が outbox を直ちに観測できるようにする。
            await _drain()

    async def _handle_stop_engine(self, msg: dict) -> None:
        """StopEngine IPC: 走行中 NautilusRunner を停止する。

        N1.4 では BacktestEngine.run() が完了するまで待つしかないため、
        記録されている runner があれば ``stop()`` を呼ぶだけ。完了待ちは
        ``_handle_start_engine`` の to_thread 完了に任せる。
        """
        strategy_id = msg.get("strategy_id", "")
        runner = self._engine_tasks.get(strategy_id)
        if runner is None:
            log.info("StopEngine: no running engine for strategy_id=%r", strategy_id)
            return
        try:
            runner.stop()
        except Exception as exc:
            log.warning("StopEngine: runner.stop() raised: %s", exc)
        # M-8 (R2 review-fix R2): StopEngine 受理時点で _replay_strategy_id を直ちに
        # クリアする。runner 完了は別 thread で進行するため、その間の
        # GetBuyingPower(replay) も「未実行」として扱う。
        if self._mode == "replay" and self._replay_strategy_id == strategy_id:
            self._replay_strategy_id = ""

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
