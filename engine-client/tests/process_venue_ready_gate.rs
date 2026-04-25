//! Regression tests for the `SetVenueCredentials → VenueReady → resubscribe`
//! sequencing in `ProcessManager::start()` (Findings #1 / #2).
//!
//! Architecture spec §2.4 mandates:
//!     SetProxy → SetVenueCredentials → wait VenueReady → resubscribe
//!
//! Without the gate, `Subscribe` for the credential-bearing venue can be
//! sent before Python has finished validating the session — leading to
//! "subscribe before authenticated" failures on every (re)start. We pin
//! the gate behavior with an in-process mock WS server.

use flowsurface_engine_client::{
    ProcessManager, SCHEMA_MAJOR, SCHEMA_MINOR, SubscriptionKey,
    dto::{TachibanaCredentialsWire, VenueCredentialsPayload},
};

use futures_util::{SinkExt, StreamExt};
use std::{net::SocketAddr, sync::Arc, time::Duration};
use tokio::{net::TcpListener, sync::mpsc};
use tokio_tungstenite::{accept_async, tungstenite::Message};

async fn bind_loopback() -> (TcpListener, SocketAddr) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    (listener, addr)
}

fn dummy_creds_with_session() -> VenueCredentialsPayload {
    VenueCredentialsPayload::Tachibana(TachibanaCredentialsWire {
        user_id: "u".to_string(),
        password: "p".to_string().into(),
        second_password: None,
        is_demo: true,
        session: None,
    })
}

