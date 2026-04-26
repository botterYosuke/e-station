//! T6 — VenueReady idempotency regression tests.
//!
//! architecture.md §3 mandates:
//!   "resubscribe は ProcessManager 1 箇所のみ。UI view 側は VenueReady で
//!    新規 subscribe を発行しない"
//!
//! These tests verify from the ProcessManager perspective:
//!   1. A single `apply_after_handshake` call issues Subscribe exactly once
//!      per active subscription — not twice, even if VenueReady fires more
//!      than once on the wire.
//!   2. ProcessManager does NOT re-subscribe when VenueReady arrives outside
//!      the `apply_after_handshake` window (i.e. the second VenueReady that
//!      Python may emit during a within-session session refresh).
//!
//! The UI-layer test (that `VenueState::Ready` does not fire another
//! `Subscribe` command on `VenueEvent::Ready`) lives in `src/venue_state.rs`
//! unit tests — not here. The engine-client layer only sees IPC traffic.

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

fn dummy_creds() -> VenueCredentialsPayload {
    VenueCredentialsPayload::Tachibana(TachibanaCredentialsWire {
        user_id: "u".to_string(),
        password: "p".to_string().into(),
        second_password: None,
        is_demo: true,
        session: None,
    })
}

/// Mock server that:
///   1. Completes Hello/Ready handshake.
///   2. Records every `op` received from the client.
///   3. Responds to `SetVenueCredentials` with `VenueReady`.
///   4. After `subscribe_count` Subscribes have been received, sends a
///      **second** `VenueReady` unprompted — simulating a within-session
///      session refresh that Python might emit.
///
/// The test then asserts Subscribe was sent exactly `subscribe_count` times.
async fn mock_server_double_venue_ready(
    listener: TcpListener,
    token: String,
    subscribe_count: usize,
    ops_tx: mpsc::UnboundedSender<String>,
) {
    tokio::spawn(async move {
        let (tcp, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(tcp).await.unwrap();

        // Hello
        if let Some(Ok(msg)) = ws.next().await {
            let parsed: serde_json::Value =
                serde_json::from_str(&msg.into_text().unwrap_or_default()).unwrap_or_default();
            assert_eq!(parsed["op"], "Hello");
            assert_eq!(parsed["token"], token.as_str());
        }

        // Ready
        let ready = serde_json::json!({
            "event": "Ready",
            "schema_major": SCHEMA_MAJOR,
            "schema_minor": SCHEMA_MINOR,
            "engine_version": "0.0.1-mock",
            "engine_session_id": "00000000-0000-0000-0000-000000000003",
            "capabilities": {}
        });
        ws.send(Message::Text(ready.to_string().into())).await.ok();

        let mut subs_seen = 0usize;
        let mut second_ready_sent = false;

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

            match op.as_str() {
                "SetVenueCredentials" => {
                    let request_id = parsed["request_id"].as_str().unwrap_or("").to_string();
                    let venue_ready = serde_json::json!({
                        "event": "VenueReady",
                        "venue": "tachibana",
                        "request_id": request_id,
                    });
                    ws.send(Message::Text(venue_ready.to_string().into()))
                        .await
                        .ok();
                }
                "Subscribe" => {
                    subs_seen += 1;
                    // Once we've seen `subscribe_count` Subscribes, fire an
                    // extra VenueReady — the client must NOT react with a new
                    // batch of Subscribes.
                    if subs_seen >= subscribe_count && !second_ready_sent {
                        second_ready_sent = true;
                        // Short delay so apply_after_handshake (which waits for
                        // VenueReady before sending Subscribe) has fully returned
                        // and the client-side event loop is back in a quiescent
                        // state. 50 ms is well within the test drain deadline
                        // (500 ms) even under CI load, so this does not cause
                        // spurious failures.
                        tokio::time::sleep(Duration::from_millis(50)).await;
                        let extra_ready = serde_json::json!({
                            "event": "VenueReady",
                            "venue": "tachibana",
                            "request_id": serde_json::Value::Null,
                        });
                        ws.send(Message::Text(extra_ready.to_string().into()))
                            .await
                            .ok();
                    }
                }
                _ => {}
            }
        }
    });
}

