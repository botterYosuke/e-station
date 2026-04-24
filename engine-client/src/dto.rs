/// IPC Data Transfer Objects for the Rust ↔ Python data engine protocol.
///
/// Commands flow Rust → Python; Events flow Python → Rust.
/// Both are transported as JSON text frames over a local WebSocket.
use serde::{Deserialize, Serialize};

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
        venue: String,
        ticker: String,
        market: String,
    },
    Ping {
        request_id: String,
    },
    Shutdown,
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
    },
    Disconnected {
        venue: String,
        ticker: String,
        stream: String,
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
