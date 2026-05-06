"""IPC message schemas — pydantic models matching docs/schemas/*.json"""

from __future__ import annotations

from typing import Any, Literal, Optional, Union, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from engine.exchanges.tachibana_codec import deserialize_tachibana_list

SCHEMA_MAJOR: int = 3
SCHEMA_MINOR: int = 14

# ---------------------------------------------------------------------------
# Phase 8 review-fix-loop R1 / Phase 1 (型基盤) — type aliases shared across
# IPC layer, server state machine guards, and helper-class translation.
# ---------------------------------------------------------------------------

# AppMode: 起動時固定モード (`Hello.mode` と `DataEngineServer._mode` の共有)
AppMode = Literal["live", "replay"]

# ReplayStateName / LiveStateName: 直交する 2 つの state machine の wire 名前。
# server.py の `ReplayState` / `LiveState` Enum の `.name` と一致させる。
ReplayStateName = Literal["IDLE", "LOADED", "RUNNING", "STOPPING"]
LiveStateName = Literal["DISCONNECTED", "CONNECTING", "CONNECTED"]

# CurrentEngineState: EngineBusy.current_state の wire 形 (どちらかの state)
CurrentEngineState = Union[ReplayStateName, LiveStateName]

# Replay-only / Live-only コマンドの分類 (EngineBusy 直交制約に使用)
ReplayOnlyCommand = Literal[
    "LoadReplayData",
    "StartEngine",
    "StopEngine",
    "SetReplaySpeed",
    "StopReplay",
    "ForceStopReplay",
]
LiveOnlyCommand = Literal[
    "ModifyOrder",
    "CancelOrder",
    "CancelAllOrders",
    "RequestVenueLogin",
    "GetBuyingPower",
    "GetPositions",
    "GetOrderList",
]
# SubmitOrder は venue により replay/live の両方で発生し得る (server.py 980, 1040)。
SharedCommand = Literal["SubmitOrder"]

# AttemptedCommand: EngineBusy.attempted_command の wire 形。
# server.py `_check_replay_state` / `_check_live_state` の `command:` 引数も同じ alias を使う。
AttemptedCommand = Literal[
    "LoadReplayData",
    "StartEngine",
    "StopEngine",
    "SetReplaySpeed",
    "StopReplay",
    "ForceStopReplay",
    "SubmitOrder",
    "ModifyOrder",
    "CancelOrder",
    "CancelAllOrders",
    "RequestVenueLogin",
    "GetBuyingPower",
    "GetPositions",
    "GetOrderList",
]

# AUTH_FAILED_CODE: 認証失敗時の EngineError.code (Phase 2 以降が import 可能)
AUTH_FAILED_CODE: str = "auth_failed"

# SaveErrorCode (F6 / レビュー反映 2026-05-04 ラウンド1 / H1, ラウンド2 / H-R2-2):
# `StrategyScenarioSaved.error` の wire 形を Literal で固定する。これら 8 値以外は
# pydantic validation で reject される。server.py 側の例外→error コード変換は
# `_do_save_strategy_scenario` の責務。
#
# ラウンド2 で `tempfile_failed` を削除（dead code: `tempfile.mkstemp` 失敗は
# OSError errno に応じて parent_missing / disk_full / permission_denied のいずれかに
# 吸収される。専用コードは server.py の例外マッピングから到達しない）。
SaveErrorCode = Literal[
    "permission_denied",
    "parent_missing",
    "disk_full",
    "path_guard_violation",
    "rename_failed",
    "missing_scenario_field",
    "validate_failed",
    "syntax_error",
]

# 内部: EngineBusy validator が使う set
_REPLAY_STATES: frozenset[str] = frozenset(get_args(ReplayStateName))
_LIVE_STATES: frozenset[str] = frozenset(get_args(LiveStateName))
_REPLAY_ONLY_COMMANDS: frozenset[str] = frozenset(get_args(ReplayOnlyCommand))
_LIVE_ONLY_COMMANDS: frozenset[str] = frozenset(get_args(LiveOnlyCommand))


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class IpcMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")


# ---------------------------------------------------------------------------
# Rust → Python (commands)
# ---------------------------------------------------------------------------


class Hello(IpcMessage):
    op: Literal["Hello"] = "Hello"
    schema_major: int
    schema_minor: int
    client_version: str
    token: str
    # N1.13: 起動時固定 mode (`"live"` | `"replay"`).
    # 旧クライアント互換のため省略時は "live" にフォールバック。
    mode: AppMode = "live"


