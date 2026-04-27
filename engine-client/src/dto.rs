/// IPC Data Transfer Objects for the Rust ↔ Python data engine protocol.
///
/// Commands flow Rust → Python; Events flow Python → Rust.
/// Both are transported as JSON text frames over a local WebSocket.
use serde::{Deserialize, Serialize};
use zeroize::Zeroizing;

// HIGH-8 (ラウンド 6 強制修正 / Group F): the venue wire DTOs now live
// in `data::wire::tachibana`. We re-export them here so existing
// consumers (`engine_client::dto::TachibanaCredentialsWire` etc) see no
// API change. The dependency edge is `engine-client → data`; the
// previous reverse edge was eliminated.
pub use ::data::wire::tachibana::{TachibanaCredentialsWire, TachibanaSessionWire};

// ── Commands (Rust → Python) ──────────────────────────────────────────────────

// NOTE: Debug is intentionally hand-implemented below to mask SetSecondPassword.value.
#[derive(Serialize)]
#[serde(tag = "op")]
pub enum Command {
    Hello {
        schema_major: u16,
        schema_minor: u16,
        client_version: String,
        token: String,
    },
    SetProxy {
        url: Option<String>,
    },
    Subscribe {
        venue: String,
        ticker: String,
        stream: String,
        timeframe: Option<String>,
        market: String,
    },
    Unsubscribe {
        venue: String,
        ticker: String,
        stream: String,
        timeframe: Option<String>,
        market: String,
    },
    FetchKlines {
        request_id: String,
        venue: String,
        ticker: String,
        timeframe: String,
        limit: u32,
        start_ms: Option<i64>,
        end_ms: Option<i64>,
        market: String,
    },
    FetchTrades {
        request_id: String,
        venue: String,
        ticker: String,
        market: String,
        start_ms: i64,
        end_ms: i64,
        data_path: Option<String>,
    },
    FetchOpenInterest {
        request_id: String,
        venue: String,
        ticker: String,
        timeframe: String,
        limit: u32,
        start_ms: Option<i64>,
        end_ms: Option<i64>,
        market: String,
    },
    FetchTickerStats {
        request_id: String,
        venue: String,
        ticker: String,
        market: String,
    },
    ListTickers {
        request_id: String,
        venue: String,
        market: String,
    },
    GetTickerMetadata {
        request_id: String,
        venue: String,
        ticker: String,
    },
    RequestDepthSnapshot {
        request_id: String,
        venue: String,
        ticker: String,
        market: String,
    },
    Ping {
        request_id: String,
    },
    Shutdown,
    /// Inject venue-scoped credentials into the Python engine. Used at
    /// startup (keyring restore) and after a managed-mode restart.
    /// `payload` is a tagged enum so each venue can carry its own typed
    /// secret material; `serde_json::Value` is intentionally avoided so
    /// the `Debug` impl on the wire DTO can mask sensitive fields.
    SetVenueCredentials {
        request_id: String,
        payload: VenueCredentialsPayload,
    },
    /// Rust UI asks the engine to drive the venue's login UI — currently
    /// only Tachibana, which spawns a tkinter helper subprocess.
    RequestVenueLogin {
        request_id: String,
        venue: String,
    },

    // ── Order Phase (schema 1.3) ──────────────────────────────────────────
    /// Set the second password in Python memory for order submission.
    /// The `value` field carries the raw secret over IPC (plain String because
    /// SecretString cannot cross the JSON boundary); Python must immediately
    /// wrap it in `SecretStr`. Debug output masks `value` as `[REDACTED]`.
    SetSecondPassword {
        request_id: String,
        value: String,
    },
    /// Clear the second password from Python memory (idle forget / explicit logout).
    ForgetSecondPassword,

    /// Submit a new order. `order` shape matches the nautilus OrderFactory input.
    SubmitOrder {
        request_id: String,
        venue: String,
        order: SubmitOrderRequest,
    },
    /// Modify an existing order (price / qty / trigger / expire).
    ModifyOrder {
        request_id: String,
        venue: String,
        client_order_id: String,
        change: OrderModifyChange,
    },
    /// Cancel a specific order. Rust looks up `venue_order_id` via
    /// `OrderSessionState` before sending — Python receives both IDs.
    CancelOrder {
        request_id: String,
        venue: String,
        client_order_id: String,
        venue_order_id: String,
    },
    /// Cancel all open orders, optionally filtered by instrument and side.
    CancelAllOrders {
        request_id: String,
        venue: String,
        instrument_id: Option<String>,
        order_side: Option<OrderSide>,
    },
    /// Fetch today's order list from the venue.
    GetOrderList {
        request_id: String,
        venue: String,
        filter: OrderListFilter,
    },
}