/// ProcessManager sends Subscribe exactly once per active subscription even
/// when the server emits a second VenueReady (within-session session refresh).
///
/// The invariant: resubscribe lives in ProcessManager.apply_after_handshake
/// only. A stray VenueReady arriving after the handshake completes must NOT
/// trigger another batch of Subscribes from any code path.
#[tokio::test]
async fn second_venue_ready_does_not_trigger_extra_subscribe() {
    const ACTIVE_SUBS: usize = 2;

    let (listener, addr) = bind_loopback().await;
    let (ops_tx, mut ops_rx) = mpsc::unbounded_channel::<String>();

    let token = "idempotent-ready-token";
    mock_server_double_venue_ready(listener, token.to_string(), ACTIVE_SUBS, ops_tx).await;

    let url = format!("ws://{addr}");
    let conn = flowsurface_engine_client::EngineConnection::connect(&url, token)
        .await
        .expect("handshake");

    let manager = Arc::new(ProcessManager::new("python"));
    manager.set_venue_credentials(dummy_creds()).await;
    {
        let mut subs = manager.active_subscriptions.lock().await;
        subs.insert(SubscriptionKey {
            venue: "tachibana".into(),
            ticker: "7203".into(),
            stream: "trade".into(),
            timeframe: None,
            market: "stock".into(),
        });
        subs.insert(SubscriptionKey {
            venue: "tachibana".into(),
            ticker: "8306".into(),
            stream: "depth".into(),
            timeframe: None,
            market: "stock".into(),
        });
    }

    manager
        .apply_after_handshake_with_timeout(&conn, Duration::from_secs(5))
        .await;

    // Drain ops with a deadline — wait long enough for the server's extra
    // VenueReady to arrive and for any hypothetical second Subscribe burst to
    // be sent, but without hard-coding a wall-clock sleep.
    let mut ops: Vec<String> = Vec::new();
    let deadline = tokio::time::Instant::now() + Duration::from_millis(500);
    loop {
        match tokio::time::timeout_at(deadline, ops_rx.recv()).await {
            Ok(Some(op)) => ops.push(op),
            Ok(None) | Err(_) => break,
        }
    }

    let subscribe_count = ops.iter().filter(|o| o.as_str() == "Subscribe").count();
    assert_eq!(
        subscribe_count, ACTIVE_SUBS,
        "Subscribe must be sent exactly once per active subscription \
         ({ACTIVE_SUBS} expected), got {subscribe_count}. \
         Full op log: {ops:?}"
    );

    drop(conn);
}

/// A single `apply_after_handshake` with one active subscription sends
/// Subscribe exactly once — baseline for the idempotency contract.
#[tokio::test]
async fn apply_after_handshake_sends_subscribe_exactly_once_per_subscription() {
    let (listener, addr) = bind_loopback().await;
    let (ops_tx, mut ops_rx) = mpsc::unbounded_channel::<String>();

    // Use the double-ready mock but trigger second ready only after 1 sub
    mock_server_double_venue_ready(listener, "baseline-token".to_string(), 1, ops_tx).await;

    let url = format!("ws://{addr}");
    let conn = flowsurface_engine_client::EngineConnection::connect(&url, "baseline-token")
        .await
        .expect("handshake");

    let manager = Arc::new(ProcessManager::new("python"));
    manager.set_venue_credentials(dummy_creds()).await;
    manager
        .active_subscriptions
        .lock()
        .await
        .insert(SubscriptionKey {
            venue: "tachibana".into(),
            ticker: "7203".into(),
            stream: "kline".into(),
            timeframe: Some("1d".into()),
            market: "stock".into(),
        });

    manager
        .apply_after_handshake_with_timeout(&conn, Duration::from_secs(5))
        .await;

    // Drain ops with a deadline instead of a fixed sleep.
    let mut ops: Vec<String> = Vec::new();
    let deadline = tokio::time::Instant::now() + Duration::from_millis(500);
    loop {
        match tokio::time::timeout_at(deadline, ops_rx.recv()).await {
            Ok(Some(op)) => ops.push(op),
            Ok(None) | Err(_) => break,
        }
    }

    let subscribe_count = ops.iter().filter(|o| o.as_str() == "Subscribe").count();
    assert_eq!(
        subscribe_count, 1,
        "exactly 1 Subscribe expected for 1 active subscription, got {subscribe_count}. \
         Full op log: {ops:?}"
    );

    drop(conn);
}