class SetProxy(IpcMessage):
    op: Literal["SetProxy"] = "SetProxy"
    url: str | None = None


class Subscribe(IpcMessage):
    op: Literal["Subscribe"] = "Subscribe"
    venue: str
    ticker: str
    stream: str
    timeframe: str | None = None
    market: str | None = None


class Unsubscribe(IpcMessage):
    op: Literal["Unsubscribe"] = "Unsubscribe"
    venue: str
    ticker: str
    stream: str
    timeframe: str | None = None
    market: str | None = None


class FetchKlines(IpcMessage):
    op: Literal["FetchKlines"] = "FetchKlines"
    request_id: str
    venue: str
    ticker: str
    timeframe: str
    limit: int
    start_ms: int | None = None
    end_ms: int | None = None


class FetchTrades(IpcMessage):
    op: Literal["FetchTrades"] = "FetchTrades"
    request_id: str
    venue: str
    ticker: str
    market: str | None = None
    start_ms: int
    end_ms: int
    data_path: str | None = None


class FetchOpenInterest(IpcMessage):
    op: Literal["FetchOpenInterest"] = "FetchOpenInterest"
    request_id: str
    venue: str
    ticker: str
    timeframe: str
    limit: int
    start_ms: int | None = None
    end_ms: int | None = None


class FetchTickerStats(IpcMessage):
    op: Literal["FetchTickerStats"] = "FetchTickerStats"
    request_id: str
    venue: str
    ticker: str
    market: str | None = None


class ListTickers(IpcMessage):
    op: Literal["ListTickers"] = "ListTickers"
    request_id: str
    venue: str
    market: str | None = None


class GetTickerMetadata(IpcMessage):
    op: Literal["GetTickerMetadata"] = "GetTickerMetadata"
    request_id: str
    venue: str
    ticker: str
    market: str | None = None


class RequestDepthSnapshot(IpcMessage):
    op: Literal["RequestDepthSnapshot"] = "RequestDepthSnapshot"
    request_id: str
    venue: str
    ticker: str
    market: str | None = None


class Shutdown(IpcMessage):
    op: Literal["Shutdown"] = "Shutdown"


class RequestVenueLogin(IpcMessage):
    op: Literal["RequestVenueLogin"] = "RequestVenueLogin"
    request_id: str
    venue: str


# ── Order Phase commands (schema 1.3) ────────────────────────────────────────


class SetSecondPassword(IpcMessage):
    """Set second password in Python memory for order submission.
    `value` is transmitted as plain string; Python must wrap it in SecretStr.
    extra="forbid" prevents unknown-field injection (C-1).
    """

    model_config = ConfigDict(extra="forbid")

    op: Literal["SetSecondPassword"] = "SetSecondPassword"
    request_id: str
    value: str


class ForgetSecondPassword(IpcMessage):
    """Clear the second password from Python memory."""

    model_config = ConfigDict(extra="forbid")

    op: Literal["ForgetSecondPassword"] = "ForgetSecondPassword"


class SubmitOrderRequest(IpcMessage):
    """Order placement request — shape matches the nautilus OrderFactory input.
    extra='forbid' prevents second_password / p_no injection (C-R2-M3 / D3-1).
    """

    model_config = ConfigDict(extra="forbid")

    client_order_id: str = Field(min_length=1, max_length=36)

    @field_validator("client_order_id")
    @classmethod
    def _validate_client_order_id(cls, v: str) -> str:
        if not v.isascii() or not v.isprintable():
            raise ValueError("client_order_id must be ASCII printable (spec.md §5)")
        return v

    instrument_id: str
    order_side: Literal["BUY", "SELL"]
    order_type: Literal[
        "MARKET",
        "LIMIT",
        "STOP_MARKET",
        "STOP_LIMIT",
        "MARKET_IF_TOUCHED",
        "LIMIT_IF_TOUCHED",
    ]
    quantity: str
    price: str | None = None
    trigger_price: str | None = None
    trigger_type: str | None = None
    time_in_force: Literal[
        "DAY", "GTC", "GTD", "IOC", "FOK", "AT_THE_OPEN", "AT_THE_CLOSE"
    ]
    expire_time_ns: int | None = None
    post_only: bool
    reduce_only: bool
    tags: list[str] = Field(default_factory=list)
    # xxh3_64 computed by Rust before sending; 0 means unknown (skip WAL restore).
    # H-E: passed through IPC so Python can write it verbatim to the WAL submit row,
    # enabling OrderSessionState::load_from_wal() to restore the idempotency map.
    request_key: int = 0


