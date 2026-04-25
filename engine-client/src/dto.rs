/// IPC Data Transfer Objects for the Rust ↔ Python data engine protocol.
///
/// Commands flow Rust → Python; Events flow Python → Rust.
/// Both are transported as JSON text frames over a local WebSocket.
use serde::{Deserialize, Serialize};
use zeroize::Zeroizing;

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

/// Plain-`String` mirror of `data::config::tachibana::TachibanaCredentials`,
/// used only as the IPC wire format. Construct via [`From`] from the secret-
/// holding internal type and drop the value as soon as serialization is
/// done. Hand-rolled `Debug` masks every secret field.
#[derive(Clone, Serialize)]
pub struct TachibanaCredentialsWire {
    pub user_id: String,
    /// Plain-text password held in a `Zeroizing<String>` so the heap buffer
    /// is wiped on drop (M4 / MEDIUM-B2-2). `Serialize` falls through to
    /// `String`'s impl via `Deref` — no `serde` feature on `zeroize` needed.
    pub password: Zeroizing<String>,
    pub second_password: Option<Zeroizing<String>>,
    pub is_demo: bool,
    pub session: Option<TachibanaSessionWire>,
}

impl std::fmt::Debug for TachibanaCredentialsWire {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("TachibanaCredentialsWire")
            .field("user_id", &self.user_id)
            .field("password", &"***")
            .field("second_password", &self.second_password.as_ref().map(|_| "***"))
            .field("is_demo", &self.is_demo)
            .field("session", &self.session)
            .finish()
    }
}

#[derive(Serialize, Deserialize, Clone)]
pub struct TachibanaSessionWire {
    /// Virtual URLs are session-bound secrets (architecture.md §2.1, F-B2)
    /// and must be wiped on drop. `Zeroizing<String>` derives `Serialize` /
    /// `Deserialize` transparently through the inner `String`.
    pub url_request: Zeroizing<String>,
    pub url_master: Zeroizing<String>,
    pub url_price: Zeroizing<String>,
    pub url_event: Zeroizing<String>,
    pub url_event_ws: Zeroizing<String>,
    pub expires_at_ms: Option<i64>,
    pub zyoutoeki_kazei_c: String,
}

impl std::fmt::Debug for TachibanaSessionWire {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("TachibanaSessionWire")
            .field("url_request", &"***")
            .field("url_master", &"***")
            .field("url_price", &"***")
            .field("url_event", &"***")
            .field("url_event_ws", &"***")
            .field("expires_at_ms", &self.expires_at_ms)
            .field("zyoutoeki_kazei_c", &self.zyoutoeki_kazei_c)
            .finish()
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
