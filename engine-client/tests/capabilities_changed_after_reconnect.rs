//! B4 R3 M3 繰越 — capabilities are updated when the engine reconnects.
//!
//! TODO(O1): Add a test that verifies a single ProcessManager/EngineClientBackend
//! instance reflects updated capabilities after reconnect (same-instance reuse).
//!
//! When a new `EngineConnection` is created after a Python restart, the
//! `capabilities()` snapshot on the **new** connection object must reflect
//! the `Ready.capabilities` that arrived in the **second** handshake — not
//! the stale value from the first connection.
//!
//! This test uses two independent mock servers (two TcpListeners on ephemeral
//! ports) — one sends `capabilities: {}` and the other sends
//! `capabilities: {"supported_venues": ["tachibana"]}`. Connecting to each
//! in turn and asserting the capabilities snapshot updates is sufficient to
//! pin the contract.

use flowsurface_engine_client::{EngineConnection, SCHEMA_MAJOR, SCHEMA_MINOR};

use futures_util::{SinkExt, StreamExt};
use std::{net::SocketAddr, sync::Arc, time::Duration};
use tokio::net::TcpListener;
use tokio_tungstenite::{accept_async, tungstenite::Message};

// ── helpers ───────────────────────────────────────────────────────────────────

async fn bind_loopback() -> (TcpListener, SocketAddr) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    (listener, addr)
}

/// Spawn a mock server that performs Hello/Ready with the given `capabilities`
/// JSON, then keeps the connection alive for `open_ms` milliseconds.
async fn mock_server_with_caps(
    listener: TcpListener,
    token: &'static str,
    capabilities: serde_json::Value,
    open_ms: u64,
) {
    tokio::spawn(async move {
        let (tcp, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(tcp).await.unwrap();

        // Read Hello
        if let Some(Ok(msg)) = ws.next().await {
            let text = msg.into_text().unwrap_or_default();
            let parsed: serde_json::Value = serde_json::from_str(&text).unwrap_or_default();
            assert_eq!(parsed["op"], "Hello");
            assert_eq!(parsed["token"], token);
        }

        // Send Ready with the supplied capabilities
        let ready = serde_json::json!({
            "event": "Ready",
            "schema_major": SCHEMA_MAJOR,
            "schema_minor": SCHEMA_MINOR,
            "engine_version": "0.0.1-mock",
            "engine_session_id": "00000000-0000-0000-0000-000000000002",
            "capabilities": capabilities,
        });
        ws.send(Message::Text(ready.to_string().into())).await.ok();

        tokio::time::sleep(Duration::from_millis(open_ms)).await;
    });
}

// ── tests ─────────────────────────────────────────────────────────────────────

/// Connecting to a new engine (simulating a reconnect) must surface the
/// updated capabilities on the new `EngineConnection` instance.
///
/// Concretely:
///   - first connection  → caps = `{}`
///   - second connection → caps = `{"supported_venues": ["tachibana"]}`
///
/// After both handshakes the assertion is that `second.capabilities()` contains
/// the tachibana venue, whereas `first.capabilities()` does not — demonstrating
/// that creating a new `EngineConnection` captures the fresh capabilities.
#[tokio::test]
async fn capabilities_snapshot_updated_on_reconnect() {
    let token = "cap-reconnect-test-token";

    // -- First server: empty capabilities --------------------------------
    let (listener1, addr1) = bind_loopback().await;
    mock_server_with_caps(listener1, token, serde_json::json!({}), 500).await;
    tokio::time::sleep(Duration::from_millis(10)).await;

    let url1 = format!("ws://{addr1}");
    let conn1 = Arc::new(
        EngineConnection::connect(&url1, token)
            .await
            .expect("first connect must succeed"),
    );

    let caps1 = conn1.capabilities();
    let venues1 = caps1
        .get("supported_venues")
        .and_then(|v| v.as_array())
        .map(|a| a.iter().filter_map(|v| v.as_str()).collect::<Vec<_>>())
        .unwrap_or_default();
    assert!(
        !venues1.contains(&"tachibana"),
        "first connection: tachibana must NOT be in capabilities (got {caps1:?})"
    );

    // -- Second server: tachibana in capabilities -------------------------
    let (listener2, addr2) = bind_loopback().await;
    mock_server_with_caps(
        listener2,
        token,
        serde_json::json!({"supported_venues": ["tachibana"]}),
        500,
    )
    .await;
    tokio::time::sleep(Duration::from_millis(10)).await;

    let url2 = format!("ws://{addr2}");
    let conn2 = Arc::new(
        EngineConnection::connect(&url2, token)
            .await
            .expect("second connect must succeed"),
    );

    let caps2 = conn2.capabilities();
    let venues2 = caps2
        .get("supported_venues")
        .and_then(|v| v.as_array())
        .map(|a| a.iter().filter_map(|v| v.as_str()).collect::<Vec<_>>())
        .unwrap_or_default();
    assert!(
        venues2.contains(&"tachibana"),
        "second connection: tachibana MUST be in capabilities (got {caps2:?})"
    );

    // Sanity: the first connection's snapshot is unchanged (not mutated).
    let caps1_after = conn1.capabilities();
    assert!(
        !caps1_after
            .get("supported_venues")
            .and_then(|v| v.as_array())
            .map(|a| a.iter().any(|v| v.as_str() == Some("tachibana")))
            .unwrap_or(false),
        "first connection snapshot must remain unchanged after reconnect"
    );
}