class OrderModifyChange(IpcMessage):
    """Fields that can be modified on an existing order; None = unchanged."""

    model_config = ConfigDict(extra="forbid")

    new_quantity: str | None = None
    new_price: str | None = None
    new_trigger_price: str | None = None
    new_expire_time_ns: int | None = None


class OrderListFilter(IpcMessage):
    """Filter for GetOrderList. All fields optional.
    extra="forbid" prevents unknown-field injection (C-1).
    """

    model_config = ConfigDict(extra="forbid")

    # M-Type3: 既知の OrderStatus (tachibana_orders._STATUS_TEXT_MAP の出力値) のみ許可。
    # docs: see python/engine/exchanges/tachibana_orders.py `_STATUS_TEXT_MAP`.
    status: Optional[
        Literal[
            "SUBMITTED",
            "ACCEPTED",
            "FILLED",
            "PENDING_CANCEL",
            "CANCELED",
            "EXPIRED",
            "REJECTED",
        ]
    ] = None
    instrument_id: str | None = None
    date: str | None = None


class SubmitOrder(IpcMessage):
    op: Literal["SubmitOrder"] = "SubmitOrder"
    request_id: str
    venue: str
    order: SubmitOrderRequest


class ModifyOrder(IpcMessage):
    op: Literal["ModifyOrder"] = "ModifyOrder"
    request_id: str
    venue: str
    client_order_id: str
    venue_order_id: Optional[str] = None
    change: OrderModifyChange


class CancelOrder(IpcMessage):
    op: Literal["CancelOrder"] = "CancelOrder"
    request_id: str
    venue: str
    client_order_id: str
    venue_order_id: str


class CancelAllOrders(IpcMessage):
    op: Literal["CancelAllOrders"] = "CancelAllOrders"
    request_id: str
    venue: str
    instrument_id: str | None = None
    order_side: str | None = None


class GetOrderList(IpcMessage):
    op: Literal["GetOrderList"] = "GetOrderList"
    request_id: str
    venue: str
    filter: OrderListFilter = Field(default_factory=OrderListFilter)


# ── Buying Power Phase (schema 2.1) ─────────────────────────────────────────


class GetBuyingPower(IpcMessage):
    op: Literal["GetBuyingPower"] = "GetBuyingPower"
    request_id: str
    venue: str


# ── Positions Phase (schema 2.7) ────────────────────────────────────────────

class GetPositions(IpcMessage):
    op: Literal["GetPositions"] = "GetPositions"
    request_id: str
    venue: str


# ---------------------------------------------------------------------------
# Python → Rust (events)
# ---------------------------------------------------------------------------


class Ready(IpcMessage):
    event: Literal["Ready"] = "Ready"
    schema_major: int
    schema_minor: int
    engine_version: str
    engine_session_id: UUID
    capabilities: dict = Field(default_factory=dict)


class EngineError(IpcMessage):
    """接続レベル切断 frame と StartEngine 例外通知 outbox event の **両用 wire 形** を共有する (H-F).

    - ``strategy_id is None`` → 接続レベル/handshake 系のエラー
      (auth_failed / schema_mismatch 等)。Rust 側は接続を切断する。
    - ``strategy_id == <id>`` → 走行中 strategy の outbox event。Rust 側は
      該当 strategy の state machine にだけ反映し、接続自体は維持する。

    解釈分岐の責務は受信側 (Rust ``EngineEvent::EngineError``) にある。
    送出側は意味に応じて strategy_id を必ず正しく付ける／付けないを選ぶこと。
    """

    event: Literal["EngineError"] = "EngineError"
    code: str
    message: str
    strategy_id: str | None = None  # None = 接続レベルエラー

    @field_validator("strategy_id", mode="before")
    @classmethod
    def _normalize_empty_strategy_id(cls, v: Any) -> Any:
        # M-4: 空文字を None に正規化する。送出側が strategy_id="" を渡しても
        # 受信側の解釈が「接続レベル」(None) と一致するようにする。
        if isinstance(v, str) and v == "":
            return None
        return v


