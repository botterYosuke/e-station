//! HIGH-2 (ラウンド 7) regression — `VenueLoginCancelled` arriving
//! during the in-`start()` VenueReady wait must terminate the wait
//! for the matching `request_id` immediately, without poisoning
//! `failed_venues`. Earlier code's catch-all `Ok(Ok(_)) => {}` arm
//! ignored cancellations, so the wait blocked for the full 60-second
//! VenueReady timeout and the venue then ended up in `failed_venues`,
//! which silently skipped Subscribe even though the user might have
//! re-issued a successful login immediately afterward.
//!
//! Pin: with a 5-second timeout and a mock that responds to
//! `SetVenueCredentials` with `VenueLoginCancelled`, the call returns
//! well under the timeout AND `Subscribe` is sent (cancellation is
//! not a credential failure).

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

async fn mock_handshake_then_cancel(
    listener: TcpListener,
    token: String,
    received: Arc<Mutex<Vec<String>>>,
) {
    tokio::spawn(async move {
        let (tcp, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(tcp).await.unwrap();
        // Hello
        if let Some(Ok(msg)) = ws.next().await {
            let text = msg.into_text().unwrap_or_default();
            let parsed: serde_json::Value = serde_json::from_str(&text).unwrap_or_default();
            assert_eq!(parsed["op"], "Hello");
            assert_eq!(parsed["token"], token.as_str());
        }
        // Ready
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
                            // When we observe SetVenueCredentials, parse out
                            // the request_id and respond with VenueLoginCancelled.
                            if s.contains("\"SetVenueCredentials\"") {
                                let parsed: serde_json::Value =
                                    serde_json::from_str(&s).unwrap_or_default();
                                let rid = parsed["request_id"].as_str().unwrap_or("").to_string();
                                let cancelled = serde_json::json!({
                                    "event": "VenueLoginCancelled",
                                    "venue": "tachibana",
                                    "request_id": rid,
                                });
                                ws.send(Message::Text(cancelled.to_string().into()))
                                    .await
                                    .ok();
                            }
                        }
                        Some(Ok(Message::Close(_))) | None => break,
                        _ => {}
                    }
                }
                _ = tokio::time::sleep(Duration::from_secs(10)) => break,
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
async fn venue_login_cancelled_unblocks_wait_immediately_and_does_not_skip_subscribe() {
    let (listener, addr) = bind_loopback().await;
    let token = "venue-cancel-test-token";
    let received = Arc::new(Mutex::new(Vec::<String>::new()));
    mock_handshake_then_cancel(listener, token.to_string(), Arc::clone(&received)).await;
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

    // 5-second timeout — the cancel arm should resolve in tens of ms.
    let started = std::time::Instant::now();
    manager
        .apply_after_handshake_with_timeout(&connection, Duration::from_secs(5))
        .await;
    let elapsed = started.elapsed();
    assert!(
        elapsed < Duration::from_secs(2),
        "VenueLoginCancelled must unblock the wait immediately; elapsed={elapsed:?}"
    );

    // Give the writer a beat to flush.
    tokio::time::sleep(Duration::from_millis(100)).await;

    let frames = received.lock().await.clone();
    let saw_set_creds = frames.iter().any(|f| f.contains("\"SetVenueCredentials\""));
    let saw_subscribe = frames.iter().any(|f| f.contains("\"Subscribe\""));
    assert!(
        saw_set_creds,
        "SetVenueCredentials must reach engine. frames={frames:?}"
    );
    assert!(
        saw_subscribe,
        "Subscribe MUST be sent when login is cancelled (not a credential failure). frames={frames:?}"
    );
}

// ── M-R8-3 (ラウンド 8) — multi-pending cancel without rid pin ─────────────────
//
// **Phase 1 single-venue note**: this test deliberately pins the
// **current** behaviour rather than the desired Phase-2 behaviour. The
// intent is to reproduce the scenario "two SetVenueCredentials are in
// flight, one VenueLoginCancelled arrives without `request_id`" so a
// future fix that disambiguates by `venue` (currently the cancel arm
// already carries `venue` but does not consult the pending tag map)
// is easy to land + flip the assertion to the new contract.
//
// Today's behaviour: with two pendings and a request_id-less cancel,
// the wait loop logs a warning and `take_only()` returns None, so the
// pending entries linger until the explicit timeout. We therefore
// drive a tight 250ms timeout and assert the call returns within the
// timeout window (i.e. the timeout DID fire — current behaviour).
// When Phase 2 introduces multi-venue concurrency, replace the
// timing assertion with "Subscribe sent within 100ms" + "warn log
// absent".

use flowsurface_engine_client::dto::TachibanaCredentialsWire as TCW;

async fn mock_handshake_then_cancel_no_rid(
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

        let mut set_cred_count = 0u32;
        loop {
            tokio::select! {
                msg = ws.next() => {
                    match msg {
                        Some(Ok(Message::Text(text))) => {
                            let s = text.to_string();
                            received.lock().await.push(s.clone());
                            if s.contains("\"SetVenueCredentials\"") {
                                set_cred_count += 1;
                                // After we have observed BOTH pendings
                                // arrive, send a single rid-less cancel.
                                if set_cred_count >= 2 {
                                    let cancel = serde_json::json!({
                                        "event": "VenueLoginCancelled",
                                        "venue": "tachibana",
                                        // No request_id on purpose.
                                    });
                                    ws.send(Message::Text(cancel.to_string().into())).await.ok();
                                }
                            }
                        }
                        Some(Ok(Message::Close(_))) | None => break,
                        _ => {}
                    }
                }
                _ = tokio::time::sleep(Duration::from_secs(10)) => break,
            }
        }
    });
}

