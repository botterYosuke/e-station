//! HTTP control API for E2E test driving (port 9876) and the nautilus
//! replay endpoints (`/api/replay/load`, `/api/replay/order`,
//! `/api/replay/portfolio`).
//!
//! Provides the local HTTP endpoints used by `tests/e2e/*.sh` scripts and the
//! REPLAY-mode user flow to drive the Iced app without a GUI automation
//! framework.  Iced itself has no built-in HTTP surface; this module runs a
//! minimal raw-TCP HTTP/1.1 server as a background tokio task.
//!
//! Architecture:
//! ```text
//! E2E bash script / REPLAY user
//!     ↓ HTTP/1.1 (port 9876)
//! replay_api — raw TCP listener
//!     ↓ tokio::sync::mpsc::Sender<ControlApiCommand>
//! main.rs — Iced Subscription (replay_api_stream)
//!     ↓ Message::ControlApi(ControlApiCommand)
//! Flowsurface::update()
//!
//! Replay flow (N1.3):
//!     POST /api/replay/load → engine_client.send(Command::LoadReplayData)
//!         → wait for EngineEvent::ReplayDataLoaded (60 s timeout)
//!     POST /api/replay/order → engine_client.send(Command::SubmitOrder { venue: "replay", .. })
//!         → returns 202 Accepted (OrderFilled await is N1.5)
//!     GET  /api/replay/portfolio → 200 not_implemented (N1.16 will fill in)
//! ```
//!
//! **Debug-build note**: The Tachibana session-deletion endpoint
//! (`POST /api/test/tachibana/delete-session`) is only enabled in debug builds
//! so it cannot accidentally clear prod keyring entries.

use std::{sync::Arc, time::Duration};

use engine_client::{
    EngineConnection,
    dto::{Command, EngineEvent, EngineKind, EngineStartConfig, ReplayGranularity},
};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    net::{TcpListener, TcpStream},
    sync::{Mutex, mpsc, watch},
};

use crate::api::agent_api::AgentApiState;
use crate::api::order_api::OrderApiState;

/// Commands the HTTP server forwards into the Iced application via mpsc.
// TODO(O1): venue fields are consumed in Flowsurface::update() once the
// full ControlApi subscription is wired up.
#[allow(dead_code)]
#[derive(Debug, Clone)]
pub enum ControlApiCommand {
    /// Toggle the Tachibana venue (equivalent to clicking the sidebar toggle).
    ToggleVenue { venue: String },
    /// Cancel the in-flight login helper subprocess.
    CancelLoginHelper,
    /// Request venue login (equivalent to pressing the "再ログイン" button).
    RequestVenueLogin { venue: String },
    /// Instruct the Iced app to auto-generate REPLAY panes for the given
    /// instrument (N1.14). Sent after `ReplayDataLoaded` is received.
    ///
    /// `strategy_id` は `ReplayDataLoaded.strategy_id`（`Option<String>`）を
    /// そのまま伝搬する。単独 `LoadReplayData` 経路では `None`、
    /// `StartEngine` 経由の load では具体値が入る (M-2, R2 review-fix R2)。
    AutoGenerateReplayPanes {
        instrument_id: String,
        strategy_id: Option<String>,
    },
}

/// Status snapshot returned by `GET /api/replay/status`.
#[derive(serde::Serialize)]
struct StatusResponse<'a> {
    status: &'a str,
    version: &'a str,
}

// ── N1.16: Replay portfolio cache ────────────────────────────────────────────

/// Last-seen `ReplayBuyingPower` snapshot, cached for `GET /api/replay/portfolio`.
#[derive(Debug, Clone, serde::Serialize)]
pub struct ReplayPortfolioSnapshot {
    pub strategy_id: String,
    pub cash: String,
    pub buying_power: String,
    pub equity: String,
    pub ts_event_ms: i64,
}

// ── Replay API state (N1.3) ───────────────────────────────────────────────────

/// Shared state for the nautilus replay endpoints.
///
/// Holds a `watch::Receiver` of the current `EngineConnection` (so the
/// receiver follows engine reconnects), the current startup `mode`
/// (`"live"` | `"replay"`), and a serialisation `Mutex` used for
/// `LoadReplayData` correlation: because `ReplayDataLoaded` does **not**
/// carry `request_id` in IPC schema 2.4, concurrent loads cannot be
/// disambiguated, so we serialise them.
pub struct ReplayApiState {
    pub engine_rx: watch::Receiver<Option<Arc<EngineConnection>>>,
    /// R1b H-E: 起動時固定モード。`AppMode::Live` か `AppMode::Replay`。
    /// 旧 `String` 比較は typo (`"reply"`) で sliently 通る危険があったため
    /// enum に格上げ。`AppMode::default() == Live` (handshake fallback と同じ)。
    pub mode: engine_client::dto::AppMode,
    /// Timeout for `LoadReplayData` → `ReplayDataLoaded`. Default 60 s
    /// (J-Quants 1-month trade tick load target per spec.md §3.3).
    pub load_timeout: Duration,
    /// Timeout for `StartEngine` → `EngineStarted`. Default 30 s.
    pub start_timeout: Duration,
    /// Serialise concurrent `/api/replay/load` calls so that
    /// `ReplayDataLoaded` (which has no `request_id` in schema 2.4) cannot
    /// be cross-correlated.
    load_lock: Mutex<()>,
    /// Instruments loaded at least once this session (used to enforce
    /// MAX_REPLAY_INSTRUMENTS and to pass instrument_id to AutoGenerateReplayPanes).
    loaded_instruments: Mutex<Vec<String>>,
    /// Channel to send ControlApiCommand to the iced app (None in tests).
    /// Wrapped in `Mutex` so it can be set after `Arc::new()` inside `spawn()`.
    control_tx: Mutex<Option<mpsc::Sender<ControlApiCommand>>>,
    /// N1.16: last-seen ReplayBuyingPower event, served by GET /api/replay/portfolio.
    /// Uses std::sync::Mutex so it can be updated synchronously from Flowsurface::update().
    portfolio: std::sync::Mutex<Option<ReplayPortfolioSnapshot>>,
}

/// Maximum number of distinct instruments that can be loaded in one replay session.
pub const MAX_REPLAY_INSTRUMENTS: usize = 4;

impl ReplayApiState {
    pub fn new(
        engine_rx: watch::Receiver<Option<Arc<EngineConnection>>>,
        mode: impl Into<engine_client::dto::AppMode>,
    ) -> Self {
        Self {
            engine_rx,
            mode: mode.into(),
            load_timeout: Duration::from_secs(60),
            start_timeout: Duration::from_secs(30),
            load_lock: Mutex::new(()),
            loaded_instruments: Mutex::new(Vec::new()),
            control_tx: Mutex::new(None),
            portfolio: std::sync::Mutex::new(None),
        }
    }

    /// Attach a `ControlApiCommand` sender so that successful loads can notify
    /// the Iced app to auto-generate panes (N1.14).
    /// This is called inside `spawn()` after `Arc::new()`.
    pub async fn set_control_tx(&self, tx: mpsc::Sender<ControlApiCommand>) {
        *self.control_tx.lock().await = Some(tx);
    }

    /// Cache a `ReplayBuyingPower` snapshot so `GET /api/replay/portfolio` can serve it.
    /// Called synchronously from `Flowsurface::update()` on `Message::ReplayBuyingPower`.
    pub fn update_replay_portfolio(
        &self,
        strategy_id: String,
        cash: String,
        buying_power: String,
        equity: String,
        ts_event_ms: i64,
    ) {
        match self.portfolio.lock() {
            Ok(mut guard) => {
                *guard = Some(ReplayPortfolioSnapshot {
                    strategy_id,
                    cash,
                    buying_power,
                    equity,
                    ts_event_ms,
                });
            }
            Err(e) => {
                log::error!("replay_api: portfolio Mutex poisoned in update_replay_portfolio: {e}");
            }
        }
    }

    /// Override the load timeout (test-only / future config hook).
    #[cfg(test)]
    pub fn with_load_timeout(mut self, t: Duration) -> Self {
        self.load_timeout = t;
        self
    }

    /// Override the start timeout (test-only).
    #[cfg(test)]
    pub fn with_start_timeout(mut self, t: Duration) -> Self {
        self.start_timeout = t;
        self
    }
}

// ── HTTP wire types (N1.3) ────────────────────────────────────────────────────

#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct ReplayLoadBody {
    instrument_id: String,
    start_date: String,
    end_date: String,
    /// `ReplayGranularity` を serde で直接受ける（H-D 修正）。`"Trade"` /
    /// `"Minute"` / `"Daily"` 以外は serde が `Err` を返し、呼出側で 400 に
    /// 変換される。手動の `parse_granularity()` ヘルパーは廃止。
    granularity: ReplayGranularity,
    #[serde(default)]
    strategy_file: Option<String>,
    /// JSON object only — array/scalar rejected by serde at HTTP boundary before IPC send.
    #[serde(default)]
    strategy_init_kwargs: Option<serde_json::Map<String, serde_json::Value>>,
}

#[derive(serde::Serialize)]
struct ReplayLoadOk {
    status: &'static str,
    bars_loaded: u64,
    trades_loaded: u64,
}

// ── N1.17: Replay start request body ─────────────────────────────────────────

#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct ReplayStartBody {
    instrument_id: String,
    start_date: String,
    end_date: String,
    granularity: ReplayGranularity,
    strategy_id: String,
    initial_cash: String,
    #[serde(default)]
    strategy_file: Option<String>,
    /// JSON object only — array/scalar は serde_json::Map で弾く。
    #[serde(default)]
    strategy_init_kwargs: Option<serde_json::Map<String, serde_json::Value>>,
}

#[derive(serde::Serialize)]
struct ReplayStartOk {
    status: &'static str,
    strategy_id: String,
    account_id: String,
}

// ── Internal raw HTTP/1.1 helpers ────────────────────────────────────────────

struct Request {
    method: String,
    path: String,
    body: String,
}

async fn parse_request(stream: &mut BufReader<&mut TcpStream>) -> Option<Request> {
    // Read the request line
    let mut request_line = String::new();
    stream.read_line(&mut request_line).await.ok()?;
    let mut parts = request_line.split_whitespace();
    let method = parts.next()?.to_uppercase();
    let path = parts.next()?.to_string();

    // Read headers until blank line; note Content-Length if present
    let mut content_length: usize = 0;
    loop {
        let mut header_line = String::new();
        stream.read_line(&mut header_line).await.ok()?;
        let trimmed = header_line.trim();
        if trimmed.is_empty() {
            break;
        }
        if let Some(rest) = trimmed.to_lowercase().strip_prefix("content-length:") {
            content_length = rest.trim().parse().unwrap_or(0);
        }
    }

    // Read body up to Content-Length bytes, capped at 65536 bytes to limit
    // memory use from unexpectedly large or malicious requests.
    let body = if content_length > 0 {
        let mut buf = vec![0u8; content_length.min(65_536)];
        use tokio::io::AsyncReadExt;
        stream.read_exact(&mut buf).await.ok()?;
        String::from_utf8_lossy(&buf).into_owned()
    } else {
        String::new()
    };

    Some(Request { method, path, body })
}

async fn write_response(stream: &mut TcpStream, status: u16, status_text: &str, body: &str) {
    let response = format!(
        "HTTP/1.1 {status} {status_text}\r\n\
         Content-Type: application/json\r\n\
         Content-Length: {}\r\n\
         Connection: close\r\n\
         \r\n\
         {body}",
        body.len()
    );
    if let Err(e) = stream.write_all(response.as_bytes()).await {
        log::debug!("replay_api: write_response failed (client disconnected?) — {e}");
    }
}