/// Hand-rolled `Debug` for `Command` that masks `SetSecondPassword.value`
/// as `[REDACTED]`. All other variants delegate to their field `Debug` impls.
impl std::fmt::Debug for Command {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Command::SetSecondPassword { request_id, .. } => f
                .debug_struct("SetSecondPassword")
                .field("request_id", request_id)
                .field("value", &"[REDACTED]")
                .finish(),
            Command::Hello {
                schema_major,
                schema_minor,
                client_version,
                token: _,
            } => f
                .debug_struct("Hello")
                .field("schema_major", schema_major)
                .field("schema_minor", schema_minor)
                .field("client_version", client_version)
                .field("token", &"***")
                .finish(),
            Command::SetProxy { url } => f.debug_struct("SetProxy").field("url", url).finish(),
            Command::Subscribe {
                venue,
                ticker,
                stream,
                timeframe,
                market,
            } => f
                .debug_struct("Subscribe")
                .field("venue", venue)
                .field("ticker", ticker)
                .field("stream", stream)
                .field("timeframe", timeframe)
                .field("market", market)
                .finish(),
            Command::Unsubscribe {
                venue,
                ticker,
                stream,
                timeframe,
                market,
            } => f
                .debug_struct("Unsubscribe")
                .field("venue", venue)
                .field("ticker", ticker)
                .field("stream", stream)
                .field("timeframe", timeframe)
                .field("market", market)
                .finish(),
            Command::FetchKlines {
                request_id,
                venue,
                ticker,
                timeframe,
                limit,
                start_ms,
                end_ms,
                market,
            } => f
                .debug_struct("FetchKlines")
                .field("request_id", request_id)
                .field("venue", venue)
                .field("ticker", ticker)
                .field("timeframe", timeframe)
                .field("limit", limit)
                .field("start_ms", start_ms)
                .field("end_ms", end_ms)
                .field("market", market)
                .finish(),
            Command::FetchTrades {
                request_id,
                venue,
                ticker,
                market,
                start_ms,
                end_ms,
                data_path,
            } => f
                .debug_struct("FetchTrades")
                .field("request_id", request_id)
                .field("venue", venue)
                .field("ticker", ticker)
                .field("market", market)
                .field("start_ms", start_ms)
                .field("end_ms", end_ms)
                .field("data_path", data_path)
                .finish(),
            Command::FetchOpenInterest {
                request_id,
                venue,
                ticker,
                timeframe,
                limit,
                start_ms,
                end_ms,
                market,
            } => f
                .debug_struct("FetchOpenInterest")
                .field("request_id", request_id)
                .field("venue", venue)
                .field("ticker", ticker)
                .field("timeframe", timeframe)
                .field("limit", limit)
                .field("start_ms", start_ms)
                .field("end_ms", end_ms)
                .field("market", market)
                .finish(),
            Command::FetchTickerStats {
                request_id,
                venue,
                ticker,
                market,
            } => f
                .debug_struct("FetchTickerStats")
                .field("request_id", request_id)
                .field("venue", venue)
                .field("ticker", ticker)
                .field("market", market)
                .finish(),
            Command::ListTickers {
                request_id,
                venue,
                market,
            } => f
                .debug_struct("ListTickers")
                .field("request_id", request_id)
                .field("venue", venue)
                .field("market", market)
                .finish(),
            Command::GetTickerMetadata {
                request_id,
                venue,
                ticker,
            } => f
                .debug_struct("GetTickerMetadata")
                .field("request_id", request_id)
                .field("venue", venue)
                .field("ticker", ticker)
                .finish(),
            Command::RequestDepthSnapshot {
                request_id,
                venue,
                ticker,
                market,
            } => f
                .debug_struct("RequestDepthSnapshot")
                .field("request_id", request_id)
                .field("venue", venue)
                .field("ticker", ticker)
                .field("market", market)
                .finish(),
            Command::Ping { request_id } => f
                .debug_struct("Ping")
                .field("request_id", request_id)
                .finish(),
            Command::Shutdown => write!(f, "Shutdown"),
            Command::SetVenueCredentials {
                request_id,
                payload,
            } => f
                .debug_struct("SetVenueCredentials")
                .field("request_id", request_id)
                .field("payload", payload)
                .finish(),
            Command::RequestVenueLogin { request_id, venue } => f
                .debug_struct("RequestVenueLogin")
                .field("request_id", request_id)
                .field("venue", venue)
                .finish(),
            Command::ForgetSecondPassword => write!(f, "ForgetSecondPassword"),
            Command::SubmitOrder {
                request_id,
                venue,
                order,
            } => f
                .debug_struct("SubmitOrder")
                .field("request_id", request_id)
                .field("venue", venue)
                .field("order", order)
                .finish(),
            Command::ModifyOrder {
                request_id,
                venue,
                client_order_id,
                change,
            } => f
                .debug_struct("ModifyOrder")
                .field("request_id", request_id)
                .field("venue", venue)
                .field("client_order_id", client_order_id)
                .field("change", change)
                .finish(),
            Command::CancelOrder {
                request_id,
                venue,
                client_order_id,
                venue_order_id,
            } => f
                .debug_struct("CancelOrder")
                .field("request_id", request_id)
                .field("venue", venue)
                .field("client_order_id", client_order_id)
                .field("venue_order_id", venue_order_id)
                .finish(),
            Command::CancelAllOrders {
                request_id,
                venue,
                instrument_id,
                order_side,
            } => f
                .debug_struct("CancelAllOrders")
                .field("request_id", request_id)
                .field("venue", venue)
                .field("instrument_id", instrument_id)
                .field("order_side", order_side)
                .finish(),
            Command::GetOrderList {
                request_id,
                venue,
                filter,
            } => f
                .debug_struct("GetOrderList")
                .field("request_id", request_id)
                .field("venue", venue)
                .field("filter", filter)
                .finish(),
        }
    }
}