class Connected(IpcMessage):
    """Exchange WebSocket connection established (mirrors exchange::Event::Connected)."""

    event: Literal["Connected"] = "Connected"
    venue: str
    ticker: str
    stream: str
    market: str = ""


class Disconnected(IpcMessage):
    """Exchange WebSocket connection lost (mirrors exchange::Event::Disconnected)."""

    event: Literal["Disconnected"] = "Disconnected"
    venue: str
    ticker: str
    stream: str
    market: str = ""
    reason: str | None = None


class TradeMsg(IpcMessage):
    price: str
    qty: str
    side: str
    ts_ms: int
    is_liquidation: bool = False


class Trades(IpcMessage):
    event: Literal["Trades"] = "Trades"
    venue: str
    ticker: str
    market: str
    stream_session_id: str
    trades: list[TradeMsg]


class KlineMsg(IpcMessage):
    open_time_ms: int
    open: str
    high: str
    low: str
    close: str
    volume: str
    is_closed: bool
    quote_volume: str | None = None
    taker_buy_volume: str | None = None
    taker_buy_quote_volume: str | None = None


class KlineUpdate(IpcMessage):
    event: Literal["KlineUpdate"] = "KlineUpdate"
    venue: str
    ticker: str
    market: str
    timeframe: str
    kline: KlineMsg


class OpenInterestPoint(IpcMessage):
    ts_ms: int
    open_interest: str


class Klines(IpcMessage):
    event: Literal["Klines"] = "Klines"
    request_id: str
    venue: str
    ticker: str
    timeframe: str
    klines: list[KlineMsg]


class DepthLevel(IpcMessage):
    price: str
    qty: str


class DepthSnapshotMsg(IpcMessage):
    event: Literal["DepthSnapshot"] = "DepthSnapshot"
    request_id: str | None = None
    venue: str
    ticker: str
    market: str
    stream_session_id: str
    sequence_id: int
    bids: list[DepthLevel]
    asks: list[DepthLevel]
    checksum: int | None = None


class DepthDiffMsg(IpcMessage):
    event: Literal["DepthDiff"] = "DepthDiff"
    venue: str
    ticker: str
    market: str
    stream_session_id: str
    sequence_id: int
    prev_sequence_id: int
    bids: list[DepthLevel]
    asks: list[DepthLevel]


class DepthGap(IpcMessage):
    event: Literal["DepthGap"] = "DepthGap"
    venue: str
    ticker: str
    market: str
    stream_session_id: str


class OpenInterestMsg(IpcMessage):
    event: Literal["OpenInterest"] = "OpenInterest"
    request_id: str
    venue: str
    ticker: str
    data: list[OpenInterestPoint]


class TickerInfoMsg(IpcMessage):
    event: Literal["TickerInfo"] = "TickerInfo"
    request_id: str
    venue: str
    tickers: list[dict]


class TickerStatsMsg(IpcMessage):
    event: Literal["TickerStats"] = "TickerStats"
    request_id: str
    venue: str
    ticker: str
    stats: dict


class TradesFetched(IpcMessage):
    event: Literal["TradesFetched"] = "TradesFetched"
    request_id: str
    venue: str
    ticker: str
    trades: list[TradeMsg]
    is_last: bool


class Error(IpcMessage):
    event: Literal["Error"] = "Error"
    request_id: str | None = None
    code: str
    message: str


# ── Tachibana / venue lifecycle events (Phase 1, T0.2) ─────────────────────


class VenueReady(IpcMessage):
    """Venue-scoped session validation completed (idempotent).

    Tachibana emits this after startup_login succeeds — including after a
    Python restart. The Rust UI must not generate fresh subscriptions on
    receipt; ProcessManager owns the resubscribe pathway (architecture.md §3, F8).
    """

    event: Literal["VenueReady"] = "VenueReady"
    venue: str
    request_id: str | None = None


class VenueError(IpcMessage):
    """Venue-scoped error. ``message`` is user-facing and Python-authored
    (F-Banner1) — the Rust UI displays it verbatim and never composes its
    own banner text.
    """

    event: Literal["VenueError"] = "VenueError"
    venue: str
    request_id: str | None = None
    code: str  # e.g. "session_expired", "unread_notices", "login_failed"
    message: str


class VenueLoginStarted(IpcMessage):
    """Python has spawned the tkinter login helper subprocess."""

    event: Literal["VenueLoginStarted"] = "VenueLoginStarted"
    venue: str
    request_id: str | None = None


