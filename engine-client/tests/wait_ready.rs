/// Phase 7 T1.3 — regression: `wait_ready()` resolves once the connection
/// is established, since `connect()` blocks on the Hello/Ready handshake.
///
/// Catches any future refactor that decouples connect() from Ready
/// (e.g. moving the handshake to a deferred task), which would silently
/// re-introduce the UI-1 race window where `ListTickers` is sent before
/// the engine is warm.
use flowsurface_engine_client::{EngineConnection, SCHEMA_MAJOR, SCHEMA_MINOR};

use futures_util::{SinkExt, StreamExt};
use std::time::Duration;
use tokio::net::TcpListener;
use tokio_tungstenite::{accept_async, tungstenite::Message};

#[tokio::test]
async fn wait_ready_resolves_immediately_post_connect() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let token = "wait-ready-token";

    tokio::spawn(async move {
        let (tcp, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(tcp).await.unwrap();
        let _ = ws.next().await; // consume Hello
        let ready = serde_json::json!({
            "event": "Ready",
            "schema_major": SCHEMA_MAJOR,
            "schema_minor": SCHEMA_MINOR,
            "engine_version": "0.0.1-mock",
            "engine_session_id": "00000000-0000-0000-0000-000000000001",
            "capabilities": {}
        });
        ws.send(Message::Text(ready.to_string().into())).await.ok();
        // Hold the socket open so the client doesn't see EOF mid-test.
        tokio::time::sleep(Duration::from_secs(2)).await;
    });

    let conn = EngineConnection::connect(&format!("ws://{addr}"), token)
        .await
        .expect("handshake should succeed");

    // wait_ready() must not block — connect() already awaited Ready.
    tokio::time::timeout(Duration::from_millis(50), conn.wait_ready())
        .await
        .expect("wait_ready should resolve immediately, never block")
        .expect("wait_ready should be Ok");
}