// ── Order sub-types (schema 1.3) ──────────────────────────────────────────────

/// Order placement request — shape matches the nautilus OrderFactory input.
/// `deny_unknown_fields` prevents second_password / p_no injection via IPC (C-R2-M3 / D3-1).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SubmitOrderRequest {
    pub client_order_id: String,
    pub instrument_id: String,
    pub order_side: OrderSide,
    pub order_type: OrderType,
    pub quantity: String,
    pub price: Option<String>,
    pub trigger_price: Option<String>,
    pub trigger_type: Option<TriggerType>,
    pub time_in_force: TimeInForce,
    pub expire_time_ns: Option<i64>,
    pub post_only: bool,
    pub reduce_only: bool,
    pub tags: Vec<String>,
    /// xxh3_64 hash of the canonical order request, computed by Rust before sending.
    /// Python uses this value verbatim when writing the WAL submit row so that
    /// `OrderSessionState::load_from_wal()` can restore the idempotency map on
    /// restart.  A value of `0` means "unknown / not computed" and causes the
    /// WAL entry to be skipped during restore (H-E / architecture.md §4.1).
    #[serde(default)]
    pub request_key: u64,
}

/// Fields that can be modified on an existing order; `None` = unchanged.
/// `deny_unknown_fields` prevents second_password injection (C-R2-M3 / D3-1).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OrderModifyChange {
    pub new_quantity: Option<String>,
    pub new_price: Option<String>,
    pub new_trigger_price: Option<String>,
    pub new_expire_time_ns: Option<i64>,
}

/// Filter for `GetOrderList`. All fields are optional.
/// `deny_unknown_fields` prevents injection of unexpected fields via IPC (M-10).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OrderListFilter {
    pub status: Option<String>,
    pub instrument_id: Option<String>,
    pub date: Option<String>,
}

/// Wire representation of a single order record in `OrderListUpdated`.
/// `deny_unknown_fields` prevents unknown Python-side fields from silently
/// passing through to Rust (C-2).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OrderRecordWire {
    pub client_order_id: Option<String>,
    pub venue_order_id: String,
    pub instrument_id: String,
    pub order_side: OrderSide,
    pub order_type: OrderType,
    pub quantity: String,
    pub filled_qty: String,
    pub leaves_qty: String,
    pub price: Option<String>,
    pub trigger_price: Option<String>,
    pub time_in_force: TimeInForce,
    pub expire_time_ns: Option<i64>,
    pub status: String,
    pub ts_event_ms: i64,
}