#[tokio::test]
async fn multi_pending_cancel_without_rid_currently_falls_through_to_timeout() {
    let (listener, addr) = bind_loopback().await;
    let token = "multi-pending-cancel-token";
    let received = Arc::new(Mutex::new(Vec::<String>::new()));
    mock_handshake_then_cancel_no_rid(listener, token.to_string(), Arc::clone(&received)).await;
    tokio::time::sleep(Duration::from_millis(20)).await;

    let url = format!("ws://{addr}");
    let connection = EngineConnection::connect(&url, token)
        .await
        .expect("handshake should succeed");

    let manager = ProcessManager::new("python");
    // Push TWO credential payloads to force multi-pending. We use the
    // same venue tag twice, but `set_venue_credentials` dedupes by tag
    // — so we craft two payloads and write the store directly so the
    // wait loop sends two SetVenueCredentials with distinct request_ids.
    {
        let mut store = manager.venue_credentials.lock().await;
        store.push(VenueCredentialsPayload::Tachibana(TCW {
            user_id: "u1".to_string(),
            password: "p1".to_string().into(),
            second_password: None,
            is_demo: true,
            session: None,
        }));
        store.push(VenueCredentialsPayload::Tachibana(TCW {
            user_id: "u2".to_string(),
            password: "p2".to_string().into(),
            second_password: None,
            is_demo: true,
            session: None,
        }));
    }

    // Tight timeout — Phase 1 expectation: the rid-less cancel cannot
    // disambiguate so we wait up to the timeout.
    let started = std::time::Instant::now();
    manager
        .apply_after_handshake_with_timeout(&connection, Duration::from_millis(300))
        .await;
    let elapsed = started.elapsed();

    // **Phase 1 pin**: the call returned at-or-after the timeout deadline.
    // Phase 2 (multi-venue) should drive this under 50ms by disambiguating
    // via `venue` — when that lands, flip the assertion direction.
    assert!(
        elapsed >= Duration::from_millis(280),
        "Phase 1: rid-less multi-pending cancel must currently fall through to timeout; elapsed={elapsed:?}"
    );
}
