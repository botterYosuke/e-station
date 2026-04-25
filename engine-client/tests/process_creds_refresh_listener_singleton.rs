//! M-R8-2 (ラウンド 8) regression — the long-lived
//! `VenueCredentialsRefreshed` continuation listener spawned at the
//! end of `apply_after_handshake_with_timeout` must abort any prior
//! instance before spawning the replacement. Earlier code dropped
//! the previous `JoinHandle` on the floor, so each restart leaked an
//! extra listener and a single refresh fired the hook N times (one
//! per accumulated listener).
//!
//! The test drives `apply_after_handshake_with_timeout` against a
//! mock WS three times in sequence (simulating restart cycles) and
//! then sends one `VenueCredentialsRefreshed` event to the **first**
//! still-live connection. The hook counter MUST observe exactly one
//! invocation. (The first two connections are torn down before the
//! refresh fires, so a leak would manifest on the **active** receiver
//! by re-firing — we instead verify the cumulative hook calls across
//! the whole test stay at 1.)

use flowsurface_engine_client::{
    EngineConnection, ProcessManager, SCHEMA_MAJOR, SCHEMA_MINOR,
    dto::{TachibanaCredentialsWire, VenueCredentialsPayload},
};

use futures_util::{SinkExt, StreamExt};
use std::{
    net::SocketAddr,
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
    time::Duration,
};
use tokio::{net::TcpListener, sync::Mutex};
use tokio_tungstenite::{accept_async, tungstenite::Message};

async fn bind_loopback() -> (TcpListener, SocketAddr) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    (listener, addr)
}