// ── Order enums (nautilus string representations, SCREAMING_SNAKE_CASE) ────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum OrderSide {
    Buy,
    Sell,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum OrderType {
    Market,
    Limit,
    StopMarket,
    StopLimit,
    MarketIfTouched,
    LimitIfTouched,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum TimeInForce {
    Day,
    Gtc,
    Gtd,
    Ioc,
    Fok,
    AtTheOpen,
    AtTheClose,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum TriggerType {
    Last,
    BidAsk,
    Index,
}

// ── Venue credential payload (Rust → Python) ──────────────────────────────────

/// Tagged enum. Today only `Tachibana` is defined; future venues add
/// variants here.
#[derive(Clone, Serialize)]
#[serde(tag = "venue", rename_all = "snake_case")]
pub enum VenueCredentialsPayload {
    Tachibana(TachibanaCredentialsWire),
}

impl VenueCredentialsPayload {
    /// Stable venue identifier used for dedup in the credential store
    /// (M2 / HIGH-B2-2). Adding a new variant here forces a new tag,
    /// preventing the old variant-list-based retain logic from silently
    /// going wrong when a second venue is added.
    pub fn venue_tag(&self) -> &'static str {
        match self {
            VenueCredentialsPayload::Tachibana(_) => "tachibana",
        }
    }
}

impl std::fmt::Debug for VenueCredentialsPayload {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            VenueCredentialsPayload::Tachibana(_) => f
                .debug_struct("VenueCredentialsPayload::Tachibana")
                .field("creds", &"***")
                .finish(),
        }
    }
}

// ── Events (Python → Rust) ────────────────────────────────────────────────────

#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "event")]
pub enum EngineEvent {
    Ready {
        schema_major: u16,
        schema_minor: u16,
        engine_version: String,
        engine_session_id: String,
        // `#[serde(default)]` is a defensive read for older engines that may
        // omit the field. The current Python `schemas.py` always emits a
        // `dict` (via `Field(default_factory=dict)`), so `Value::Null` is not
        // expected from production engines — `{}` is the empty case.
        #[serde(default)]
        capabilities: serde_json::Value,
    },
    EngineError {
        code: String,
        message: String,
    },
    Connected {
        venue: String,
        ticker: String,
        stream: String,
        #[serde(default)]
        market: String,
    },
    Disconnected {
        venue: String,
        ticker: String,
        stream: String,
        #[serde(default)]
        market: String,
        reason: Option<String>,
    },
    Trades {
        venue: String,
        ticker: String,
        #[serde(default)]
        market: String,
        stream_session_id: String,
        trades: Vec<TradeMsg>,
    },
    TradesFetched {
        request_id: String,
        venue: String,
        ticker: String,
        trades: Vec<TradeMsg>,
        /// `false` when more chunks follow; `true` on the final (or only) chunk.
        /// Absent in legacy responses — treated as `true` for backward compat.
        #[serde(default = "default_true")]
        is_last: bool,
    },
    KlineUpdate {
        venue: String,
        ticker: String,
        #[serde(default)]
        market: String,
        timeframe: String,
        kline: KlineMsg,
    },
    Klines {
        request_id: String,
        venue: String,
        ticker: String,
        timeframe: String,
        klines: Vec<KlineMsg>,
    },
    DepthSnapshot {
        #[serde(default)]
        request_id: Option<String>,
        venue: String,
        ticker: String,
        #[serde(default)]
        market: String,
        stream_session_id: String,
        sequence_id: i64,
        bids: Vec<DepthLevel>,
        asks: Vec<DepthLevel>,
        checksum: Option<i64>,
    },
    DepthDiff {
        venue: String,
        ticker: String,
        #[serde(default)]
        market: String,
        stream_session_id: String,
        sequence_id: i64,
        prev_sequence_id: i64,
        bids: Vec<DepthLevel>,
        asks: Vec<DepthLevel>,
    },
    DepthGap {
        venue: String,
        ticker: String,
        #[serde(default)]
        market: String,
        stream_session_id: String,
    },
    OpenInterest {
        request_id: String,
        venue: String,
        ticker: String,
        data: Vec<OiPoint>,
    },
    TickerInfo {
        request_id: String,
        venue: String,
        tickers: Vec<serde_json::Value>,
    },
    TickerStats {
        request_id: String,
        venue: String,
        ticker: String,
        stats: serde_json::Value,
    },
    Pong {
        request_id: String,
    },
    Error {
        request_id: Option<String>,
        code: String,
        message: String,
    },
    // ── Venue lifecycle events ────────────────────────────────────────────
    //
    // `VenueReady` is **idempotent**: Python re-emits it after every restart
    // / `SetVenueCredentials`, and consumers must not generate side-effects
    // that depend on it being a one-shot. `VenueError` carries a Python-
    // authored `message` string — the Rust UI displays it verbatim and never
    // composes its own banner text (see architecture.md §6, F-Banner1).
    VenueReady {
        venue: String,
        request_id: Option<String>,
    },
    VenueError {
        venue: String,
        request_id: Option<String>,
        code: String,
        message: String,
    },
    VenueCredentialsRefreshed {
        venue: String,
        session: TachibanaSessionWire,
        /// Account identifier the user actually authenticated with. Optional
        /// only for back-compat with schema 1.2 emitters that did not
        /// include it; current Python populates it on every refresh.
        #[serde(default)]
        user_id: Option<String>,
        /// Plain-text password held in `Zeroizing<String>` so the heap buffer
        /// is wiped when the event is dropped.
        #[serde(default)]
        password: Option<Zeroizing<String>>,
        /// `true` when the login hit the demo host. Mirrors the value the
        /// dialog (or env / fallback path) used.
        #[serde(default)]
        is_demo: Option<bool>,
    },
    /// Python has spawned its tkinter login helper subprocess. The UI
    /// shows a generic "login dialog open" banner — it does NOT render
    /// the form itself.
    VenueLoginStarted {
        venue: String,
        request_id: Option<String>,
    },
    VenueLoginCancelled {
        venue: String,
        request_id: Option<String>,
    },
    /// Synthetic event emitted by the Rust read loop when the WS connection drops.
    /// Never sent by Python — used to unblock in-flight fetch waiters immediately.
    #[serde(skip_deserializing)]
    ConnectionDropped,

