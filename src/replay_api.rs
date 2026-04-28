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
    dto::{Command, EngineEvent, ReplayGranularity},
};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    net::{TcpListener, TcpStream},
    sync::{Mutex, mpsc, watch},
};

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
}

/// Status snapshot returned by `GET /api/replay/status`.
#[derive(serde::Serialize)]
struct StatusResponse<'a> {
    status: &'a str,
    version: &'a str,
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
    /// `"live"` | `"replay"`.
    pub mode: String,
    /// Timeout for `LoadReplayData` → `ReplayDataLoaded`. Default 60 s
    /// (J-Quants 1-month trade tick load target per spec.md §3.3).
    pub load_timeout: Duration,
    /// Serialise concurrent `/api/replay/load` calls so that
    /// `ReplayDataLoaded` (which has no `request_id` in schema 2.4) cannot
    /// be cross-correlated.
    load_lock: Mutex<()>,
}

impl ReplayApiState {
    pub fn new(
        engine_rx: watch::Receiver<Option<Arc<EngineConnection>>>,
        mode: impl Into<String>,
    ) -> Self {
        Self {
            engine_rx,
            mode: mode.into(),
            load_timeout: Duration::from_secs(60),
            load_lock: Mutex::new(()),
        }
    }

    /// Override the load timeout (test-only / future config hook).
    #[cfg(test)]
    pub fn with_load_timeout(mut self, t: Duration) -> Self {
        self.load_timeout = t;
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
    granularity: String,
}

#[derive(serde::Serialize)]
struct ReplayLoadOk {
    status: &'static str,
    bars_loaded: u64,
    trades_loaded: u64,
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

fn parse_granularity(raw: &str) -> Option<ReplayGranularity> {
    match raw {
        "Trade" => Some(ReplayGranularity::Trade),
        "Minute" => Some(ReplayGranularity::Minute),
        "Daily" => Some(ReplayGranularity::Daily),
        _ => None,
    }
}

/// Validate ISO-8601 date `YYYY-MM-DD` (very strict — Python loader expects this form).
fn is_iso_date(s: &str) -> bool {
    if s.len() != 10 {
        return false;
    }
    let bytes = s.as_bytes();
    bytes[4] == b'-'
        && bytes[7] == b'-'
        && bytes[..4].iter().all(|b| b.is_ascii_digit())
        && bytes[5..7].iter().all(|b| b.is_ascii_digit())
        && bytes[8..10].iter().all(|b| b.is_ascii_digit())
}

/// `POST /api/replay/load` — bridge to `Command::LoadReplayData`.
///
/// Awaits `EngineEvent::ReplayDataLoaded` (no `request_id` in schema 2.4 →
/// concurrent loads are serialised via `state.load_lock`). On
/// `EngineEvent::Error{code: "mode_mismatch"}` returns HTTP 400; any other
/// `Error{}` returns 503. Timeout → 504.
async fn handle_replay_load(stream: &mut TcpStream, body: &str, state: &Arc<ReplayApiState>) {
    // ① Reject early on live mode
    if state.mode != "replay" {
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
    let granularity = match parse_granularity(&parsed.granularity) {
        Some(g) => g,
        None => {
            write_error(
                stream,
                400,
                "Bad Request",
                "granularity must be 'Trade', 'Minute', or 'Daily'",
            )
            .await;
            return;
        }
    };

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
                    bars_loaded,
                    trades_loaded,
                    ..
                }) => {
                    return ReplayLoadOutcome::Ok {
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
                    log::warn!("replay_api: broadcast lagged by {n}; ReplayDataLoaded may be lost");
                    continue;
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
            bars_loaded,
            trades_loaded,
        }) => {
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
        Err(_timeout) => {
            write_response(stream, 504, "Gateway Timeout", r#"{"error":"timeout"}"#).await;
        }
    }
}

enum ReplayLoadOutcome {
    Ok {
        bars_loaded: u64,
        trades_loaded: u64,
    },
    EngineError {
        code: String,
        message: String,
    },
    Disconnected,
}

/// `GET /api/replay/portfolio` — N1.3 skeleton.
///
/// Real nautilus `Portfolio` integration lands in **N1.16** (`ReplayBuyingPower`).
/// Until then the endpoint returns 200 with a deterministic
/// `{ status: "not_implemented", phase: "N1.16" }` body so callers and tests
/// can pin the expected wire shape.
async fn handle_replay_portfolio(stream: &mut TcpStream, state: &Arc<ReplayApiState>) {
    if state.mode != "replay" {
        write_error(
            stream,
            400,
            "Bad Request",
            "replay endpoints are only available in --mode replay",
        )
        .await;
        return;
    }
    let body = serde_json::json!({
        "status": "not_implemented",
        "phase": "N1.16",
    })
    .to_string();
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
    if state.mode != "replay" {
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

    let cid = order_obj
        .get("client_order_id")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    if cid.is_empty() {
        write_error(stream, 400, "Bad Request", "client_order_id is required").await;
        return;
    }

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
    let order: engine_client::dto::SubmitOrderRequest = match serde_json::from_value(parsed.clone())
    {
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

    rt.spawn(async move {
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
                    tokio::spawn(handle_request(
                        stream,
                        tx_clone,
                        order_state_clone,
                        replay_state_clone,
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
    ) {
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
                    "strategy_id": "",
                    "bars_loaded": bars_loaded,
                    "trades_loaded": trades_loaded,
                    "ts_event_ms": 1_700_000_000_000_i64,
                });
                ws.send(Message::Text(evt.to_string().into()))
                    .await
                    .unwrap();
            }
            tokio::time::sleep(Duration::from_millis(200)).await;
        });
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
        // No-op tx (channel never read in these tests).
        let (tx, _rx) = mpsc::channel::<ControlApiCommand>(8);
        tokio::spawn(async move {
            while let Ok((stream, _)) = listener.accept().await {
                let tx_clone = tx.clone();
                let replay_state = Arc::clone(&replay_state);
                tokio::spawn(handle_request(stream, tx_clone, None, Some(replay_state)));
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
        spawn_mock_engine_load(ws_listener, 0, 1234, None, false);
        let conn = connect_engine(ws_addr).await;
        let (engine_tx, engine_rx) = watch::channel(Some(conn));
        let state = Arc::new(
            ReplayApiState::new(engine_rx, "replay").with_load_timeout(Duration::from_secs(5)),
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
    }

    #[tokio::test]
    async fn replay_load_rejects_invalid_json() {
        let (_engine_tx, engine_rx) = watch::channel::<Option<Arc<EngineConnection>>>(None);
        let state = Arc::new(ReplayApiState::new(engine_rx, "replay"));
        let port = spawn_test_http_server(state).await;
        tokio::time::sleep(Duration::from_millis(10)).await;
        let (status, body) =
            http_request(port, "POST", "/api/replay/load", "{not valid json").await;
        assert_eq!(status, 400, "expected 400; body={body}");
    }

    #[tokio::test]
    async fn replay_load_rejects_unknown_granularity() {
        let (_engine_tx, engine_rx) = watch::channel::<Option<Arc<EngineConnection>>>(None);
        let state = Arc::new(ReplayApiState::new(engine_rx, "replay"));
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
        let state = Arc::new(ReplayApiState::new(engine_rx, "replay"));
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

    #[tokio::test]
    async fn replay_load_rejects_empty_instrument_id() {
        let (_engine_tx, engine_rx) = watch::channel::<Option<Arc<EngineConnection>>>(None);
        let state = Arc::new(ReplayApiState::new(engine_rx, "replay"));
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
        spawn_mock_engine_load(
            ws_listener,
            0,
            0,
            Some(("mode_mismatch".to_string(), "wrong mode".to_string())),
            false,
        );
        let conn = connect_engine(ws_addr).await;
        let (engine_tx, engine_rx) = watch::channel(Some(conn));
        let state = Arc::new(
            ReplayApiState::new(engine_rx, "replay").with_load_timeout(Duration::from_secs(5)),
        );
        let port = spawn_test_http_server(Arc::clone(&state)).await;
        tokio::time::sleep(Duration::from_millis(10)).await;

        let (status, body) =
            http_request(port, "POST", "/api/replay/load", &default_load_body()).await;
        assert_eq!(status, 400, "mode_mismatch should map to 400; body={body}");
        let json: serde_json::Value = serde_json::from_str(&body).unwrap();
        assert_eq!(json["code"], "mode_mismatch");
        drop(engine_tx);
    }

    #[tokio::test]
    async fn replay_load_returns_504_on_timeout() {
        let (ws_listener, ws_addr) = bind_ws_loopback().await;
        spawn_mock_engine_load(ws_listener, 0, 0, None, true); // silent
        let conn = connect_engine(ws_addr).await;
        let (engine_tx, engine_rx) = watch::channel(Some(conn));
        let state = Arc::new(
            ReplayApiState::new(engine_rx, "replay").with_load_timeout(Duration::from_millis(150)),
        );
        let port = spawn_test_http_server(Arc::clone(&state)).await;
        tokio::time::sleep(Duration::from_millis(10)).await;
        let (status, body) =
            http_request(port, "POST", "/api/replay/load", &default_load_body()).await;
        assert_eq!(status, 504, "timeout should map to 504; body={body}");
        let json: serde_json::Value = serde_json::from_str(&body).unwrap();
        assert_eq!(json["error"], "timeout");
        drop(engine_tx);
    }

    #[tokio::test]
    async fn replay_load_rejected_in_live_mode() {
        let (_engine_tx, engine_rx) = watch::channel::<Option<Arc<EngineConnection>>>(None);
        let state = Arc::new(ReplayApiState::new(engine_rx, "live"));
        let port = spawn_test_http_server(state).await;
        tokio::time::sleep(Duration::from_millis(10)).await;
        let (status, _) =
            http_request(port, "POST", "/api/replay/load", &default_load_body()).await;
        assert_eq!(status, 400, "live mode must reject /api/replay/load early");
    }

    // ── /api/replay/portfolio ────────────────────────────────────────────────

    #[tokio::test]
    async fn replay_portfolio_skeleton_returns_not_implemented() {
        let (_engine_tx, engine_rx) = watch::channel::<Option<Arc<EngineConnection>>>(None);
        let state = Arc::new(ReplayApiState::new(engine_rx, "replay"));
        let port = spawn_test_http_server(state).await;
        tokio::time::sleep(Duration::from_millis(10)).await;
        let (status, body) = http_request(port, "GET", "/api/replay/portfolio", "").await;
        assert_eq!(status, 200, "skeleton should return 200; body={body}");
        let json: serde_json::Value = serde_json::from_str(&body).unwrap();
        assert_eq!(json["status"], "not_implemented");
        assert_eq!(json["phase"], "N1.16");
    }

    #[tokio::test]
    async fn replay_portfolio_rejected_in_live_mode() {
        let (_engine_tx, engine_rx) = watch::channel::<Option<Arc<EngineConnection>>>(None);
        let state = Arc::new(ReplayApiState::new(engine_rx, "live"));
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
        let state = Arc::new(ReplayApiState::new(engine_rx, "replay"));
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
        let state = Arc::new(ReplayApiState::new(engine_rx, "live"));
        let port = spawn_test_http_server(state).await;
        tokio::time::sleep(Duration::from_millis(10)).await;
        let (status, _) = http_request(port, "POST", "/api/replay/order", "{}").await;
        assert_eq!(status, 400);
    }

    #[tokio::test]
    async fn replay_order_rejects_invalid_body() {
        let (_engine_tx, engine_rx) = watch::channel::<Option<Arc<EngineConnection>>>(None);
        let state = Arc::new(ReplayApiState::new(engine_rx, "replay"));
        let port = spawn_test_http_server(state).await;
        tokio::time::sleep(Duration::from_millis(10)).await;
        let (status, _) = http_request(port, "POST", "/api/replay/order", "not json").await;
        assert_eq!(status, 400);
    }
}