class VenueLoginCancelled(IpcMessage):
    event: Literal["VenueLoginCancelled"] = "VenueLoginCancelled"
    venue: str
    request_id: str | None = None


# ── Order Phase events (schema 1.3) ──────────────────────────────────────────


class SecondPasswordRequired(IpcMessage):
    event: Literal["SecondPasswordRequired"] = "SecondPasswordRequired"
    request_id: str


class OrderSubmitted(IpcMessage):
    event: Literal["OrderSubmitted"] = "OrderSubmitted"
    client_order_id: str
    ts_event_ms: int


class OrderAccepted(IpcMessage):
    event: Literal["OrderAccepted"] = "OrderAccepted"
    client_order_id: str
    venue_order_id: str | None = (
        None  # B-1: None when venue did not return an order number
    )
    ts_event_ms: int


class OrderRejected(IpcMessage):
    event: Literal["OrderRejected"] = "OrderRejected"
    client_order_id: str
    reason_code: str
    reason_text: str = ""
    ts_event_ms: int


class OrderPendingUpdate(IpcMessage):
    event: Literal["OrderPendingUpdate"] = "OrderPendingUpdate"
    client_order_id: str
    ts_event_ms: int


class OrderPendingCancel(IpcMessage):
    event: Literal["OrderPendingCancel"] = "OrderPendingCancel"
    client_order_id: str
    ts_event_ms: int


class OrderFilled(IpcMessage):
    """Order filled event. `leaves_qty == "0"` means full fill (nautilus convention)."""

    event: Literal["OrderFilled"] = "OrderFilled"
    client_order_id: str
    venue_order_id: str
    trade_id: str
    last_qty: str
    last_price: str
    cumulative_qty: str
    leaves_qty: str
    ts_event_ms: int


class OrderCanceled(IpcMessage):
    event: Literal["OrderCanceled"] = "OrderCanceled"
    client_order_id: str
    venue_order_id: str
    ts_event_ms: int


class OrderExpired(IpcMessage):
    event: Literal["OrderExpired"] = "OrderExpired"
    client_order_id: str
    venue_order_id: str
    ts_event_ms: int


class OrderRecordWire(IpcMessage):
    """Single order record in OrderListUpdated."""

    model_config = ConfigDict(extra="forbid")

    client_order_id: str | None = None
    venue_order_id: str
    instrument_id: str
    order_side: str
    order_type: str
    quantity: str
    filled_qty: str
    leaves_qty: str
    price: str | None = None
    trigger_price: str | None = None
    time_in_force: str
    expire_time_ns: int | None = None
    status: str
    ts_event_ms: int
    # P-1: venue フィールド追加（dto.rs OrderRecordWire との IPC 契約一致）
    venue: str = "tachibana"


class OrderListUpdated(IpcMessage):
    event: Literal["OrderListUpdated"] = "OrderListUpdated"
    request_id: str
    orders: list[OrderRecordWire] = Field(default_factory=list)


# ── nautilus_trader 統合 (schema 2.4 / N1.1) ────────────────────────────────


class EngineStartConfig(IpcMessage):
    """Engine start config — mirrors `engine_runner.py` arguments."""

    model_config = ConfigDict(extra="forbid")

    instrument_id: str
    instrument_ids: list[str] | None = None
    start_date: str
    end_date: str
    initial_cash: str
    granularity: Literal["Trade", "Minute", "Daily"]
    strategy_file: str | None = None
    strategy_init_kwargs: dict[str, Any] | None = None


class StartEngine(IpcMessage):
    op: Literal["StartEngine"] = "StartEngine"
    request_id: str
    engine: Literal["Backtest", "Live"]
    strategy_id: str
    config: EngineStartConfig


class StopEngine(IpcMessage):
    op: Literal["StopEngine"] = "StopEngine"
    request_id: str
    strategy_id: str


class LoadReplayData(IpcMessage):
    op: Literal["LoadReplayData"] = "LoadReplayData"
    request_id: str
    instrument_id: str
    start_date: str
    end_date: str
    granularity: Literal["Trade", "Minute", "Daily"]
    instrument_ids: list[str] | None = None


class EngineStarted(IpcMessage):
    event: Literal["EngineStarted"] = "EngineStarted"
    strategy_id: str
    account_id: str
    ts_event_ms: int