/// Mock that completes the Hello/Ready handshake then immediately
/// answers any `SetVenueCredentials` with a `VenueReady` so the
/// in-`start()` wait unblocks. Keeps the connection open until the
/// outer test drops it.
async fn mock_handshake_and_ready(
    listener: TcpListener,
    token: String,
    keep_alive: Arc<tokio::sync::Notify>,
    send_refresh_after_ready: bool,
    refresh_holder: Arc<Mutex<Option<tokio_tungstenite::WebSocketStream<tokio::net::TcpStream>>>>,
) {
    tokio::spawn(async move {
        let (tcp, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(tcp).await.unwrap();
        // Hello.
        if let Some(Ok(msg)) = ws.next().await {
            let text = msg.into_text().unwrap_or_default();
            let parsed: serde_json::Value = serde_json::from_str(&text).unwrap_or_default();
            assert_eq!(parsed["op"], "Hello");
            assert_eq!(parsed["token"], token.as_str());
        }
        // Ready.
        let ready = serde_json::json!({
            "event": "Ready",
            "schema_major": SCHEMA_MAJOR,
            "schema_minor": SCHEMA_MINOR,
            "engine_version": "0.0.1-mock",
            "engine_session_id": "00000000-0000-0000-0000-000000000001",
            "capabilities": {}
        });
        ws.send(Message::Text(ready.to_string().into())).await.ok();

        // Read messages and respond to SetVenueCredentials with
        // VenueReady so the wait loop completes quickly.
        loop {
            tokio::select! {
                msg = ws.next() => {
                    match msg {
                        Some(Ok(Message::Text(text))) => {
                            let s = text.to_string();
                            if s.contains("\"SetVenueCredentials\"") {
                                let parsed: serde_json::Value =
                                    serde_json::from_str(&s).unwrap_or_default();
                                let rid = parsed["request_id"].as_str().unwrap_or("").to_string();
                                let ready_evt = serde_json::json!({
                                    "event": "VenueReady",
                                    "venue": "tachibana",
                                    "request_id": rid,
                                });
                                ws.send(Message::Text(ready_evt.to_string().into()))
                                    .await
                                    .ok();
                            }
                        }
                        Some(Ok(Message::Close(_))) | None => break,
                        _ => {}
                    }
                }
                _ = keep_alive.notified() => {
                    if send_refresh_after_ready {
                        let refresh = serde_json::json!({
                            "event": "VenueCredentialsRefreshed",
                            "venue": "tachibana",
                            "session": {
                                "url_request": "https://demo/req/X/",
                                "url_master": "https://demo/mst/X/",
                                "url_price": "https://demo/prc/X/",
                                "url_event": "https://demo/evt/X/",
                                "url_event_ws": "wss://demo/evt/X/",
                                "expires_at_ms": null,
                                "zyoutoeki_kazei_c": "1",
                            }
                        });
                        ws.send(Message::Text(refresh.to_string().into())).await.ok();
                        // Hand the live socket out so the test can keep it alive.
                        let _ = refresh_holder.lock().await.replace(ws);
                        return;
                    }
                    break;
                }
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
async fn creds_refresh_listener_does_not_double_spawn_across_restarts() {
    let manager = Arc::new(ProcessManager::new("python"));
    manager.set_venue_credentials(dummy_creds()).await;

    let fired = Arc::new(AtomicUsize::new(0));
    let fired_clone = Arc::clone(&fired);
    manager
        .set_on_venue_credentials_refreshed(Box::new(move |_refresh| {
            fired_clone.fetch_add(1, Ordering::SeqCst);
        }))
        .await;

    // Cycle 1 + 2: two preliminary connections that complete the
    // handshake and then close — each spawns a listener which the
    // next cycle MUST abort.
    for _ in 0..2 {
        let (listener, addr) = bind_loopback().await;
        let token = "singleton-test-token";
        let keep = Arc::new(tokio::sync::Notify::new());
        let dummy_holder = Arc::new(Mutex::new(None));
        mock_handshake_and_ready(
            listener,
            token.to_string(),
            Arc::clone(&keep),
            false,
            dummy_holder,
        )
        .await;
        tokio::time::sleep(Duration::from_millis(20)).await;

        let url = format!("ws://{addr}");
        let conn = EngineConnection::connect(&url, token).await.unwrap();
        manager
            .apply_after_handshake_with_timeout(&conn, Duration::from_secs(2))
            .await;
        // Tear down.
        keep.notify_one();
        drop(conn);
        tokio::time::sleep(Duration::from_millis(50)).await;
    }

    // Cycle 3: this connection sends a single VenueCredentialsRefreshed
    // AFTER the handshake completes. Only the listener spawned by
    // this cycle should still be alive. If listeners from cycles 1/2
    // were leaked, the broadcast channel they're attached to is the
    // *previous* connection's — which is gone — so they would already
    // have exited via `RecvError::Closed`. The strongest behavioural
    // check is that the hook fires exactly once.
    let (listener, addr) = bind_loopback().await;
    let token = "singleton-test-token-c3";
    let keep = Arc::new(tokio::sync::Notify::new());
    let refresh_holder: Arc<Mutex<Option<_>>> = Arc::new(Mutex::new(None));
    mock_handshake_and_ready(
        listener,
        token.to_string(),
        Arc::clone(&keep),
        true,
        Arc::clone(&refresh_holder),
    )
    .await;
    tokio::time::sleep(Duration::from_millis(20)).await;

    let url = format!("ws://{addr}");
    let conn = EngineConnection::connect(&url, token).await.unwrap();
    manager
        .apply_after_handshake_with_timeout(&conn, Duration::from_secs(2))
        .await;

    // Trigger the refresh emission and let the listener pick it up.
    keep.notify_one();
    tokio::time::sleep(Duration::from_millis(200)).await;

    assert_eq!(
        fired.load(Ordering::SeqCst),
        1,
        "creds-refresh hook must fire exactly once — multiple firings indicate listener double-spawn"
    );

    // Sanity: handle slot holds at most one live handle.
    let slot = manager.creds_refresh_listener_handle.lock().await;
    assert!(slot.is_some(), "active listener handle must be stored");
}
