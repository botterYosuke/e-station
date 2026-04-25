//! M10 regression — when `SetVenueCredentials` is sent but the engine
//! never returns `VenueReady` within the timeout window, the affected
//! venue must be marked as failed so subsequent `Subscribe` commands
//! are skipped. Without this, Subscribe races the user's eventual
//! re-login and surfaces as "not authenticated" stream errors.
//!
//! We drive `apply_after_handshake_with_timeout` (test-only seam) with
//! a tight 200ms timeout. The mock server reads Hello, sends Ready,
//! then deliberately ignores any further commands. After the timeout
//! lapses we observe by side-effect: no Subscribe frame ever leaves
//! the connection because `failed_venues` includes "tachibana".

use flowsurface_engine_client::{
    SCHEMA_MAJOR, SCHEMA_MINOR,
    dto::{TachibanaCredentialsWire, VenueCredentialsPayload},
    EngineConnection, ProcessManager,
};
use flowsurface_engine_client::process::SubscriptionKey;

use futures_util::{SinkExt, StreamExt};
use std::{net::SocketAddr, sync::Arc, time::Duration};
use tokio::{net::TcpListener, sync::Mutex};
use tokio_tungstenite::{accept_async, tungstenite::Message};

async fn bind_loopback() -> (TcpListener, SocketAddr) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    (listener, addr)
}

/// Mock server that completes the handshake and then records every
/// further frame received so the test can assert the absence of
/// `Subscribe` after the VenueReady timeout fires.
async fn mock_handshake_record_frames(
    listener: TcpListener,
    token: String,
    received: Arc<Mutex<Vec<String>>>,
) {
    tokio::spawn(async move {
        let (tcp, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(tcp).await.unwrap();
        // Read Hello
        if let Some(Ok(msg)) = ws.next().await {
            let text = msg.into_text().unwrap_or_default();
            let parsed: serde_json::Value = serde_json::from_str(&text).unwrap_or_default();
            assert_eq!(parsed["op"], "Hello");
            assert_eq!(parsed["token"], token.as_str());
        }
        // Send Ready
        let ready = serde_json::json!({
            "event": "Ready",
            "schema_major": SCHEMA_MAJOR,
            "schema_minor": SCHEMA_MINOR,
            "engine_version": "0.0.1-mock",
            "engine_session_id": "00000000-0000-0000-0000-000000000001",
            "capabilities": {}
        });
        ws.send(Message::Text(ready.to_string().into())).await.ok();

        // From here on, record every inbound frame but DO NOT respond
        // — we want to drive the VenueReady timeout path.
        loop {
            tokio::select! {
                msg = ws.next() => {
                    match msg {
                        Some(Ok(Message::Text(text))) => {
                            received.lock().await.push(text.to_string());
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
async fn venue_ready_timeout_marks_venue_failed_and_skips_subscribe() {
    let (listener, addr) = bind_loopback().await;
    let token = "venue-timeout-test-token";
    let received = Arc::new(Mutex::new(Vec::<String>::new()));
    mock_handshake_record_frames(listener, token.to_string(), Arc::clone(&received)).await;
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

    // 200ms VenueReady timeout — well below the test's outer budget.
    manager
        .apply_after_handshake_with_timeout(&connection, Duration::from_millis(200))
        .await;

    // Give the writer a beat to flush any buffered frames.
    tokio::time::sleep(Duration::from_millis(100)).await;

    let frames = received.lock().await.clone();
    let saw_set_creds = frames.iter().any(|f| f.contains("\"SetVenueCredentials\""));
    let saw_subscribe = frames.iter().any(|f| f.contains("\"Subscribe\""));
    assert!(saw_set_creds, "SetVenueCredentials must reach the engine. frames={frames:?}");
    assert!(
        !saw_subscribe,
        "Subscribe must NOT be sent for a venue whose VenueReady never arrived. frames={frames:?}"
    );
}