class EngineStopped(IpcMessage):
    event: Literal["EngineStopped"] = "EngineStopped"
    strategy_id: str
    final_equity: str
    ts_event_ms: int


class ReplayDataLoaded(IpcMessage):
    event: Literal["ReplayDataLoaded"] = "ReplayDataLoaded"
    # M-8 (R1b / schema 2.5): 単独 LoadReplayData は戦略未起動なので
    # strategy_id を None で送る。``start_backtest_replay`` 経路は具体値を入れる。
    strategy_id: str | None = None
    bars_loaded: int
    trades_loaded: int
    # schema 3.12: GUI が helper attach mode で `auto_generate_replay_panes` を
    # 呼べるよう、replay 対象の instrument_id と bar 粒度を同梱する。
    # 旧 GUI (minor<12) は serde(default) で安全に無視される。
    instrument_id: str | None = None
    instrument_ids: list[str] | None = None
    granularity: Literal["Trade", "Minute", "Daily"] | None = None
    # schema 3.14: 新セッション識別子。LoadReplayData 受理ごとに +1。
    # 旧 GUI (minor<14) は Option<u64>::None で deserialise され無視される。
    session_epoch: int | None = None
    ts_event_ms: int


class PositionOpened(IpcMessage):
    event: Literal["PositionOpened"] = "PositionOpened"
    strategy_id: str
    venue: str
    instrument_id: str
    position_id: str
    side: str
    opened_qty: str
    avg_open_price: str
    ts_event_ms: int


class PositionClosed(IpcMessage):
    event: Literal["PositionClosed"] = "PositionClosed"
    strategy_id: str
    venue: str
    instrument_id: str
    position_id: str
    realized_pnl: str
    ts_event_ms: int


# ── N1.11: Replay speed control command ─────────────────────────────────────


class SetReplaySpeed(IpcMessage):
    """Set replay playback speed multiplier (N1.11).

    ``multiplier`` must be >= 1. Sent via ``POST /api/replay/control``
    with ``{"action": "speed", "multiplier": N}``.
    """

    op: Literal["SetReplaySpeed"] = "SetReplaySpeed"
    request_id: str
    multiplier: int


# ── N1.12: Execution marker + strategy signal events ────────────────────────


class ExecutionMarker(IpcMessage):
    """Emitted by NarrativeHook when an OrderFilled event is received (N1.12).

    One ``ExecutionMarker`` is emitted per ``OrderFilled`` (1:1 mapping).
    """

    event: Literal["ExecutionMarker"] = "ExecutionMarker"
    strategy_id: str
    instrument_id: str
    side: str  # "BUY" | "SELL"
    price: str  # decimal string
    qty: str | None = None  # filled quantity as decimal string (added N1.13 Step A+)
    ts_event_ms: int


class StrategySignal(IpcMessage):
    """Explicit signal emitted by a strategy via ``StrategySignalMixin.emit_signal()`` (N1.12).

    Independent of fills — a strategy can emit signals without any order activity.
    ``signal_kind`` vocabulary is provisional (Q13 open): EntryLong / EntryShort / Exit / Annotate.
    """

    event: Literal["StrategySignal"] = "StrategySignal"
    strategy_id: str
    instrument_id: str
    signal_kind: Literal["EntryLong", "EntryShort", "Exit", "Annotate"]
    side: str | None = None  # "BUY" | "SELL" | None
    price: str | None = None  # decimal string or None
    tag: str | None = None  # short machine-readable label
    note: str | None = None  # human-readable annotation
    ts_event_ms: int


# ── N1.16: REPLAY 仮想買付余力 ──────────────────────────────────────────────


class ReplayBuyingPower(IpcMessage):
    """REPLAY モードの仮想買付余力（portfolio_view.py が送出）。
    cash / buying_power / equity はすべて decimal 文字列（float 丸め防止）。
    """

    model_config = ConfigDict(extra="forbid")

    event: Literal["ReplayBuyingPower"] = "ReplayBuyingPower"
    strategy_id: str
    cash: str
    buying_power: str
    equity: str
    ts_event_ms: int


# ── Buying Power Phase (schema 2.1) ─────────────────────────────────────────