async fn write_error(stream: &mut TcpStream, status: u16, status_text: &str, error: &str) {
    let body = serde_json::json!({ "error": error }).to_string();
    write_response(stream, status, status_text, &body).await;
}

// ── Replay endpoint helpers (N1.3) ────────────────────────────────────────────

/// Validate ISO-8601 date `YYYY-MM-DD` with **calendar validation** (H-A 修正).
///
/// `chrono::NaiveDate::parse_from_str` で月日範囲・閏年も検証する。旧実装は
/// 文字種・桁数しか見ていなかったため `"2024-13-01"` / `"2024-02-30"` /
/// 非閏年の `"2023-02-29"` 等が通過し、Python loader 側で
/// `FileNotFoundError` 等を引き起こす危険があった。
fn is_iso_date(s: &str) -> bool {
    if s.len() != 10 {
        return false;
    }
    chrono::NaiveDate::parse_from_str(s, "%Y-%m-%d").is_ok()
}

/// `POST /api/replay/load` — bridge to `Command::LoadReplayData`.
///
/// Awaits `EngineEvent::ReplayDataLoaded` (no `request_id` in schema 2.4 →
/// concurrent loads are serialised via `state.load_lock`). On
/// `EngineEvent::Error{code: "mode_mismatch"}` returns HTTP 400; any other
/// `Error{}` returns 503. Timeout → 504.
async fn handle_replay_load(stream: &mut TcpStream, body: &str, state: &Arc<ReplayApiState>) {
    // ① Reject early on live mode
    if state.mode != engine_client::dto::AppMode::Replay {
        write_error(
            stream,
            400,
            "Bad Request",
            "replay endpoints are only available in --mode replay",
        )
        .await;
        return;
    }

    // ② Parse body
    let parsed: ReplayLoadBody = match serde_json::from_str(body) {
        Ok(v) => v,
        Err(e) => {
            write_error(stream, 400, "Bad Request", &format!("invalid JSON: {e}")).await;
            return;
        }
    };

    // ③ Validate fields
    if parsed.instrument_id.is_empty() {
        write_error(stream, 400, "Bad Request", "instrument_id is required").await;
        return;
    }

    // ③b Check MAX_REPLAY_INSTRUMENTS (N1.14).
    // Reload of an already-loaded instrument is always allowed.
    {
        let instruments = state.loaded_instruments.lock().await;
        if !instruments.contains(&parsed.instrument_id)
            && instruments.len() >= MAX_REPLAY_INSTRUMENTS
        {
            let body = serde_json::json!({
                "error": "max_instruments_exceeded",
                "max": MAX_REPLAY_INSTRUMENTS,
            })
            .to_string();
            write_response(stream, 400, "Bad Request", &body).await;
            return;
        }
    }
    if !is_iso_date(&parsed.start_date) {
        write_error(
            stream,
            400,
            "Bad Request",
            "start_date must be ISO-8601 (YYYY-MM-DD)",
        )
        .await;
        return;
    }
    if !is_iso_date(&parsed.end_date) {
        write_error(
            stream,
            400,
            "Bad Request",
            "end_date must be ISO-8601 (YYYY-MM-DD)",
        )
        .await;
        return;
    }
    // H-D: `granularity` は `ReplayLoadBody` で serde 直受け済み。不正値は
    // ② の `serde_json::from_str` が 400 を返している。
    let granularity = parsed.granularity;

    // ④ Get engine connection (drop the watch::Ref before any await)
    let conn_opt = state.engine_rx.borrow().clone();
    let conn = match conn_opt {
        Some(c) => c,
        None => {
            write_error(stream, 502, "Bad Gateway", "engine not connected").await;
            return;
        }
    };

    // ⑤ Serialise concurrent loads (ReplayDataLoaded has no request_id in 2.4)
    let _guard = state.load_lock.lock().await;

    // ⑥ Subscribe BEFORE send so we never miss the event
    let mut events_rx = conn.subscribe_events();

    // ⑦ Build & send command
    let request_id = uuid::Uuid::new_v4().to_string();
    let cmd = Command::LoadReplayData {
        request_id: request_id.clone(),
        instrument_id: parsed.instrument_id.clone(),
        start_date: parsed.start_date.clone(),
        end_date: parsed.end_date.clone(),
        granularity,
        strategy_file: parsed.strategy_file,
        strategy_init_kwargs: parsed.strategy_init_kwargs,
    };
    if let Err(e) = conn.send(cmd).await {
        write_error(
            stream,
            502,
            "Bad Gateway",
            &format!("failed to forward LoadReplayData to engine: {e}"),
        )
        .await;
        return;
    }

    // ⑧ Await ReplayDataLoaded (or matching Error{request_id}) with timeout
    let outcome = tokio::time::timeout(state.load_timeout, async {
        loop {
            match events_rx.recv().await {
                Ok(EngineEvent::ReplayDataLoaded {
                    strategy_id,
                    bars_loaded,
                    trades_loaded,
                    ..
                }) => {
                    return ReplayLoadOutcome::Ok {
                        strategy_id,
                        bars_loaded,
                        trades_loaded,
                    };
                }
                Ok(EngineEvent::Error {
                    request_id: rid,
                    code,
                    message,
                }) if rid.as_deref() == Some(request_id.as_str()) => {
                    return ReplayLoadOutcome::EngineError { code, message };
                }
                Ok(EngineEvent::ConnectionDropped) => {
                    return ReplayLoadOutcome::Disconnected;
                }
                Ok(_) => continue,
                Err(tokio::sync::broadcast::error::RecvError::Lagged(n)) => {
                    // H-B: broadcast が遅延すると `ReplayDataLoaded` を取りこぼす
                    // 可能性があるため即時に 503 を返し、呼出側に再試行させる。
                    log::warn!("replay_api: broadcast lagged by {n}; aborting LoadReplayData wait");
                    return ReplayLoadOutcome::Lagged { skipped: n };
                }
                Err(tokio::sync::broadcast::error::RecvError::Closed) => {
                    return ReplayLoadOutcome::Disconnected;
                }
            }
        }
    })
    .await;

    match outcome {
        Ok(ReplayLoadOutcome::Ok {
            strategy_id: loaded_strategy_id,
            bars_loaded,
            trades_loaded,
        }) => {
            // N1.14: Track loaded instrument and notify Iced app.
            // M-2 (R2 review-fix R2): propagate strategy_id from ReplayDataLoaded as
            // Option<String>. None は単独 LoadReplayData 経路、Some(_) は
            // StartEngine 経由 load を表す（情報損失せず Iced まで運ぶ）。
            let strategy_id_for_cmd: Option<String> = loaded_strategy_id;
            {
                let mut instruments = state.loaded_instruments.lock().await;
                if !instruments.contains(&parsed.instrument_id) {
                    instruments.push(parsed.instrument_id.clone());
                }
            }
            {
                let tx_guard = state.control_tx.lock().await;
                if let Some(tx) = tx_guard.as_ref() {
                    let cmd = ControlApiCommand::AutoGenerateReplayPanes {
                        instrument_id: parsed.instrument_id.clone(),
                        strategy_id: strategy_id_for_cmd.clone(),
                    };
                    match tx.try_send(cmd) {
                        Ok(_) => {}
                        Err(tokio::sync::mpsc::error::TrySendError::Full(_)) => {
                            log::warn!(
                                "replay_api: AutoGenerateReplayPanes channel full, dropping"
                            );
                        }
                        Err(tokio::sync::mpsc::error::TrySendError::Closed(_)) => {
                            log::error!("replay_api: AutoGenerateReplayPanes channel closed");
                        }
                    }
                }
            }
            let body = serde_json::to_string(&ReplayLoadOk {
                status: "ok",
                bars_loaded,
                trades_loaded,
            })
            .unwrap_or_else(|_| r#"{"status":"ok"}"#.to_string());
            write_response(stream, 200, "OK", &body).await;
        }
        Ok(ReplayLoadOutcome::EngineError { code, message }) => {
            // mode_mismatch is a client error (wrong startup mode), all others
            // are engine-side problems → 503.
            let status = if code == "mode_mismatch" { 400 } else { 503 };
            let status_text = if status == 400 {
                "Bad Request"
            } else {
                "Service Unavailable"
            };
            let body =
                serde_json::json!({ "error": code, "message": message, "code": code }).to_string();
            write_response(stream, status, status_text, &body).await;
        }
        Ok(ReplayLoadOutcome::Disconnected) => {
            write_error(
                stream,
                502,
                "Bad Gateway",
                "engine connection lost while waiting",
            )
            .await;
        }
        Ok(ReplayLoadOutcome::Lagged { skipped }) => {
            // H-B: broadcast 遅延を 503 で明示的に通知する
            let body = serde_json::json!({
                "error": "events lagged",
                "skipped": skipped,
            })
            .to_string();
            write_response(stream, 503, "Service Unavailable", &body).await;
        }
        Err(_timeout) => {
            write_response(stream, 504, "Gateway Timeout", r#"{"error":"timeout"}"#).await;
        }
    }
}

enum ReplayLoadOutcome {
    Ok {
        strategy_id: Option<String>,
        bars_loaded: u64,
        trades_loaded: u64,
    },
    EngineError {
        code: String,
        message: String,
    },
    Disconnected,
    /// H-B: tokio broadcast の `RecvError::Lagged` を表す。
    Lagged {
        skipped: u64,
    },
}

// ── N1.11: Replay control request body ───────────────────────────────────────

#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct ReplayControlBody {
    action: String,
    multiplier: Option<u32>,
}

/// `POST /api/replay/control` — N1.11 speed control.
///
/// Only `action="speed"` is accepted in N1 scope (Q14: Pause/Resume/Seek are
/// out of scope for N1). Returns `200 {"status":"ok","multiplier":N}` on
/// success, `400` for unknown actions or missing/zero multiplier.
async fn handle_replay_control(stream: &mut TcpStream, body: &str, state: &Arc<ReplayApiState>) {
    if state.mode != engine_client::dto::AppMode::Replay {
        write_error(
            stream,
            400,
            "Bad Request",
            "replay endpoints are only available in --mode replay",
        )
        .await;
        return;
    }

    let parsed: ReplayControlBody = match serde_json::from_str(body) {
        Ok(v) => v,
        Err(e) => {
            write_error(stream, 400, "Bad Request", &format!("invalid JSON: {e}")).await;
            return;
        }
    };

    if parsed.action != "speed" {
        write_error(
            stream,
            400,
            "Bad Request",
            &format!(
                "unsupported action {:?}; only \"speed\" is supported in N1 (Q14)",
                parsed.action
            ),
        )
        .await;
        return;
    }

    let multiplier = match parsed.multiplier {
        Some(m) if m >= 1 => m,
        Some(_) => {
            write_error(stream, 400, "Bad Request", "multiplier must be >= 1").await;
            return;
        }
        None => {
            write_error(
                stream,
                400,
                "Bad Request",
                "multiplier is required for action=speed",
            )
            .await;
            return;
        }
    };

    let conn_opt = state.engine_rx.borrow().clone();
    let conn = match conn_opt {
        Some(c) => c,
        None => {
            write_error(stream, 502, "Bad Gateway", "engine not connected").await;
            return;
        }
    };

    let request_id = uuid::Uuid::new_v4().to_string();
    let cmd = Command::SetReplaySpeed {
        request_id,
        multiplier,
    };
    if let Err(e) = conn.send(cmd).await {
        write_error(
            stream,
            502,
            "Bad Gateway",
            &format!("failed to forward SetReplaySpeed to engine: {e}"),
        )
        .await;
        return;
    }

    let resp_body = serde_json::json!({
        "status": "ok",
        "multiplier": multiplier,
    })
    .to_string();
    write_response(stream, 200, "OK", &resp_body).await;
}

/// `POST /api/replay/start` — N1.17: start a BuyAndHold backtest via `Command::StartEngine`.
///
/// Sends `StartEngine { engine: Backtest, ... }` and awaits `EngineEvent::EngineStarted`
/// (max 30 s). Returns 202 Accepted with `{ status, strategy_id, account_id }` on success.
/// 504 on timeout, 503 on engine error, 502 on disconnect.
async fn handle_replay_start(stream: &mut TcpStream, body: &str, state: &Arc<ReplayApiState>) {
    // ① Reject early on live mode
    if state.mode != engine_client::dto::AppMode::Replay {
        write_error(
            stream,
            400,
            "Bad Request",
            "replay endpoints are only available in --mode replay",
        )
        .await;
        return;
    }

    // ② Parse body
    let parsed: ReplayStartBody = match serde_json::from_str(body) {
        Ok(v) => v,
        Err(e) => {
            write_error(stream, 400, "Bad Request", &format!("invalid JSON: {e}")).await;
            return;
        }
    };

    // ③ Validate fields
    if parsed.instrument_id.is_empty() {
        write_error(stream, 400, "Bad Request", "instrument_id is required").await;
        return;
    }
    if !is_iso_date(&parsed.start_date) {
        write_error(
            stream,
            400,
            "Bad Request",
            "start_date must be ISO-8601 (YYYY-MM-DD)",
        )
        .await;
        return;
    }
    if !is_iso_date(&parsed.end_date) {
        write_error(
            stream,
            400,
            "Bad Request",
            "end_date must be ISO-8601 (YYYY-MM-DD)",
        )
        .await;
        return;
    }
    if parsed.strategy_id.is_empty() {
        write_error(stream, 400, "Bad Request", "strategy_id is required").await;
        return;
    }
    if parsed.initial_cash.is_empty() {
        write_error(stream, 400, "Bad Request", "initial_cash is required").await;
        return;
    }
    match &parsed.strategy_file {
        None => {
            write_error(stream, 400, "Bad Request", "strategy_file is required").await;
            return;
        }
        Some(s) if s.is_empty() => {
            write_error(stream, 400, "Bad Request", "strategy_file is required").await;
            return;
        }
        Some(_) => {}
    }

    // ④ Get engine connection
    let conn_opt = state.engine_rx.borrow().clone();
    let conn = match conn_opt {
        Some(c) => c,
        None => {
            write_error(stream, 502, "Bad Gateway", "engine not connected").await;
            return;
        }
    };

    // ⑤ Subscribe BEFORE send so we never miss the event
    let mut events_rx = conn.subscribe_events();

    // ⑥ Build & send command
    let request_id = uuid::Uuid::new_v4().to_string();
    let strategy_id = parsed.strategy_id.clone();
    let cmd = Command::StartEngine {
        request_id: request_id.clone(),
        engine: EngineKind::Backtest,
        strategy_id: strategy_id.clone(),
        config: EngineStartConfig {
            instrument_id: parsed.instrument_id.clone(),
            start_date: parsed.start_date.clone(),
            end_date: parsed.end_date.clone(),
            initial_cash: parsed.initial_cash.clone(),
            granularity: parsed.granularity,
            strategy_file: parsed.strategy_file,
            strategy_init_kwargs: parsed.strategy_init_kwargs,
        },
    };
    if let Err(e) = conn.send(cmd).await {
        write_error(
            stream,
            502,
            "Bad Gateway",
            &format!("failed to forward StartEngine to engine: {e}"),
        )
        .await;
        return;
    }

    // ⑦ Await EngineStarted (or matching error) with timeout
    let outcome = tokio::time::timeout(state.start_timeout, async {
        loop {
            match events_rx.recv().await {
                Ok(EngineEvent::EngineStarted {
                    strategy_id: sid,
                    account_id,
                    ..
                }) if sid == strategy_id => {
                    return ReplayStartOutcome::Ok {
                        strategy_id: sid,
                        account_id,
                    };
                }
                Ok(EngineEvent::EngineError {
                    strategy_id: Some(sid),
                    code,
                    message,
                }) if sid == strategy_id => {
                    return ReplayStartOutcome::EngineError { code, message };
                }
                Ok(EngineEvent::Error {
                    request_id: Some(rid),
                    code,
                    message,
                }) if rid == request_id => {
                    return ReplayStartOutcome::EngineError { code, message };
                }
                Ok(EngineEvent::ConnectionDropped) => {
                    return ReplayStartOutcome::Disconnected;
                }
                Ok(ev) => {
                    log::debug!("replay_api: StartEngine wait skipping unrelated event: {ev:?}");
                    continue;
                }
                Err(tokio::sync::broadcast::error::RecvError::Lagged(n)) => {
                    log::warn!("replay_api: broadcast lagged by {n}; aborting StartEngine wait");
                    return ReplayStartOutcome::Lagged { skipped: n };
                }
                Err(tokio::sync::broadcast::error::RecvError::Closed) => {
                    return ReplayStartOutcome::Disconnected;
                }
            }
        }
    })
    .await;

    match outcome {
        Ok(ReplayStartOutcome::Ok {
            strategy_id: started_sid,
            account_id,
        }) => {
            let body = serde_json::to_string(&ReplayStartOk {
                status: "started",
                strategy_id: started_sid,
                account_id,
            })
            .unwrap_or_else(|_| r#"{"status":"started"}"#.to_string());
            write_response(stream, 202, "Accepted", &body).await;
        }
        Ok(ReplayStartOutcome::EngineError { code, message }) => {
            let status = match code.as_str() {
                "mode_mismatch" | "strategy_file_required" | "invalid_config" => 400,
                _ => 503,
            };
            let status_text = if status == 400 {
                "Bad Request"
            } else {
                "Service Unavailable"
            };
            let body =
                serde_json::json!({ "error": code, "message": message, "code": code }).to_string();
            write_response(stream, status, status_text, &body).await;
        }
        Ok(ReplayStartOutcome::Disconnected) => {
            write_error(
                stream,
                502,
                "Bad Gateway",
                "engine connection lost while waiting",
            )
            .await;
        }
        Ok(ReplayStartOutcome::Lagged { skipped }) => {
            let body = serde_json::json!({
                "error": "events lagged",
                "skipped": skipped,
            })
            .to_string();
            write_response(stream, 503, "Service Unavailable", &body).await;
        }
        Err(_timeout) => {
            write_response(stream, 504, "Gateway Timeout", r#"{"error":"timeout"}"#).await;
        }
    }
}

enum ReplayStartOutcome {
    Ok {
        strategy_id: String,
        account_id: String,
    },
    EngineError {
        code: String,
        message: String,
    },
    Disconnected,
    Lagged {
        skipped: u64,
    },
}

/// `GET /api/replay/portfolio` — N1.16 live data.
///
/// Returns the last `ReplayBuyingPower` snapshot received from the Python engine.
/// Returns `{"status":"not_ready"}` if no fill events have occurred yet.
async fn handle_replay_portfolio(stream: &mut TcpStream, state: &Arc<ReplayApiState>) {
    if state.mode != engine_client::dto::AppMode::Replay {
        write_error(
            stream,
            400,
            "Bad Request",
            "replay endpoints are only available in --mode replay",
        )
        .await;
        return;
    }
    let snapshot_result: Result<Option<ReplayPortfolioSnapshot>, String> = state
        .portfolio
        .lock()
        .map(|g| g.clone())
        .map_err(|e| e.to_string());
    let snapshot = match snapshot_result {
        Ok(s) => s,
        Err(e) => {
            log::error!("replay_api: portfolio Mutex poisoned in handle_replay_portfolio: {e}");
            write_error(stream, 500, "Internal Server Error", "internal").await;
            return;
        }
    };
    let body = match snapshot {
        Some(snap) => serde_json::json!({
            "status": "ok",
            "strategy_id": snap.strategy_id,
            "cash": snap.cash,
            "buying_power": snap.buying_power,
            "equity": snap.equity,
            "ts_event_ms": snap.ts_event_ms,
        })
        .to_string(),
        None => serde_json::json!({ "status": "not_ready" }).to_string(),
    };
    write_response(stream, 200, "OK", &body).await;
}

/// `POST /api/replay/order` — N1.3 wiring.
///
/// Sends `Command::SubmitOrder { venue: "replay", .. }` to the Python engine
/// without waiting for `OrderFilled` (the wrapper Strategy that emits
/// `OrderFilled` for replay venues is implemented in **N1.5**). Until N1.5
/// completes, this endpoint returns **202 Accepted** as soon as the IPC has
/// been forwarded so callers know the command was queued.
///
/// In live mode this endpoint returns 400 (replay-only).
async fn handle_replay_order(stream: &mut TcpStream, body: &str, state: &Arc<ReplayApiState>) {
    if state.mode != engine_client::dto::AppMode::Replay {
        write_error(
            stream,
            400,
            "Bad Request",
            "replay endpoints are only available in --mode replay",
        )
        .await;
        return;
    }

    // The body shape mirrors `/api/order/submit` exactly. We re-parse only the
    // fields needed to build the IPC command — full validation lives in
    // `/api/order/submit` and will be re-used in N1.5 once we add a unified
    // dispatcher.
    let parsed: serde_json::Value = match serde_json::from_str(body) {
        Ok(v) => v,
        Err(e) => {
            write_error(stream, 400, "Bad Request", &format!("invalid JSON: {e}")).await;
            return;
        }
    };

    let order_obj = match parsed.as_object() {
        Some(o) => o,
        None => {
            write_error(stream, 400, "Bad Request", "body must be a JSON object").await;
            return;
        }
    };

    let cid: String = match order_obj.get("client_order_id").and_then(|v| v.as_str()) {
        Some(s) if !s.is_empty() => s.to_owned(),
        _ => {
            write_error(stream, 400, "Bad Request", "client_order_id is required").await;
            return;
        }
    };

    let conn_opt = state.engine_rx.borrow().clone();
    let conn = match conn_opt {
        Some(c) => c,
        None => {
            write_error(stream, 502, "Bad Gateway", "engine not connected").await;
            return;
        }
    };

    // Build IPC SubmitOrder. Use serde_json::from_value to reuse the
    // engine_client SubmitOrderRequest shape (same field names).
    // M-1 (R2 review-fix R2): `parsed` を move して clone を削減する。
    let order: engine_client::dto::SubmitOrderRequest = match serde_json::from_value(parsed) {
        Ok(o) => o,
        Err(e) => {
            write_error(
                stream,
                400,
                "Bad Request",
                &format!("invalid SubmitOrderRequest fields: {e}"),
            )
            .await;
            return;
        }
    };

    let request_id = uuid::Uuid::new_v4().to_string();
    let cmd = Command::SubmitOrder {
        request_id: request_id.clone(),
        venue: "replay".to_string(),
        order,
    };
    if let Err(e) = conn.send(cmd).await {
        write_error(
            stream,
            502,
            "Bad Gateway",
            &format!("failed to forward SubmitOrder to engine: {e}"),
        )
        .await;
        return;
    }

    // N1.5 繰越: OrderFilled await is implemented in N1.5 alongside the
    // wrapper Strategy + tachibana_orders_replay.jsonl WAL. For now we
    // acknowledge that the command has been queued.
    let body = serde_json::json!({
        "status": "accepted",
        "client_order_id": cid,
        "request_id": request_id,
        "phase": "N1.5",
    })
    .to_string();
    write_response(stream, 202, "Accepted", &body).await;
}

// ── Request handler ───────────────────────────────────────────────────────────

async fn handle_request(
    mut stream: TcpStream,
    tx: mpsc::Sender<ControlApiCommand>,
    order_state: Option<Arc<OrderApiState>>,
    replay_state: Option<Arc<ReplayApiState>>,
    agent_state: Option<Arc<AgentApiState>>,
) {
    let mut reader = BufReader::new(&mut stream);
    let req = match parse_request(&mut reader).await {
        Some(r) => r,
        None => return,
    };

    // Drop the BufReader to get `stream` back
    drop(reader);

    match (req.method.as_str(), req.path.as_str()) {
        ("GET", "/api/replay/status") => {
            let body = serde_json::to_string(&StatusResponse {
                status: "ok",
                version: env!("CARGO_PKG_VERSION"),
            })
            .unwrap_or_else(|_| r#"{"status":"ok"}"#.to_string());
            write_response(&mut stream, 200, "OK", &body).await;
        }
        ("POST", "/api/replay/load") => {
            if let Some(rs) = replay_state.as_ref() {
                handle_replay_load(&mut stream, &req.body, rs).await;
            } else {
                write_error(
                    &mut stream,
                    503,
                    "Service Unavailable",
                    "replay API not configured",
                )
                .await;
            }
        }
        ("POST", "/api/replay/start") => {
            if let Some(rs) = replay_state.as_ref() {
                handle_replay_start(&mut stream, &req.body, rs).await;
            } else {
                write_error(
                    &mut stream,
                    503,
                    "Service Unavailable",
                    "replay API not configured",
                )
                .await;
            }
        }
        ("POST", "/api/replay/order") => {
            if let Some(rs) = replay_state.as_ref() {
                handle_replay_order(&mut stream, &req.body, rs).await;
            } else {
                write_error(
                    &mut stream,
                    503,
                    "Service Unavailable",
                    "replay API not configured",
                )
                .await;
            }
        }
        ("POST", "/api/replay/control") => {
            if let Some(rs) = replay_state.as_ref() {
                handle_replay_control(&mut stream, &req.body, rs).await;
            } else {
                write_error(
                    &mut stream,
                    503,
                    "Service Unavailable",
                    "replay API not configured",
                )
                .await;
            }
        }
        ("GET", "/api/replay/portfolio") => {
            if let Some(rs) = replay_state.as_ref() {
                handle_replay_portfolio(&mut stream, rs).await;
            } else {
                write_error(
                    &mut stream,
                    503,
                    "Service Unavailable",
                    "replay API not configured",
                )
                .await;
            }
        }
        ("POST", "/api/sidebar/toggle-venue") => {
            let venue = serde_json::from_str::<serde_json::Value>(&req.body)
                .ok()
                .and_then(|v| v["venue"].as_str().map(ToOwned::to_owned))
                .unwrap_or_default();
            if venue.is_empty() {
                write_response(
                    &mut stream,
                    400,
                    "Bad Request",
                    r#"{"error":"venue required"}"#,
                )
                .await;
                return;
            }
            if let Err(e) = tx.try_send(ControlApiCommand::ToggleVenue { venue }) {
                log::warn!("replay_api: ToggleVenue dropped — channel full or closed: {e}");
            }
            write_response(&mut stream, 202, "Accepted", r#"{"status":"accepted"}"#).await;
        }
        ("POST", "/api/sidebar/tachibana/request-login") => {
            if let Err(e) = tx.try_send(ControlApiCommand::RequestVenueLogin {
                venue: "tachibana".to_string(),
            }) {
                log::warn!("replay_api: RequestVenueLogin dropped — channel full or closed: {e}");
            }
            write_response(&mut stream, 202, "Accepted", r#"{"status":"accepted"}"#).await;
        }
        // A-7 (H-5): テスト専用エンドポイントはデバッグビルドのみ有効。
        // リリースビルドでは 404 を返す。
        #[cfg(debug_assertions)]
        ("POST", "/api/test/tachibana/cancel-helper") => {
            if let Err(e) = tx.try_send(ControlApiCommand::CancelLoginHelper) {
                log::warn!("replay_api: CancelLoginHelper dropped — channel full or closed: {e}");
            }
            write_response(&mut stream, 202, "Accepted", r#"{"status":"accepted"}"#).await;
        }
        #[cfg(not(debug_assertions))]
        ("POST", path) if path.starts_with("/api/test/") => {
            write_response(
                &mut stream,
                404,
                "Not Found",
                r#"{"error":"test endpoints not available in release builds"}"#,
            )
            .await;
        }
        ("POST", "/api/order/submit") => {
            if let Some(state) = order_state {
                crate::api::order_api::handle_submit_request(&mut stream, &req.body, &state).await;
            } else {
                write_response(
                    &mut stream,
                    503,
                    "Service Unavailable",
                    r#"{"reason_code":"INTERNAL_ERROR","reason_text":"order API not configured"}"#,
                )
                .await;
            }
        }
        ("POST", "/api/order/modify") => {
            if let Some(state) = order_state {
                crate::api::order_api::handle_modify_request(&mut stream, &req.body, &state).await;
            } else {
                write_response(
                    &mut stream,
                    503,
                    "Service Unavailable",
                    r#"{"reason_code":"INTERNAL_ERROR","reason_text":"order API not configured"}"#,
                )
                .await;
            }
        }
        ("POST", "/api/order/cancel") => {
            if let Some(state) = order_state {
                crate::api::order_api::handle_cancel_request(&mut stream, &req.body, &state).await;
            } else {
                write_response(
                    &mut stream,
                    503,
                    "Service Unavailable",
                    r#"{"reason_code":"INTERNAL_ERROR","reason_text":"order API not configured"}"#,
                )
                .await;
            }
        }
        ("POST", "/api/order/cancel-all") => {
            if let Some(state) = order_state {
                crate::api::order_api::handle_cancel_all_request(&mut stream, &req.body, &state)
                    .await;
            } else {
                write_response(
                    &mut stream,
                    503,
                    "Service Unavailable",
                    r#"{"reason_code":"INTERNAL_ERROR","reason_text":"order API not configured"}"#,
                )
                .await;
            }
        }
        ("GET", "/api/order/list") => {
            if let Some(state) = order_state {
                crate::api::order_api::handle_list_request(&mut stream, &req.body, &state).await;
            } else {
                write_response(
                    &mut stream,
                    503,
                    "Service Unavailable",
                    r#"{"reason_code":"INTERNAL_ERROR","reason_text":"order API not configured"}"#,
                )
                .await;
            }
        }
        ("POST", "/api/agent/narrative") => {
            if let Some(state) = agent_state {
                crate::api::agent_api::handle_post_narrative(&mut stream, &req.body, &state).await;
            } else {
                write_error(
                    &mut stream,
                    503,
                    "Service Unavailable",
                    "agent API not configured",
                )
                .await;
            }
        }
        ("GET", "/api/agent/narrative") => {
            if let Some(state) = agent_state {
                crate::api::agent_api::handle_get_narrative(&mut stream, &state).await;
            } else {
                write_error(
                    &mut stream,
                    503,
                    "Service Unavailable",
                    "agent API not configured",
                )
                .await;
            }
        }
        _ => {
            write_response(&mut stream, 404, "Not Found", r#"{"error":"not found"}"#).await;
        }
    }
}

// ── Public API ────────────────────────────────────────────────────────────────

/// Spawn the HTTP control API server on `127.0.0.1:9876`.
///
/// Returns a `Receiver` that the Iced subscription (`replay_api_stream`) must
/// poll to forward `ControlApiCommand` values into `Message::ControlApi`.
///
/// The server binds immediately; if port 9876 is already in use the spawn
/// is a no-op and `None` is returned (caller should log a warning).
pub fn spawn(
    rt: &tokio::runtime::Handle,
    order_state: Option<Arc<OrderApiState>>,
    replay_state: Option<Arc<ReplayApiState>>,
    agent_state: Option<Arc<AgentApiState>>,
) -> Option<mpsc::Receiver<ControlApiCommand>> {
    let (tx, rx) = mpsc::channel::<ControlApiCommand>(64);

    let listener = match std::net::TcpListener::bind("127.0.0.1:9876") {
        Ok(l) => l,
        Err(e) => {
            log::warn!("replay_api: could not bind :9876 — {e}");
            return None;
        }
    };
    if let Err(e) = listener.set_nonblocking(true) {
        log::warn!("replay_api: set_nonblocking failed — {e}");
        return None;
    }

    // N1.14: wire the ControlApiCommand sender into ReplayApiState so that
    // successful LoadReplayData can trigger pane auto-generation.
    let tx_for_replay = tx.clone();

    rt.spawn(async move {
        // Inject the sender before the accept loop starts.
        if let Some(rs) = &replay_state {
            rs.set_control_tx(tx_for_replay).await;
        }

        let listener = match TcpListener::from_std(listener) {
            Ok(l) => l,
            Err(e) => {
                log::error!("replay_api: failed to convert listener — {e}");
                return;
            }
        };
        log::info!("replay_api: HTTP control API listening on 127.0.0.1:9876");
        loop {
            match listener.accept().await {
                Ok((stream, _addr)) => {
                    let tx_clone = tx.clone();
                    let order_state_clone = order_state.clone();
                    let replay_state_clone = replay_state.clone();
                    let agent_state_clone = agent_state.clone();
                    tokio::spawn(handle_request(
                        stream,
                        tx_clone,
                        order_state_clone,
                        replay_state_clone,
                        agent_state_clone,
                    ));
                }
                Err(e) => {
                    log::warn!("replay_api: accept error — {e}");
                    // Back off briefly on persistent errors (e.g. EMFILE) to
                    // avoid a CPU-spinning tight loop.
                    tokio::time::sleep(std::time::Duration::from_millis(100)).await;
                }
            }
        }
    });

    Some(rx)
}

// ── Tests (N1.3) ──────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use engine_client::EngineConnection;
    use futures_util::{SinkExt, StreamExt};
    use std::net::SocketAddr;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::{TcpListener, TcpStream as StdTcpStream};
    use tokio_tungstenite::{accept_async, tungstenite::Message};

    // ── Mock WS engine ────────────────────────────────────────────────────────

    async fn bind_ws_loopback() -> (TcpListener, SocketAddr) {
        let l = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = l.local_addr().unwrap();
        (l, addr)
    }

    async fn ws_send_ready<S>(ws: &mut tokio_tungstenite::WebSocketStream<S>)
    where
        S: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin,
    {
        let ready = serde_json::json!({
            "event": "Ready",
            "schema_major": engine_client::SCHEMA_MAJOR,
            "schema_minor": engine_client::SCHEMA_MINOR,
            "engine_version": "1.0.0-mock",
            "engine_session_id": "00000000-0000-0000-0000-000000000001",
            "capabilities": {}
        });
        ws.send(Message::Text(ready.to_string().into()))
            .await
            .unwrap();
    }

    /// Mock engine: handshake, then on `LoadReplayData` send `ReplayDataLoaded`
    /// (or `Error{mode_mismatch}` if `error_code` is `Some`). Silent if `silent`.
    fn spawn_mock_engine_load(
        listener: TcpListener,
        bars_loaded: u64,
        trades_loaded: u64,
        error: Option<(String, String)>,
        silent: bool,
    ) -> tokio::task::JoinHandle<()> {
        spawn_mock_engine_load_with_strategy_id(
            listener,
            bars_loaded,
            trades_loaded,
            error,
            silent,
            serde_json::Value::String(String::new()),
        )
    }

    /// `spawn_mock_engine_load` の strategy_id 指定可能版。
    /// `serde_json::Value::Null` を渡すと `"strategy_id": null` を emit する。
    fn spawn_mock_engine_load_with_strategy_id(
        listener: TcpListener,
        bars_loaded: u64,
        trades_loaded: u64,
        error: Option<(String, String)>,
        silent: bool,
        strategy_id: serde_json::Value,
    ) -> tokio::task::JoinHandle<()> {
        tokio::spawn(async move {
            let (tcp, _) = listener.accept().await.unwrap();
            let mut ws = accept_async(tcp).await.unwrap();
            let _ = ws.next().await; // Hello
            ws_send_ready(&mut ws).await;

            // Wait for LoadReplayData
            let cmd_msg = ws.next().await;
            let request_id: Option<String> = if let Some(Ok(m)) = cmd_msg {
                let text = m.into_text().unwrap();
                let v: serde_json::Value = serde_json::from_str(&text).unwrap();
                v["request_id"].as_str().map(ToOwned::to_owned)
            } else {
                None
            };

            if silent {
                tokio::time::sleep(Duration::from_secs(10)).await;
                return;
            }

            if let Some((code, message)) = error {
                let evt = serde_json::json!({
                    "event": "Error",
                    "request_id": request_id,
                    "code": code,
                    "message": message,
                });
                ws.send(Message::Text(evt.to_string().into()))
                    .await
                    .unwrap();
            } else {
                let evt = serde_json::json!({
                    "event": "ReplayDataLoaded",
                    "strategy_id": strategy_id,
                    "bars_loaded": bars_loaded,
                    "trades_loaded": trades_loaded,
                    "ts_event_ms": 1_700_000_000_000_i64,
                });
                ws.send(Message::Text(evt.to_string().into()))
                    .await
                    .unwrap();
            }
            tokio::time::sleep(Duration::from_millis(200)).await;
        })
    }

    /// Mock engine: on any command, drain it and send nothing — the test
    /// inspects the command bytes instead.
    fn spawn_mock_engine_capture(
        listener: TcpListener,
    ) -> tokio::sync::oneshot::Receiver<serde_json::Value> {
        let (tx, rx) = tokio::sync::oneshot::channel::<serde_json::Value>();
        tokio::spawn(async move {
            let (tcp, _) = listener.accept().await.unwrap();
            let mut ws = accept_async(tcp).await.unwrap();
            let _ = ws.next().await; // Hello
            ws_send_ready(&mut ws).await;

            if let Some(Ok(m)) = ws.next().await {
                let text = m.into_text().unwrap();
                let v: serde_json::Value = serde_json::from_str(&text).unwrap();
                let _ = tx.send(v);
            }
            tokio::time::sleep(Duration::from_millis(200)).await;
        });
        rx
    }

    async fn connect_engine(addr: SocketAddr) -> Arc<EngineConnection> {
        tokio::time::sleep(Duration::from_millis(5)).await;
        let url = format!("ws://{addr}");
        Arc::new(
            EngineConnection::connect(&url, "test-token")
                .await
                .expect("engine connect failed"),
        )
    }

    /// Spawn an HTTP server bound to a random port using the same routing
    /// table as production `spawn()`.
    async fn spawn_test_http_server(replay_state: Arc<ReplayApiState>) -> u16 {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let (tx, _rx) = mpsc::channel::<ControlApiCommand>(8);
        tokio::spawn(async move {
            while let Ok((stream, _)) = listener.accept().await {
                let tx_clone = tx.clone();
                let replay_state = Arc::clone(&replay_state);
                tokio::spawn(handle_request(
                    stream,
                    tx_clone,
                    None,
                    Some(replay_state),
                    None,
                ));
            }
        });
        port
    }

    async fn http_request(port: u16, method: &str, path: &str, body: &str) -> (u16, String) {
        let mut stream = StdTcpStream::connect(format!("127.0.0.1:{port}"))
            .await
            .unwrap();
        let req = format!(
            "{method} {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n\
             Content-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len()
        );
        stream.write_all(req.as_bytes()).await.unwrap();
        let mut response = String::new();
        stream.read_to_string(&mut response).await.unwrap();
        let status = response
            .lines()
            .next()
            .and_then(|l| l.split_whitespace().nth(1))
            .and_then(|s| s.parse::<u16>().ok())
            .unwrap_or(0);
        let resp_body = response.split("\r\n\r\n").nth(1).unwrap_or("").to_string();
        (status, resp_body)
    }

    fn default_load_body() -> String {
        serde_json::json!({
            "instrument_id": "1301.TSE",
            "start_date": "2024-01-04",
            "end_date": "2024-01-31",
            "granularity": "Trade",
        })
        .to_string()
    }

    // ── /api/replay/load ──────────────────────────────────────────────────────

    #[tokio::test]
    async fn replay_load_returns_200_when_engine_acknowledges() {
        let (ws_listener, ws_addr) = bind_ws_loopback().await;
        let mock = spawn_mock_engine_load(ws_listener, 0, 1234, None, false);
        let conn = connect_engine(ws_addr).await;
        let (engine_tx, engine_rx) = watch::channel(Some(conn));
        let state = Arc::new(
            ReplayApiState::new(engine_rx, engine_client::dto::AppMode::Replay)
                .with_load_timeout(Duration::from_secs(5)),
        );
        let port = spawn_test_http_server(Arc::clone(&state)).await;
        tokio::time::sleep(Duration::from_millis(10)).await;

        let (status, body) =
            http_request(port, "POST", "/api/replay/load", &default_load_body()).await;
        assert_eq!(status, 200, "expected 200; body={body}");
        let json: serde_json::Value = serde_json::from_str(&body).unwrap();
        assert_eq!(json["status"], "ok");
        assert_eq!(json["trades_loaded"].as_u64(), Some(1234));
        assert_eq!(json["bars_loaded"].as_u64(), Some(0));
        drop(engine_tx);
        mock.await.expect("mock server panicked");
    }

    /// M-2 (R2 review-fix R2): `ReplayDataLoaded.strategy_id` を Option<String> として
    /// `AutoGenerateReplayPanes` コマンドにそのまま伝搬する（情報損失しない）。
    ///
    /// engine が `null` を送れば None、`"strat-001"` を送れば Some("strat-001") に
    /// なることを検証する。
    #[tokio::test]
    async fn replay_load_propagates_strategy_id_to_auto_generate_command() {
        // Case A: strategy_id = null (単独 LoadReplayData 経路)
        {
            let (ws_listener, ws_addr) = bind_ws_loopback().await;
            let mock_a = spawn_mock_engine_load_with_strategy_id(
                ws_listener,
                0,
                1234,
                None,
                false,
                serde_json::Value::Null,
            );
            let conn = connect_engine(ws_addr).await;
            let (engine_tx, engine_rx) = watch::channel(Some(conn));
            let state = Arc::new(
                ReplayApiState::new(engine_rx, engine_client::dto::AppMode::Replay)
                    .with_load_timeout(Duration::from_secs(5)),
            );
            let (cmd_tx, mut cmd_rx) = mpsc::channel::<ControlApiCommand>(8);
            state.set_control_tx(cmd_tx).await;
            let port = spawn_test_http_server(Arc::clone(&state)).await;
            tokio::time::sleep(Duration::from_millis(10)).await;

            let (status, _) =
                http_request(port, "POST", "/api/replay/load", &default_load_body()).await;
            assert_eq!(status, 200);

            let cmd = tokio::time::timeout(Duration::from_secs(2), cmd_rx.recv())
                .await
                .expect("AutoGenerateReplayPanes timeout")
                .expect("channel closed");
            match cmd {
                ControlApiCommand::AutoGenerateReplayPanes {
                    instrument_id,
                    strategy_id,
                } => {
                    assert_eq!(instrument_id, "1301.TSE");
                    assert_eq!(
                        strategy_id, None,
                        "null strategy_id should propagate as None"
                    );
                }
                other => panic!("unexpected command: {other:?}"),
            }
            drop(engine_tx);
            mock_a.await.expect("mock server (Case A) panicked");
        }

        // Case B: strategy_id = "strat-001" (StartEngine 経路)
        {
            let (ws_listener, ws_addr) = bind_ws_loopback().await;
            let mock_b = spawn_mock_engine_load_with_strategy_id(
                ws_listener,
                0,
                1234,
                None,
                false,
                serde_json::Value::String("strat-001".to_string()),
            );
            let conn = connect_engine(ws_addr).await;
            let (engine_tx, engine_rx) = watch::channel(Some(conn));
            let state = Arc::new(
                ReplayApiState::new(engine_rx, engine_client::dto::AppMode::Replay)
                    .with_load_timeout(Duration::from_secs(5)),
            );
            let (cmd_tx, mut cmd_rx) = mpsc::channel::<ControlApiCommand>(8);
            state.set_control_tx(cmd_tx).await;
            let port = spawn_test_http_server(Arc::clone(&state)).await;
            tokio::time::sleep(Duration::from_millis(10)).await;

            let (status, _) =
                http_request(port, "POST", "/api/replay/load", &default_load_body()).await;
            assert_eq!(status, 200);

            let cmd = tokio::time::timeout(Duration::from_secs(2), cmd_rx.recv())
                .await
                .expect("AutoGenerateReplayPanes timeout")
                .expect("channel closed");
            match cmd {
                ControlApiCommand::AutoGenerateReplayPanes { strategy_id, .. } => {
                    assert_eq!(strategy_id.as_deref(), Some("strat-001"));
                }
                other => panic!("unexpected command: {other:?}"),
            }
            drop(engine_tx);
            mock_b.await.expect("mock server (Case B) panicked");
        }
    }

    #[tokio::test]
    async fn replay_load_rejects_invalid_json() {
        let (_engine_tx, engine_rx) = watch::channel::<Option<Arc<EngineConnection>>>(None);
        let state = Arc::new(ReplayApiState::new(
            engine_rx,
            engine_client::dto::AppMode::Replay,
        ));
        let port = spawn_test_http_server(state).await;
        tokio::time::sleep(Duration::from_millis(10)).await;
        let (status, body) =
            http_request(port, "POST", "/api/replay/load", "{not valid json").await;
        assert_eq!(status, 400, "expected 400; body={body}");
    }

    #[tokio::test]
    async fn replay_load_rejects_unknown_granularity() {
        let (_engine_tx, engine_rx) = watch::channel::<Option<Arc<EngineConnection>>>(None);
        let state = Arc::new(ReplayApiState::new(
            engine_rx,
            engine_client::dto::AppMode::Replay,
        ));
        let port = spawn_test_http_server(state).await;
        tokio::time::sleep(Duration::from_millis(10)).await;
        let body = serde_json::json!({
            "instrument_id": "1301.TSE",
            "start_date": "2024-01-04",
            "end_date": "2024-01-31",
            "granularity": "Hourly",
        })
        .to_string();
        let (status, resp) = http_request(port, "POST", "/api/replay/load", &body).await;
        assert_eq!(status, 400, "expected 400; body={resp}");
    }

    #[tokio::test]
    async fn replay_load_rejects_invalid_date() {
        let (_engine_tx, engine_rx) = watch::channel::<Option<Arc<EngineConnection>>>(None);
        let state = Arc::new(ReplayApiState::new(
            engine_rx,
            engine_client::dto::AppMode::Replay,
        ));
        let port = spawn_test_http_server(state).await;
        tokio::time::sleep(Duration::from_millis(10)).await;
        let body = serde_json::json!({
            "instrument_id": "1301.TSE",
            "start_date": "2024/01/04",
            "end_date": "2024-01-31",
            "granularity": "Trade",
        })
        .to_string();
        let (status, _) = http_request(port, "POST", "/api/replay/load", &body).await;
        assert_eq!(status, 400);
    }

    /// H-A: カレンダー検証（月日範囲・閏年）。
    #[tokio::test]
    async fn replay_load_rejects_calendar_invalid_dates() {
        let (_engine_tx, engine_rx) = watch::channel::<Option<Arc<EngineConnection>>>(None);
        let state = Arc::new(ReplayApiState::new(
            engine_rx,
            engine_client::dto::AppMode::Replay,
        ));
        let port = spawn_test_http_server(state).await;
        tokio::time::sleep(Duration::from_millis(10)).await;

        let invalid_cases = [
            ("2024-13-01", "2024-01-31"), // 月 13
            ("2024-01-32", "2024-01-31"), // 日 32
            ("2024-02-30", "2024-02-28"), // 2 月に 30 日は無い
            ("2023-02-29", "2023-03-01"), // 非閏年の 2/29
        ];
        for (start, end) in invalid_cases {
            let body = serde_json::json!({
                "instrument_id": "1301.TSE",
                "start_date": start,
                "end_date": end,
                "granularity": "Trade",
            })
            .to_string();
            let (status, resp_body) = http_request(port, "POST", "/api/replay/load", &body).await;
            assert_eq!(
                status, 400,
                "expected 400 for start={start} end={end}; got status={status}, body={resp_body}"
            );
        }
    }

    /// H-B: broadcast が `Lagged` を起こした場合に 503 + body `{"error":"events lagged"}`
    /// を返す。
    #[tokio::test]
    async fn replay_load_returns_503_on_broadcast_lagged() {
        // BROADCAST_CAPACITY (engine-client::connection: 512) を超える数の
        // ダミーイベントを ReplayDataLoaded より前に送り、receiver の lag を強制する。
        let (ws_listener, ws_addr) = bind_ws_loopback().await;

        tokio::spawn(async move {
            let (tcp, _) = ws_listener.accept().await.unwrap();
            let mut ws = accept_async(tcp).await.unwrap();
            let _ = ws.next().await; // Hello
            ws_send_ready(&mut ws).await;

            // Wait for LoadReplayData
            let cmd_msg = ws.next().await;
            let _request_id: Option<String> = if let Some(Ok(m)) = cmd_msg {
                let text = m.into_text().unwrap();
                let v: serde_json::Value = serde_json::from_str(&text).unwrap();
                v["request_id"].as_str().map(ToOwned::to_owned)
            } else {
                None
            };

            // Flood with > BROADCAST_CAPACITY filler events to force Lagged.
            // EngineEvent::Disconnected は handler の `Ok(_) => continue` 分岐で
            // 単純に skip されるので副作用が少ないフィラーとして使う。
            // BROADCAST_CAPACITY = 512 を大きく超える数を `feed` で一気に積み、
            // 最後にまとめて `flush` することで receiver が Lagged を起こすほどの
            // backpressure を作る。
            for i in 0..20_000_u32 {
                let evt = serde_json::json!({
                    "event": "Disconnected",
                    "venue": "filler",
                    "ticker": format!("F{i}"),
                    "stream": "k",
                    "market": "",
                    "reason": null,
                });
                ws.feed(Message::Text(evt.to_string().into()))
                    .await
                    .unwrap();
            }
            ws.flush().await.unwrap();
            // 最後に ReplayDataLoaded を送るが、Lagged が先に発火するはず
            let evt = serde_json::json!({
                "event": "ReplayDataLoaded",
                "strategy_id": "",
                "bars_loaded": 0,
                "trades_loaded": 0,
                "ts_event_ms": 1_700_000_000_000_i64,
            });
            ws.send(Message::Text(evt.to_string().into()))
                .await
                .unwrap();
            tokio::time::sleep(Duration::from_millis(500)).await;
        });

        let conn = connect_engine(ws_addr).await;
        let (engine_tx, engine_rx) = watch::channel(Some(conn));
        let state = Arc::new(
            ReplayApiState::new(engine_rx, engine_client::dto::AppMode::Replay)
                .with_load_timeout(Duration::from_secs(5)),
        );
        let port = spawn_test_http_server(Arc::clone(&state)).await;
        // Receiver が drain しないよう、HTTP リクエストを投げる前に少しだけ待ち、
        // mock 側がフィラーを送り始めてから処理させる。
        tokio::time::sleep(Duration::from_millis(50)).await;

        let (status, body) =
            http_request(port, "POST", "/api/replay/load", &default_load_body()).await;
        assert_eq!(status, 503, "Lagged should map to 503; body={body}");
        let json: serde_json::Value =
            serde_json::from_str(&body).unwrap_or(serde_json::Value::Null);
        assert_eq!(json["error"], "events lagged");
        drop(engine_tx);
    }

    #[tokio::test]
    async fn replay_load_rejects_empty_instrument_id() {
        let (_engine_tx, engine_rx) = watch::channel::<Option<Arc<EngineConnection>>>(None);
        let state = Arc::new(ReplayApiState::new(
            engine_rx,
            engine_client::dto::AppMode::Replay,
        ));
        let port = spawn_test_http_server(state).await;
        tokio::time::sleep(Duration::from_millis(10)).await;
        let body = serde_json::json!({
            "instrument_id": "",
            "start_date": "2024-01-04",
            "end_date": "2024-01-31",
            "granularity": "Trade",
        })
        .to_string();
        let (status, _) = http_request(port, "POST", "/api/replay/load", &body).await;
        assert_eq!(status, 400);
    }

    #[tokio::test]
    async fn replay_load_returns_400_on_mode_mismatch_error() {
        let (ws_listener, ws_addr) = bind_ws_loopback().await;
        let mock = spawn_mock_engine_load(
            ws_listener,
            0,
            0,
            Some(("mode_mismatch".to_string(), "wrong mode".to_string())),
            false,
        );
        let conn = connect_engine(ws_addr).await;
        let (engine_tx, engine_rx) = watch::channel(Some(conn));
        let state = Arc::new(
            ReplayApiState::new(engine_rx, engine_client::dto::AppMode::Replay)
                .with_load_timeout(Duration::from_secs(5)),
        );
        let port = spawn_test_http_server(Arc::clone(&state)).await;
        tokio::time::sleep(Duration::from_millis(10)).await;

        let (status, body) =
            http_request(port, "POST", "/api/replay/load", &default_load_body()).await;
        assert_eq!(status, 400, "mode_mismatch should map to 400; body={body}");
        let json: serde_json::Value = serde_json::from_str(&body).unwrap();
        assert_eq!(json["code"], "mode_mismatch");
        drop(engine_tx);
        mock.await.expect("mock server panicked");
    }

    #[tokio::test]
    async fn replay_load_returns_504_on_timeout() {
        let (ws_listener, ws_addr) = bind_ws_loopback().await;
        let mock = spawn_mock_engine_load(ws_listener, 0, 0, None, true); // silent
        let conn = connect_engine(ws_addr).await;
        let (engine_tx, engine_rx) = watch::channel(Some(conn));
        let state = Arc::new(
            ReplayApiState::new(engine_rx, engine_client::dto::AppMode::Replay)
                .with_load_timeout(Duration::from_millis(150)),
        );
        let port = spawn_test_http_server(Arc::clone(&state)).await;
        tokio::time::sleep(Duration::from_millis(10)).await;
        let (status, body) =
            http_request(port, "POST", "/api/replay/load", &default_load_body()).await;
        assert_eq!(status, 504, "timeout should map to 504; body={body}");
        let json: serde_json::Value = serde_json::from_str(&body).unwrap();
        assert_eq!(json["error"], "timeout");
        drop(engine_tx);
        mock.await.expect("mock server panicked");
    }

    #[tokio::test]
    async fn replay_load_rejected_in_live_mode() {
        let (_engine_tx, engine_rx) = watch::channel::<Option<Arc<EngineConnection>>>(None);
        let state = Arc::new(ReplayApiState::new(
            engine_rx,
            engine_client::dto::AppMode::Live,
        ));
        let port = spawn_test_http_server(state).await;
        tokio::time::sleep(Duration::from_millis(10)).await;
        let (status, _) =
            http_request(port, "POST", "/api/replay/load", &default_load_body()).await;
        assert_eq!(status, 400, "live mode must reject /api/replay/load early");
    }

    // ── /api/replay/portfolio ────────────────────────────────────────────────

    #[tokio::test]
    async fn replay_portfolio_returns_not_ready_before_fill() {
        let (_engine_tx, engine_rx) = watch::channel::<Option<Arc<EngineConnection>>>(None);
        let state = Arc::new(ReplayApiState::new(
            engine_rx,
            engine_client::dto::AppMode::Replay,
        ));
        let port = spawn_test_http_server(state).await;
        tokio::time::sleep(Duration::from_millis(10)).await;
        let (status, body) = http_request(port, "GET", "/api/replay/portfolio", "").await;
        assert_eq!(
            status, 200,
            "should return 200 before any fill; body={body}"
        );
        let json: serde_json::Value = serde_json::from_str(&body).unwrap();
        assert_eq!(json["status"], "not_ready");
    }

    #[tokio::test]
    async fn replay_portfolio_returns_cached_snapshot_after_update() {
        let (_engine_tx, engine_rx) = watch::channel::<Option<Arc<EngineConnection>>>(None);
        let state = Arc::new(ReplayApiState::new(
            engine_rx,
            engine_client::dto::AppMode::Replay,
        ));
        state.update_replay_portfolio(
            "strat-001".to_string(),
            "980000".to_string(),
            "980000".to_string(),
            "990000".to_string(),
            1_704_268_800_000,
        );
        let port = spawn_test_http_server(Arc::clone(&state)).await;
        tokio::time::sleep(Duration::from_millis(10)).await;
        let (status, body) = http_request(port, "GET", "/api/replay/portfolio", "").await;
        assert_eq!(
            status, 200,
            "should return 200 with cached data; body={body}"
        );
        let json: serde_json::Value = serde_json::from_str(&body).unwrap();
        assert_eq!(json["status"], "ok");
        assert_eq!(json["cash"], "980000");
        assert_eq!(json["equity"], "990000");
        assert_eq!(json["strategy_id"], "strat-001");
    }

    #[tokio::test]
    async fn replay_portfolio_rejected_in_live_mode() {
        let (_engine_tx, engine_rx) = watch::channel::<Option<Arc<EngineConnection>>>(None);
        let state = Arc::new(ReplayApiState::new(
            engine_rx,
            engine_client::dto::AppMode::Live,
        ));
        let port = spawn_test_http_server(state).await;
        tokio::time::sleep(Duration::from_millis(10)).await;
        let (status, _) = http_request(port, "GET", "/api/replay/portfolio", "").await;
        assert_eq!(status, 400);
    }

    // ── /api/replay/order ────────────────────────────────────────────────────

    #[tokio::test]
    async fn replay_order_forwards_submit_with_replay_venue() {
        let (ws_listener, ws_addr) = bind_ws_loopback().await;
        let cmd_rx = spawn_mock_engine_capture(ws_listener);
        let conn = connect_engine(ws_addr).await;
        let (engine_tx, engine_rx) = watch::channel(Some(conn));
        let state = Arc::new(ReplayApiState::new(
            engine_rx,
            engine_client::dto::AppMode::Replay,
        ));
        let port = spawn_test_http_server(Arc::clone(&state)).await;
        tokio::time::sleep(Duration::from_millis(10)).await;

        let body = serde_json::json!({
            "client_order_id": "replay-cid-001",
            "instrument_id": "1301.TSE",
            "order_side": "BUY",
            "order_type": "MARKET",
            "quantity": "100",
            "price": null,
            "trigger_price": null,
            "trigger_type": null,
            "time_in_force": "DAY",
            "expire_time_ns": null,
            "post_only": false,
            "reduce_only": false,
            "tags": [],
        })
        .to_string();

        let (status, resp_body) = http_request(port, "POST", "/api/replay/order", &body).await;
        assert_eq!(
            status, 202,
            "replay/order should ack with 202; body={resp_body}"
        );

        let captured = cmd_rx.await.expect("mock engine should capture command");
        assert_eq!(captured["op"], "SubmitOrder");
        assert_eq!(captured["venue"], "replay");
        assert_eq!(captured["order"]["client_order_id"], "replay-cid-001");
        drop(engine_tx);
    }

    #[tokio::test]
    async fn replay_order_rejected_in_live_mode() {
        let (_engine_tx, engine_rx) = watch::channel::<Option<Arc<EngineConnection>>>(None);
        let state = Arc::new(ReplayApiState::new(
            engine_rx,
            engine_client::dto::AppMode::Live,
        ));
        let port = spawn_test_http_server(state).await;
        tokio::time::sleep(Duration::from_millis(10)).await;
        let (status, _) = http_request(port, "POST", "/api/replay/order", "{}").await;
        assert_eq!(status, 400);
    }

    #[tokio::test]
    async fn replay_order_rejects_invalid_body() {
        let (_engine_tx, engine_rx) = watch::channel::<Option<Arc<EngineConnection>>>(None);
        let state = Arc::new(ReplayApiState::new(
            engine_rx,
            engine_client::dto::AppMode::Replay,
        ));
        let port = spawn_test_http_server(state).await;
        tokio::time::sleep(Duration::from_millis(10)).await;
        let (status, _) = http_request(port, "POST", "/api/replay/order", "not json").await;
        assert_eq!(status, 400);
    }

    // ── N1.14: MAX_REPLAY_INSTRUMENTS & reload ────────────────────────────────

    fn load_body_for(instrument_id: &str) -> String {
        serde_json::json!({
            "instrument_id": instrument_id,
            "start_date": "2024-01-04",
            "end_date": "2024-01-31",
            "granularity": "Trade",
        })
        .to_string()
    }

    /// Helper: spawn a mock engine that responds to N successive LoadReplayData
    /// commands with ReplayDataLoaded, then sleeps.
    fn spawn_mock_engine_multi_load(listener: TcpListener, responses: usize) {
        tokio::spawn(async move {
            let (tcp, _) = listener.accept().await.unwrap();
            let mut ws = accept_async(tcp).await.unwrap();
            let _ = ws.next().await; // Hello
            ws_send_ready(&mut ws).await;

            for _ in 0..responses {
                // Wait for a LoadReplayData command
                let msg = ws.next().await;
                if msg.is_none() {
                    return;
                }
                let evt = serde_json::json!({
                    "event": "ReplayDataLoaded",
                    "strategy_id": "",
                    "bars_loaded": 0u64,
                    "trades_loaded": 0u64,
                    "ts_event_ms": 1_700_000_000_000_i64,
                });
                ws.send(Message::Text(evt.to_string().into()))
                    .await
                    .unwrap();
            }
            tokio::time::sleep(Duration::from_secs(10)).await;
        });
    }

    /// 5th distinct instrument is rejected with HTTP 400 and `max_instruments_exceeded`.
    #[tokio::test]
    async fn replay_load_rejects_fifth_instrument() {
        let (ws_listener, ws_addr) = bind_ws_loopback().await;
        // Need 4 successful loads before the 5th is rejected.
        spawn_mock_engine_multi_load(ws_listener, 4);

        let conn = connect_engine(ws_addr).await;
        let (_engine_tx, engine_rx) = watch::channel(Some(conn));
        let state = Arc::new(
            ReplayApiState::new(engine_rx, engine_client::dto::AppMode::Replay)
                .with_load_timeout(Duration::from_secs(5)),
        );
        let port = spawn_test_http_server(Arc::clone(&state)).await;
        tokio::time::sleep(Duration::from_millis(10)).await;

        // Load 4 distinct instruments — all should succeed.
        for i in 1..=4 {
            let (status, body) = http_request(
                port,
                "POST",
                "/api/replay/load",
                &load_body_for(&format!("{i}30{i}.TSE")),
            )
            .await;
            assert_eq!(status, 200, "instrument {i} should succeed; body={body}");
        }

        // 5th distinct instrument must fail.
        let (status, body) =
            http_request(port, "POST", "/api/replay/load", &load_body_for("9999.TSE")).await;
        assert_eq!(
            status, 400,
            "5th distinct instrument must be rejected; body={body}"
        );
        let json: serde_json::Value = serde_json::from_str(&body).unwrap();
        assert_eq!(json["error"], "max_instruments_exceeded");
        assert_eq!(json["max"].as_u64(), Some(4));
    }

    /// Reloading the same instrument does not count as a new entry.
    #[tokio::test]
    async fn replay_load_allows_reload_same_instrument() {
        let (ws_listener, ws_addr) = bind_ws_loopback().await;
        // Two loads for the same instrument.
        spawn_mock_engine_multi_load(ws_listener, 2);

        let conn = connect_engine(ws_addr).await;
        let (_engine_tx, engine_rx) = watch::channel(Some(conn));
        let state = Arc::new(
            ReplayApiState::new(engine_rx, engine_client::dto::AppMode::Replay)
                .with_load_timeout(Duration::from_secs(5)),
        );
        let port = spawn_test_http_server(Arc::clone(&state)).await;
        tokio::time::sleep(Duration::from_millis(10)).await;

        let (status1, body1) =
            http_request(port, "POST", "/api/replay/load", &load_body_for("1301.TSE")).await;
        assert_eq!(status1, 200, "1st load should succeed; body={body1}");

        // Reload the same instrument — must not be rejected.
        let (status2, body2) =
            http_request(port, "POST", "/api/replay/load", &load_body_for("1301.TSE")).await;
        assert_eq!(
            status2, 200,
            "reload of same instrument must succeed; body={body2}"
        );

        // loaded_instruments count must remain 1 (not 2).
        let count = state.loaded_instruments.lock().await.len();
        assert_eq!(
            count, 1,
            "loaded_instruments should have 1 entry after reload"
        );
    }

    // ── N1.17: /api/replay/start ─────────────────────────────────────────────

    fn default_start_body() -> String {
        serde_json::json!({
            "instrument_id": "7203.TSE",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "granularity": "Daily",
            "strategy_id": "buy-and-hold",
            "initial_cash": "1000000",
            "strategy_file": "docs/example/buy_and_hold.py",
        })
        .to_string()
    }

    /// Mock engine: handshake, then on `StartEngine` respond with `EngineStarted`.
    fn spawn_mock_engine_start(
        listener: TcpListener,
        account_id: String,
        silent: bool,
    ) -> tokio::task::JoinHandle<()> {
        tokio::spawn(async move {
            let (tcp, _) = listener.accept().await.unwrap();
            let mut ws = accept_async(tcp).await.unwrap();
            let _ = ws.next().await; // Hello
            ws_send_ready(&mut ws).await;

            let cmd_msg = ws.next().await;
            let strategy_id: String = if let Some(Ok(m)) = cmd_msg {
                let text = m.into_text().unwrap();
                let v: serde_json::Value = serde_json::from_str(&text).unwrap();
                v["strategy_id"].as_str().unwrap_or("").to_string()
            } else {
                String::new()
            };

            if silent {
                tokio::time::sleep(Duration::from_secs(10)).await;
                return;
            }

            let evt = serde_json::json!({
                "event": "EngineStarted",
                "strategy_id": strategy_id,
                "account_id": account_id,
                "ts_event_ms": 1_704_067_200_000_i64,
            });
            ws.send(Message::Text(evt.to_string().into()))
                .await
                .unwrap();
            tokio::time::sleep(Duration::from_millis(200)).await;
        })
    }

    #[tokio::test]
    async fn replay_start_returns_202_when_engine_starts() {
        let (ws_listener, ws_addr) = bind_ws_loopback().await;
        let mock =
            spawn_mock_engine_start(ws_listener, "replay-REPLAY-BUYANDHO".to_string(), false);
        let conn = connect_engine(ws_addr).await;
        let (engine_tx, engine_rx) = watch::channel(Some(conn));
        let state = Arc::new(
            ReplayApiState::new(engine_rx, engine_client::dto::AppMode::Replay)
                .with_start_timeout(Duration::from_secs(5)),
        );
        let port = spawn_test_http_server(Arc::clone(&state)).await;
        tokio::time::sleep(Duration::from_millis(10)).await;

        let (status, body) =
            http_request(port, "POST", "/api/replay/start", &default_start_body()).await;
        assert_eq!(status, 202, "expected 202; body={body}");
        let json: serde_json::Value = serde_json::from_str(&body).unwrap();
        assert_eq!(json["status"], "started");
        assert_eq!(json["strategy_id"], "buy-and-hold");
        assert_eq!(json["account_id"], "replay-REPLAY-BUYANDHO");
        drop(engine_tx);
        mock.await.expect("mock server panicked");
    }

    #[tokio::test]
    async fn replay_start_rejected_in_live_mode() {
        let (_engine_tx, engine_rx) = watch::channel::<Option<Arc<EngineConnection>>>(None);
        let state = Arc::new(ReplayApiState::new(
            engine_rx,
            engine_client::dto::AppMode::Live,
        ));
        let port = spawn_test_http_server(state).await;
        tokio::time::sleep(Duration::from_millis(10)).await;
        let (status, _) =
            http_request(port, "POST", "/api/replay/start", &default_start_body()).await;
        assert_eq!(status, 400, "live mode must reject /api/replay/start");
    }

    #[tokio::test]
    async fn replay_start_rejects_invalid_json() {
        let (_engine_tx, engine_rx) = watch::channel::<Option<Arc<EngineConnection>>>(None);
        let state = Arc::new(ReplayApiState::new(
            engine_rx,
            engine_client::dto::AppMode::Replay,
        ));
        let port = spawn_test_http_server(state).await;
        tokio::time::sleep(Duration::from_millis(10)).await;
        let (status, _) = http_request(port, "POST", "/api/replay/start", "{not json").await;
        assert_eq!(status, 400);
    }

    #[tokio::test]
    async fn replay_start_rejects_empty_instrument_id() {
        let (_engine_tx, engine_rx) = watch::channel::<Option<Arc<EngineConnection>>>(None);
        let state = Arc::new(ReplayApiState::new(
            engine_rx,
            engine_client::dto::AppMode::Replay,
        ));
        let port = spawn_test_http_server(state).await;
        tokio::time::sleep(Duration::from_millis(10)).await;
        let body = serde_json::json!({
            "instrument_id": "",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "granularity": "Daily",
            "strategy_id": "buy-and-hold",
            "initial_cash": "1000000",
        })
        .to_string();
        let (status, _) = http_request(port, "POST", "/api/replay/start", &body).await;
        assert_eq!(status, 400);
    }

    #[tokio::test]
    async fn replay_start_rejects_empty_strategy_id() {
        let (_engine_tx, engine_rx) = watch::channel::<Option<Arc<EngineConnection>>>(None);
        let state = Arc::new(ReplayApiState::new(
            engine_rx,
            engine_client::dto::AppMode::Replay,
        ));
        let port = spawn_test_http_server(state).await;
        tokio::time::sleep(Duration::from_millis(10)).await;
        let body = serde_json::json!({
            "instrument_id": "7203.TSE",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "granularity": "Daily",
            "strategy_id": "",
            "initial_cash": "1000000",
        })
        .to_string();
        let (status, _) = http_request(port, "POST", "/api/replay/start", &body).await;
        assert_eq!(status, 400);
    }

    #[tokio::test]
    async fn replay_start_returns_504_on_timeout() {
        let (ws_listener, ws_addr) = bind_ws_loopback().await;
        let mock = spawn_mock_engine_start(ws_listener, String::new(), true); // silent
        let conn = connect_engine(ws_addr).await;
        let (engine_tx, engine_rx) = watch::channel(Some(conn));
        let state = Arc::new(
            ReplayApiState::new(engine_rx, engine_client::dto::AppMode::Replay)
                .with_start_timeout(Duration::from_millis(150)),
        );
        let port = spawn_test_http_server(Arc::clone(&state)).await;
        tokio::time::sleep(Duration::from_millis(10)).await;
        let (status, body) =
            http_request(port, "POST", "/api/replay/start", &default_start_body()).await;
        assert_eq!(status, 504, "timeout should map to 504; body={body}");
        let json: serde_json::Value = serde_json::from_str(&body).unwrap();
        assert_eq!(json["error"], "timeout");
        drop(engine_tx);
        mock.await.expect("mock server panicked");
    }

    #[tokio::test]
    async fn replay_start_returns_503_on_engine_error() {
        let (ws_listener, ws_addr) = bind_ws_loopback().await;
        tokio::spawn(async move {
            let (tcp, _) = ws_listener.accept().await.unwrap();
            let mut ws = accept_async(tcp).await.unwrap();
            let _ = ws.next().await; // Hello
            ws_send_ready(&mut ws).await;

            // Receive StartEngine command
            let cmd_msg = ws.next().await;
            let strategy_id: String = if let Some(Ok(m)) = cmd_msg {
                let text = m.into_text().unwrap();
                let v: serde_json::Value = serde_json::from_str(&text).unwrap();
                v["strategy_id"].as_str().unwrap_or("").to_string()
            } else {
                String::new()
            };

            // Respond with EngineError
            let evt = serde_json::json!({
                "event": "EngineError",
                "code": "strategy_error",
                "message": "strategy failed to start",
                "strategy_id": strategy_id,
            });
            ws.send(Message::Text(evt.to_string().into()))
                .await
                .unwrap();
            tokio::time::sleep(Duration::from_millis(200)).await;
        });

        let conn = connect_engine(ws_addr).await;
        let (engine_tx, engine_rx) = watch::channel(Some(conn));
        let state = Arc::new(
            ReplayApiState::new(engine_rx, engine_client::dto::AppMode::Replay)
                .with_start_timeout(Duration::from_secs(5)),
        );
        let port = spawn_test_http_server(Arc::clone(&state)).await;
        tokio::time::sleep(Duration::from_millis(10)).await;

        let (status, body) =
            http_request(port, "POST", "/api/replay/start", &default_start_body()).await;
        assert_eq!(status, 503, "engine error should map to 503; body={body}");
        drop(engine_tx);
    }

    #[tokio::test]
    async fn replay_start_forwards_start_engine_command() {
        let (ws_listener, ws_addr) = bind_ws_loopback().await;
        let cmd_rx = spawn_mock_engine_capture(ws_listener);
        let conn = connect_engine(ws_addr).await;
        let (engine_tx, engine_rx) = watch::channel(Some(conn));
        let state = Arc::new(
            ReplayApiState::new(engine_rx, engine_client::dto::AppMode::Replay)
                .with_start_timeout(Duration::from_secs(5)),
        );
        let port = spawn_test_http_server(Arc::clone(&state)).await;
        tokio::time::sleep(Duration::from_millis(10)).await;

        // Fire and forget — mock engine won't reply, so we expect 504 after timeout,
        // but we can still inspect the captured command before that.
        tokio::spawn(async move {
            http_request(port, "POST", "/api/replay/start", &default_start_body()).await
        });

        let captured = tokio::time::timeout(Duration::from_secs(5), cmd_rx)
            .await
            .expect("mock engine capture timed out")
            .expect("channel closed");

        assert_eq!(captured["op"], "StartEngine");
        assert_eq!(captured["engine"], "Backtest");
        assert_eq!(captured["strategy_id"], "buy-and-hold");
        assert_eq!(captured["config"]["instrument_id"], "7203.TSE");
        assert_eq!(captured["config"]["granularity"], "Daily");
        assert_eq!(captured["config"]["initial_cash"], "1000000");
        drop(engine_tx);
    }

    #[tokio::test]
    async fn replay_start_forwards_strategy_file_in_config() {
        let (ws_listener, ws_addr) = bind_ws_loopback().await;
        let cmd_rx = spawn_mock_engine_capture(ws_listener);
        let conn = connect_engine(ws_addr).await;
        let (engine_tx, engine_rx) = watch::channel(Some(conn));
        let state = Arc::new(
            ReplayApiState::new(engine_rx, engine_client::dto::AppMode::Replay)
                .with_start_timeout(Duration::from_secs(5)),
        );
        let port = spawn_test_http_server(Arc::clone(&state)).await;
        tokio::time::sleep(Duration::from_millis(10)).await;

        let body = serde_json::json!({
            "instrument_id": "1301.TSE",
            "start_date": "2024-01-01",
            "end_date": "2024-03-31",
            "granularity": "Daily",
            "strategy_id": "user-defined",
            "initial_cash": "1000000",
            "strategy_file": "examples/strategies/buy_and_hold.py",
            "strategy_init_kwargs": {"instrument_id": "1301.TSE", "lot_size": 100},
        })
        .to_string();

        tokio::spawn(async move { http_request(port, "POST", "/api/replay/start", &body).await });

        let captured = tokio::time::timeout(Duration::from_secs(5), cmd_rx)
            .await
            .expect("mock engine capture timed out")
            .expect("channel closed");

        assert_eq!(
            captured["config"]["strategy_file"],
            "examples/strategies/buy_and_hold.py"
        );
        assert_eq!(captured["config"]["strategy_init_kwargs"]["lot_size"], 100);
        drop(engine_tx);
    }

    /// H-1 / M-6: `/api/replay/load` must reject non-object `strategy_init_kwargs`
    /// (e.g. JSON array) with HTTP 400 before the IPC command is forwarded.
    #[tokio::test]
    async fn replay_load_rejects_array_strategy_init_kwargs() {
        let (ws_listener, ws_addr) = bind_ws_loopback().await;
        let _capture = spawn_mock_engine_capture(ws_listener);
        let conn = connect_engine(ws_addr).await;
        let (engine_tx, engine_rx) = watch::channel(Some(conn));
        let state = Arc::new(
            ReplayApiState::new(engine_rx, engine_client::dto::AppMode::Replay)
                .with_load_timeout(Duration::from_secs(5)),
        );
        let port = spawn_test_http_server(Arc::clone(&state)).await;
        tokio::time::sleep(Duration::from_millis(10)).await;

        let body = serde_json::json!({
            "instrument_id": "1301.TSE",
            "start_date": "2024-01-01",
            "end_date": "2024-03-31",
            "granularity": "Trade",
            "strategy_init_kwargs": [1, 2, 3],
        })
        .to_string();

        let (status, _) = http_request(port, "POST", "/api/replay/load", &body).await;
        assert_eq!(
            status, 400,
            "array strategy_init_kwargs should be rejected at /api/replay/load"
        );
        drop(engine_tx);
    }

    #[tokio::test]
    async fn replay_start_rejects_array_strategy_init_kwargs() {
        let (ws_listener, ws_addr) = bind_ws_loopback().await;
        let _capture = spawn_mock_engine_capture(ws_listener);
        let conn = connect_engine(ws_addr).await;
        let (engine_tx, engine_rx) = watch::channel(Some(conn));
        let state = Arc::new(
            ReplayApiState::new(engine_rx, engine_client::dto::AppMode::Replay)
                .with_start_timeout(Duration::from_secs(5)),
        );
        let port = spawn_test_http_server(Arc::clone(&state)).await;
        tokio::time::sleep(Duration::from_millis(10)).await;

        let body = serde_json::json!({
            "instrument_id": "1301.TSE",
            "start_date": "2024-01-01",
            "end_date": "2024-03-31",
            "granularity": "Daily",
            "strategy_id": "user-defined",
            "initial_cash": "1000000",
            "strategy_init_kwargs": [1, 2, 3],
        })
        .to_string();

        let (status, _) = http_request(port, "POST", "/api/replay/start", &body).await;
        assert_eq!(status, 400, "array strategy_init_kwargs should be rejected");
        drop(engine_tx);
    }

    #[tokio::test]
    async fn replay_start_rejects_missing_strategy_file() {
        let (_engine_tx, engine_rx) = watch::channel::<Option<Arc<EngineConnection>>>(None);
        let state = Arc::new(ReplayApiState::new(
            engine_rx,
            engine_client::dto::AppMode::Replay,
        ));
        let port = spawn_test_http_server(state).await;
        tokio::time::sleep(Duration::from_millis(10)).await;

        let body = serde_json::json!({
            "instrument_id": "7203.TSE",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "granularity": "Daily",
            "strategy_id": "buy-and-hold",
            "initial_cash": "1000000",
        })
        .to_string();
        let (status, body_str) = http_request(port, "POST", "/api/replay/start", &body).await;
        assert_eq!(status, 400, "missing strategy_file must return 400; body={body_str}");
        let json: serde_json::Value = serde_json::from_str(&body_str).unwrap();
        assert!(
            json["error"].as_str().unwrap_or("").contains("strategy_file"),
            "error message must mention strategy_file; got: {json}"
        );
    }

    #[tokio::test]
    async fn replay_start_rejects_empty_strategy_file() {
        let (_engine_tx, engine_rx) = watch::channel::<Option<Arc<EngineConnection>>>(None);
        let state = Arc::new(ReplayApiState::new(
            engine_rx,
            engine_client::dto::AppMode::Replay,
        ));
        let port = spawn_test_http_server(state).await;
        tokio::time::sleep(Duration::from_millis(10)).await;

        let body = serde_json::json!({
            "instrument_id": "7203.TSE",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "granularity": "Daily",
            "strategy_id": "buy-and-hold",
            "initial_cash": "1000000",
            "strategy_file": "",
        })
        .to_string();
        let (status, body_str) = http_request(port, "POST", "/api/replay/start", &body).await;
        assert_eq!(status, 400, "empty strategy_file must return 400; body={body_str}");
        let json: serde_json::Value = serde_json::from_str(&body_str).unwrap();
        assert!(
            json["error"].as_str().unwrap_or("").contains("strategy_file"),
            "error message must mention strategy_file; got: {json}"
        );
    }
}
