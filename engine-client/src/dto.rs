/// IPC Data Transfer Objects for the Rust ↔ Python data engine protocol.
///
/// Commands flow Rust → Python; Events flow Python → Rust.
/// Both are transported as JSON text frames over a local WebSocket.
use serde::{Deserialize, Serialize};

/// N1.13 / R1b H-E: 起動時固定モード。CLI `--mode {live|replay}` で指定する。
///
/// 旧コードは `String` (`"live"` | `"replay"`) で保持していたが、型不一致と
/// typo を防ぐために enum に格上げ。wire 表現 (Hello.mode の JSON 値) は
/// `serde(rename_all = "lowercase")` により従来通り小文字文字列。
///
/// `Default = Live` は handshake 時の旧クライアント互換 (mode 欠落時の
/// fallback) を保つために存在する。Python 側 `Hello.mode: Literal[...] = "live"`
/// と同じ意味。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum AppMode {
    #[default]
    Live,
    Replay,
}

impl AppMode {
    /// Wire-form string (`"live"` | `"replay"`). Convenient for log lines and
    /// HTTP error messages. Always lowercase to match the JSON serialisation.
    pub fn as_wire_str(self) -> &'static str {
        match self {
            AppMode::Live => "live",
            AppMode::Replay => "replay",
        }
    }

    /// Parse the wire-form string. Returns `Err` for any value other than
    /// `"live"` / `"replay"`. Use this at parse boundaries (CLI / HTTP query)
    /// where typos must be caught early.
    pub fn from_wire_str(s: &str) -> Result<Self, &'static str> {
        match s {
            "live" => Ok(AppMode::Live),
            "replay" => Ok(AppMode::Replay),
            _ => Err("unknown app mode (expected \"live\" or \"replay\")"),
        }
    }
}

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
        /// N1.13 / R1b H-E: 起動時に固定する mode (`AppMode::Live` | `AppMode::Replay`).
        /// Python 側で StartEngine.engine との整合チェックに使う。
        /// wire 表現は serde rename_all = "lowercase" で `"live"` / `"replay"` の
        /// 小文字文字列 (Python `Hello.mode: Literal["live","replay"]` と互換)。
        /// `#[serde(default)]` で旧 client (mode 欠落) から `Live` にフォールバック。
        #[serde(default)]
        mode: AppMode,
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
        /// Present when the caller knows the venue order ID directly (other-terminal orders).
        #[serde(default, skip_serializing_if = "Option::is_none")]
        venue_order_id: Option<String>,
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

    // ── Buying Power Phase (schema 2.1) ───────────────────────────────────
    /// Fetch current buying power (cash + credit) from the venue.
    GetBuyingPower {
        request_id: String,
        venue: String,
    },

    // ── nautilus_trader 統合 (schema 2.4 / N1.1) ──────────────────────────
    /// Start a nautilus engine (Backtest or Live) for the given strategy.
    StartEngine {
        request_id: String,
        engine: EngineKind,
        strategy_id: String,
        config: EngineStartConfig,
    },
    /// Stop the running engine for the given strategy.
    StopEngine {
        request_id: String,
        strategy_id: String,
    },
    /// Load replay data (J-Quants) into the BacktestEngine.
    LoadReplayData {
        request_id: String,
        instrument_id: String,
        start_date: String,
        end_date: String,
        granularity: ReplayGranularity,
        /// N4.2: optional path to a user-supplied strategy `.py` file.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        strategy_file: Option<String>,
        /// N4.2: optional JSON object of strategy constructor kwargs.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        strategy_init_kwargs: Option<serde_json::Value>,
    },

    // ── N1.11: Replay speed control ───────────────────────────────────────
    /// Set the replay playback speed multiplier.
    /// `multiplier` is a positive integer: 1 = real-time, 10 = 10x speed, etc.
    /// Sent via `POST /api/replay/control` with `{"action":"speed","multiplier":N}`.
    SetReplaySpeed {
        request_id: String,
        multiplier: u32,
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
                mode,
            } => f
                .debug_struct("Hello")
                .field("schema_major", schema_major)
                .field("schema_minor", schema_minor)
                .field("client_version", client_version)
                .field("token", &"***")
                .field("mode", &mode.as_wire_str())
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
                venue_order_id,
                change,
            } => f
                .debug_struct("ModifyOrder")
                .field("request_id", request_id)
                .field("venue", venue)
                .field("client_order_id", client_order_id)
                .field("venue_order_id", venue_order_id)
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
            Command::GetBuyingPower { request_id, venue } => f
                .debug_struct("GetBuyingPower")
                .field("request_id", request_id)
                .field("venue", venue)
                .finish(),
            Command::StartEngine {
                request_id,
                engine,
                strategy_id,
                config,
            } => f
                .debug_struct("StartEngine")
                .field("request_id", request_id)
                .field("engine", engine)
                .field("strategy_id", strategy_id)
                .field("config", config)
                .finish(),
            Command::StopEngine {
                request_id,
                strategy_id,
            } => f
                .debug_struct("StopEngine")
                .field("request_id", request_id)
                .field("strategy_id", strategy_id)
                .finish(),
            Command::LoadReplayData {
                request_id,
                instrument_id,
                start_date,
                end_date,
                granularity,
                strategy_file,
                strategy_init_kwargs,
            } => f
                .debug_struct("LoadReplayData")
                .field("request_id", request_id)
                .field("instrument_id", instrument_id)
                .field("start_date", start_date)
                .field("end_date", end_date)
                .field("granularity", granularity)
                .field("strategy_file", strategy_file)
                .field("strategy_init_kwargs", strategy_init_kwargs)
                .finish(),
            Command::SetReplaySpeed {
                request_id,
                multiplier,
            } => f
                .debug_struct("SetReplaySpeed")
                .field("request_id", request_id)
                .field("multiplier", multiplier)
                .finish(),
        }
    }
}

