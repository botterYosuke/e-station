/// Integration test: `EngineConnection::connect` performs the Hello/Ready handshake.
///
/// A mock WebSocket server (tokio-tungstenite) is started in-process.
/// It waits for a `Hello` frame and responds with `Ready`.
use flowsurface_engine_client::{EngineConnection, SCHEMA_MAJOR, SCHEMA_MINOR};

use std::net::SocketAddr;
use tokio::net::TcpListener;
use tokio_tungstenite::{accept_async, tungstenite::Message};
use futures_util::{SinkExt, StreamExt};

/// Bind a random loopback port and return the listener + address.
async fn bind_loopback() -> (TcpListener, SocketAddr) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    (listener, addr)
}

/// Spawn a mock WS server that:
/// 1. Accepts the first connection.
/// 2. Reads one frame (expects `Hello`).
/// 3. Responds with a `Ready` JSON frame.
async fn spawn_mock_server(listener: TcpListener, token: &str) {
    let token = token.to_owned();
    tokio::spawn(async move {
        let (tcp, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(tcp).await.unwrap();

        // Read Hello
        let msg = ws.next().await.unwrap().unwrap();
        let text = msg.into_text().unwrap();
        let parsed: serde_json::Value = serde_json::from_str(&text).unwrap();
        assert_eq!(parsed["op"], "Hello");
        assert_eq!(parsed["token"], token.as_str());

        // Send Ready
        let ready = serde_json::json!({
            "event": "Ready",
            "schema_major": SCHEMA_MAJOR,
            "schema_minor": SCHEMA_MINOR,
            "engine_version": "1.0.0-mock",
            "engine_session_id": "00000000-0000-0000-0000-000000000001",
            "capabilities": {}
        });
        ws.send(Message::Text(ready.to_string().into())).await.unwrap();
    });
}

#[tokio::test]
async fn connect_performs_hello_ready_handshake() {
    let (listener, addr) = bind_loopback().await;
    let token = "test-token-abc";
    spawn_mock_server(listener, token).await;

    // Give the server task a tick to start.
    tokio::time::sleep(std::time::Duration::from_millis(10)).await;

    let url = format!("ws://{addr}");
    let conn = EngineConnection::connect(&url, token).await;
    assert!(conn.is_ok(), "connect failed: {:?}", conn.err());
}

#[tokio::test]
async fn connect_rejects_wrong_schema_major() {
    let (listener, addr) = bind_loopback().await;
    let token = "tok";

    tokio::spawn(async move {
        let (tcp, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(tcp).await.unwrap();
        let _ = ws.next().await; // consume Hello
        let ready = serde_json::json!({
            "event": "Ready",
            "schema_major": 99u16,  // wrong!
            "schema_minor": 0u16,
            "engine_version": "1.0.0-mock",
            "engine_session_id": "00000000-0000-0000-0000-000000000002",
            "capabilities": {}
        });
        let _ = ws.send(Message::Text(ready.to_string().into())).await;
    });

    tokio::time::sleep(std::time::Duration::from_millis(10)).await;

    let url = format!("ws://{addr}");
    let result = EngineConnection::connect(&url, token).await;
    assert!(result.is_err());
    let err_str = result.unwrap_err().to_string();
    assert!(err_str.contains("Schema version mismatch"), "unexpected error: {err_str}");
}

#[tokio::test]
async fn connect_refused_returns_error() {
    // Nothing listening on this port.
    let result = EngineConnection::connect("ws://127.0.0.1:19999", "tok").await;
    assert!(result.is_err());
}
