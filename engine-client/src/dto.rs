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

#[derive(Debug, Serialize)]
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
