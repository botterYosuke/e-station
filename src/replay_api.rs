//! HTTP control API for E2E test driving (port 9876).
//!
//! Provides the local HTTP endpoints used by `tests/e2e/tachibana_relogin_after_cancel.sh`
//! and other bash-driven E2E scripts to drive the Iced app without a GUI automation
//! framework.  Iced itself has no built-in HTTP surface; this module runs a minimal
//! raw-TCP HTTP/1.1 server as a background tokio task.
//!
//! Architecture:
//! ```text
//! E2E bash script
//!     ↓ HTTP/1.1 (port 9876)
//! replay_api — raw TCP listener
//!     ↓ tokio::sync::mpsc::Sender<ControlApiCommand>
//! main.rs — Iced Subscription (replay_api_stream)
//!     ↓ Message::ControlApi(ControlApiCommand)
//! Flowsurface::update()
//! ```
//!
//! **Debug-build note**: The Tachibana session-deletion endpoint
//! (`POST /api/test/tachibana/delete-session`) is only enabled in debug builds
//! so it cannot accidentally clear prod keyring entries.

use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    net::{TcpListener, TcpStream},
    sync::mpsc,
};

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

// ── Request handler ───────────────────────────────────────────────────────────

async fn handle_request(mut stream: TcpStream, tx: mpsc::Sender<ControlApiCommand>) {
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
        ("POST", "/api/test/tachibana/cancel-helper") => {
            if let Err(e) = tx.try_send(ControlApiCommand::CancelLoginHelper) {
                log::warn!("replay_api: CancelLoginHelper dropped — channel full or closed: {e}");
            }
            write_response(&mut stream, 202, "Accepted", r#"{"status":"accepted"}"#).await;
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
pub fn spawn(rt: &tokio::runtime::Handle) -> Option<mpsc::Receiver<ControlApiCommand>> {
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
                    tokio::spawn(handle_request(stream, tx_clone));
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
