"""WebSocket IPC server — loopback-only, multi-client (broadcast event, FCFS command), token-authenticated."""

from __future__ import annotations

import asyncio
import copy
import hmac
import logging
import os
import queue as _stdlib_queue
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable
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
from engine.exchanges.kabusapi import KabuStationVenue
from engine.exchanges.kabusapi_adapter import KabuStationAdapter
from engine.exchanges.kabusapi_url import resolve_kabu_env
from engine.exchanges.kabusapi_auth import (
    KabuApiError,
    KabuConnectionError as KabuConnectionError,
    KabuLoginCancelledError,
    KabuRateLimitError,
    KabuTradeCancelledError,
    KabuTradeLockedOutError,
    KabuTradePasswordInvalidError,
    KabuTokenExpiredError,
)
from engine.exchanges.binance import BinanceWorker
from engine.exchanges.bybit import BybitWorker
from engine.exchanges.hyperliquid import HyperliquidWorker
from engine.exchanges.mexc import MexcWorker
from engine.exchanges.okex import OkexWorker
from engine.kabu_push_pipeline import kabu_board_to_wire_dict, kabu_execution_to_wire_dict
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
    fetch_positions as tachibana_fetch_positions,
)
from engine.schemas import OrderListFilter as SchemaOrderListFilter, OrderModifyChange as SchemaOrderModifyChange
from engine.schemas import (
    AUTH_FAILED_CODE,
    SCHEMA_MAJOR,
    SCHEMA_MINOR,
    AppMode,
    AttemptedCommand,
    ClientConnected,
    ClientDisconnected,
    EngineBusy,
    EngineError,
    Hello,
    Ready,
    SubmitOrderRequest,
)
from engine.mode import (
    ModeMismatchError,
    UnknownEngineKindError,
    nautilus_capabilities,
    validate_start_engine,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# B3: Engine state machines (Replay and Live)
# ---------------------------------------------------------------------------


class ReplayState(Enum):
    IDLE = auto()
    LOADED = auto()
    RUNNING = auto()
    PAUSED = auto()
    STOPPING = auto()


class LiveState(Enum):
    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    TRADING = auto()    # Phase 2: live strategy 実行中
    STOPPING = auto()   # Phase 2: graceful stop 待ち


# ── Issue 5: replay PositionsUpdated push helper ────────────────────────────


def build_replay_positions_event(portfolio_state: dict, ts_ms: int) -> dict:
    """portfolio_state dict から replay 用 PositionsUpdated event を生成する。

    portfolio_state 形式: ``{"cash": str, "positions": {inst: {qty, cost}},
    "last_prices": {inst: str}}`` (PortfolioView.to_snapshot_dict() 由来)。

    market_value = qty * last_price。last_price 不明時は "" を返す
    (PositionRecordWire schema が "" を許容)。
    instrument_id 順にソートし、決定論性を確保する。
    """
    from decimal import Decimal, InvalidOperation

    positions = portfolio_state.get("positions", {}) or {}
    last_prices = portfolio_state.get("last_prices", {}) or {}

    rows = []
    for inst_id in sorted(positions.keys()):
        pos_data = positions[inst_id]
        qty_val = pos_data.get("qty", "0")

        # H-1: Wrap Decimal(qty_val) in try/except to handle malformed qty
        # (e.g., qty_val="invalid", "N/A", etc.) that slipped past validation.
        # Silent failure would stop fill emit path (PositionsUpdated never sent).
        try:
            qty_str = str(int(Decimal(qty_val))) if qty_val not in ("", None) else "0"
        except (InvalidOperation, ValueError, TypeError):
            qty_str = "0"

        price_raw = last_prices.get(inst_id, "")
        try:
            if price_raw not in ("", None):
                # H-1: Also protect market_value conversion from invalid price
                mv = Decimal(qty_str) * Decimal(str(price_raw))
                mv_str = str(int(mv))
            else:
                mv_str = ""
        except (InvalidOperation, ValueError, TypeError):
            mv_str = "0"

        rows.append({
            "instrument_id": inst_id,
            "qty": qty_str,
            "market_value": mv_str,
            # PositionType (Rust dto.rs) は cash | margin_credit | margin_general のみ。
            # replay は現物のみシミュレートするため "cash" 固定。
            "position_type": "cash",
            "tategyoku_id": None,
            "venue": "replay",
        })

    return {
        "event": "PositionsUpdated",
        "request_id": "",
        "venue": "replay",
        "positions": rows,
        "ts_ms": ts_ms,
    }


# ── schema 3.16: Snapshot ring buffer ────────────────────────────────────────


@dataclass
class ReplaySnapshot:
    """One granularity-boundary snapshot for Step- (snapshot ring buffer).

    仕様書 §Snapshot ring buffer 設計参照。
    """

    step_index: int           # 粒度ステップ番号（0-based）
    portfolio: dict           # to_ipc_dict() 由来の ReplayBuyingPower event dict（UI 送信用）
    open_orders: list         # 未約定注文リスト
    strategy_state: object    # copy.deepcopy(strategy) — 任意の Python オブジェクト
    ui_events: list = field(default_factory=list)  # この step で送出した UI イベント群
    # schema 3.16 HIGH-2: runner 側 _portfolio / _last_prices 復元用のフル状態。
    # to_snapshot_dict() 由来: {"cash": str, "positions": {...}, "last_prices": {...}}
    # engine_runner がスナップショット取得時に populate し、StepBackward 後に runner へ戻す。
    portfolio_state: dict = field(default_factory=dict)


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

# Maximum concurrent WebSocket clients (GUI 1 + helper 1 + headroom 2).
MAX_CONNECTIONS = 4

# Phase 8 R1 / Phase 2 (WS-MED1): handshake() で最初の Hello を待つタイムアウト秒数。
# 接続後に Hello を送って来ないクライアント（半開接続 / probe / 不正クライアント）を
# 検出して 1002 で切断する。テストでは monkeypatch で短縮する。
_HANDSHAKE_TIMEOUT_S: float = 15.0


class _Broadcaster:
    """Per-connection asyncio.Queue-based broadcaster.

    Each connected client gets its own queue so event fanout is non-blocking
    and slow clients cannot block fast ones.  The ``append`` interface is
    identical to the old ``_Outbox`` so all ``self._outbox.append(...)`` call
    sites continue to work without modification.

    ``_q`` is a backward-compatibility deque for unit tests that inspect
    ``srv._outbox._q`` directly (e.g. test_ec_server_dispatch.py). Items
    appended while no client is connected land only in ``_q``. Items appended
    while clients are connected go to every client queue AND ``_q`` so the
    combined length/iteration matches the old single-client _Outbox behavior.
    """

    def __init__(self) -> None:
        # H2: dict key は ServerConnection (WebSocket パス) または _GrpcSessionKey
        # (gRPC パス) のいずれか。Union 型を避けて Any にすることで両パスの型エラーを回避する。
        self._queues: dict[Any, asyncio.Queue] = {}
        # Compat deque used by tests that inspect _outbox._q directly.
        self._q: deque[dict] = deque()

    def add_conn(self, ws: Any) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._queues[ws] = q
        return q

    def remove_conn(self, ws: Any) -> None:
        self._queues.pop(ws, None)

    def append(self, item: dict) -> None:
        """Enqueue item to every connected client's queue (fanout).

        H-SF2: 1クライアントのキューが満杯でも後続クライアントへの broadcast を
        継続する。QueueFull は WARNING でログし、イベントを破棄する（drop）。
        """
        for ws, q in list(self._queues.items()):
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                log.warning(
                    "[_Broadcaster] outbox full for client, dropping event %s",
                    item.get("event", "?"),
                )
            except Exception as exc:
                log.warning("[_Broadcaster] put_nowait failed: %s", exc)
        # Also write to compat deque so unit tests that read _q still work.
        self._q.append(item)

    def send_to(self, ws: Any, item: dict) -> None:
        """Unicast: enqueue item only to a single connection's queue.

        M-GP8 / Silent-M2 (Phase 8 R1 / Phase 2): broadcast でなく特定の接続だけに
        送るべきイベント (EngineBusy / malformed_json Error 等) のための経路。
        対象 ws が登録されていない場合 (テストが ``__new__`` で server を生成して
        ws を登録せずに dispatch するケース) は compat deque への書き込みのみ行う。
        queue 満杯のときは WARNING ログだけ残して drop する。

        いずれの場合も compat deque (``self._q``) には append する。これは既存テスト
        が ``server._outbox`` を反復して event を観測する規約に合わせるため。
        """
        q = self._queues.get(ws) if ws is not None else None
        if q is None:
            # テスト経路 / 登録前: compat deque だけに記録する。
            self._q.append(item)
            return
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:
            log.warning(
                "[_Broadcaster] send_to: outbox full, dropping event %s",
                item.get("event", "?"),
            )
        except Exception as exc:
            log.warning("[_Broadcaster] send_to: put_nowait failed: %s", exc)
        # Mirror to compat deque so unit tests inspecting ``_outbox`` keep working.
        self._q.append(item)

    def popleft(self) -> dict:
        return self._q.popleft()

    def count(self) -> int:
        return len(self._queues)

    def __len__(self) -> int:
        # For test assertions: return number of pending items in compat deque.
        return len(self._q)

    def __bool__(self) -> bool:
        return bool(self._q) or bool(self._queues)

    def __iter__(self):
        return iter(list(self._q))


class _Outbox:

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
        dev_kabu_login_allowed: bool = False,
        dev_kabu_trade_password_allowed: bool = False,
        cache_dir: Path | None = None,
        config_dir: Path | None = None,
        tachibana_is_demo: bool = True,
        wal_path: Path | None = None,
    ) -> None:
        self._port = port
        self._token = token
        self._dev_tachibana_login_allowed = dev_tachibana_login_allowed
        self._dev_kabu_login_allowed = dev_kabu_login_allowed
        self._dev_kabu_trade_password_allowed = dev_kabu_trade_password_allowed
        # B3: cache_dir is plumbed from __main__.py (T4 stdin payload). It
        # falls back to a per-user default so dev mode (`uv run python -m
        # engine --port ... --token ...`) keeps working without an explicit
        # arg. The TachibanaWorker is the only worker that uses it (master
        # cache path) so a None here would crash worker construction below.
        if cache_dir is None:
            _env_cache = os.environ.get("FLOWSURFACE_CACHE_DIR")
            cache_dir = Path(_env_cache) if _env_cache else Path.home() / ".cache" / "flowsurface" / "engine"
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
        self._connections: set[ServerConnection] = set()
        self._shutdown_event = asyncio.Event()
        self._outbox_event = asyncio.Event()  # kept for shutdown() compat
        self._engine_session_id: UUID = uuid.uuid4()
        # N1.13: 起動時固定 mode (`"live"` | `"replay"`).
        # Hello 受信時に上書きする。default は旧クライアント互換の "live"。
        # C-Type2: AppMode Literal で illegal 文字列代入を静的に防ぐ。
        self._mode: AppMode = "live"
        # 防衛: 不正値の代入をルートで検出するための pin (mypy/pyright で漏れた場合の保険)。
        # L-R4-1 (ラウンド4): `python -O` で assert は無効化されるため if/raise に統一。
        # `_emit` invariant (M-R3-2) と同じ方針。
        if self._mode not in ("live", "replay"):
            raise RuntimeError(f"DataEngineServer: invalid mode {self._mode!r}")

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

        # ── KabuStation state (K7 / Phase 1) ─────────────────────────────
        # H-2: resolve_kabu_env() をインスタンス初期化時にキャッシュ。
        # _build_ready() と _startup_kabu_station() が同じ env を参照するよう保証する。
        self._kabu_env: str = resolve_kabu_env()
        self._kabu_venue: KabuStationVenue | None = None
        self._kabu_login_inflight = asyncio.Lock()
        self._kabu_startup_task: asyncio.Task | None = None
        self._kabu_fill_poller_task: asyncio.Task | None = None  # [H-1]
        # C1 配線: kabu PUSH adapter と採番カウンター
        self._kabu_adapter: KabuStationAdapter = KabuStationAdapter([])
        self._kabu_push_seq: int = 0
        self._kabu_push_ssid: str | None = None
        # VenueReady を emit した venue 名。None = 未接続 / "tachibana" / "kabu_station"。
        # StartEngine(engine="Live") の venue guard に使う。
        self._connected_venue: str | None = None

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
        # N1.11: streaming replay 中断用 stop_event レジストリ。
        # _handle_stop_engine が set()、_handle_start_engine の finally で pop する。
        self._engine_stop_events: dict[str, Any] = {}
        # N1.11: streaming replay の pacing 倍率。SetReplaySpeed で変更される。
        # compute_sleep_sec は multiplier >= 1 を要求するため、デフォルト 1 は安全。
        self._replay_speed_multiplier: int = 1
        # schema 3.15: Pause/Step 制御フィールド。
        self._replay_paused: bool = False
        self._replay_step_request: int = 0  # StepReplay で +1、runner が消費して -1
        # schema 3.16: Snapshot ring buffer for Step- (StepBackward).
        # M-3: protected by threading.Lock because push_snapshot is called from the
        # runner thread (asyncio.to_thread), while StepBackward reads/pops from the
        # asyncio event loop thread.
        self._replay_snapshots: deque[ReplaySnapshot] = deque(maxlen=1000)
        self._replay_snapshots_lock = threading.Lock()
        # HIGH-2: called (thread-safe) when buffer transitions 0→1 so Rust ⏮ button enables.
        # Set to _emit_threadsafe lambda in _handle_start_engine; None outside an engine run.
        self._notify_history_fn: "Callable[[], None] | None" = None
        # HIGH-1: runner thread writes strategy_state here when _restore_snapshot fires.
        # Engine runner reads + clears before processing the next step.
        self._restore_strategy_holder: list = [None]
        # HIGH-2: runner thread reads portfolio_state from here when _restore_snapshot fires.
        # Holds {"cash": str, "positions": {...}, "last_prices": {...}} or None.
        self._restore_portfolio_holder: list = [None]

        # B3: State machines for replay and live command gating.
        self._replay_state: ReplayState = ReplayState.IDLE
        self._live_state: LiveState = LiveState.DISCONNECTED

        # Phase 2: live strategy 用の FD/EC frame キュー。
        # server.py (loop A) が put_nowait し、start_live worker が get して消費する。
        self._live_fd_queue: _stdlib_queue.Queue = _stdlib_queue.Queue(maxsize=10_000)
        self._live_ec_queue: _stdlib_queue.Queue = _stdlib_queue.Queue(maxsize=1_000)

        # N1.16: REPLAY 仮想ポートフォリオ（CLMZanKaiKanougaku を呼ばない純粋 Python 実装）。
        # LoadReplayData 受信時に initial_cash で reset() する。
        from decimal import Decimal as _Decimal
        from engine.nautilus.portfolio_view import PortfolioView as _PortfolioView
        self._replay_portfolio = _PortfolioView(_Decimal("1000000"))
        self._replay_strategy_id: str = ""
        # Streaming replay 中に約定した注文を蓄積する（GetOrderList{venue:"replay"} で返す）。
        # _handle_start_engine の EngineStarted 受信時にリセットされる。
        self._replay_streaming_fills: list[dict] = []
        # schema 3.14: Replay セッション識別子。LoadReplayData / start_backtest_replay
        # 受理ごとに `_next_replay_session_epoch()` で +1 し ReplayDataLoaded に同梱する。
        # GUI は前 epoch のペインを全閉じして新 epoch のペインを構築する。
        self._replay_session_epoch: int = 0

        self._outbox = _Broadcaster()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        self._shutdown_event.set()
        self._outbox_event.set()

    def _next_replay_session_epoch(self) -> int:
        """schema 3.14: 新しい replay セッションを開始し epoch を払い出す。

        **発番元は ``LoadReplayData`` ハンドラのみ**。``start_backtest_replay`` /
        ``start_backtest_replay_streaming`` 経路は ``self._replay_session_epoch``
        の現在値を再利用すること（1 ファイル切替 = 1 epoch の不変条件を守るため）。
        ``LoadReplayData→StartEngine`` の 2-hop で +2 進めると GUI が直前
        ``LoadReplayData`` で生成したペインを drain して誤再生成する
        （review-fix R1 HIGH-1）。

        engine プロセス起動後に単調増加（初期値 0、初回呼び出しで 1 を返す）。
        プロセス再起動で 0 に戻るが GUI 側は engine 切断時に
        ``last_replay_session_epoch`` を ``None`` にリセットするため衝突しない。

        スレッド安全: 本メソッドは ``DataEngineServer`` の event loop main thread
        からのみ呼ぶこと（``_handle_load_replay_data`` のみで OK）。worker thread
        (``asyncio.to_thread`` 内) からは呼ばない。
        """
        self._replay_session_epoch += 1
        return self._replay_session_epoch

    def _push_replay_snapshot(
        self,
        step_index: int,
        portfolio: dict,
        open_orders: list,
        strategy_state: object,
        ui_events: list,
        portfolio_state: "dict | None" = None,
    ) -> None:
        """schema 3.16: snapshot ring buffer に 1 粒度境界スナップショットを push する。

        - ``copy.deepcopy(strategy_state)`` が失敗した場合はスナップショットを保存せずに
          ``logger.warning`` でログして skip する（deepcopy 失敗対策）。
        - ``has_history`` の変化は呼び出し元の pacing ループが担当しない。
          StepBackward ハンドラが pop 後に ReplayHistoryChanged を emit する。

        スレッド安全: ``_replay_snapshots_lock`` で保護する。runner thread
        (asyncio.to_thread 内) から呼ばれる想定。
        """
        try:
            strategy_copy = copy.deepcopy(strategy_state)
        except Exception as exc:
            log.warning(
                "snapshot deepcopy failed at step %d: %s — skipping snapshot",
                step_index,
                exc,
            )
            return
        snap = ReplaySnapshot(
            step_index=step_index,
            portfolio=portfolio,
            open_orders=list(open_orders),
            strategy_state=strategy_copy,
            ui_events=list(ui_events),
            portfolio_state=dict(portfolio_state) if portfolio_state else {},
        )
        with self._replay_snapshots_lock:
            was_empty = len(self._replay_snapshots) == 0
            self._replay_snapshots.append(snap)
        log.debug("push_snapshot: step_index=%d buffer_len=%d", step_index, len(self._replay_snapshots))
        # HIGH-2: notify Rust that history is now available (0→1 transition only).
        if was_empty and (fn := self._notify_history_fn) is not None:
            fn()

    def _restore_snapshot(self, snap: ReplaySnapshot) -> None:
        """schema 3.16: ``ReplaySnapshot`` の内容を server state に復元する。

        ポートフォリオ・注文・戦略状態を snapshot から書き戻す。
        呼び出し元（StepBackward ハンドラ）が ``_replay_snapshots_lock`` を
        保持した状態で pop した後に呼ぶこと（unlock 後に呼んでも安全）。
        """
        # ポートフォリオ復元: _replay_portfolio はデシリアライズ済み dict から再構築
        try:
            from decimal import Decimal as _Decimal
            from engine.nautilus.portfolio_view import PortfolioView as _PortfolioView
            # portfolio_state（フル状態）があればそれを優先。なければ IPC dict（cash のみ）で復元。
            _pstate = snap.portfolio_state if snap.portfolio_state else snap.portfolio
            restored_portfolio = _PortfolioView(_Decimal(str(_pstate.get("cash", "0"))))
            restored_portfolio._restore_from_dict(_pstate)
            self._replay_portfolio = restored_portfolio
        except Exception as exc:
            log.warning("_restore_snapshot: portfolio restore failed: %s", exc)
        # 注文リスト復元
        self._replay_streaming_fills = list(snap.open_orders)
        # HIGH-1: strategy_state をホルダーに置く。engine_runner が次ステップ開始前に
        # strategy_instance の属性を in-place で更新して None にリセットする。
        self._restore_strategy_holder[0] = snap.strategy_state
        # HIGH-2: portfolio_state をホルダーに置く。engine_runner が次ステップ開始前に
        # _portfolio / _last_prices をローカルで復元して None にリセットする。
        if snap.portfolio_state:
            self._restore_portfolio_holder[0] = snap.portfolio_state
        log.debug("_restore_snapshot: step_index=%d restored strategy_state queued", snap.step_index)

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
        # Reject connections exceeding the limit before handshake.
        if self._outbox.count() >= MAX_CONNECTIONS:
            # M-2 (silent): close する前に EngineError を送って、
            # クライアント側で「server full」を分かるようにする（token は含まない）。
            try:
                import orjson
                from engine.schemas import EngineError as _EngineError
                err_payload = _EngineError(
                    code="max_connections", message="server full"
                ).model_dump()
                await ws.send(orjson.dumps(err_payload).decode())
            except Exception:
                pass
            try:
                await ws.close(1008, "max connections exceeded")
            except Exception:
                pass
            log.warning("Connection rejected: MAX_CONNECTIONS=%d reached", MAX_CONNECTIONS)
            return

        recv_task: asyncio.Task | None = None
        send_task: asyncio.Task | None = None
        startup_task: asyncio.Task | None = None
        try:
            await self._handshake(ws)
            q = self._outbox.add_conn(ws)
            self._connections.add(ws)
            # Broadcast ClientConnected to all clients (including the new one).
            count = self._outbox.count()
            self._outbox.append(ClientConnected(count=count).model_dump())

            # T-SC3: Python self-initiates Tachibana login after handshake.
            # The task runs concurrently with the recv/send loops.
            # Skip in replay mode — Tachibana is irrelevant for backtesting.
            if self._mode != "replay":
                startup_task = asyncio.create_task(self._startup_tachibana())
                self._tachibana_startup_task = startup_task
            recv_task = asyncio.create_task(self._recv_loop(ws))
            send_task = asyncio.create_task(self._send_loop(ws, q))
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
            self._outbox.remove_conn(ws)
            self._connections.discard(ws)
            # Broadcast ClientDisconnected after removal so count is accurate.
            count = self._outbox.count()
            self._outbox.append(ClientDisconnected(count=count).model_dump())

            # C1 (Phase 8.1b R10 / B1): Server-lifecycle resources — Tachibana
            # startup task, EVENT loop, login latch, and stream subscriptions —
            # are shared across all clients. Tearing them down on every
            # per-connection finally block was killing live state for the OTHER
            # connected clients. Only cleanup when the LAST client disconnects.
            if not self._connections:
                task_to_cancel = self._tachibana_startup_task
                if task_to_cancel is not None and not task_to_cancel.done():
                    task_to_cancel.cancel()
                    try:
                        await task_to_cancel
                    except (asyncio.CancelledError, Exception):
                        pass
                self._tachibana_startup_task = None
                kabu_task = self._kabu_startup_task
                if kabu_task is not None and not kabu_task.done():
                    kabu_task.cancel()
                    try:
                        await kabu_task
                    except (asyncio.CancelledError, Exception):
                        pass
                self._kabu_startup_task = None
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
                await self._cancel_all_streams()

                # HIGH-R2-4: 全接続が切れた時点で replay/live state を初期値に
                # 戻す。これは「LoadReplayData 実行 → 接続全断 → 再接続後に
                # LoadReplayData が IDLE state guard を通過する」を保証する。
                # 動作中の runner があれば後で EngineStopped を emit するが、
                # 既に listener が居ないため state は別経路で reset しないと
                # stuck する。
                self._replay_state = ReplayState.IDLE
                self._live_state = LiveState.DISCONNECTED
                self._connected_venue = None
                self._replay_streaming_fills.clear()

    async def _handshake(self, ws: ServerConnection) -> None:
        # WS-MED1 (Phase 8 R1 / Phase 2): Hello を `_HANDSHAKE_TIMEOUT_S` 秒以内に
        # 受信できなかった場合は protocol error (1002) で切断する。半開接続や
        # probe、不正クライアントが ServerConnection を専有し続けるのを防ぐ。
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=_HANDSHAKE_TIMEOUT_S)
        except asyncio.TimeoutError:
            try:
                await ws.close(1002, "handshake timeout")
            except Exception:
                pass
            raise ValueError("handshake_timeout")
        msg = Hello.model_validate(orjson.loads(raw))

        # Constant-time token comparison to defeat timing attacks.
        if not hmac.compare_digest(msg.token, self._token):
            await ws.send(
                orjson.dumps(
                    EngineError(code=AUTH_FAILED_CODE, message="token mismatch").model_dump()
                ).decode()
            )
            # WS-MED2 / L-WS-INFO (Phase 8 R1 / Phase 2): 認証失敗時の close code を
            # 1008 Policy Violation に統一する。fastwebsockets / 任意 WS クライアント
            # が「拒否されたか / 単に切断されたか」を区別できるようにする。
            await ws.close(1008, "auth failed")
            raise ValueError(AUTH_FAILED_CODE)

        # N1.13 / M-TA6: capture mode from Hello.
        # 最初の Hello のみ _mode を設定する。以降の接続で mode が異なる場合は
        # EngineError で reject して接続を閉じる。
        if self._mode == "live" and not self._connections:
            # 接続がまだない（最初の handshake）場合は mode を設定する。
            # 注: _handle で handshake() 後に add_conn されるので、handshake 時点では
            # _connections は空（まだ追加されていない）。
            self._mode = msg.mode
        elif msg.mode != self._mode:
            await ws.send(
                orjson.dumps(
                    EngineError(
                        code="mode_mismatch",
                        message=f"Engine is in {self._mode!r} mode, cannot attach as {msg.mode!r}",
                    ).model_dump()
                ).decode()
            )
            await ws.close(1008, "mode mismatch")
            raise ValueError(f"mode_mismatch: engine={self._mode!r}, client={msg.mode!r}")

        if msg.schema_major != SCHEMA_MAJOR:
            await ws.send(
                orjson.dumps(
                    EngineError(
                        code="schema_mismatch",
                        message=f"expected major={SCHEMA_MAJOR}, got {msg.schema_major}",
                    ).model_dump()
                ).decode()
            )
            # WS-MED2 / L-WS-INFO (Phase 8 R1 / Phase 2): schema mismatch も 1008 で
            # 統一。クライアント側で auth_failed と同じハンドリングロジックに乗る。
            await ws.close(1008, "schema mismatch")
            raise ValueError("schema_mismatch")

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

        # Phase 1: kabu_station は _workers に含まれないため capabilities を直接追記
        from engine.exchanges.kabusapi_register import RegisterSet as _KabuRegisterSet
        # P4-3: is_production フラグを追加。KABU_ALLOW_PROD=1 + KABU_ENV=prod の二重判定で True。
        venue_caps["kabu_station"] = {
            "requires_local_app": True,
            "max_push_symbols": _KabuRegisterSet.MAX,  # architecture.md §8: 50 と一致を保証
            "supports_amend": False,
            "requires_trade_password_for_cancel": True,
            "is_production": self._kabu_env == "prod",
        }

        ready = Ready(
            schema_major=SCHEMA_MAJOR,
            schema_minor=SCHEMA_MINOR,
            engine_version=_ENGINE_VERSION,
            engine_session_id=self._engine_session_id,
            capabilities={
                "supported_venues": list(self._workers.keys()) + ["kabu_station"],
                "supports_bulk_trades": True,
                "supports_depth_binary": False,
                "venue_capabilities": venue_caps,
                # N3: Phase 2+7 で live strategy 対応済み。mode.py から読む。
                "nautilus": nautilus_capabilities(self._mode),
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
            # Silent-M2 (Phase 8 R1 / Phase 2): 不正 JSON フレームでは接続を切断
            # しない。当該接続にだけ Error event を送って `continue` する。
            # 旧実装は orjson.JSONDecodeError を catch せず async-for から
            # propagate してこの client (および呼び出し側 finally) を黙って閉じていた。
            try:
                msg: dict[str, Any] = orjson.loads(raw)
            except orjson.JSONDecodeError as exc:
                log.warning("malformed JSON frame from client: %s", exc)
                self._outbox.send_to(
                    ws,
                    {
                        "event": "Error",
                        "request_id": None,
                        "code": "malformed_json",
                        "message": str(exc),
                    },
                )
                continue
            op = msg.get("op")
            try:
                await self._dispatch(op, msg, ws)
            except Exception as exc:
                log.error("Dispatch error op=%s: %s", op, exc)
                await self._send_error(ws, msg.get("request_id"), str(exc))

    # ------------------------------------------------------------------
    # Send loop — drains outbox and writes to client
    # ------------------------------------------------------------------

    async def _send_loop(self, ws: ServerConnection, q: asyncio.Queue) -> None:
        # H8: ws.send が ConnectionClosed / その他で raise したときに send_loop が
        # 全 client 道連れになるのを防ぐ。送信失敗時はこの client 用 loop を
        # 抜けるだけ（呼び出し元の handler の finally で接続クリーンアップが走る）。
        #
        # H-GP7 (Phase 8 R1 / Phase 2): shutdown 直前に append された最終 event
        # （特に EngineStopped）が drop されないように、shutdown フラグを観測したら
        # ループを抜ける前に queue を完全ドレインしてから return する。
        import websockets as _ws_pkg

        async def _send_one(event: dict) -> bool:
            """Send one event; return True if loop should exit."""
            try:
                await ws.send(orjson.dumps(event).decode())
                return False
            except _ws_pkg.ConnectionClosed:
                log.info("send_loop: client closed cleanly")
                return True
            except Exception as exc:
                log.error("send_loop: send failed: %s", type(exc).__name__)
                return True

        while True:
            try:
                event = q.get_nowait()
                if await _send_one(event):
                    return
                continue
            except asyncio.QueueEmpty:
                pass

            if self._shutdown_event.is_set():
                # Final drain: ensure any event appended just before shutdown
                # is delivered before we exit.
                while True:
                    try:
                        event = q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if await _send_one(event):
                        return
                return

            try:
                event = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if await _send_one(event):
                return

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
            await self._do_request_venue_login(msg, ws=ws)

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
                self._do_submit_order(msg, ws=ws), msg.get("request_id")
            )

        elif op == "ModifyOrder":
            self._spawn_fetch(
                self._do_modify_order(msg, ws=ws), msg.get("request_id")
            )

        elif op == "CancelOrder":
            self._spawn_fetch(
                self._do_cancel_order(msg, ws=ws), msg.get("request_id")
            )

        elif op == "CancelAllOrders":
            self._spawn_fetch(
                self._do_cancel_all_orders(msg, ws=ws), msg.get("request_id")
            )

        elif op == "GetOrderList":
            self._spawn_fetch(
                self._do_get_order_list(msg, ws=ws), msg.get("request_id")
            )

        elif op == "GetBuyingPower":
            self._spawn_fetch(
                self._do_get_buying_power(msg, ws=ws), msg.get("request_id")
            )

        elif op == "GetPositions":
            self._spawn_fetch(
                self._do_get_positions(msg, ws=ws), msg.get("request_id")
            )

        elif op == "StartEngine":
            # N1.4: BacktestEngine 起動。replay モード必須。
            self._spawn_fetch(
                self._handle_start_engine(msg, ws=ws), msg.get("request_id")
            )

        elif op == "StopEngine":
            # N1.4: 走行中 BacktestEngine を停止する。
            self._spawn_fetch(
                self._handle_stop_engine(msg, ws=ws), msg.get("request_id")
            )

        elif op == "StopReplay":
            # F7: replay セッションを graceful に停止する（replay モード限定）。
            self._spawn_fetch(
                self._handle_stop_replay(msg, ws=ws), msg.get("request_id")
            )

        elif op == "ForceStopReplay":
            # F7: replay セッションを強制停止する（タイムアウト fallback）。
            self._spawn_fetch(
                self._handle_force_stop_replay(msg, ws=ws), msg.get("request_id")
            )

        elif op == "LoadReplayData":
            # N1.4: J-Quants データを事前ロードして件数だけ通知する。
            # StartEngine が config に同等情報を持つので、本コマンドは事前確認用途。
            self._spawn_fetch(
                self._handle_load_replay_data(msg, ws=ws), msg.get("request_id")
            )

        elif op == "SetReplaySpeed":
            # N1.11: streaming 経路の pacing multiplier を変更する。
            # B3: SetReplaySpeed は Replay::RUNNING 状態のみ受理する。
            multiplier = msg.get("multiplier", 1)
            request_id = msg.get("request_id", "")
            if not isinstance(multiplier, int) or multiplier <= 0:
                log.warning(
                    "SetReplaySpeed: invalid multiplier=%r, ignored", multiplier
                )
                return
            if not self._check_replay_state("SetReplaySpeed", ReplayState.RUNNING, ws=ws):
                return
            self._replay_speed_multiplier = multiplier
            log.info(
                "SetReplaySpeed: multiplier=%d request_id=%s",
                multiplier,
                request_id,
            )
            # 簡易 ack: SetReplaySpeed は Error response を返さないのが Rust 期待。
            # 走行中の streaming runner は self._replay_speed_multiplier を読むことで
            # 次の tick から新しい速度を使う。

        elif op == "PauseReplay":
            request_id = msg.get("request_id", "")
            if not self._check_replay_state("PauseReplay", ReplayState.RUNNING, ws=ws, request_id=request_id):
                return
            self._replay_paused = True
            self._replay_state = ReplayState.PAUSED
            log.info("PauseReplay: request_id=%s", request_id)

        elif op == "ResumeReplay":
            request_id = msg.get("request_id", "")
            if not self._check_replay_state("ResumeReplay", ReplayState.PAUSED, ws=ws, request_id=request_id):
                return
            self._replay_paused = False
            self._replay_state = ReplayState.RUNNING
            log.info("ResumeReplay: request_id=%s", request_id)

        elif op == "StepReplay":
            request_id = msg.get("request_id", "")
            if not self._check_replay_state("StepReplay", ReplayState.PAUSED, ws=ws, request_id=request_id):
                return
            self._replay_step_request += 1
            log.info("StepReplay: request_id=%s pending_steps=%d", request_id, self._replay_step_request)

        elif op == "StepBackward":
            # schema 3.16: 1 粒度後退（snapshot 復元）。受理状態: PAUSED かつ snapshot 非空。
            request_id = msg.get("request_id", "")
            if not self._check_replay_state("StepBackward", ReplayState.PAUSED, ws=ws, request_id=request_id):
                return
            with self._replay_snapshots_lock:
                if len(self._replay_snapshots) < 2:
                    # buffer に「現在状態 + 1つ前」が揃っていない → 戻れない
                    self._outbox.send_to(ws, {
                        "event": "EngineBusy",
                        "current_state": self._replay_state.name,
                        "attempted_command": "StepBackward",
                        "reason": "no previous snapshot to restore",
                        "request_id": request_id,
                    })
                    return
                self._replay_snapshots.pop()  # 現在状態を捨てる
                snap = self._replay_snapshots[-1]  # 1つ前（新しい末尾）を復元
                has_history = len(self._replay_snapshots) > 1
            # 1. RestoreSnapshot event を先行 emit して Rust UI をリセットモードに切り替える
            self._outbox.append({
                "event": "RestoreSnapshot",
                "step_index": snap.step_index,
                "ts_event_ms": int(snap.portfolio.get("ts_event_ms", 0)),
            })
            # 2. portfolio / orders / strategy を復元
            self._restore_snapshot(snap)
            # 2.5: BuyingPower UI を復元済みポートフォリオ値で更新する
            # (ui_events は engine_runner が [] で渡すため空。portfolio dict は
            #  to_ipc_dict() が生成した ReplayBuyingPower event を直接 emit する)
            if snap.portfolio.get("event") == "ReplayBuyingPower":
                self._outbox.append(snap.portfolio)
            # 2.6: OrderList UI を復元済み order list で push 更新する。
            # _restore_snapshot で _replay_streaming_fills は復元済み。GetOrderList を
            # 待たずに UI に push して order panel を即座にロールバックさせる。
            self._outbox.append({
                "event": "OrderListUpdated",
                "request_id": "",
                "orders": list(self._replay_streaming_fills),
            })
            # 2.7 (Issue 5): PositionsUpdated を push して replay Positions pane を更新。
            # snap.portfolio_state から build (request_id="" の push 型)。
            self._outbox.append(
                build_replay_positions_event(
                    snap.portfolio_state,
                    int(time.time() * 1000),
                )
            )
            # 3. ui_events を再 emit（Rust UI が RestoreSnapshot 受信済みのため重複扱いにならない）
            for ev in snap.ui_events:
                self._outbox.append(ev)
            # 4. ReplayHistoryChanged で Rust ボタン有効/無効を更新
            self._outbox.append({
                "event": "ReplayHistoryChanged",
                "has_history": has_history,
            })
            log.info(
                "StepBackward: restored step_index=%d has_history=%s request_id=%s",
                snap.step_index,
                has_history,
                request_id,
            )

        elif op == "LoadStrategyScenario":
            # F6: SCENARIO 定数の安全抽出（ast.parse のみ・import なし）。
            self._spawn_fetch(
                self._do_load_strategy_scenario(msg), msg.get("request_id")
            )

        elif op == "SaveStrategyScenario":
            # F6: SCENARIO ブロックの libcst atomic 書き戻し。
            self._spawn_fetch(
                self._do_save_strategy_scenario(msg), msg.get("request_id")
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

    async def _do_submit_order(self, msg: dict, *, ws: ServerConnection | None = None) -> None:
        # C-2: in-flight カウンタをインクリメント（architecture.md §2.4 競合ポリシー）。
        self._submit_order_inflight_count += 1
        try:
            await self._do_submit_order_inner(msg, ws=ws)
        finally:
            self._submit_order_inflight_count -= 1

    async def _do_submit_order_inner(self, msg: dict, *, ws: ServerConnection | None = None) -> None:
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
            # B3: SubmitOrder{venue="replay"} は Replay::RUNNING 状態のみ受理する。
            # mode="replay" のときのみ state guard を適用する。
            # mode="live" で venue="replay" が来た場合は state guard をスキップして
            # 既存ロジック（REPLAY_NOT_IMPLEMENTED path）に流す。
            if getattr(self, "_mode", "live") == "replay":
                if not self._check_replay_state("SubmitOrder", ReplayState.RUNNING, ws=ws):
                    return

            # Parse order (replay は session/second_password 不要)
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

            ts_event_ms = int(time.time() * 1000)
            self._outbox.append(
                {
                    "event": "OrderSubmitted",
                    "client_order_id": order.client_order_id,
                    "ts_event_ms": ts_event_ms,
                }
            )

            from engine.order_router import submit_order_replay

            envelope = NautilusOrderEnvelope.model_validate(order.model_dump())
            wal_path = self._cache_dir / "tachibana_orders_replay.jsonl"
            try:
                result = submit_order_replay(envelope, wal_path=wal_path)
            except OSError as exc:
                log.error(
                    "_do_submit_order_replay: WAL I/O error for cid=%s — %s",
                    order.client_order_id,
                    exc,
                )
                self._outbox.append(
                    {
                        "event": "OrderRejected",
                        "client_order_id": order.client_order_id,
                        "reason_code": "TRANSPORT_ERROR",
                        "reason_text": "WAL write failed",
                        "ts_event_ms": int(time.time() * 1000),
                    }
                )
                return

            self._outbox.append(
                {
                    "event": "OrderAccepted",
                    "client_order_id": result["client_order_id"],
                    "venue_order_id": "",
                    "ts_event_ms": int(time.time() * 1000),
                }
            )
            return

        # B3: live 発注（replay 以外）は Live::CONNECTED 状態のみ受理する。
        if not self._check_live_state("SubmitOrder", LiveState.CONNECTED, ws=ws):
            return

        # N3.C: 発注経路は "tachibana" / "kabu_station" ("replay" は上のブランチで処理済み)
        # 暗号資産 venue は自身のデータワーカーを持つが、発注 IPC 経路はサポートしない
        if venue == "kabu_station":
            await self._do_submit_order_kabu(msg, ws=ws)
            return
        elif venue != "tachibana":
            self._outbox.append(
                {
                    "event": "Error",
                    "request_id": req_id,
                    "code": "unsupported_order_venue",
                    "message": f"SubmitOrder: venue {venue!r} does not support orders via this IPC path",
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

    async def _do_modify_order(self, msg: dict, *, ws: ServerConnection | None = None) -> None:
        import time

        req_id = msg.get("request_id", "")
        venue = msg.get("venue", "")
        client_order_id = msg.get("client_order_id", "")
        raw_change = msg.get("change", {})

        # B3: ModifyOrder は Live::CONNECTED 状態のみ受理する。
        if not self._check_live_state("ModifyOrder", LiveState.CONNECTED, ws=ws):
            return

        # N3.C: 発注 IPC 経路は tachibana のみサポート。
        # "hyperliquid" / "replay" / その他 venue はここで拒否する。
        # replay 注文のキャンセル/訂正は N1.15 の UI ガードで事前に抑止されているため
        # IPC に届くことは通常ない。
        if venue != "tachibana":
            self._outbox.append({
                "event": "Error",
                "request_id": req_id,
                "code": "unsupported_order_venue",
                "message": f"ModifyOrder: venue {venue!r} does not support orders via this IPC path",
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

        if self._tachibana_session is None:
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": client_order_id,
                "reason_code": "NOT_LOGGED_IN",
                "reason_text": "Tachibana session is not established",
                "ts_event_ms": int(time.time() * 1000),
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

    def _clear_kabu_session(self) -> None:
        """トークン期限切れ・接続断時に kabu セッション状態をリセットする。"""
        if self._kabu_venue is not None:
            self._kabu_venue.clear()
        self._kabu_venue = None  # [M-2] 古い venue 参照が残らないようにする
        # [H-1] fill poller タスクをキャンセル
        if self._kabu_fill_poller_task is not None and not self._kabu_fill_poller_task.done():
            self._kabu_fill_poller_task.cancel()
        self._kabu_fill_poller_task = None
        self._connected_venue = None
        self._kabu_push_ssid = None  # C1: PUSH セッション ID をリセット
        self._live_state = LiveState.DISCONNECTED

    async def _do_submit_order_kabu(self, msg: dict, *, ws: ServerConnection | None = None) -> None:
        """kabu_station 発注経路 (Phase 3: Stock / Future / Option に対応)。"""
        import time

        req_id = msg.get("request_id", "")

        # 接続チェック
        if self._kabu_venue is None:
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": msg.get("order", {}).get("client_order_id", ""),
                "reason_code": "NOT_LOGGED_IN",
                "reason_text": "KabuStation venue is not connected",
                "ts_event_ms": int(time.time() * 1000),
            })
            return

        # lockout チェック
        if self._kabu_venue.is_trade_locked_out():
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": msg.get("order", {}).get("client_order_id", ""),
                "reason_code": "TRADE_PASSWORD_LOCKED",
                "reason_text": "Trade password is locked out due to repeated failures",
                "ts_event_ms": int(time.time() * 1000),
            })
            return

        raw_order = msg.get("order", {})
        try:
            order = SubmitOrderRequest.model_validate(raw_order)
        except Exception as exc:
            # [M-3] tachibana 対称化: Error → OrderRejected{VALIDATION_ERROR}
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": raw_order.get("client_order_id", ""),
                "reason_code": "VALIDATION_ERROR",
                "reason_text": str(exc),
                "ts_event_ms": int(time.time() * 1000),
            })
            return

        # Phase 3: instrument_id 末尾で Stock / Future / Option を分岐
        # instrument_id 形式は "<symbol>.KabuStation <ExchangeKind>"
        instrument_id = order.instrument_id
        symbol = instrument_id.split(".")[0]
        side = "buy" if order.order_side == "BUY" else "sell"

        is_future = instrument_id.endswith(".KabuStation Future")
        is_option = instrument_id.endswith(".KabuStation Option")

        if is_future or is_option:
            # 先物・OP: FrontOrderType 120=成行, 20=指値 — OpenAPI RequestSendOrderDerivFuture/Option
            if order.order_type == "MARKET":
                deriv_kwargs: dict = {
                    "symbol": symbol,
                    "exchange": 23,  # OSE (Osaka Exchange) デフォルト
                    "side": side,
                    "qty": int(float(order.quantity)),
                    "front_order_type": 120,  # 成行（マーケットオーダー）
                    "price": 0,
                }
            else:  # LIMIT
                if order.price is None:
                    self._outbox.append({
                        "event": "OrderRejected",
                        "client_order_id": order.client_order_id,
                        "reason_code": "INVALID_PRICE",
                        "reason_text": "LIMIT order requires price",
                        "ts_event_ms": int(time.time() * 1000),
                    })
                    return
                deriv_kwargs = {
                    "symbol": symbol,
                    "exchange": 23,
                    "side": side,
                    "qty": int(float(order.quantity)),
                    "front_order_type": 20,  # 指値
                    "price": float(order.price),
                }

            # [M-1] OrderSubmitted を API 呼び出し前に発火（nautilus 流 2 段イベント）
            self._outbox.append({
                "event": "OrderSubmitted",
                "client_order_id": order.client_order_id,
                "ts_event_ms": int(time.time() * 1000),
            })

            try:
                if is_future:
                    result = await self._kabu_venue.send_order_future(**deriv_kwargs)
                else:
                    result = await self._kabu_venue.send_order_option(**deriv_kwargs)
            except KabuTradePasswordInvalidError:
                self._outbox.append({
                    "event": "OrderRejected",
                    "client_order_id": order.client_order_id,
                    "reason_code": "TRADE_PASSWORD_INVALID",
                    "reason_text": "Trade password is invalid",
                    "ts_event_ms": int(time.time() * 1000),
                })
                return
            except KabuTradeLockedOutError:
                self._outbox.append({
                    "event": "OrderRejected",
                    "client_order_id": order.client_order_id,
                    "reason_code": "TRADE_PASSWORD_LOCKED",
                    "reason_text": "Trade password is locked out",
                    "ts_event_ms": int(time.time() * 1000),
                })
                return
            except KabuTradeCancelledError:
                self._outbox.append({
                    "event": "OrderRejected",
                    "client_order_id": order.client_order_id,
                    "reason_code": "TRADE_PASSWORD_CANCELLED",
                    "reason_text": "Trade password entry was cancelled",
                    "ts_event_ms": int(time.time() * 1000),
                })
                return
            except KabuTokenExpiredError:
                log.warning("_do_submit_order_kabu(deriv): KabuTokenExpiredError for cid=%s", order.client_order_id)
                self._clear_kabu_session()
                self._outbox.append({
                    "event": "OrderRejected",
                    "client_order_id": order.client_order_id,
                    "reason_code": "NOT_LOGGED_IN",
                    "reason_text": "kabu token expired or not logged in",
                    "ts_event_ms": int(time.time() * 1000),
                })
                return
            except KabuConnectionError as exc:
                log.error("_do_submit_order_kabu(deriv): KabuConnectionError for cid=%s: %s", order.client_order_id, exc)
                self._clear_kabu_session()
                self._outbox.append({
                    "event": "OrderRejected",
                    "client_order_id": order.client_order_id,
                    "reason_code": "CONNECTION_ERROR",
                    "reason_text": "kabu connection lost",
                    "ts_event_ms": int(time.time() * 1000),
                })
                return
            except KabuRateLimitError:
                log.warning("kabu sendorder(deriv): rate limited, cid=%s", order.client_order_id)
                self._outbox.append({
                    "event": "OrderRejected",
                    "client_order_id": order.client_order_id,
                    "reason_code": "RATE_LIMITED",
                    "reason_text": "kabu API rate limit exceeded",
                    "ts_event_ms": int(time.time() * 1000),
                })
                return
            except KabuApiError as exc:
                log.error("_do_submit_order_kabu(deriv): KabuApiError for cid=%s: code=%s", order.client_order_id, exc.code)
                self._outbox.append({
                    "event": "OrderRejected",
                    "client_order_id": order.client_order_id,
                    "reason_code": "VENUE_REJECTED",
                    "reason_text": str(exc),
                    "ts_event_ms": int(time.time() * 1000),
                })
                return
            except Exception:
                log.exception("_do_submit_order_kabu(deriv): unexpected error for cid=%s", order.client_order_id)
                self._outbox.append({
                    "event": "OrderRejected",
                    "client_order_id": order.client_order_id,
                    "reason_code": "INTERNAL_ERROR",
                    "reason_text": "Internal error during kabu order submission",
                    "ts_event_ms": int(time.time() * 1000),
                })
                return

            order_id = result.get("OrderID", "")
            if not order_id:
                log.error(
                    "_do_submit_order_kabu(deriv): OrderID missing in response for cid=%s",
                    order.client_order_id,
                )
                self._outbox.append({
                    "event": "OrderRejected",
                    "client_order_id": order.client_order_id,
                    "reason_code": "MISSING_ORDER_ID",
                    "reason_text": "API response did not contain OrderID",
                    "ts_event_ms": int(time.time() * 1000),
                })
                return

            self._venue_to_client[order_id] = order.client_order_id
            self._outbox.append({
                "event": "OrderAccepted",
                "client_order_id": order.client_order_id,
                "venue_order_id": order_id,
                "ts_event_ms": int(time.time() * 1000),
            })
            return

        else:
            # 株式経路（既存ロジック）
            # instrument_id から銘柄コードを抽出 ("9433.KabuStation Stock" → "9433")
            exchange = 1  # TSE (Phase 2: KabuStationStock のみ)

        # [C-1] FrontOrderType: 10=成行, 20=指値 — OpenAPI RequestSendOrder 参照
        if order.order_type == "MARKET":
            send_kwargs: dict = {
                "symbol": symbol,
                "exchange": exchange,
                "side": side,
                "qty": int(float(order.quantity)),
                "front_order_type": 10,  # 成行
                "price": 0,
            }
        else:  # LIMIT
            if order.price is None:
                self._outbox.append({
                    "event": "OrderRejected",
                    "client_order_id": order.client_order_id,
                    "reason_code": "INVALID_PRICE",
                    "reason_text": "LIMIT order requires price",
                    "ts_event_ms": int(time.time() * 1000),
                })
                return
            send_kwargs = {
                "symbol": symbol,
                "exchange": exchange,
                "side": side,
                "qty": int(float(order.quantity)),
                "front_order_type": 20,  # 指値
                "price": float(order.price),
            }

        # [M-1] OrderSubmitted を API 呼び出し前に発火（nautilus 流 2 段イベント、tachibana と対称）
        self._outbox.append({
            "event": "OrderSubmitted",
            "client_order_id": order.client_order_id,
            "ts_event_ms": int(time.time() * 1000),
        })

        try:
            result = await self._kabu_venue.send_order(**send_kwargs)
        except KabuTradePasswordInvalidError:
            # on_invalid() は KabuOrderClient.send_order() 内で既に呼ばれている (A-1)
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": order.client_order_id,
                "reason_code": "TRADE_PASSWORD_INVALID",
                "reason_text": "Trade password is invalid",
                "ts_event_ms": int(time.time() * 1000),
            })
            return
        except KabuTradeLockedOutError:
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": order.client_order_id,
                "reason_code": "TRADE_PASSWORD_LOCKED",
                "reason_text": "Trade password is locked out",
                "ts_event_ms": int(time.time() * 1000),
            })
            return
        except KabuTradeCancelledError:
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": order.client_order_id,
                "reason_code": "TRADE_PASSWORD_CANCELLED",
                "reason_text": "Trade password entry was cancelled",
                "ts_event_ms": int(time.time() * 1000),
            })
            return
        except KabuTokenExpiredError:
            # NOT_LOGGED_IN ≠ CONNECTION_ERROR: トークン期限切れは「再ログインで復旧」、
            # ConnectionError は「kabuStation 本体または網路の問題」を意味する。意図的に分離。
            log.warning("_do_submit_order_kabu: KabuTokenExpiredError for cid=%s", order.client_order_id)
            self._clear_kabu_session()
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": order.client_order_id,
                "reason_code": "NOT_LOGGED_IN",
                "reason_text": "kabu token expired or not logged in",
                "ts_event_ms": int(time.time() * 1000),
            })
            return
        except KabuConnectionError as exc:
            log.error("_do_submit_order_kabu: KabuConnectionError for cid=%s: %s", order.client_order_id, exc)
            self._clear_kabu_session()
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": order.client_order_id,
                "reason_code": "CONNECTION_ERROR",  # [H-2] NOT_LOGGED_IN から変更
                "reason_text": "kabu connection lost",
                "ts_event_ms": int(time.time() * 1000),
            })
            return
        except KabuRateLimitError:
            log.warning("kabu sendorder: rate limited, request_id=%s", req_id)
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": order.client_order_id,
                "reason_code": "RATE_LIMITED",
                "reason_text": "kabu API rate limit exceeded",
                "ts_event_ms": int(time.time() * 1000),
            })
            return
        except KabuApiError as exc:
            log.error("_do_submit_order_kabu: KabuApiError for cid=%s: code=%s", order.client_order_id, exc.code)
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": order.client_order_id,
                "reason_code": "VENUE_REJECTED",
                "reason_text": str(exc),
                "ts_event_ms": int(time.time() * 1000),
            })
            return
        except Exception:
            log.exception("_do_submit_order_kabu: unexpected error for cid=%s", order.client_order_id)
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": order.client_order_id,
                "reason_code": "INTERNAL_ERROR",
                "reason_text": "Internal error during kabu order submission",
                "ts_event_ms": int(time.time() * 1000),
            })
            return

        order_id = result.get("OrderID", "")
        if not order_id:
            log.error(
                "_do_submit_order_kabu: OrderID missing in response for cid=%s",
                order.client_order_id,
            )
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": order.client_order_id,
                "reason_code": "MISSING_ORDER_ID",
                "reason_text": "API response did not contain OrderID",
                "ts_event_ms": int(time.time() * 1000),
            })
            return

        # [C-3] venue_order_id → client_order_id のマッピングを記録（fill 受信時の逆引き用）
        self._venue_to_client[order_id] = order.client_order_id
        self._outbox.append({
            "event": "OrderAccepted",
            "client_order_id": order.client_order_id,
            "venue_order_id": order_id,
            "ts_event_ms": int(time.time() * 1000),
        })

    async def _do_cancel_order_kabu(self, msg: dict, *, ws: ServerConnection | None = None) -> None:
        """kabu_station 取消経路 (Phase 2: KabuStationStock, 検証環境のみ)。"""
        import time

        client_order_id = msg.get("client_order_id", "")
        venue_order_id = msg.get("venue_order_id", "")

        if not venue_order_id:
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": client_order_id,
                "reason_code": "VALIDATION_ERROR",
                "reason_text": "venue_order_id is required for CancelOrder",
                "ts_event_ms": int(time.time() * 1000),
            })
            return

        # 接続チェック
        if self._kabu_venue is None:
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": client_order_id,
                "reason_code": "NOT_LOGGED_IN",
                "reason_text": "KabuStation venue is not connected",
                "ts_event_ms": int(time.time() * 1000),
            })
            return

        # lockout チェック
        if self._kabu_venue.is_trade_locked_out():
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": client_order_id,
                "reason_code": "TRADE_PASSWORD_LOCKED",
                "reason_text": "Trade password is locked out due to repeated failures",
                "ts_event_ms": int(time.time() * 1000),
            })
            return

        try:
            await self._kabu_venue.cancel_order(order_id=venue_order_id)
        except KabuTradePasswordInvalidError:
            # on_invalid() は KabuOrderClient.cancel_order() 内で既に呼ばれている (A-1)
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": client_order_id,
                "reason_code": "TRADE_PASSWORD_INVALID",
                "reason_text": "Trade password is invalid",
                "ts_event_ms": int(time.time() * 1000),
            })
            return
        except KabuTradeLockedOutError:
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": client_order_id,
                "reason_code": "TRADE_PASSWORD_LOCKED",
                "reason_text": "Trade password is locked out",
                "ts_event_ms": int(time.time() * 1000),
            })
            return
        except KabuTradeCancelledError:
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": client_order_id,
                "reason_code": "TRADE_PASSWORD_CANCELLED",
                "reason_text": "Trade password entry was cancelled",
                "ts_event_ms": int(time.time() * 1000),
            })
            return
        except KabuTokenExpiredError:
            # NOT_LOGGED_IN ≠ CONNECTION_ERROR: トークン期限切れは「再ログインで復旧」、
            # ConnectionError は「kabuStation 本体または網路の問題」を意味する。意図的に分離。
            log.warning("_do_cancel_order_kabu: KabuTokenExpiredError for cid=%s", client_order_id)
            self._clear_kabu_session()
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": client_order_id,
                "reason_code": "NOT_LOGGED_IN",
                "reason_text": "kabu token expired or not logged in",
                "ts_event_ms": int(time.time() * 1000),
            })
            return
        except KabuConnectionError as exc:
            log.error("_do_cancel_order_kabu: KabuConnectionError for cid=%s: %s", client_order_id, exc)
            self._clear_kabu_session()
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": client_order_id,
                "reason_code": "CONNECTION_ERROR",  # [H-2] NOT_LOGGED_IN から変更
                "reason_text": "kabu connection lost",
                "ts_event_ms": int(time.time() * 1000),
            })
            return
        except KabuRateLimitError:
            log.warning("kabu cancelorder: rate limited, request_id=%s", client_order_id)
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": client_order_id,
                "reason_code": "RATE_LIMITED",
                "reason_text": "kabu API rate limit exceeded",
                "ts_event_ms": int(time.time() * 1000),
            })
            return
        except KabuApiError as exc:
            log.error("_do_cancel_order_kabu: KabuApiError for cid=%s: code=%s", client_order_id, exc.code)
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": client_order_id,
                "reason_code": "VENUE_REJECTED",
                "reason_text": str(exc),
                "ts_event_ms": int(time.time() * 1000),
            })
            return
        except Exception:
            log.exception("_do_cancel_order_kabu: unexpected error for cid=%s", client_order_id)
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": client_order_id,
                "reason_code": "INTERNAL_ERROR",
                "reason_text": "Internal error during kabu order cancellation",
                "ts_event_ms": int(time.time() * 1000),
            })
            return

        self._outbox.append({
            "event": "OrderCancelled",
            "client_order_id": client_order_id,
            "venue_order_id": venue_order_id,
            "ts_event_ms": int(time.time() * 1000),
        })

    async def _do_cancel_order(self, msg: dict, *, ws: ServerConnection | None = None) -> None:
        import time

        req_id = msg.get("request_id", "")
        venue = msg.get("venue", "")
        client_order_id = msg.get("client_order_id", "")
        venue_order_id = msg.get("venue_order_id", "")

        # B3: CancelOrder は Live::CONNECTED 状態のみ受理する。
        if not self._check_live_state("CancelOrder", LiveState.CONNECTED, ws=ws):
            return

        # N3.C: 発注 IPC 経路は tachibana / kabu_station をサポート。
        # "hyperliquid" / "replay" / その他 venue はここで拒否する。
        # replay 注文のキャンセル/訂正は N1.15 の UI ガードで事前に抑止されているため
        # IPC に届くことは通常ない。
        if venue == "kabu_station":
            await self._do_cancel_order_kabu(msg, ws=ws)
            return
        elif venue != "tachibana":
            self._outbox.append({
                "event": "Error",
                "request_id": req_id,
                "code": "unsupported_order_venue",
                "message": f"CancelOrder: venue {venue!r} does not support orders via this IPC path",
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

        if self._tachibana_session is None:
            self._outbox.append({
                "event": "OrderRejected",
                "client_order_id": client_order_id,
                "reason_code": "NOT_LOGGED_IN",
                "reason_text": "Tachibana session is not established",
                "ts_event_ms": int(time.time() * 1000),
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

    async def _do_cancel_all_orders(self, msg: dict, *, ws: ServerConnection | None = None) -> None:
        req_id = msg.get("request_id", "")
        venue = msg.get("venue", "")
        instrument_id = msg.get("instrument_id")
        order_side = msg.get("order_side")

        # B3: CancelAllOrders は Live::CONNECTED 状態のみ受理する。
        if not self._check_live_state("CancelAllOrders", LiveState.CONNECTED, ws=ws):
            return

        # N3.C: 発注 IPC 経路は tachibana のみサポート。
        # "hyperliquid" / "replay" / その他 venue はここで拒否する。
        # replay 注文の一括キャンセルは N1.15 の UI ガードで事前に抑止されているため
        # IPC に届くことは通常ない。
        if venue != "tachibana":
            self._outbox.append({
                "event": "Error",
                "request_id": req_id,
                "code": "unsupported_order_venue",
                "message": f"CancelAllOrders: venue {venue!r} does not support orders via this IPC path",
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

        if self._tachibana_session is None:
            self._outbox.append({
                "event": "Error",
                "request_id": req_id,
                "code": "NOT_LOGGED_IN",
                "message": "Tachibana session is not established",
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
        """N1.15: GetOrderList{venue='replay'} — streaming fills + WAL から注文一覧を返す。

        優先順位:
        1. streaming replay 中に約定した fills（self._replay_streaming_fills）
        2. WAL（tachibana_orders_replay.jsonl）の submit エントリ

        streaming fills が存在する場合（replay_streaming 経路）は WAL を読まない。
        どちらも存在しない場合は空リストを返す。
        """
        import json

        req_id = msg.get("request_id", "")

        # streaming replay 経路: メモリ蓄積の fills を優先して返す。
        if self._replay_streaming_fills:
            self._outbox.append({
                "event": "OrderListUpdated",
                "request_id": req_id,
                "orders": list(self._replay_streaming_fills),
            })
            return

        # submit-based replay 経路: WAL から読む。
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

    async def _do_get_order_list(self, msg: dict, *, ws: ServerConnection | None = None) -> None:
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

        # H-Type4: live 注文一覧取得は Live::CONNECTED 状態のみ受理する。
        if not self._check_live_state("GetOrderList", LiveState.CONNECTED, ws=ws):
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

    async def _do_get_buying_power(self, msg: dict, *, ws: ServerConnection | None = None) -> None:
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

        # H-Type4: live 余力取得は Live::CONNECTED 状態のみ受理する。
        if not self._check_live_state("GetBuyingPower", LiveState.CONNECTED, ws=ws):
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

    async def _do_get_positions(self, msg: dict, *, ws: ServerConnection | None = None) -> None:
        req_id = msg.get("request_id", "")
        venue = msg.get("venue", "")
        if venue != "tachibana":
            self._outbox.append({
                "event": "Error",
                "request_id": req_id,
                "code": "unknown_venue",
                "message": f"GetPositions: unknown venue {venue!r}",
            })
            return

        # H-Type4: live 建玉取得は Live::CONNECTED 状態のみ受理する。
        if not self._check_live_state("GetPositions", LiveState.CONNECTED, ws=ws):
            return

        if self._tachibana_session is None:
            log.warning("_do_get_positions: tachibana session not established (request_id=%s)", req_id)
            self._outbox.append({
                "event": "Error",
                "request_id": req_id,
                "code": "SESSION_NOT_ESTABLISHED",
                "message": "GetPositions: tachibana session not established",
            })
            return

        try:
            records = await tachibana_fetch_positions(
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
        except TachibanaError as e:
            log.error("[get_positions] %s", e)
            self._outbox.append({
                "event": "Error",
                "request_id": req_id,
                "code": "fetch_error",
                "message": str(e),
            })
            return
        except Exception:
            log.exception("_do_get_positions: unexpected error")
            self._outbox.append({
                "event": "Error",
                "request_id": req_id,
                "code": "INTERNAL_ERROR",
                "message": "Internal error fetching positions",
            })
            return

        ts_ms = int(time.time() * 1000)
        self._outbox.append({
            "event": "PositionsUpdated",
            "request_id": req_id,
            "venue": venue,
            "positions": [
                {
                    "instrument_id": r.instrument_id,
                    "qty": str(int(r.qty)),
                    "market_value": str(int(r.market_value)) if r.market_value is not None else "",
                    "position_type": r.position_type,
                    "tategyoku_id": r.tategyoku_id,
                    "venue": "tachibana",
                }
                for r in records
            ],
            "ts_ms": ts_ms,
        })

    # ------------------------------------------------------------------
    # F6: SCENARIO 定数 load / save ハンドラ
    # ------------------------------------------------------------------

    async def _do_load_strategy_scenario(self, msg: dict) -> None:
        """LoadStrategyScenario: .py から SCENARIO 定数を ast.literal_eval で安全抽出する。

        - SCENARIO が存在する → StrategyScenarioLoaded(scenario=<dict>)
        - SCENARIO が不在（extract が None を返す）→ StrategyScenarioLoaded(scenario=None)
        - ファイルが存在しない / 構文エラー 等 → StrategyScenarioLoadFailed

        M4 (レビュー反映 2026-05-04 ラウンド1): 失敗時のログ形式を
        `WARN scenario.load failed reason=... path=...` に統一。
        """
        from engine import scenario as scenario_mod
        from engine.schemas import StrategyScenarioLoaded, StrategyScenarioLoadFailed

        request_id = msg.get("request_id", "")
        path_str = msg.get("path", "")
        try:
            scenario = scenario_mod.extract(Path(path_str))
            resolved_instruments: list[str] | None = None
            if scenario is not None:
                # base_dir = 読み込み元 .py の親（GUI Load 経路では path_str == loaded_path）
                resolved = scenario_mod.resolve_refs(scenario, base_dir=Path(path_str).parent)
                if scenario.get("schema_version") == 3 and "instruments_ref" in scenario:
                    # v3 + ref 経由のみ resolved_instruments を別フィールドとして送る
                    resolved_instruments = list(resolved.get("instruments", []))
            # H-R2-1 (ラウンド2): 送信パイプラインを model_dump 経由に統一。
            self._outbox.append(
                StrategyScenarioLoaded(
                    request_id=request_id,
                    path=path_str,
                    scenario=scenario,          # raw のまま（instruments_ref 保持）
                    resolved_instruments=resolved_instruments,
                ).model_dump(exclude_none=False)
            )
        except Exception as exc:
            log.warning("scenario.load failed reason=%s path=%r", exc, path_str)
            self._outbox.append(
                StrategyScenarioLoadFailed(
                    request_id=request_id,
                    path=path_str,
                    reason=str(exc),
                ).model_dump(exclude_none=False)
            )

    async def _do_save_strategy_scenario(self, msg: dict) -> None:
        """SaveStrategyScenario: .py の SCENARIO ブロックを libcst で atomic 書き戻す。

        - 成功 → StrategyScenarioSaved(ok=True, error=None)
        - 失敗（path ガード違反 / 検証失敗 / IO エラー等）
          → StrategyScenarioSaved(ok=False, error=<SaveErrorCode Literal>)

        レビュー反映 (2026-05-04 ラウンド1):
            - H1: error は SaveErrorCode Literal に固定
            - H2: write_back に current_path は渡さない（loaded_path 一軸）
            - M3: msg["scenario"] が None / 欠落で error="missing_scenario_field"
            - M5: rollback は ERROR ログ（reason=syntax_error|validate_failed）
            - M8: save_as のデフォルトを False に統一（dto.rs と整合）
            - M11: 入口で validate(scenario_dict) を試行 → 失敗で validate_failed

        レビュー反映 (2026-05-04 ラウンド2):
            - H-R2-1: `_emit` を `StrategyScenarioSaved(...).model_dump()` 経由に統一。
              未知 error コードは pydantic ValidationError で構築段階に検出する。
            - H-R2-2: `tempfile_failed` を SaveErrorCode から削除（dead code）。
              `tempfile.mkstemp` 失敗は errno に応じて parent_missing / disk_full /
              permission_denied のいずれかに吸収される。
            - H-R2-3: `OSError.winerror` (Windows) を `permission_denied` にマップ。
              errno が None でも winerror が分類できればそちらを優先する。
        """
        import errno as _errno
        from typing import Optional, get_args

        from engine import scenario as scenario_mod
        from engine.schemas import SaveErrorCode, StrategyScenarioSaved

        _KNOWN_SAVE_ERROR_CODES = frozenset(get_args(SaveErrorCode))

        request_id = msg.get("request_id", "")
        path_str = msg.get("path", "")

        def _emit(ok: bool, error: Optional[str]) -> None:
            # M-R3-2 (ラウンド3): assert ではなく ValueError で内部不変条件を強制。
            # `python -O` 最適化で assert は無効化されるため、本番でも保護を効かせる。
            # L-R2-1: ok / error の不整合を構築前にガード（pydantic 自身は
            # ok=True かつ error!=None の組合せを reject しないため明示チェック）。
            if ok and error is not None:
                raise ValueError(
                    f"_emit invariant: ok=True must imply error is None (got {error!r})"
                )
            # H-R2-1: 未知 error コードは StrategyScenarioSaved 構築時の
            # pydantic ValidationError で reject される。念押しのガードもここで行う。
            if error is not None and error not in _KNOWN_SAVE_ERROR_CODES:
                raise ValueError(
                    f"_emit invariant: unknown SaveErrorCode {error!r}"
                )
            evt = StrategyScenarioSaved(
                request_id=request_id,
                path=path_str,
                ok=ok,
                error=error,  # type: ignore[arg-type]
            )
            self._outbox.append(evt.model_dump(exclude_none=False))

        # M3: scenario フィールド必須チェック
        scenario_dict = msg.get("scenario")
        if scenario_dict is None or not isinstance(scenario_dict, dict):
            log.warning(
                "scenario.writeback rejected reason=missing_scenario_field path=%r",
                path_str,
            )
            _emit(False, "missing_scenario_field")
            return

        loaded_path_str = msg.get("loaded_path")
        # M8: save_as のデフォルトは False（dto.rs `#[serde(default)]` と整合）
        save_as = msg.get("save_as", False)

        # Step 9: base_dir 選定（R1-Finding-2）
        # Load 履歴がある場合は常に loaded_path.parent を基準にして ref を検証する。
        # これにより「保存先ディレクトリを変えただけで相対 ref の意味が静かに変わる」事故を防ぐ。
        if loaded_path_str is not None:
            base_dir = Path(loaded_path_str).parent
        else:
            base_dir = Path(path_str).parent

        # Step 9: Save As 別ディレクトリ + 相対 ref の事前ガード（R1-Finding-2 / 4-2）
        if save_as and loaded_path_str is not None:
            ref = scenario_dict.get("instruments_ref") if isinstance(scenario_dict, dict) else None
            if isinstance(ref, str):
                ref_path_str = ref.split("#", 1)[0]
                is_abs = bool(ref_path_str) and Path(ref_path_str).is_absolute()
                if not is_abs and Path(loaded_path_str).parent.resolve() != Path(path_str).parent.resolve():
                    log.warning(
                        "scenario.writeback rejected reason=relative_ref_crosses_dir path=%r "
                        "loaded_path=%r ref=%r",
                        path_str, loaded_path_str, ref,
                    )
                    _emit(False, "relative_ref_crosses_dir")
                    return

        # M11: 入口での形状検証（resolve → validate の順）
        # v3 の場合は resolve_refs で instruments を解決してから validate する。
        # unresolved_ref は write_back 呼び出し前に server 層で return する（R1-Finding-3 / -5）。
        try:
            resolved = scenario_mod.resolve_refs(scenario_dict, base_dir=base_dir)
            scenario_mod.validate(resolved)
        except scenario_mod.ScenarioValidationError as exc:
            if getattr(exc, "code", None) == "unresolved_ref":
                log.warning(
                    "scenario.writeback rejected reason=unresolved_ref path=%r: %s",
                    path_str, exc,
                )
                _emit(False, "unresolved_ref")
            else:
                log.warning(
                    "scenario.writeback rejected reason=validate_failed path=%r: %s",
                    path_str, exc,
                )
                _emit(False, "validate_failed")
            return

        try:
            scenario_mod.write_back(
                Path(path_str),
                scenario_dict,          # raw dict を渡す（instruments_ref 保持）
                save_as=save_as,
                loaded_path=Path(loaded_path_str) if loaded_path_str else None,
            )
            _emit(True, None)
            return
        except ValueError as exc:
            # path guard violation は ValueError("path_guard_violation: ...")
            msg_str = str(exc)
            if msg_str.startswith("path_guard_violation"):
                error_code = "path_guard_violation"
            else:
                error_code = "validate_failed"
            log.error(
                "scenario.writeback rollback reason=%s path=%r: %s",
                error_code, path_str, exc,
            )
            _emit(False, error_code)
        except scenario_mod.ScenarioValidationError as exc:
            log.error(
                "scenario.writeback rollback reason=validate_failed path=%r: %s",
                path_str, exc,
            )
            _emit(False, "validate_failed")
        except SyntaxError as exc:
            log.error(
                "scenario.writeback rollback reason=syntax_error path=%r: %s",
                path_str, exc,
            )
            _emit(False, "syntax_error")
        except OSError as exc:
            err = getattr(exc, "errno", None)
            winerr = getattr(exc, "winerror", None)
            # H-R2-3 (ラウンド2): Windows の winerror を permission_denied にマップする。
            # ERROR_ACCESS_DENIED (5) / ERROR_SHARING_VIOLATION (32) は errno に
            # 表れない経路があるため、errno が None でも winerror で分類する。
            if err == _errno.ENOSPC:
                error_code = "disk_full"
            elif err in (_errno.EACCES, _errno.EPERM):
                error_code = "permission_denied"
            elif err == _errno.ENOENT:
                error_code = "parent_missing"
            elif winerr in (5, 32):
                error_code = "permission_denied"
            else:
                error_code = "rename_failed"
            log.error(
                "scenario.writeback rollback reason=%s path=%r errno=%s winerror=%s: %s",
                error_code, path_str, err, winerr, exc,
            )
            _emit(False, error_code)
        except Exception as exc:
            # 未知例外はリグレッションを抑えるため validate_failed にフォールバック
            log.error(
                "scenario.writeback rollback reason=validate_failed path=%r: %s (%s)",
                path_str, exc, type(exc).__name__,
            )
            _emit(False, "validate_failed")

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

        # ── kabu_station は _workers 外で管理されるため専用パス ──
        if venue == "kabu_station":
            tickers = await self._list_kabu_tickers(market)
            self._outbox.append(
                {
                    "event": "TickerInfo",
                    "request_id": req_id,
                    "venue": "kabu_station",
                    "tickers": tickers,
                }
            )
            return

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

    async def _list_kabu_tickers(self, market: str) -> list[dict]:
        """KabuStation 銘柄一覧。

        stock: 立花ワーカーのキャッシュマスターを kabu_station venue として返す。
               立花未接続でもディスクキャッシュがあれば動作する。
               キャッシュなし / 失敗時は空リストで graceful degradation。
        future / option: 現時点では空リスト（TODO）。
        """
        if market != "stock":
            return []

        tachibana: TachibanaWorker | None = self._workers.get("tachibana")  # type: ignore[assignment]
        if tachibana is None:
            return []

        try:
            entries = await tachibana.list_tickers("stock")
        except Exception as exc:
            log.debug("kabu list_tickers: tachibana cache unavailable: %s", exc)
            return []

        # venue_caps を kabu 用に上書き（立花固有フラグを除去）
        kabu_caps = tachibana.venue_caps()
        return [{**e, "venue_caps": kabu_caps} for e in entries]

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
                "stats": orjson.dumps(stats).decode(),  # proto field is string (JSON-encoded)
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

    # ------------------------------------------------------------------
    # F7: unicast helper — used by _handle_stop_replay / _handle_force_stop_replay
    # for malformed_json / mode_mismatch errors that must go ONLY to the caller.
    # ------------------------------------------------------------------

    def _send_unicast(self, ws: ServerConnection, payload: dict) -> None:
        """Send `payload` only to `ws`. Used when an error response is
        per-connection (e.g. mode_mismatch / malformed_json) and must not
        leak to other clients. Mirrors the convention used by
        ``_check_replay_state`` (`_outbox.send_to(ws, payload)``).

        M-silent4: this helper is sync (the underlying `_outbox.send_to` is
        sync — it merely queues into the per-connection send-loop). Callers
        should NOT `await` the return; it returns `None`.
        """
        self._outbox.send_to(ws, payload)

    # ------------------------------------------------------------------
    # schema 3.15: Pause/Step callbacks (called from worker thread via lambda)
    # ------------------------------------------------------------------

    def _consume_step_request(self) -> int:
        """Atomically consume one pending step request; return 1 if consumed, else 0."""
        if self._replay_step_request > 0:
            self._replay_step_request -= 1
            return 1
        return 0

    # ------------------------------------------------------------------
    # B3: State machine guard helpers
    # ------------------------------------------------------------------

    def _check_replay_state(
        self,
        command: AttemptedCommand,
        required: ReplayState,
        *,
        ws: ServerConnection | None = None,
        request_id: str | None = None,
    ) -> bool:
        """Return True if _replay_state == required; otherwise emit EngineBusy and return False.

        H15: assumes ``_replay_state`` is initialised in ``__init__`` (default
        ``ReplayState.IDLE``). Tests that bypass ``__init__`` via ``__new__``
        must explicitly assign ``_replay_state`` before calling guarded
        handlers — there is no longer a silent fallback that hid initialisation
        bugs.

        M-GP8 (Phase 8 R1 / Phase 2): EngineBusy は違反 command を送って来た接続
        にだけ unicast する。``ws`` が None のとき (テストや legacy 経路) は
        broadcast にフォールバックして既存挙動を保つ。

        MEDIUM-R2-6: ``request_id`` を payload に伝播する。helper 側 wait_for()
        / events() はこの id で「自分宛の reject」を識別する。
        """
        current = self._replay_state
        if current != required:
            payload = EngineBusy(
                current_state=current.name,
                attempted_command=command,
                reason=f"replay state must be {required.name}, got {current.name}",
                request_id=request_id,
            ).model_dump()
            if ws is not None:
                self._outbox.send_to(ws, payload)
            else:
                self._outbox.append(payload)
            return False
        return True

    def _check_live_state(
        self,
        command: AttemptedCommand,
        required: LiveState,
        *,
        ws: ServerConnection | None = None,
        request_id: str | None = None,
    ) -> bool:
        """Return True if _live_state == required; otherwise emit EngineBusy and return False.

        H15: assumes ``_live_state`` is initialised in ``__init__`` (default
        ``LiveState.DISCONNECTED``). Tests bypassing ``__init__`` must assign
        ``_live_state`` explicitly — no silent default that masks bugs.

        M-GP8 (Phase 8 R1 / Phase 2): EngineBusy は違反 command を送って来た接続
        にだけ unicast する。``ws`` が None のとき (テスト経路) は broadcast に
        フォールバックする。

        MEDIUM-R2-6: ``request_id`` を payload に伝播する。
        """
        current = self._live_state
        if current != required:
            payload = EngineBusy(
                current_state=current.name,
                attempted_command=command,
                reason=f"live state must be {required.name}, got {current.name}",
                request_id=request_id,
            ).model_dump()
            if ws is not None:
                self._outbox.send_to(ws, payload)
            else:
                self._outbox.append(payload)
            return False
        return True

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
                # B3: ログインキャンセル → DISCONNECTED に戻す。
                self._live_state = LiveState.DISCONNECTED
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
                # B3: ログイン失敗 → DISCONNECTED に戻す。
                self._live_state = LiveState.DISCONNECTED
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
                # Honour the message that the auth layer composed (Python is
                # the banner-text source of truth, F-Banner1) so situational
                # variants like "service out of hours" reach the UI instead
                # of being flattened to the generic "ID/パスワード" string.
                # Falls back when the underlying exception did not supply one
                # (e.g. raw `Exception`).
                msg = getattr(exc, "message", "") or _MSG_LOGIN_FAILED
                # B3: ログイン失敗 → DISCONNECTED に戻す。
                self._live_state = LiveState.DISCONNECTED
                self._emit({
                    "event": "VenueError",
                    "venue": "tachibana",
                    "request_id": request_id,
                    "code": "login_failed",
                    "message": msg,
                })
                return

            self._apply_tachibana_session(session)
            # B3: ログイン成功 → CONNECTED 状態へ遷移。
            self._live_state = LiveState.CONNECTED
            self._connected_venue = "tachibana"
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

    async def _startup_kabu_station(self, request_id: str | None = None) -> None:
        """Drive KabuStation startup login (K7 / Phase 4). env={_kabu_env} (verify|prod).

        Emits VenueLoginStarted → VenueReady / VenueError to outbox.
        """
        try:
            async with self._kabu_login_inflight:
                self._emit({
                    "event": "VenueLoginStarted",
                    "venue": "kabu_station",
                    "request_id": request_id,
                })
                try:
                    if self._kabu_venue is None:
                        # P4-2: env は KABU_ALLOW_PROD=1 + KABU_ENV=prod の二重判定で解決。
                        # H-2: インスタンスキャッシュ済みの _kabu_env を使う（_build_ready との一貫性）。
                        # prod では dev_login / dev_trade_password を強制 False にして
                        # 本番口座への誤発注経路を最小化する（release ガードの第二段）。
                        is_prod = self._kabu_env == "prod"
                        self._kabu_venue = KabuStationVenue(
                            env=self._kabu_env,
                            dev_login_allowed=(
                                self._dev_kabu_login_allowed and not is_prod
                            ),
                            dev_trade_password_allowed=(
                                self._dev_kabu_trade_password_allowed and not is_prod
                            ),
                        )
                    await self._kabu_venue.startup_login()
                except KabuLoginCancelledError:
                    log.info("_startup_kabu_station: login cancelled by user")
                    self._live_state = LiveState.DISCONNECTED
                    self._emit({
                        "event": "VenueLoginCancelled",
                        "venue": "kabu_station",
                        "request_id": request_id,
                    })
                    return
                except KabuConnectionError as exc:
                    log.error("_startup_kabu_station: local_app_down: %s", exc)
                    self._live_state = LiveState.DISCONNECTED
                    self._emit({
                        "event": "VenueError",
                        "venue": "kabu_station",
                        "request_id": request_id,
                        "code": "local_app_down",
                        "message": str(exc),
                    })
                    return
                except Exception as exc:
                    log.exception("_startup_kabu_station: login failed: %s", exc)
                    self._live_state = LiveState.DISCONNECTED
                    self._emit({
                        "event": "VenueError",
                        "venue": "kabu_station",
                        "request_id": request_id,
                        "code": "login_failed",
                        "message": str(exc),
                    })
                    return

                self._live_state = LiveState.CONNECTED
                self._connected_venue = "kabu_station"
                log.info("KabuStation session established successfully")
                # C1: PUSH セッション ID をログイン成功時に採番（seq リセット）
                self._kabu_push_ssid = f"{self._engine_session_id}:kabu_push"
                self._kabu_push_seq = 0
                # [H-1] fill poller バックグラウンドタスクを起動
                if self._kabu_fill_poller_task is None or self._kabu_fill_poller_task.done():
                    self._kabu_fill_poller_task = asyncio.create_task(
                        self._kabu_fill_poller()
                    )
                self._emit({
                    "event": "VenueReady",
                    "venue": "kabu_station",
                    "request_id": request_id,
                })
        finally:
            # MEDIUM-C: 完了後（成功・キャンセル・エラー全パス）に stale 参照をリセットする。
            # disconnect 時の cleanup が done タスクに対して cancel を試みないよう None にする。
            self._kabu_startup_task = None

    async def _kabu_fill_poller(self) -> None:
        """kabu venue の約定 polling ループ（30 秒間隔）。[H-1]

        VenueReady 後にバックグラウンドタスクとして起動される。
        _clear_kabu_session() でキャンセルされる。
        # TODO Phase 4: emit OrderFilled IPC event（現状はログのみ）
        """
        _POLL_INTERVAL = 30.0
        try:
            while True:
                await asyncio.sleep(_POLL_INTERVAL)
                if self._kabu_venue is None:
                    break
                try:
                    fills = await self._kabu_venue.poll_fills()
                    for fill in fills:
                        log.info("kabu fill: %s", fill)
                        # TODO Phase 4: emit OrderFilled IPC event
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.error("_kabu_fill_poller: poll_fills error: %s", exc)
        except asyncio.CancelledError:
            log.info("_kabu_fill_poller: cancelled")
            raise

    # ------------------------------------------------------------------
    # C1: kabu PUSH → adapter → mapper → outbox pipeline entry points
    # ------------------------------------------------------------------

    def _on_kabu_board_push(self, raw: dict) -> None:
        """Raw kabu PUSH 板スナップショット JSON → DepthSnapshot wire dict → outbox.

        kabu PUSH には stream_session_id / sequence_id が存在しないため、
        server 層が _kabu_push_ssid / _kabu_push_seq を採番して補充する。

        TODO C1-next: Subscribe(kabu_station) コマンドを受け付けて
        _kabu_adapter の登録銘柄を動的に更新する（現状は全 PUSH 受信・全配信）。
        """
        try:
            ticker = str(raw.get("Symbol", ""))
            if not ticker:
                log.warning("_on_kabu_board_push: missing Symbol field, skipping")
                return
            ssid = self._kabu_push_ssid or str(self._engine_session_id)
            # seq は Symbol チェック通過後・parse 前にインクリメントする。
            # parse 失敗時は seq が消費されたまま outbox は空白になる（gap 発生）。
            # kabu は DepthSnapshot のみであり Rust 側 gap detector は DepthDiff の
            # prev_sequence_id 連続性を監視するため、このギャップは現行実装で影響なし。
            # 将来 kabu が DepthDiff を提供する場合はロールバック方式を再検討すること。
            self._kabu_push_seq += 1
            payload = kabu_board_to_wire_dict(
                raw,
                adapter=self._kabu_adapter,
                ticker=ticker,
                ssid=ssid,
                seq=self._kabu_push_seq,
            )
            self._outbox.append(payload)
        except Exception as exc:
            log.warning(
                "_on_kabu_board_push: parse/map error at seq=%d ticker=%s: %s",
                self._kabu_push_seq,
                ticker,
                exc,
                exc_info=True,
            )

    def _on_kabu_trade_push(self, raw: dict) -> None:
        """Raw kabu PUSH 板 JSON の CurrentPrice から Trades wire dict → outbox.

        kabu PUSH は独立した約定ストリームを持たないため、
        板スナップショットの CurrentPrice / CurrentPriceTime を約定として扱う。
        qty は 0 で正規化（kabu_push_pipeline 仕様、累積値のため）。
        """
        ticker = ""
        try:
            ticker = str(raw.get("Symbol", ""))
            if not ticker:
                log.warning("_on_kabu_trade_push: missing Symbol field, skipping")
                return
            ssid = self._kabu_push_ssid or str(self._engine_session_id)
            payload = kabu_execution_to_wire_dict(
                raw,
                adapter=self._kabu_adapter,
                ticker=ticker,
                ssid=ssid,
            )
            self._outbox.append(payload)
        except Exception as exc:
            log.warning(
                "_on_kabu_trade_push: parse/map error ticker=%s: %s",
                ticker,
                exc,
                exc_info=True,
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
        # N3 Phase 2: live strategy 実行中は FD/EC フレームをキューに投入する。
        if self._mode == "live" and self._live_state == LiveState.TRADING:
            if frame_type == "FD":
                try:
                    self._live_fd_queue.put_nowait(event)
                except _stdlib_queue.Full:
                    log.warning("live_fd_queue full, dropping FD frame")
                return
            elif frame_type == "EC":
                try:
                    self._live_ec_queue.put_nowait(event)
                except _stdlib_queue.Full:
                    log.warning("live_ec_queue full, dropping EC frame")

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

    async def _do_request_venue_login(self, msg: dict, *, ws: ServerConnection | None = None) -> None:
        """`RequestVenueLogin` from the Rust UI — drive a fresh login."""
        request_id = msg.get("request_id")
        venue = msg.get("venue")
        # Reject in replay mode: backtesting must not trigger broker login.
        # Without this guard a UI button or `/api/sidebar/tachibana/request-login`
        # HTTP call would still walk the full Tachibana → VenueReady →
        # bulk stats fetch path that broke replay startup (2026-04-30).
        if self._mode == "replay":
            # HIGH-R2-2 (M-GP8 と同じ方針): reject 応答は要求した接続にだけ
            # unicast する。broadcast すると別 client にも mode_mismatch が
            # 流れ、その client の attach session が誤って失敗扱いになる。
            payload = {
                "event": "VenueError",
                "venue": venue or "",
                "request_id": request_id,
                "code": "mode_mismatch",
                "message": "RequestVenueLogin not allowed in replay mode",
            }
            if ws is not None:
                self._outbox.send_to(ws, payload)
            else:
                self._emit(payload)
            return
        if venue not in ("tachibana", "kabu_station"):
            log.warning("RequestVenueLogin: unsupported venue=%r", venue)
            # HIGH-R2-2: unsupported venue 応答も unicast 化する。
            payload = {
                "event": "VenueError",
                "venue": venue or "",
                "request_id": request_id,
                "code": "unsupported_venue",
                "message": "対応していない venue です",
            }
            if ws is not None:
                self._outbox.send_to(ws, payload)
            else:
                self._emit(payload)
            return

        if venue == "kabu_station":
            if self._live_state == LiveState.CONNECTING and self._kabu_login_inflight.locked():
                log.info("RequestVenueLogin(kabu_station): login already in-progress (CONNECTING)")
                self._emit({"event": "VenueLoginStarted", "venue": "kabu_station", "request_id": request_id})
                return
            if self._live_state == LiveState.CONNECTED:
                log.info("RequestVenueLogin(kabu_station): re-login requested while CONNECTED — clearing session")
                self._live_state = LiveState.DISCONNECTED
                # tachibana 接続中なら旧 EVENT WS と session もクリア (M3)
                if self._connected_venue == "tachibana":
                    if self._event_task is not None and not self._event_task.done():
                        self._event_task.cancel()
                    self._tachibana_session = None
                    self._workers["tachibana"].set_session(None)
                    tachibana_clear_session(self._cache_dir)
                if self._kabu_venue is not None:
                    self._kabu_venue.clear()
                self._connected_venue = None
            if self._kabu_login_inflight.locked():
                log.info("RequestVenueLogin(kabu_station): login already in-flight")
                self._emit({"event": "VenueLoginStarted", "venue": "kabu_station", "request_id": request_id})
                return
            self._live_state = LiveState.CONNECTING
            task = asyncio.create_task(self._startup_kabu_station(request_id=request_id))
            task.add_done_callback(
                lambda t: log.error(
                    "_startup_kabu_station task raised: %s", t.exception()
                ) if not t.cancelled() and t.exception() is not None else None
            )
            self._kabu_startup_task = task
            return

        # H-TA1: CONNECTING 中の RequestVenueLogin は「ログイン中」として VenueLoginStarted を返す。
        if self._live_state == LiveState.CONNECTING:
            log.info("RequestVenueLogin: login already in-progress (CONNECTING), re-emitting VenueLoginStarted")
            self._emit({"event": "VenueLoginStarted", "venue": "tachibana", "request_id": request_id})
            return

        # CONNECTED 中の RequestVenueLogin は「ユーザーが明示的に再ログイン要求」とみなして
        # 既存セッションをクリアし DISCONNECTED に戻してから通常フローへ進む。
        # Rust 側 FSM (`venue_state.rs::try_claim_login_in_flight_succeeds_from_ready`) が
        # Ready からの再ログインを許可しているため、ここで弾くと UI と齟齬が出る (2026-05-04)。
        #
        # Bug X (docs/✅tachibana/fix-event-ws-lifecycle-2026-05-04.md):
        # 旧 EVENT WS ループ (`_event_task`) もここで cancel する。新ログインが
        # 失敗・キャンセルされると `_startup_tachibana` の終端 (2454-2455) を
        # 通らず、旧セッション URL から EC 約定通知を受け続けるゴースト状態が
        # 残るため。新ログイン成功時は `_startup_tachibana` が新タスクを建て
        # 直すので await は不要。
        if self._live_state == LiveState.CONNECTED:
            log.info(
                "RequestVenueLogin: re-login requested while CONNECTED — "
                "clearing session and cancelling old EVENT loop"
            )
            self._live_state = LiveState.DISCONNECTED
            if self._event_task is not None and not self._event_task.done():
                self._event_task.cancel()

        # B3: 上記の救済を経た上で DISCONNECTED であることを保証する。
        if not self._check_live_state("RequestVenueLogin", LiveState.DISCONNECTED, ws=ws):
            return

        if self._tachibana_login_inflight.locked():
            log.info("RequestVenueLogin: login already in-flight, re-emitting VenueLoginStarted")
            self._emit({"event": "VenueLoginStarted", "venue": "tachibana", "request_id": request_id})
            return

        self._tachibana_session = None
        self._workers["tachibana"].set_session(None)
        tachibana_clear_session(self._cache_dir)
        # B3: RequestVenueLogin 受理 → CONNECTING 状態へ遷移。
        self._live_state = LiveState.CONNECTING
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
        self,
        msg: dict,
        *,
        base_dir: Path | None = None,
        ws: ServerConnection | None = None,
    ) -> None:
        """LoadReplayData IPC: J-Quants ファイルを件数確認だけして ReplayDataLoaded 送出。

        ``base_dir`` はテスト用 fixtures パス上書き。本番は ``None`` (= S:/j-quants)。

        M3: mode="live" では replay 系 IPC を受理しない（D8 / spec §3.2 起動時固定）。
        Error{request_id, code="mode_mismatch"} を返して即 return する。

        .. note:: ``strategy_file`` / ``strategy_init_kwargs`` はこのハンドラでは使用しない。
            戦略ロードは ``StartEngine`` (``EngineStartConfig``) 経由で行う。
            ``LoadReplayData`` は件数カウントのみ担当する。
        """
        instrument_id = msg.get("instrument_id", "")
        instrument_ids = msg.get("instrument_ids")  # schema 3.13: 複数銘柄 (None なら instrument_id 単体)
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

        # B3: LoadReplayData は Replay::IDLE 状態のみ受理する。
        if not self._check_replay_state("LoadReplayData", ReplayState.IDLE, ws=ws):
            return

        from engine.nautilus.jquants_loader import check_data_exists

        # schema 3.13: instrument_ids が指定されていればそちらを使う
        _iids_to_check = instrument_ids if instrument_ids else [instrument_id]
        try:
            bars_loaded = 0
            trades_loaded = 0
            kwargs = {"base_dir": base_dir} if base_dir is not None else {}
            for _iid in _iids_to_check:
                check_data_exists(_iid, start_date, end_date, granularity, **kwargs)
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

        # B3: データ確認成功 → LOADED 状態へ遷移。
        self._replay_state = ReplayState.LOADED

        # H-H: LoadReplayData 時にポートフォリオをリセット（strategy 確定前なので cash=0）。
        from decimal import Decimal as _Decimal
        self._replay_strategy_id = ""
        self._replay_portfolio.reset(_Decimal(0))

        # schema 3.14: 新セッション識別子。GUI は前 epoch の registered ペインを
        # 全閉じしてから新 epoch のペインを構築する。発番元は本ハンドラのみ。
        epoch = self._next_replay_session_epoch()
        log.info(
            "LoadReplayData accepted: session_epoch=%d instrument_ids=%r granularity=%r",
            epoch, _iids_to_check, granularity,
        )
        self._outbox.append(
            {
                "event": "ReplayDataLoaded",
                # M-8 (R1b / schema 2.5): 単独 LoadReplayData は strategy 未起動のため
                # null を送る (旧 minor=4 では "" だった)。Rust 側は Option<String>::None
                # で受ける。strategy 経路 (start_backtest_replay) は別 emit で具体値。
                "strategy_id": None,
                "bars_loaded": bars_loaded,
                "trades_loaded": trades_loaded,
                # schema 3.12: GUI helper-attach 経路で `auto_generate_replay_panes`
                # を起動できるよう instrument_id / granularity を同梱する。
                "instrument_id": instrument_id,
                "instrument_ids": instrument_ids,  # schema 3.13: 複数銘柄 (None なら後方互換)
                "granularity": granularity,
                "session_epoch": epoch,
                "ts_event_ms": int(time.time() * 1000),
            }
        )

    async def _handle_start_engine(
        self,
        msg: dict,
        *,
        base_dir: Path | None = None,
        ws: ServerConnection | None = None,
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
        from pydantic import ValidationError
        from engine.schemas import EngineError as EngineErrorModel, EngineStartConfig
        from engine.nautilus.engine_runner import NautilusRunner

        engine_kind = msg.get("engine", "")
        strategy_id = msg.get("strategy_id", "")
        config_raw = msg.get("config", {})
        request_id = msg.get("request_id")

        # MEDIUM-2: request_id が None/空の場合は防御的に早期 return する。
        # Rust 側の StartEngine は必ず request_id を付けるが、不正メッセージ対策。
        if not request_id:
            log.warning("StartEngine: missing request_id, ignoring")
            return

        # B3: StartEngine は Replay::LOADED 状態のみ受理する（mode="replay" の場合）。
        # mode="live" の場合は validate_start_engine が ModeMismatchError を raise するので
        # state guard は replay モードのみ適用する。
        if self._mode == "replay":
            if not self._check_replay_state("StartEngine", ReplayState.LOADED, ws=ws):
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

        # HIGH-2: reset snapshot buffer for the new session and wire notify callback.
        # Guard with hasattr: tests that create the server via __new__ may not have called
        # __init__, so these fields might be absent on synthetic server instances.
        if hasattr(self, "_replay_snapshots_lock"):
            with self._replay_snapshots_lock:
                self._replay_snapshots.clear()
            self._restore_strategy_holder[0] = None
            self._notify_history_fn = lambda: _emit_threadsafe(
                {"event": "ReplayHistoryChanged", "has_history": True}
            )
            _emit_direct({"event": "ReplayHistoryChanged", "has_history": False})

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

        # H-4: EngineStartConfig.model_validate() で extra フィールドや型違いを弾く。
        # extra="forbid" が機能するのはここだけ（raw dict のままでは機能しない）。
        try:
            config_obj = EngineStartConfig.model_validate(config_raw)
        except ValidationError as exc:
            _emit(
                {
                    "event": "Error",
                    "request_id": request_id,
                    "code": "invalid_config",
                    "message": str(exc),
                }
            )
            await _drain()
            return

        # Phase 2: initial_cash のパースは replay 分岐内で行う（live は initial_cash=None 許容）。
        # initial_cash 変数は後の _run() クロージャ内 replay 分岐で定義される。
        # live では使わないため None で初期化しておく。
        initial_cash: "int | None" = None

        if not config_obj.strategy_file:
            _emit(
                {
                    "event": "Error",
                    "request_id": request_id,
                    "code": "strategy_file_required",
                    "message": "strategy_file is required",
                }
            )
            await _drain()
            return

        runner = NautilusRunner()
        # 走行中ハンドルを保持 (StopEngine で参照)。N1.4 は同時 1 戦略想定。
        self._engine_tasks[strategy_id] = runner

        # B3: StartEngine 受理 → RUNNING 状態へ遷移（replay モードのみ）。
        if self._mode == "replay":
            self._replay_state = ReplayState.RUNNING
            # schema 3.15: pause/step フィールドを初期化。
            self._replay_paused = False
            self._replay_step_request = 0

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

        # Silent-M1 (Phase 8 R1 / Phase 2): 旧実装で使っていた started_marker は
        # 例外時の EngineStopped 補完を抑制するためだけに存在し、未送出時の
        # silent failure を生んでいた。except 経路で常に補完するように変更したため
        # marker は不要になり削除した。以下の callback は ExecutionMarker /
        # streaming fills 集約 + EngineStopped emission tracking を担当する。
        # MEDIUM-R2-8: runner が EngineStopped を emit したら ``engine_stopped_emitted``
        # を True にし、except / timeout 補完パスで二重送出を防ぐ。
        engine_stopped_emitted: list[bool] = [False]

        def _on_event_tracked(evt: dict) -> None:
            ev_kind = evt.get("event")
            if ev_kind == "EngineStarted":
                # Phase 2: replay のみ streaming fills をリセット（live では操作しない）。
                if self._mode == "replay":
                    self._replay_streaming_fills.clear()
            elif ev_kind == "EngineStopped":
                # MEDIUM-R2-8: runner が自前で EngineStopped を流した。
                # 例外パスの補完送出をスキップするためのフラグを立てる。
                engine_stopped_emitted[0] = True
            elif ev_kind == "ExecutionMarker":
                # Phase 2: streaming replay の約定蓄積は replay のみ（live では操作しない）。
                if self._mode == "replay":
                    qty_str = evt.get("qty", "0")
                    ts = evt.get("ts_event_ms", 0)
                    self._replay_streaming_fills.append({
                        "client_order_id": f"replay-fill-{ts}",
                        "venue_order_id": "",
                        "instrument_id": evt.get("instrument_id", ""),
                        "order_side": evt.get("side", "BUY"),
                        "order_type": "MARKET",
                        "quantity": qty_str,
                        "filled_qty": qty_str,
                        "leaves_qty": "0",
                        "price": evt.get("price"),
                        "trigger_price": None,
                        "time_in_force": "DAY",
                        "expire_time_ns": None,
                        "status": "FILLED",
                        "ts_event_ms": ts,
                        "venue": "replay",
                    })
            _on_event(evt)

        # C-1: result_holder で ReplayBacktestResult をキャプチャし、fills を portfolio に反映。
        result_holder: list = [None]

        def _run() -> None:
            if self._mode == "replay":
                # Phase 2: initial_cash のパースを replay 分岐内へ移設。
                # live では initial_cash=None を許容するため、_run() 外でパースしない。
                nonlocal initial_cash
                try:
                    initial_cash = int(config_obj.initial_cash)
                except (ValueError, TypeError) as exc:
                    _on_event_tracked(
                        {
                            "event": "Error",
                            "request_id": request_id,
                            "code": "invalid_config",
                            "message": f"initial_cash: {exc}",
                        }
                    )
                    return
                stop_event = self._engine_stop_events.setdefault(
                    strategy_id, threading.Event()
                )
                result_holder[0] = runner.start_backtest_replay_streaming(
                    strategy_id=strategy_id,
                    instrument_id=config_obj.instrument_id,
                    instrument_ids=config_obj.instrument_ids,  # schema 3.13
                    start_date=config_obj.start_date,
                    end_date=config_obj.end_date,
                    granularity=config_obj.granularity,
                    initial_cash=initial_cash,
                    get_multiplier=lambda: getattr(self, "_replay_speed_multiplier", 1),
                    base_dir=base_dir,
                    on_event=_on_event_tracked,
                    strategy_file=config_obj.strategy_file,
                    strategy_init_kwargs=config_obj.strategy_init_kwargs,
                    stop_event=stop_event,
                    # schema 3.14: 1 ファイル切替 = 1 epoch。LoadReplayData で発番済みの
                    # epoch をそのまま再利用する（StartEngine は B3 ガードで LOADED 状態
                    # 必須なので必ず >= 1）。ここで `_next_replay_session_epoch()` を
                    # 呼ぶと epoch が +2 進み、GUI が直前 LoadReplayData で生成した
                    # ペインを drain して誤再生成する（review-fix R1 HIGH）。
                    session_epoch=self._replay_session_epoch,
                    # schema 3.15: pause/step コールバック。
                    get_paused=lambda: getattr(self, "_replay_paused", False),
                    consume_step_request=self._consume_step_request,
                    # schema 3.16: snapshot ring buffer push クロージャ。
                    push_snapshot=self._push_replay_snapshot,
                    # HIGH-1: strategy_state restore channel (asyncio ↔ runner thread).
                    restore_strategy_holder=getattr(self, "_restore_strategy_holder", None),
                    # HIGH-2: portfolio_state restore channel (asyncio ↔ runner thread).
                    restore_portfolio_holder=getattr(self, "_restore_portfolio_holder", None),
                    # schema 3.16: snapshot 時点の streaming fills をコピーして渡す。
                    # StepBackward 後の OrderList rollback で _replay_streaming_fills
                    # を正確に復元するために必要。
                    get_orders_snapshot=lambda: list(self._replay_streaming_fills),
                )
            elif self._mode == "live":
                # Phase 2: live 分岐。CONNECTED 状態のみ受理する。
                if not self._check_live_state("StartEngine", LiveState.CONNECTED):
                    return
                # tachibana 以外の venue でログイン済みの場合は拒否する。
                # kabu_station の live 実行は未実装のため、ここで明示的にエラーを返す。
                if self._connected_venue != "tachibana":
                    _on_event_tracked(
                        {
                            "event": "Error",
                            "request_id": request_id,
                            "code": "venue_not_supported",
                            "message": f"Live engine requires tachibana venue (connected: {self._connected_venue!r}). kabu_station live execution is not yet implemented.",
                        }
                    )
                    return
                # live 専用バリデーション
                if config_obj.max_qty is None or config_obj.max_notional_jpy is None:
                    _on_event_tracked(
                        {
                            "event": "Error",
                            "request_id": request_id,
                            "code": "invalid_config",
                            "message": "max_qty and max_notional_jpy required for live engine",
                        }
                    )
                    return
                # SessionHolder から第二暗証番号を取得（env 変数不使用）
                second_password = self._session_holder.get_password()
                if second_password is None:
                    _on_event_tracked(
                        {
                            "event": "SecondPasswordRequired",
                            "request_id": request_id,
                        }
                    )
                    return
                self._live_state = LiveState.TRADING
                stop_event = self._engine_stop_events.setdefault(
                    strategy_id, threading.Event()
                )
                runner.start_live(
                    instrument_id=config_obj.instrument_id,
                    strategy_file=config_obj.strategy_file,
                    strategy_init_kwargs=config_obj.strategy_init_kwargs,
                    max_qty=config_obj.max_qty,
                    max_notional_jpy=config_obj.max_notional_jpy,
                    second_password=second_password,
                    session=self._tachibana_session,
                    fd_queue=self._live_fd_queue,
                    ec_queue=self._live_ec_queue,
                    on_event=_on_event_tracked,
                    stop_event=stop_event,
                    strategy_id=strategy_id,
                )

        try:
            # H2: timeout=3600s でラップし、TimeoutError を code="timeout" で送出。
            await asyncio.wait_for(asyncio.to_thread(_run), timeout=3600.0)
            # C-1: 実行完了後に fills を PortfolioView に反映する（replay のみ）。
            if self._mode == "replay":
                result = result_holder[0]
                if result is not None:
                    from decimal import Decimal as _Decimal
                    self._replay_strategy_id = result.strategy_id
                    self._replay_portfolio.reset(_Decimal(initial_cash))
                    for fill in result.portfolio_fills:
                        self._replay_portfolio.on_fill(
                            fill.instrument_id, fill.side, fill.qty, fill.price
                        )
                else:
                    log.warning(
                        "[StartEngine] start_backtest_replay returned None; portfolio not updated for strategy_id=%r",
                        strategy_id,
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
            except Exception as stop_exc:
                log.warning("[StartEngine] runner.stop() failed during timeout cleanup: %s", stop_exc)
            # HIGH-1: timeout 後も worker thread は走り続けるため started_marker に依存しない。
            # Rust 側は EngineStarted なしの EngineStopped を no-op として扱う。
            # MEDIUM-R2-8: runner が既に EngineStopped を流していたら二重送出を避ける。
            if not engine_stopped_emitted[0]:
                _emit(
                    {
                        "event": "EngineStopped",
                        "strategy_id": strategy_id,
                        "final_equity": "0",
                        "ts_event_ms": int(time.time() * 1000),
                    }
                )
                engine_stopped_emitted[0] = True
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
            # Silent-M1 (Phase 8 R1 / Phase 2): 旧実装は EngineStarted 送出済み
            # (started_marker["sent"]) のときだけ EngineStopped を補完していたが、
            # NautilusRunner が EngineStarted を emit する前に raise すると
            # EngineStopped が一度も流れず、Rust 側 state machine が stuck する
            # silent failure を生んでいた。常に補完送出する。Rust 側は
            # EngineStarted なしの EngineStopped を no-op として扱う前提
            # （TimeoutError パスと同じ契約）。
            # MEDIUM-R2-8: runner が emit していたら二重送出を避ける。
            if not engine_stopped_emitted[0]:
                _emit(
                    {
                        "event": "EngineStopped",
                        "strategy_id": strategy_id,
                        "final_equity": "0",
                        "ts_event_ms": int(time.time() * 1000),
                    }
                )
                engine_stopped_emitted[0] = True
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
            self._engine_stop_events.pop(strategy_id, None)
            # B3 / MEDIUM-R3-2: 走行終了（正常・例外・timeout 問わず）→ IDLE 状態へ戻す。
            # ただし「全断 → 再接続 → LoadReplayData (LOADED)」が走った後に
            # 古い runner の to_thread 完了がここに到達するケースでは、
            # 現在の state が LOADED （別セッションが用意済み）の可能性がある。
            # 古い runner の finally が新セッションの LOADED を IDLE に
            # 上書きするとリグレッションになるため、自分の終了に責任のある
            # RUNNING / STOPPING のときだけ IDLE に戻す。
            if self._mode == "replay" and self._replay_state in (
                ReplayState.RUNNING,
                ReplayState.PAUSED,
                ReplayState.STOPPING,
            ):
                self._replay_state = ReplayState.IDLE
            # M-8 (R2 review-fix R2): 走行終了時に _replay_strategy_id をリセット。
            # 次の GetBuyingPower(replay) は空 strategy_id を返し、UI 側で
            # 「未実行」を識別できるようにする。
            if self._mode == "replay" and self._replay_strategy_id == strategy_id:
                self._replay_strategy_id = ""
            # Phase 2: live 走行終了後に CONNECTED に戻す + キュードレイン。
            if self._mode == "live" and self._live_state in (
                LiveState.TRADING,
                LiveState.STOPPING,
            ):
                self._live_state = LiveState.CONNECTED
            # Phase 2: live_fd_queue / live_ec_queue の残留メッセージをドレイン。
            if self._mode == "live":
                for _q in (self._live_fd_queue, self._live_ec_queue):
                    while not _q.empty():
                        try:
                            _q.get_nowait()
                        except _stdlib_queue.Empty:
                            break
            # H-G (R1b): 関数 return 前に scheduled callback を drain して呼び出し側
            # (テスト含む) が outbox を直ちに観測できるようにする。
            await _drain()

    async def _handle_stop_engine(self, msg: dict, *, ws: ServerConnection | None = None) -> None:
        """StopEngine IPC: 走行中 NautilusRunner を停止する。

        N1.4 では BacktestEngine.run() が完了するまで待つしかないため、
        記録されている runner があれば ``stop()`` を呼ぶだけ。完了待ちは
        ``_handle_start_engine`` の to_thread 完了に任せる。
        """
        strategy_id = msg.get("strategy_id", "")

        # B3: StopEngine は Replay::RUNNING 状態のみ受理する（replay モードの場合）。
        if self._mode == "replay":
            if not self._check_replay_state("StopEngine", ReplayState.RUNNING, ws=ws):
                return
            self._replay_state = ReplayState.STOPPING
        elif self._mode == "live":
            # Phase 2: live TRADING 状態のみ StopEngine を受理する。
            if not self._check_live_state("StopEngine", LiveState.TRADING, ws=ws):
                return
            self._live_state = LiveState.STOPPING
            stop_event = self._engine_stop_events.get(strategy_id)
            if stop_event:
                stop_event.set()
            return

        runner = self._engine_tasks.get(strategy_id)
        if runner is None:
            log.info("StopEngine: no running engine for strategy_id=%r", strategy_id)
            return
        stop_event = self._engine_stop_events.get(strategy_id)
        if stop_event is not None:
            stop_event.set()
        try:
            runner.stop()
        except Exception as exc:
            log.warning("StopEngine: runner.stop() raised: %s", exc)
        # M-8 (R2 review-fix R2): StopEngine 受理時点で _replay_strategy_id を直ちに
        # クリアする。runner 完了は別 thread で進行するため、その間の
        # GetBuyingPower(replay) も「未実行」として扱う。
        if self._mode == "replay" and self._replay_strategy_id == strategy_id:
            self._replay_strategy_id = ""

    async def _handle_stop_replay(
        self, msg: dict, *, ws: ServerConnection
    ) -> None:
        """StopReplay IPC: replay セッションを graceful に停止する（F7）。

        replay モードかつ RUNNING 状態のみ受理する。
        内部で active な strategy_id を解決して runner を停止し、
        完了後に ReplayStopped を全クライアントに broadcast する。

        M12: ws is mandatory — error responses (mode_mismatch / malformed_json)
             must unicast to the originating caller, not broadcast.
        """
        # M-1: ws is structurally mandatory for unicast error replies. A None
        # ws would silently fall through to broadcast in `_send_unicast`,
        # which violates the M10/M12 contract. Fail loud at the boundary.
        assert ws is not None, "ws is mandatory for unicast error responses"

        from engine.schemas import ReplayStopped as ReplayStoppedModel

        # M10: reject malformed messages with no/empty request_id by unicasting
        # an EngineError back to the caller. Empty-string fallback was masking
        # client bugs and producing replies with request_id="".
        request_id_raw = msg.get("request_id")
        if not isinstance(request_id_raw, str) or not request_id_raw:
            self._send_unicast(
                ws,
                EngineError(
                    code="malformed_json",
                    message="StopReplay requires a non-empty request_id",
                    strategy_id=None,
                ).model_dump(),
            )
            return
        request_id: str = request_id_raw

        # replay モードでのみ有効（live モードで受信した場合は mode_mismatch Error を返す）
        # H4: live モードでの mode_mismatch error は呼び出し元のみに unicast する
        # （broadcast すると他のクライアントが偽の error 表示をする）。
        if self._mode != "replay":
            log.info("StopReplay: ignored in mode=%r", self._mode)
            self._send_unicast(
                ws,
                EngineError(
                    code="mode_mismatch",
                    message="StopReplay is only valid in replay mode",
                    strategy_id=None,
                ).model_dump(),
            )
            return

        # RUNNING または PAUSED 状態のみ受理（STOPPING / IDLE / LOADED は EngineBusy）
        if self._replay_state not in (ReplayState.RUNNING, ReplayState.PAUSED):
            from engine.schemas import EngineBusy as EngineBusyModel
            payload = EngineBusyModel(
                current_state=self._replay_state.name,
                attempted_command="StopReplay",
                reason=f"replay state must be RUNNING or PAUSED, got {self._replay_state.name}",
                request_id=request_id,
            ).model_dump()
            self._outbox.send_to(ws, payload)
            return
        if self._replay_state == ReplayState.PAUSED:
            self._replay_paused = False

        # RUNNING / PAUSED → STOPPING へ遷移
        self._replay_state = ReplayState.STOPPING

        # active な strategy_id を取得（先頭 1 件）
        strategy_ids = list(self._engine_tasks.keys())
        strategy_id = strategy_ids[0] if strategy_ids else ""

        if strategy_id:
            stop_event = self._engine_stop_events.get(strategy_id)
            if stop_event is not None:
                stop_event.set()
            runner = self._engine_tasks.get(strategy_id)
            if runner is not None:
                try:
                    runner.stop()
                except Exception as exc:
                    log.warning("StopReplay: runner.stop() raised: %s", exc)
            # strategy_id をクリア（GetBuyingPower(replay) が「未実行」として扱われるよう）
            if self._replay_strategy_id == strategy_id:
                self._replay_strategy_id = ""
        else:
            log.info("StopReplay: no active runner found, emitting ReplayStopped immediately")

        # H2: state を IDLE に戻してから broadcast する（_handle_force_stop_replay
        # の M-5 と同じ順序）。STOPPING のまま broadcast すると、ReplayStopped を
        # 受け取ったクライアントが直後に LoadReplayData / StartEngine を投げても
        # state guard で弾かれる race を防ぐ。
        self._replay_state = ReplayState.IDLE

        # ReplayStopped を全クライアントに broadcast
        self._outbox.append(
            ReplayStoppedModel(
                request_id=request_id,
                final_equity=None,
            ).model_dump()
        )
        log.info("StopReplay: broadcast ReplayStopped(request_id=%r)", request_id)

    async def _handle_force_stop_replay(
        self, msg: dict, *, ws: ServerConnection
    ) -> None:
        """ForceStopReplay IPC: replay セッションを強制停止する（F7 タイムアウト fallback）。

        _replay_state に関わらず強制実行する（state guard を使わない）。
        active な全ランナーを強制停止し、ReplayStopped を broadcast する。

        H3: 完了時に _replay_state を IDLE に戻し、後続の LoadReplayData /
            StartEngine が STOPPING 残留で弾かれないようにする。
        M10: request_id 空 / 非 str はクライアントバグなので malformed_json で reject する。
        M12: ws is mandatory — error responses must unicast to the caller.
        """
        # M-1: ws is structurally mandatory for unicast error replies.
        assert ws is not None, "ws is mandatory for unicast error responses"

        from engine.schemas import ReplayStopped as ReplayStoppedModel

        # M10: validate request_id and unicast malformed error.
        request_id_raw = msg.get("request_id")
        if not isinstance(request_id_raw, str) or not request_id_raw:
            self._send_unicast(
                ws,
                EngineError(
                    code="malformed_json",
                    message="ForceStopReplay requires a non-empty request_id",
                    strategy_id=None,
                ).model_dump(),
            )
            return
        request_id: str = request_id_raw

        log.info(
            "ForceStopReplay: force-stopping all runners (state=%r, request_id=%r)",
            self._replay_state.name if hasattr(self._replay_state, "name") else self._replay_state,
            request_id,
        )

        # 全ランナーを強制停止
        for sid, runner in list(self._engine_tasks.items()):
            stop_event = self._engine_stop_events.get(sid)
            if stop_event is not None:
                stop_event.set()
            try:
                runner.stop()
            except Exception as exc:
                log.warning("ForceStopReplay: runner.stop() for %r raised: %s", sid, exc)

        # strategy_id をクリア
        self._replay_strategy_id = ""
        # schema 3.15: pause/step フラグをリセット
        self._replay_paused = False
        self._replay_step_request = 0

        # M-5: state を IDLE に戻してから ReplayStopped を broadcast する。
        # H3 (旧コード) では broadcast の後に state リセットしていたが、
        # ReplayStopped を受け取ったクライアントが直後に LoadReplayData /
        # StartEngine を投げると、まだ STOPPING 残留状態で受信されて state
        # guard に弾かれる race があった。reset → broadcast の順序にする
        # ことで「ReplayStopped を受信した時点で IDLE が確定している」という
        # 不変条件を保証する。
        self._replay_state = ReplayState.IDLE

        # ReplayStopped を全クライアントに broadcast
        self._outbox.append(
            ReplayStoppedModel(
                request_id=request_id,
                final_equity=None,
            ).model_dump()
        )
        log.info("ForceStopReplay: broadcast ReplayStopped(request_id=%r)", request_id)

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


# Public alias used by tests and external callers that prefer the shorter name.
EngineServer = DataEngineServer