class BuyingPowerUpdated(IpcMessage):
    """Response to GetBuyingPower. Contains current cash and credit buying power."""

    model_config = ConfigDict(extra="forbid")

    event: Literal["BuyingPowerUpdated"] = "BuyingPowerUpdated"
    request_id: str
    venue: str
    cash_available: int  # 現物買付余力（円）
    cash_shortfall: int  # 現物余力不足額（円、0 は不足なし）
    credit_available: int  # 信用新規可能額（円）
    ts_ms: int  # 取得時刻 Unix ミリ秒


# ── Positions Phase (schema 2.7) ────────────────────────────────────────────


class PositionRecord(IpcMessage):
    """Single position entry in PositionsUpdated."""

    model_config = ConfigDict(extra="forbid")

    instrument_id: str
    qty: str  # 整数文字列
    market_value: str  # 整数文字列、"0" は ¥0 表示
    position_type: Literal["cash", "margin_credit", "margin_general"]
    tategyoku_id: str | None = None
    venue: Literal["tachibana"]


class PositionsUpdated(IpcMessage):
    """Response to GetPositions. Contains current positions held at the venue."""

    model_config = ConfigDict(extra="forbid")

    event: Literal["PositionsUpdated"] = "PositionsUpdated"
    request_id: str
    venue: str
    positions: list[PositionRecord]
    ts_ms: int


# ── Phase 8.1b: Multi-client connection lifecycle events ────────────────────


class ClientConnected(IpcMessage):
    event: Literal["ClientConnected"] = "ClientConnected"
    count: int


class ClientDisconnected(IpcMessage):
    event: Literal["ClientDisconnected"] = "ClientDisconnected"
    count: int


class EngineBusy(IpcMessage):
    """Engine state machine rejected a command because the current state does not allow it.

    B3: emitted when a command arrives in the wrong state (e.g. LoadReplayData while
    already Loaded, StartEngine while Idle, SubmitOrder while Disconnected).
    """

    event: Literal["EngineBusy"] = "EngineBusy"
    # M-1 (type): replay state / live state を tagged union で表現する。
    # Replay states: IDLE / LOADED / RUNNING / STOPPING
    # Live states:   DISCONNECTED / CONNECTING / CONNECTED
    # H-Type2: state と command の直交性を `model_validator` (mode='after') で
    # 強制する。Replay-only state に Live-only command (例: STOPPING /
    # RequestVenueLogin) を載せると ValidationError。
    current_state: CurrentEngineState
    attempted_command: AttemptedCommand
    reason: str
    # MEDIUM-R2-6: violation を起こした command の request_id。Optional で
    # 既存メッセージとの後方互換を保つ。helper 側の `wait_for()` /
    # `events()` はこの値で「自分宛の reject」と「broadcast / 別 client 由来」を
    # 区別する。
    request_id: str | None = None

    @model_validator(mode="after")
    def _validate_state_command_orthogonal(self) -> "EngineBusy":
        # SubmitOrder は replay/live どちらの state でも有効 (venue により分岐)。
        cmd = self.attempted_command
        state = self.current_state
        if cmd in _REPLAY_ONLY_COMMANDS and state in _LIVE_STATES:
            raise ValueError(
                f"EngineBusy: replay-only command {cmd!r} cannot be paired with "
                f"live state {state!r}"
            )
        if cmd in _LIVE_ONLY_COMMANDS and state in _REPLAY_STATES:
            raise ValueError(
                f"EngineBusy: live-only command {cmd!r} cannot be paired with "
                f"replay state {state!r}"
            )
        return self


# ── F7: モード切替 (schema 3.11) ─────────────────────────────────────────────


class StopReplay(IpcMessage):
    """active な replay セッションを graceful に停止するよう要求する（F7 モード切替）。

    Python は内部で active な strategy_id を解決して StopEngine 相当の処理を行う。
    停止完了後に `ReplayStopped` を emit する。
    replay が実行中でない場合は `EngineBusy` を返す。
    """

    op: Literal["StopReplay"] = "StopReplay"
    request_id: str


class ForceStopReplay(IpcMessage):
    """StopReplay が 5 秒タイムアウトした後の強制停止フォールバック（F7）。

    Python は runner thread を即座に停止する（可能であれば SIGKILL 相当）。
    停止完了後に `ReplayStopped` を emit する。
    """

    op: Literal["ForceStopReplay"] = "ForceStopReplay"
    request_id: str


class ReplayStopped(IpcMessage):
    """StopReplay または ForceStopReplay が replay セッションを停止したときに emit する（F7）。

    final_equity は停止時点の equity 残高（decimal 文字列）。
    セッションが正常完了する前に停止された場合は None。
    """

    event: Literal["ReplayStopped"] = "ReplayStopped"
    request_id: str
    final_equity: str | None = None


