"""IPC message schemas — pydantic models matching docs/plan/schemas/*.json"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_MAJOR: int = 1
SCHEMA_MINOR: int = 2


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
    """Emitted after a startup-time re-login produces new virtual URLs."""

    event: Literal["VenueCredentialsRefreshed"] = "VenueCredentialsRefreshed"
    venue: str
    session: TachibanaSessionWire


class VenueLoginStarted(IpcMessage):
    """Python has spawned the tkinter login helper subprocess."""

    event: Literal["VenueLoginStarted"] = "VenueLoginStarted"
    venue: str
    request_id: str | None = None


class VenueLoginCancelled(IpcMessage):
    event: Literal["VenueLoginCancelled"] = "VenueLoginCancelled"
    venue: str
    request_id: str | None = None