// ── nautilus 統合 sub-types (schema 2.4 / N1.1) ───────────────────────────────

/// Which nautilus engine to start.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum EngineKind {
    Backtest,
    Live,
}

/// Replay data granularity (Trade tick / 1-min Bar / Daily Bar).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum ReplayGranularity {
    Trade,
    Minute,
    Daily,
}

/// Engine start config — shape mirrors `python/engine/nautilus/engine_runner.py`
/// arguments. Decimal-precision fields stay as strings to avoid f64 round-trip loss.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EngineStartConfig {
    pub instrument_id: String,
    pub start_date: String,
    pub end_date: String,
    pub initial_cash: String,
    pub granularity: ReplayGranularity,
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

fn default_order_record_venue() -> String {
    "tachibana".to_string()
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
    /// Venue that owns this order: "tachibana" for live orders, "replay" for REPLAY WAL orders.
    /// Defaults to "tachibana" for backwards-compatibility.
    #[serde(default = "default_order_record_venue")]
    pub venue: String,
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
    /// 接続レベル切断 frame と StartEngine 例外通知 outbox event の **両用 wire 形** を共有する (H-F).
    ///
    /// - `strategy_id == None` … handshake / 接続レベルエラー
    ///   (auth_failed / schema_mismatch 等)。受信側は接続を切断する。
    /// - `strategy_id == Some(_)` … 走行中 strategy の outbox event。
    ///   受信側は該当 strategy の state machine にだけ反映し、接続自体は維持する。
    EngineError {
        code: String,
        message: String,
        #[serde(default)]
        strategy_id: Option<String>,
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

    // ── nautilus 統合 events (schema 2.4 / N1.1) ──────────────────────────
    /// Engine started successfully (replay BacktestEngine or live LiveExecutionEngine).
    EngineStarted {
        strategy_id: String,
        account_id: String,
        ts_event_ms: i64,
    },
    /// Engine stopped. `final_equity` is decimal-string for precision safety.
    EngineStopped {
        strategy_id: String,
        final_equity: String,
        ts_event_ms: i64,
    },
    /// Replay data load completed. Counters help the UI display progress.
    ///
    /// M-8 (R1b / schema 2.5): `strategy_id` was tightened from `String` to
    /// `Option<String>` because the standalone `LoadReplayData` IPC (used by
    /// `/api/replay/load` before any strategy is started) has no meaningful
    /// strategy id. Old senders that omit the field deserialise as `None`
    /// thanks to `#[serde(default)]`. The in-engine `start_backtest_replay`
    /// path still emits a concrete strategy id (`Some(...)`).
    ReplayDataLoaded {
        #[serde(default)]
        strategy_id: Option<String>,
        bars_loaded: u64,
        trades_loaded: u64,
        ts_event_ms: i64,
    },
    /// nautilus Position opened (transition flat → long/short).
    PositionOpened {
        strategy_id: String,
        venue: String,
        instrument_id: String,
        position_id: String,
        side: String,
        opened_qty: String,
        avg_open_price: String,
        ts_event_ms: i64,
    },
    /// nautilus Position closed (transition long/short → flat).
    PositionClosed {
        strategy_id: String,
        venue: String,
        instrument_id: String,
        position_id: String,
        realized_pnl: String,
        ts_event_ms: i64,
    },

    // ── N1.12: Execution marker + strategy signal events ─────────────────
    /// Python emitted when a `OrderFilled` event is received for a nautilus strategy.
    /// One `ExecutionMarker` is emitted per `OrderFilled` (1:1 mapping).
    ExecutionMarker {
        strategy_id: String,
        instrument_id: String,
        /// `"BUY"` | `"SELL"`
        side: String,
        /// Fill price as decimal string.
        price: String,
        ts_event_ms: i64,
    },

    /// Explicit signal emitted by a strategy via `StrategySignalMixin.emit_signal()`.
    /// Independent of fills — a strategy can emit signals without any order activity.
    StrategySignal {
        strategy_id: String,
        instrument_id: String,
        signal_kind: SignalKind,
        /// `"BUY"` | `"SELL"` | null
        #[serde(default, skip_serializing_if = "Option::is_none")]
        side: Option<String>,
        /// Price level as decimal string, or null.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        price: Option<String>,
        /// Short machine-readable label (e.g. `"entry"`, `"stop"`).
        #[serde(default, skip_serializing_if = "Option::is_none")]
        tag: Option<String>,
        /// Human-readable annotation.
        #[serde(default, skip_serializing_if = "Option::is_none")]
        note: Option<String>,
        ts_event_ms: i64,
    },

    // ── Buying Power Phase (schema 2.1) ───────────────────────────────────
    /// Response to `GetBuyingPower`. Contains current cash and credit buying power.
    BuyingPowerUpdated {
        request_id: String,
        venue: String,
        /// 現物買付余力（円）
        cash_available: i64,
        /// 現物余力不足額（円、0 は不足なし）
        cash_shortfall: i64,
        /// 信用新規可能額（円）
        credit_available: i64,
        /// 取得時刻 Unix ミリ秒
        ts_ms: i64,
    },

    // ── N1.16: REPLAY 仮想買付余力 ────────────────────────────────────────────
    /// REPLAY モードの仮想買付余力（portfolio_view.py が送出）。
    /// cash / buying_power / equity はすべて decimal 文字列（float 丸め防止）。
    ReplayBuyingPower {
        strategy_id: String,
        cash: String,
        buying_power: String,
        equity: String,
        ts_event_ms: i64,
    },
}

fn default_true() -> bool {
    true
}

// ── N1.12 signal kind (暫定語彙 / Q13 確定まで) ──────────────────────────────

/// Signal kind vocabulary for `StrategySignal`.
///
/// Provisional values covering N1 scope (Q13 open — final vocabulary TBD in N2).
/// Adding new variants is backward-compatible because the wire representation is
/// the PascalCase variant name (serde default).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum SignalKind {
    EntryLong,
    EntryShort,
    Exit,
    Annotate,
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
