"""IPC message schemas — pydantic models matching docs/plan/schemas/*.json"""

from __future__ import annotations

from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from engine.exchanges.tachibana_codec import deserialize_tachibana_list

SCHEMA_MAJOR: int = 2
SCHEMA_MINOR: int = 4


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
    mode: Literal["live", "replay"] = "live"


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
    order_type: Literal["MARKET", "LIMIT", "STOP_MARKET", "STOP_LIMIT", "MARKET_IF_TOUCHED", "LIMIT_IF_TOUCHED"]
    quantity: str
    price: str | None = None
    trigger_price: str | None = None
    trigger_type: str | None = None
    time_in_force: Literal["DAY", "GTC", "GTD", "IOC", "FOK", "AT_THE_OPEN", "AT_THE_CLOSE"]
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

    status: str | None = None
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
    event: Literal["EngineError"] = "EngineError"
    code: str
    message: str
    strategy_id: str | None = None  # None = 接続レベルエラー


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
    venue_order_id: str | None = None  # B-1: None when venue did not return an order number
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


class OrderListUpdated(IpcMessage):
    event: Literal["OrderListUpdated"] = "OrderListUpdated"
    request_id: str
    orders: list[OrderRecordWire] = Field(default_factory=list)


# ── nautilus_trader 統合 (schema 2.4 / N1.1) ────────────────────────────────


class EngineStartConfig(IpcMessage):
    """Engine start config — mirrors `engine_runner.py` arguments."""

    model_config = ConfigDict(extra="forbid")

    instrument_id: str
    start_date: str
    end_date: str
    initial_cash: str
    granularity: Literal["Trade", "Minute", "Daily"]


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
    strategy_id: str
    bars_loaded: int
    trades_loaded: int
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


# ── Buying Power Phase (schema 2.1) ─────────────────────────────────────────


class BuyingPowerUpdated(IpcMessage):
    """Response to GetBuyingPower. Contains current cash and credit buying power."""

    model_config = ConfigDict(extra="forbid")

    event: Literal["BuyingPowerUpdated"] = "BuyingPowerUpdated"
    request_id: str
    venue: str
    cash_available: int   # 現物買付余力（円）
    cash_shortfall: int   # 現物余力不足額（円、0 は不足なし）
    credit_available: int  # 信用新規可能額（円）
    ts_ms: int             # 取得時刻 Unix ミリ秒


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