# ── F6: SCENARIO 定数 (schema 3.10) ─────────────────────────────────────────


class LoadStrategyScenario(IpcMessage):
    """戦略 .py から SCENARIO 定数を ast.literal_eval で安全抽出するよう Python に要求する。

    replay モードの `開く…（Open）` で .py を選択したときに Rust が送出する。
    Python 側は importlib を使わず ast.parse + ast.literal_eval のみで抽出する（副作用ゼロ）。
    """

    op: Literal["LoadStrategyScenario"] = "LoadStrategyScenario"
    request_id: str
    path: str  # 戦略 .py の絶対パス


class SaveStrategyScenario(IpcMessage):
    """戦略 .py の SCENARIO ブロックを libcst で atomic 書き戻すよう Python に要求する。

    save_as=False: 上書き保存（path == loaded_path が必須）
    save_as=True:  新規/派生保存（任意の .py path）
    """

    op: Literal["SaveStrategyScenario"] = "SaveStrategyScenario"
    request_id: str
    path: str           # 書き戻し先 .py の絶対パス
    scenario: dict      # 書き戻す Scenario dict
    save_as: bool = False
    loaded_path: str | None = None  # 直前 LoadStrategyScenario の path（path guard 用）


class StrategyScenarioLoaded(IpcMessage):
    """LoadStrategyScenario の応答。SCENARIO が見つかった場合は scenario を返す。

    scenario=None は SCENARIO 不在（prefill なし）を示す。
    """

    event: Literal["StrategyScenarioLoaded"] = "StrategyScenarioLoaded"
    request_id: str
    path: str                      # 読み込んだ .py の絶対パス
    scenario: dict | None = None   # SCENARIO dict, または不在時 None


class StrategyScenarioLoadFailed(IpcMessage):
    """LoadStrategyScenario 失敗時の応答（構文エラー / リテラル以外の dict）。"""

    event: Literal["StrategyScenarioLoadFailed"] = "StrategyScenarioLoadFailed"
    request_id: str
    path: str
    reason: str  # エラー文言


class StrategyScenarioSaved(IpcMessage):
    """SaveStrategyScenario の応答。"""

    event: Literal["StrategyScenarioSaved"] = "StrategyScenarioSaved"
    request_id: str
    path: str
    ok: bool
    # H1 (レビュー反映 2026-05-04 ラウンド1): `error` は SaveErrorCode Literal の
    # 9 値に固定。未知値は pydantic validation で reject される。
    error: SaveErrorCode | None = None


# ---------------------------------------------------------------------------
# Tachibana REQUEST response models (T4 / B1)
# ---------------------------------------------------------------------------
#
# These wrap the body of CLMMfdsGetMarketPrice / CLMMfdsGetMarketPriceHistory
# responses, normalizing the empty-list-as-empty-string convention (R8) via
# `deserialize_tachibana_list`. Deferred from T1 §MEDIUM-C2-1 — see
# implementation-plan.md §T4.


class MarketPriceResponse(IpcMessage):
    """Response body of ``CLMMfdsGetMarketPrice``.

    The actual API response key is ``aCLMMfdsMarketPrice`` (confirmed from
    e_api_get_price_from_file_tel.py L936 and the manual response example).
    """

    sCLMID: str = ""
    sResultCode: str = ""
    aCLMMfdsMarketPrice: list[dict] = Field(default_factory=list)

    @field_validator("aCLMMfdsMarketPrice", mode="before")
    @classmethod
    def _normalize_price_data(cls, v: Any) -> list:
        return deserialize_tachibana_list(v)


class MarketPriceHistoryResponse(IpcMessage):
    """Response body of ``CLMMfdsGetMarketPriceHistory``.

    The actual API response key is ``aCLMMfdsMarketPriceHistory`` (confirmed from
    e_api_get_histrical_price_daily_tel.py L843 and the manual response example).
    """

    sCLMID: str = ""
    sResultCode: str = ""
    aCLMMfdsMarketPriceHistory: list[dict] = Field(default_factory=list)

    @field_validator("aCLMMfdsMarketPriceHistory", mode="before")
    @classmethod
    def _normalize_history_data(cls, v: Any) -> list:
        return deserialize_tachibana_list(v)