    // ── Order Phase events (schema 1.3) ───────────────────────────────────
    /// Python needs the second password before processing a SubmitOrder request.
    SecondPasswordRequired {
        request_id: String,
    },

    /// Order has been forwarded to the venue (before HTTP response).
    OrderSubmitted {
        client_order_id: String,
        ts_event_ms: i64,
    },

    /// Venue accepted the order and assigned a `venue_order_id`.
    /// `venue_order_id` is `None` when the venue has not yet assigned an ID
    /// (e.g. async acceptance paths). Python sends `null` in that case.
    OrderAccepted {
        client_order_id: String,
        #[serde(default)]
        venue_order_id: Option<String>,
        ts_event_ms: i64,
    },

    /// Order was rejected (before acceptance or after a modify/cancel attempt).
    OrderRejected {
        client_order_id: String,
        reason_code: String,
        reason_text: String,
        ts_event_ms: i64,
    },

    /// Modify request forwarded to venue; awaiting confirmation.
    OrderPendingUpdate {
        client_order_id: String,
        ts_event_ms: i64,
    },

    /// Cancel request forwarded to venue; awaiting confirmation.
    OrderPendingCancel {
        client_order_id: String,
        ts_event_ms: i64,
    },

    /// Order was fully or partially filled.
    /// `leaves_qty == "0"` means full fill (nautilus convention).
    OrderFilled {
        client_order_id: String,
        venue_order_id: String,
        trade_id: String,
        last_qty: String,
        last_price: String,
        cumulative_qty: String,
        leaves_qty: String,
        ts_event_ms: i64,
    },

    /// Order was canceled.
    OrderCanceled {
        client_order_id: String,
        venue_order_id: String,
        ts_event_ms: i64,
    },

    /// Order expired (GTD / AT_THE_CLOSE past closing time).
    OrderExpired {
        client_order_id: String,
        venue_order_id: String,
        ts_event_ms: i64,
    },

    /// Response to `GetOrderList`.
    OrderListUpdated {
        request_id: String,
        orders: Vec<OrderRecordWire>,
    },
}

fn default_true() -> bool {
    true
}

// ── Message sub-types ─────────────────────────────────────────────────────────

#[derive(Debug, Clone, Deserialize)]
pub struct TradeMsg {
    pub price: String,
    pub qty: String,
    pub side: String,
    pub ts_ms: i64,
    #[serde(default)]
    pub is_liquidation: bool,
}

#[derive(Debug, Clone, Deserialize)]
pub struct KlineMsg {
    pub open_time_ms: i64,
    pub open: String,
    pub high: String,
    pub low: String,
    pub close: String,
    pub volume: String,
    pub is_closed: bool,
    /// Taker-buy base-asset volume. Used together with `volume` for buy/sell split.
    #[serde(default)]
    pub taker_buy_volume: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct DepthLevel {
    pub price: String,
    pub qty: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct OiPoint {
    pub ts_ms: i64,
    pub open_interest: String,
}
