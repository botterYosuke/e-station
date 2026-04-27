/// Fix 1: EngineConnection::wait_closed() resolves when the remote side closes.
///
/// The mock server sends Ready then immediately closes its WS.
/// `wait_closed()` must resolve within a generous timeout.
use flowsurface_engine_client::{EngineConnection, SCHEMA_MAJOR, SCHEMA_MINOR};

use futures_util::{SinkExt, StreamExt};
use std::{net::SocketAddr, time::Duration};
use tokio::net::TcpListener;
use tokio_tungstenite::{accept_async, tungstenite::Message};

async fn bind_loopback() -> (TcpListener, SocketAddr) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    (listener, addr)
}

/// Server: accept → Hello → Ready → close.
async fn spawn_ready_then_close(listener: TcpListener, _token: &str) {
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
        // Drop ws → sends Close frame → connection closed.
    });
}

#[tokio::test]
async fn wait_closed_resolves_when_ws_drops() {
    let (listener, addr) = bind_loopback().await;
    let token = "close-test-token";
    spawn_ready_then_close(listener, token).await;
    tokio::time::sleep(Duration::from_millis(10)).await;

    let conn = EngineConnection::connect(&format!("ws://{addr}"), token)
        .await
        .expect("connect should succeed");

    // wait_closed() should resolve once the server drops the WS.
    tokio::time::timeout(Duration::from_secs(3), conn.wait_closed())
        .await
        .expect("wait_closed() should resolve within 3 s after remote close");
}

/// A second connection to a fresh server also satisfies the contract.
#[tokio::test]
async fn wait_closed_resolves_on_explicit_close_frame() {
    let (listener, addr) = bind_loopback().await;
    let token = "close-test-2";

    tokio::spawn(async move {
        let (tcp, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(tcp).await.unwrap();
        let _ = ws.next().await;
        let ready = serde_json::json!({
            "event": "Ready",
            "schema_major": SCHEMA_MAJOR,
            "schema_minor": SCHEMA_MINOR,
            "engine_version": "0.0.1-mock",
            "engine_session_id": "00000000-0000-0000-0000-000000000002",
            "capabilities": {}
        });
        ws.send(Message::Text(ready.to_string().into())).await.ok();
        tokio::time::sleep(Duration::from_millis(50)).await;
        ws.send(Message::Close(None)).await.ok();
    });

    tokio::time::sleep(Duration::from_millis(10)).await;
    let conn = EngineConnection::connect(&format!("ws://{addr}"), token)
        .await
        .expect("connect should succeed");

    tokio::time::timeout(Duration::from_secs(3), conn.wait_closed())
        .await
        .expect("wait_closed() should resolve after Close frame");
}
