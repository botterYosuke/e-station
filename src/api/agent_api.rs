//! HTTP handlers for the agent narrative API (N1.6):
//!   - `POST /api/agent/narrative`
//!   - `GET  /api/agent/narrative`
//!
//! Stores `NarrativeEntry` objects in an in-memory `Vec`. Data is not
//! persisted across restarts (N1 scope — persistence is deferred to N1.x).

use std::sync::{Arc, Mutex};

use serde::{Deserialize, Serialize};
use tokio::{io::AsyncWriteExt, net::TcpStream};
use uuid::Uuid;

// ── Data types ────────────────────────────────────────────────────────────────

/// A single narrative entry stored by `POST /api/agent/narrative`.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NarrativeEntry {
    /// Auto-generated UUID assigned at insertion time.
    pub id: String,
    pub strategy_id: String,
    pub event_type: String,
    pub instrument_id: String,
    pub linked_order_id: String,
    pub outcome: String,
    pub timestamp_ms: i64,
    pub extra: serde_json::Value,
}

/// Request body for `POST /api/agent/narrative`.
#[derive(Debug, Deserialize)]
pub struct NarrativeRequest {
    pub strategy_id: String,
    pub event_type: String,
    pub instrument_id: String,
    pub linked_order_id: String,
    pub outcome: String,
    pub timestamp_ms: i64,
    #[serde(default)]
    pub extra: serde_json::Value,
}

/// Response body for `POST /api/agent/narrative`.
#[derive(Debug, Serialize)]
struct NarrativeCreateResponse {
    id: String,
    status: &'static str,
}

// ── Shared state ──────────────────────────────────────────────────────────────

/// Shared state for the agent narrative endpoints.
///
/// Holds an in-memory store of narrative entries. Wrapped in `Arc` so that
/// `replay_api::spawn` (which owns the tokio task) and tests can share it.
#[derive(Debug, Clone)]
pub struct AgentApiState {
    pub entries: Arc<Mutex<Vec<NarrativeEntry>>>,
}

impl AgentApiState {
    pub fn new() -> Self {
        Self {
            entries: Arc::new(Mutex::new(Vec::new())),
        }
    }
}

impl Default for AgentApiState {
    fn default() -> Self {
        Self::new()
    }
}

// ── Handlers ──────────────────────────────────────────────────────────────────

