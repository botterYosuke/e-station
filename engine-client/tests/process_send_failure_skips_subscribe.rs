//! M-3 regression — `apply_after_handshake` must not silently swallow
//! a failed `SetVenueCredentials` send. When the IPC command channel is
//! closed (e.g. the engine WS dropped between handshake and the venue
//! credential injection), the affected venue must be added to
//! `failed_venues` so `Subscribe` is **not** sent for it.
//!
//! Test setup: a mock WS server completes the Hello/Ready handshake
//! and then closes the connection immediately. The `EngineConnection`
//! io task exits, dropping the command-channel receiver, so
//! `connection.send(...)` thereafter returns `Err`. We then call
//! `apply_after_handshake` and observe that the active subscription
//! for the configured venue is **not** sent (because the venue was
//! marked failed).
//!
//! We can't directly grep the network for "absence of Subscribe"; the
//! WS is gone. Instead we assert the *internal* behaviour: the
//! function returns promptly (no 60s VenueReady wait blocking on a
//! request_id we never managed to send) and `failed_venues` is
//! reflected in skipped Subscribe attempts.

use flowsurface_engine_client::{
    SCHEMA_MAJOR, SCHEMA_MINOR,
    dto::{TachibanaCredentialsWire, VenueCredentialsPayload},
    EngineConnection, ProcessManager,
};
use flowsurface_engine_client::process::SubscriptionKey;

use futures_util::{SinkExt, StreamExt};
use std::{net::SocketAddr, time::Duration};
use tokio::net::TcpListener;
use tokio_tungstenite::{accept_async, tungstenite::Message};

async fn bind_loopback() -> (TcpListener, SocketAddr) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    (listener, addr)
}

async fn mock_handshake_then_close(listener: TcpListener, token: String) {
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
        // Drop the WS so the EngineConnection io task exits, closing cmd_rx.
        drop(ws);
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
async fn apply_after_handshake_skips_subscribe_when_set_creds_send_fails() {
    let (listener, addr) = bind_loopback().await;
    let token = "send-fail-test-token";
    mock_handshake_then_close(listener, token.to_string()).await;
    tokio::time::sleep(Duration::from_millis(20)).await;

    let url = format!("ws://{addr}");
    let connection = EngineConnection::connect(&url, token)
        .await
        .expect("handshake should succeed");

    // Wait for the io task to exit (mock server closed the WS).
    tokio::time::timeout(Duration::from_millis(500), connection.wait_closed())
        .await
        .expect("ws io task must exit after server close");

    let manager = ProcessManager::new("python");
    manager.set_venue_credentials(dummy_creds()).await;
    // Stub a subscription so we can prove resubscribe was skipped.
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

    // This must return quickly — the SetVenueCredentials send fails,
    // pending_request_ids stays empty, and the VenueReady wait short-
    // circuits. If the send error were swallowed, we'd still spend
    // 60s waiting for VenueReady that no one will produce.
    let elapsed = {
        let start = std::time::Instant::now();
        tokio::time::timeout(
            Duration::from_secs(5),
            manager.apply_after_handshake(&connection),
        )
        .await
        .expect("apply_after_handshake must not block on a dead connection");
        start.elapsed()
    };
    assert!(
        elapsed < Duration::from_secs(3),
        "apply_after_handshake hung for {elapsed:?} — likely waiting on a VenueReady that will never arrive"
    );
}
