//! M-R8-4 (ラウンド 8) regression — when Python emits ONLY
//! `VenueError(session_restore_failed)` (no preceding `VenueReady` or
//! `VenueCredentialsRefreshed`) for an in-flight `SetVenueCredentials`
//! request, the Rust `apply_after_handshake` wait loop must:
//!
//! 1. Drop the matching `pending` entry.
//! 2. Insert the venue tag into `failed_venues`.
//! 3. Skip the subsequent `Subscribe` for that venue.
//!
//! The Python side already filters out `VenueReady` /
//! `VenueCredentialsRefreshed` when `restore_failed=True` (HIGH-1
//! ラウンド 7). This test pins the **Rust receiver** behaviour so a
//! future regression that changes the `VenueError` arm (e.g. dropping
//! the `failed_venues` insert) is caught here rather than only by an
//! end-to-end smoke run.

use flowsurface_engine_client::process::SubscriptionKey;
use flowsurface_engine_client::{
    EngineConnection, ProcessManager, SCHEMA_MAJOR, SCHEMA_MINOR,
    dto::{TachibanaCredentialsWire, VenueCredentialsPayload},
};

use futures_util::{SinkExt, StreamExt};
use std::{net::SocketAddr, sync::Arc, time::Duration};
use tokio::{net::TcpListener, sync::Mutex};
use tokio_tungstenite::{accept_async, tungstenite::Message};

async fn bind_loopback() -> (TcpListener, SocketAddr) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    (listener, addr)
}

async fn mock_handshake_then_session_restore_failed(
    listener: TcpListener,
    token: String,
    received: Arc<Mutex<Vec<String>>>,
) {
    tokio::spawn(async move {
        let (tcp, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(tcp).await.unwrap();
        if let Some(Ok(msg)) = ws.next().await {
            let text = msg.into_text().unwrap_or_default();
            let parsed: serde_json::Value = serde_json::from_str(&text).unwrap_or_default();
            assert_eq!(parsed["op"], "Hello");
            assert_eq!(parsed["token"], token.as_str());
        }
        let ready = serde_json::json!({
            "event": "Ready",
            "schema_major": SCHEMA_MAJOR,
            "schema_minor": SCHEMA_MINOR,
            "engine_version": "0.0.1-mock",
            "engine_session_id": "00000000-0000-0000-0000-000000000001",
            "capabilities": {}
        });
        ws.send(Message::Text(ready.to_string().into())).await.ok();

        loop {
            tokio::select! {
                msg = ws.next() => {
                    match msg {
                        Some(Ok(Message::Text(text))) => {
                            let s = text.to_string();
                            received.lock().await.push(s.clone());
                            if s.contains("\"SetVenueCredentials\"") {
                                let parsed: serde_json::Value =
                                    serde_json::from_str(&s).unwrap_or_default();
                                let rid = parsed["request_id"].as_str().unwrap_or("").to_string();
                                // Mirror the Python contract: filter out
                                // VenueReady / VenueCredentialsRefreshed,
                                // emit ONLY VenueError(session_restore_failed).
                                let err_evt = serde_json::json!({
                                    "event": "VenueError",
                                    "venue": "tachibana",
                                    "request_id": rid,
                                    "code": "session_restore_failed",
                                    "message": "セッション復元に失敗しました（テスト固定文言）",
                                });
                                ws.send(Message::Text(err_evt.to_string().into()))
                                    .await
                                    .ok();
                            }
                        }
                        Some(Ok(Message::Close(_))) | None => break,
                        _ => {}
                    }
                }
                _ = tokio::time::sleep(Duration::from_secs(5)) => break,
            }
        }
    });
}

fn dummy_creds() -> VenueCredentialsPayload {
    VenueCredentialsPayload::Tachibana(TachibanaCredentialsWire {
        user_id: "u".to_string(),
        password: "p".to_string().into(),
        second_password: None,
        is_demo: true,
        session: None,
    })
}

#[tokio::test]
async fn session_restore_failed_only_marks_venue_failed_and_skips_subscribe() {
    let (listener, addr) = bind_loopback().await;
    let token = "session-restore-failed-token";
    let received = Arc::new(Mutex::new(Vec::<String>::new()));
    mock_handshake_then_session_restore_failed(listener, token.to_string(), Arc::clone(&received))
        .await;
    tokio::time::sleep(Duration::from_millis(20)).await;

    let url = format!("ws://{addr}");
    let connection = EngineConnection::connect(&url, token)
        .await
        .expect("handshake should succeed");

    let manager = ProcessManager::new("python");
    manager.set_venue_credentials(dummy_creds()).await;
    {
        let mut subs = manager.active_subscriptions.lock().await;
        subs.insert(SubscriptionKey {
            venue: "tachibana".to_string(),
            ticker: "7203".to_string(),
            stream: "trade".to_string(),
            timeframe: None,
            market: "stock".to_string(),
        });
    }

    // Tight timeout — the VenueError arm should resolve in tens of ms.
    let started = std::time::Instant::now();
    manager
        .apply_after_handshake_with_timeout(&connection, Duration::from_secs(5))
        .await;
    let elapsed = started.elapsed();
    assert!(
        elapsed < Duration::from_secs(2),
        "VenueError(session_restore_failed) must unblock the wait immediately; elapsed={elapsed:?}"
    );

    tokio::time::sleep(Duration::from_millis(100)).await;

    let frames = received.lock().await.clone();
    let saw_set_creds = frames.iter().any(|f| f.contains("\"SetVenueCredentials\""));
    let saw_subscribe = frames.iter().any(|f| f.contains("\"Subscribe\""));
    assert!(
        saw_set_creds,
        "SetVenueCredentials must reach the engine. frames={frames:?}"
    );
    assert!(
        !saw_subscribe,
        "Subscribe MUST be skipped when only VenueError(session_restore_failed) was emitted. frames={frames:?}"
    );
}