/// `POST /api/agent/narrative` — append a new `NarrativeEntry`.
///
/// Returns `201 Created` with `{"id": "<uuid>", "status": "stored"}` on
/// success, or `400 Bad Request` if the body cannot be parsed.
pub async fn handle_post_narrative(stream: &mut TcpStream, body: &str, state: &Arc<AgentApiState>) {
    let req: NarrativeRequest = match serde_json::from_str(body) {
        Ok(v) => v,
        Err(e) => {
            let err_body = serde_json::json!({ "error": format!("invalid JSON: {e}") }).to_string();
            write_response(stream, 400, "Bad Request", &err_body).await;
            return;
        }
    };

    let id = Uuid::new_v4().to_string();
    let entry = NarrativeEntry {
        id: id.clone(),
        strategy_id: req.strategy_id,
        event_type: req.event_type,
        instrument_id: req.instrument_id,
        linked_order_id: req.linked_order_id,
        outcome: req.outcome,
        timestamp_ms: req.timestamp_ms,
        extra: req.extra,
    };

    {
        let mut guard = state
            .entries
            .lock()
            .expect("AgentApiState mutex should never be poisoned");
        guard.push(entry);
    }

    let resp_body = serde_json::to_string(&NarrativeCreateResponse {
        id,
        status: "stored",
    })
    .unwrap_or_else(|_| r#"{"id":"","status":"stored"}"#.to_string());
    write_response(stream, 201, "Created", &resp_body).await;
}

/// `GET /api/agent/narrative` — return all stored entries as a JSON array.
pub async fn handle_get_narrative(stream: &mut TcpStream, state: &Arc<AgentApiState>) {
    let entries = {
        let guard = state
            .entries
            .lock()
            .expect("AgentApiState mutex should never be poisoned");
        guard.clone()
    };

    let body = serde_json::to_string(&entries).unwrap_or_else(|_| "[]".to_string());
    write_response(stream, 200, "OK", &body).await;
}

// ── Internal HTTP helpers (mirror of replay_api helpers) ─────────────────────

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
        log::debug!("agent_api: write_response failed (client disconnected?) — {e}");
    }
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    use tokio::net::{TcpListener, TcpStream as ClientStream};

    // ── Test HTTP server helpers ──────────────────────────────────────────────

    /// Spawn a mini HTTP server on a random port that routes
    /// `POST /api/agent/narrative` and `GET /api/agent/narrative` using the
    /// shared `AgentApiState`.  Returns the bound port.
    async fn spawn_agent_http_server(state: Arc<AgentApiState>) -> u16 {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        tokio::spawn(async move {
            while let Ok((mut stream, _)) = listener.accept().await {
                let state = Arc::clone(&state);
                tokio::spawn(async move {
                    use tokio::io::{AsyncBufReadExt, BufReader};
                    let mut reader = BufReader::new(&mut stream);
                    // Read request line
                    let mut req_line = String::new();
                    reader.read_line(&mut req_line).await.unwrap_or(0);
                    let mut parts = req_line.split_whitespace();
                    let method = parts.next().unwrap_or("").to_uppercase();
                    let path = parts.next().unwrap_or("").to_string();
                    // Read headers
                    let mut content_length: usize = 0;
                    loop {
                        let mut header = String::new();
                        reader.read_line(&mut header).await.unwrap_or(0);
                        let trimmed = header.trim();
                        if trimmed.is_empty() {
                            break;
                        }
                        if let Some(rest) = trimmed.to_lowercase().strip_prefix("content-length:") {
                            content_length = rest.trim().parse().unwrap_or(0);
                        }
                    }
                    // Read body
                    let body = if content_length > 0 {
                        let mut buf = vec![0u8; content_length.min(65_536)];
                        use tokio::io::AsyncReadExt;
                        reader.read_exact(&mut buf).await.unwrap_or(0);
                        String::from_utf8_lossy(&buf).into_owned()
                    } else {
                        String::new()
                    };
                    drop(reader);

                    match (method.as_str(), path.as_str()) {
                        ("POST", "/api/agent/narrative") => {
                            handle_post_narrative(&mut stream, &body, &state).await;
                        }
                        ("GET", "/api/agent/narrative") => {
                            handle_get_narrative(&mut stream, &state).await;
                        }
                        _ => {
                            let resp = "HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n";
                            stream.write_all(resp.as_bytes()).await.ok();
                        }
                    }
                });
            }
        });
        port
    }

    /// Send a raw HTTP/1.1 request and return (status_code, body).
    async fn http_request(port: u16, method: &str, path: &str, body: &str) -> (u16, String) {
        let mut stream = ClientStream::connect(format!("127.0.0.1:{port}"))
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

    fn valid_narrative_body() -> String {
        serde_json::json!({
            "strategy_id": "buy-and-hold",
            "event_type": "OrderFilled",
            "instrument_id": "1301.TSE",
            "linked_order_id": "O-20260428-000001",
            "outcome": "filled at 3775.0",
            "timestamp_ms": 1714123456789_i64,
            "extra": {}
        })
        .to_string()
    }

    // ── POST: 201 + id ────────────────────────────────────────────────────────

    #[tokio::test]
    async fn post_narrative_returns_201_and_id() {
        let state = Arc::new(AgentApiState::new());
        let port = spawn_agent_http_server(Arc::clone(&state)).await;
        tokio::time::sleep(std::time::Duration::from_millis(5)).await;

        let (status, body) = http_request(
            port,
            "POST",
            "/api/agent/narrative",
            &valid_narrative_body(),
        )
        .await;
        assert_eq!(status, 201, "expected 201; body={body}");
        let v: serde_json::Value = serde_json::from_str(&body).unwrap();
        assert_eq!(v["status"], "stored", "body={body}");
        assert!(
            !v["id"].as_str().unwrap_or("").is_empty(),
            "id must not be empty; body={body}"
        );
    }

    // ── GET: returns stored entries ───────────────────────────────────────────

    #[tokio::test]
    async fn get_narrative_returns_stored_entries() {
        let state = Arc::new(AgentApiState::new());
        let port = spawn_agent_http_server(Arc::clone(&state)).await;
        tokio::time::sleep(std::time::Duration::from_millis(5)).await;

        // POST one entry
        let (status, _) = http_request(
            port,
            "POST",
            "/api/agent/narrative",
            &valid_narrative_body(),
        )
        .await;
        assert_eq!(status, 201);

        // GET and verify
        let (status, body) = http_request(port, "GET", "/api/agent/narrative", "").await;
        assert_eq!(status, 200, "expected 200; body={body}");
        let entries: Vec<serde_json::Value> = serde_json::from_str(&body).unwrap();
        assert_eq!(entries.len(), 1, "expected 1 entry; body={body}");
        assert_eq!(entries[0]["strategy_id"], "buy-and-hold");
        assert_eq!(entries[0]["linked_order_id"], "O-20260428-000001");
    }

    // ── POST: 400 on bad JSON ─────────────────────────────────────────────────

    #[tokio::test]
    async fn post_narrative_returns_400_on_invalid_json() {
        let state = Arc::new(AgentApiState::new());
        let port = spawn_agent_http_server(Arc::clone(&state)).await;
        tokio::time::sleep(std::time::Duration::from_millis(5)).await;

        let (status, body) = http_request(port, "POST", "/api/agent/narrative", "not-json").await;
        assert_eq!(status, 400, "expected 400; body={body}");
        let v: serde_json::Value = serde_json::from_str(&body).unwrap();
        assert!(
            v["error"].as_str().is_some(),
            "error field expected; body={body}"
        );
    }
}
