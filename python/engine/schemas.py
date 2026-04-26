"""IPC message schemas — pydantic models matching docs/plan/schemas/*.json"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from engine.exchanges.tachibana_codec import deserialize_tachibana_list

SCHEMA_MAJOR: int = 1
SCHEMA_MINOR: int = 3


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


# ── Tachibana 立花証券 venue credentials (Phase 1, T0.2) ────────────────────


class TachibanaSessionWire(IpcMessage):
    """5 virtual URLs returned by ``CLMAuthLoginRequest`` plus session metadata.

    All URL fields cross the IPC boundary as plain strings; the Python side
    keeps them in memory only (no disk) and the Rust side wraps them in
    ``SecretString`` before storage. ``__repr__`` does not need extra
    masking — pydantic does not include field values in ``BaseModel`` repr
    when they are nested ``SecretStr``, but here we use ``str`` so callers
    must avoid logging the raw model.
    """

    url_request: str
    url_master: str
    url_price: str
    url_event: str
    url_event_ws: str
    expires_at_ms: int | None = None
    zyoutoeki_kazei_c: str = ""


class TachibanaCredentialsWire(IpcMessage):
    user_id: str
    password: str
    second_password: str | None = None
    is_demo: bool = True
    session: TachibanaSessionWire | None = None


class VenueCredentialsPayload(IpcMessage):
    """Tagged union — today only the ``tachibana`` variant exists."""

    venue: Literal["tachibana"]
    user_id: str
    password: str
    second_password: str | None = None
    is_demo: bool = True
    session: TachibanaSessionWire | None = None


class SetVenueCredentials(IpcMessage):
    op: Literal["SetVenueCredentials"] = "SetVenueCredentials"
    request_id: str
    payload: VenueCredentialsPayload


class RequestVenueLogin(IpcMessage):
    op: Literal["RequestVenueLogin"] = "RequestVenueLogin"
    request_id: str
    venue: str


# ── Order Phase commands (schema 1.3) ────────────────────────────────────────


class SetSecondPassword(IpcMessage):
    """Set second password in Python memory for order submission.
    `value` is transmitted as plain string; Python must wrap it in SecretStr.
    """

    op: Literal["SetSecondPassword"] = "SetSecondPassword"
    request_id: str
    value: str


class ForgetSecondPassword(IpcMessage):
    """Clear the second password from Python memory."""

    op: Literal["ForgetSecondPassword"] = "ForgetSecondPassword"


class SubmitOrderRequest(IpcMessage):
    """Order placement request — shape matches the nautilus OrderFactory input.
    extra='forbid' prevents second_password / p_no injection (C-R2-M3 / D3-1).
    """

    model_config = ConfigDict(extra="forbid")

    client_order_id: str
    instrument_id: str
    order_side: str
    order_type: str
    quantity: str
    price: str | None = None
    trigger_price: str | None = None
    trigger_type: str | None = None
    time_in_force: str
    expire_time_ns: int | None = None
    post_only: bool
    reduce_only: bool
    tags: list[str] = Field(default_factory=list)


class OrderModifyChange(IpcMessage):
    """Fields that can be modified on an existing order; None = unchanged."""

    new_quantity: str | None = None
    new_price: str | None = None
    new_trigger_price: str | None = None
    new_expire_time_ns: int | None = None


class OrderListFilter(IpcMessage):
    """Filter for GetOrderList. All fields optional."""

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

    Tachibana emits this every time ``SetVenueCredentials`` succeeds —
    including after a Python restart. The Rust UI must not generate
    fresh subscriptions on receipt; ProcessManager owns the resubscribe
    pathway (architecture.md §3, F8).
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


class VenueCredentialsRefreshed(IpcMessage):
    """Emitted after a startup-time re-login produces new virtual URLs.

    Carries the *full* credential set used for the login (not only the
    derived session URLs) so the Rust side can persist user_id / password /
    is_demo into the keyring. Without these fields the keyring's account /
    demo-flag / password drift away from the value the user just typed
    into the dialog (e.g. account switch, demo→prod toggle, password
    change), and the next cold-start fallback login uses stale data.

    `user_id` / `password` / `is_demo` are optional only so older Rust
    clients (schema 1.2 baseline) can still deserialize the event; new
    Python emitters always populate them.
    """

    event: Literal["VenueCredentialsRefreshed"] = "VenueCredentialsRefreshed"
    venue: str
    session: TachibanaSessionWire
    user_id: str | None = None
    password: str | None = None
    is_demo: bool | None = None


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
    venue_order_id: str
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


# ---------------------------------------------------------------------------
# Tachibana REQUEST response models (T4 / B1)
# ---------------------------------------------------------------------------
#
# These wrap the body of CLMMfdsGetMarketPrice / CLMMfdsGetMarketPriceHistory
# responses, normalizing the empty-list-as-empty-string convention (R8) via
# `deserialize_tachibana_list`. Deferred from T1 §MEDIUM-C2-1 — see
# implementation-plan.md §T4.


class MarketPriceResponse(IpcMessage):
    """Response body of ``CLMMfdsGetMarketPrice``."""

    sCLMID: str = ""
    sResultCode: str = ""
    aCLMMfdsMarketPriceData: list[dict] = Field(default_factory=list)

    @field_validator("aCLMMfdsMarketPriceData", mode="before")
    @classmethod
    def _normalize_price_data(cls, v: Any) -> list:
        return deserialize_tachibana_list(v)


class MarketPriceHistoryResponse(IpcMessage):
    """Response body of ``CLMMfdsGetMarketPriceHistory``."""

    sCLMID: str = ""
    sResultCode: str = ""
    aCLMMfdsMarketPriceHistoryData: list[dict] = Field(default_factory=list)

    @field_validator("aCLMMfdsMarketPriceHistoryData", mode="before")
    @classmethod
    def _normalize_history_data(cls, v: Any) -> list:
        return deserialize_tachibana_list(v)