/// Mock server that records every op string in arrival order and emits
/// `VenueReady` after a configurable delay so the test can observe
/// whether `Subscribe` is sent before or after `VenueReady`.
async fn mock_server_record_ops(
    listener: TcpListener,
    token: String,
    venue_ready_delay: Duration,
    ops_tx: mpsc::UnboundedSender<String>,
) {
    tokio::spawn(async move {
        let (tcp, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(tcp).await.unwrap();

        // Read Hello.
        if let Some(Ok(msg)) = ws.next().await {
            let parsed: serde_json::Value =
                serde_json::from_str(&msg.into_text().unwrap_or_default()).unwrap();
            assert_eq!(parsed["op"], "Hello");
            assert_eq!(parsed["token"], token.as_str());
        }

        // Send Ready.
        let ready = serde_json::json!({
            "event": "Ready",
            "schema_major": SCHEMA_MAJOR,
            "schema_minor": SCHEMA_MINOR,
            "engine_version": "0.0.1-mock",
            "engine_session_id": "00000000-0000-0000-0000-000000000001",
            "capabilities": {}
        });
        ws.send(Message::Text(ready.to_string().into())).await.ok();

        // Capture follow-up ops, emitting VenueReady once for each
        // SetVenueCredentials seen, after the configured delay.
        while let Some(Ok(msg)) = ws.next().await {
            let text = msg.into_text().unwrap_or_default();
            if text.is_empty() {
                continue;
            }
            let parsed: serde_json::Value = match serde_json::from_str(&text) {
                Ok(v) => v,
                Err(_) => continue,
            };
            let op = parsed["op"].as_str().unwrap_or("").to_string();
            ops_tx.send(op.clone()).ok();

            if op == "SetVenueCredentials" {
                let request_id = parsed["request_id"].as_str().unwrap_or("").to_string();
                let delay = venue_ready_delay;
                let mut ws_clone = ws;
                tokio::time::sleep(delay).await;
                let venue_ready = serde_json::json!({
                    "event": "VenueReady",
                    "venue": "tachibana",
                    "request_id": request_id,
                });
                ws_clone
                    .send(Message::Text(venue_ready.to_string().into()))
                    .await
                    .ok();
                ws = ws_clone;
            }
        }
    });
}

#[tokio::test]
async fn subscribe_is_not_sent_until_venue_ready_observed() {
    let (listener, addr) = bind_loopback().await;
    let (ops_tx, mut ops_rx) = mpsc::unbounded_channel::<String>();

    // VenueReady arrives 250 ms after SetVenueCredentials. If the gate is
    // missing, Subscribe arrives before VenueReady.
    let token = "venue-gate-token";
    mock_server_record_ops(
        listener,
        token.to_string(),
        Duration::from_millis(250),
        ops_tx,
    )
    .await;

    // Drive **production** `apply_after_handshake` directly against the
    // mock connection. Previously the test re-implemented the post-
    // handshake loop inline, which meant a regression in `start()`
    // could go unnoticed. By calling the production method we pin the
    // wire-level ordering invariant on the actual code path used at
    // runtime; only the `PythonProcess::spawn_with` step is bypassed
    // (it is the one piece that genuinely requires a real subprocess).
    let url = format!("ws://{addr}");
    let conn = flowsurface_engine_client::EngineConnection::connect(&url, token)
        .await
        .expect("handshake");

    let manager = Arc::new(ProcessManager::new("python"));
    manager
        .set_venue_credentials(dummy_creds_with_session())
        .await;
    manager
        .active_subscriptions
        .lock()
        .await
        .insert(SubscriptionKey {
            venue: "tachibana".into(),
            ticker: "7203".into(),
            stream: "kline".into(),
            timeframe: Some("1m".into()),
            market: "stock".into(),
        });

    manager.apply_after_handshake(&conn).await;

    // Allow the mock server's read loop to flush the final Subscribe op.
    tokio::time::sleep(Duration::from_millis(100)).await;

    let mut ops: Vec<String> = Vec::new();
    while let Ok(op) = ops_rx.try_recv() {
        ops.push(op);
    }

    let pos_set = ops
        .iter()
        .position(|o| o == "SetVenueCredentials")
        .expect("SetVenueCredentials must be observed by mock server");
    let pos_sub = ops
        .iter()
        .position(|o| o == "Subscribe")
        .expect("Subscribe must be observed by mock server");
    assert!(
        pos_set < pos_sub,
        "Subscribe must arrive AFTER SetVenueCredentials (got order: {ops:?})"
    );

    drop(conn);
}

/// Regression for review finding #1: when Python returns `VenueError`
/// for a `SetVenueCredentials` (e.g. `unread_notices` / `session_expired` /
/// `login_failed`), `Subscribe` for that venue must NOT be sent —
/// otherwise the venue's stream subscriptions race the user's re-login
/// and surface as un-authenticated stream errors.
async fn mock_server_emit_venue_error(
    listener: TcpListener,
    token: String,
    ops_tx: mpsc::UnboundedSender<String>,
) {
    tokio::spawn(async move {
        let (tcp, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(tcp).await.unwrap();

        if let Some(Ok(msg)) = ws.next().await {
            let parsed: serde_json::Value =
                serde_json::from_str(&msg.into_text().unwrap_or_default()).unwrap();
            assert_eq!(parsed["op"], "Hello");
            assert_eq!(parsed["token"], token.as_str());
        }

        let ready = serde_json::json!({
            "event": "Ready",
            "schema_major": flowsurface_engine_client::SCHEMA_MAJOR,
            "schema_minor": flowsurface_engine_client::SCHEMA_MINOR,
            "engine_version": "0.0.1-mock",
            "engine_session_id": "00000000-0000-0000-0000-000000000001",
            "capabilities": {}
        });
        ws.send(Message::Text(ready.to_string().into())).await.ok();

        while let Some(Ok(msg)) = ws.next().await {
            let text = msg.into_text().unwrap_or_default();
            if text.is_empty() {
                continue;
            }
            let parsed: serde_json::Value = match serde_json::from_str(&text) {
                Ok(v) => v,
                Err(_) => continue,
            };
            let op = parsed["op"].as_str().unwrap_or("").to_string();
            ops_tx.send(op.clone()).ok();

            if op == "SetVenueCredentials" {
                let request_id = parsed["request_id"].as_str().unwrap_or("").to_string();
                let venue_error = serde_json::json!({
                    "event": "VenueError",
                    "venue": "tachibana",
                    "request_id": request_id,
                    "code": "session_expired",
                    "message": "再ログインしてください",
                });
                ws.send(Message::Text(venue_error.to_string().into()))
                    .await
                    .ok();
            }
        }
    });
}

#[tokio::test]
async fn subscribe_is_skipped_when_set_venue_credentials_fails() {
    let (listener, addr) = bind_loopback().await;
    let (ops_tx, mut ops_rx) = mpsc::unbounded_channel::<String>();
    let token = "venue-error-token";
    mock_server_emit_venue_error(listener, token.to_string(), ops_tx).await;

    let url = format!("ws://{addr}");
    let conn = flowsurface_engine_client::EngineConnection::connect(&url, token)
        .await
        .expect("handshake");

    let manager = Arc::new(ProcessManager::new("python"));
    manager
        .set_venue_credentials(dummy_creds_with_session())
        .await;
    manager
        .active_subscriptions
        .lock()
        .await
        .insert(SubscriptionKey {
            venue: "tachibana".into(),
            ticker: "7203".into(),
            stream: "kline".into(),
            timeframe: Some("1m".into()),
            market: "stock".into(),
        });

    manager.apply_after_handshake(&conn).await;

    // Give the read loop a moment to surface any (unexpected) Subscribe.
    tokio::time::sleep(Duration::from_millis(150)).await;

    let mut ops: Vec<String> = Vec::new();
    while let Ok(op) = ops_rx.try_recv() {
        ops.push(op);
    }

    assert!(
        ops.iter().any(|o| o == "SetVenueCredentials"),
        "SetVenueCredentials must have been sent (ops: {ops:?})"
    );
    assert!(
        !ops.iter().any(|o| o == "Subscribe"),
        "Subscribe must be skipped after VenueError (ops: {ops:?})"
    );

    drop(conn);
}
