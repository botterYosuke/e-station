/// Integration tests for `ProcessManager::start_or_attach`.
///
/// Uses `ProcessManager::try_attach_or_spawn` (the testable seam) so that
/// the probe URL and token can be injected without relying on global env vars
/// or a fixed port 19876.
///
/// # Scenarios covered
///
/// 1. **probe_success_attaches_without_spawn**: mock at random port → attach,
///    `spawn_count == 0`
/// 2. **probe_refused_falls_back_to_spawn**: nothing at probe URL →
///    `spawn_count == 1`
/// 3. **token_mismatch_falls_back_to_spawn**: mock replies with EngineError →
///    `spawn_count == 1`
/// 4. **schema_major_mismatch_falls_back_to_spawn**: mock replies with wrong
///    SCHEMA_MAJOR → `spawn_count == 1`
/// 5. **empty_token_skips_probe**: empty token → probe URL never reached,
///    `spawn_count == 1`
use flowsurface_engine_client::{ProcessManager, SCHEMA_MAJOR, SCHEMA_MINOR};

use futures_util::{SinkExt, StreamExt};
use std::{net::SocketAddr, time::Duration};
use tokio::net::TcpListener;
use tokio_tungstenite::{accept_async, tungstenite::Message};

// ── helpers ───────────────────────────────────────────────────────────────────

async fn bind_loopback() -> (TcpListener, SocketAddr) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    (listener, addr)
}

/// Spawn a mock engine that performs a full Hello/Ready handshake, then stays
/// open for `keep_open_ms` milliseconds. `token` is NOT validated — any Hello
/// token is accepted (this is the "same-token" happy-path mock).
///
/// Returns the `JoinHandle` so callers can detect mock panics via `.await`.
fn spawn_mock_engine_accept_any(
    listener: TcpListener,
    keep_open_ms: u64,
) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        let (tcp, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(tcp).await.unwrap();

        // Read Hello — don't validate token for these tests.
        let msg = ws.next().await.unwrap().unwrap();
        let hello: serde_json::Value = serde_json::from_str(&msg.into_text().unwrap()).unwrap();
        assert_eq!(hello["op"], "Hello");

        let ready = serde_json::json!({
            "event": "Ready",
            "schema_major": SCHEMA_MAJOR,
            "schema_minor": SCHEMA_MINOR,
            "engine_version": "mock",
            "engine_session_id": "00000000-0000-0000-0000-000000000001",
            "capabilities": {}
        });
        ws.send(Message::Text(ready.to_string().into())).await.ok();

        tokio::time::sleep(Duration::from_millis(keep_open_ms)).await;
    })
}

/// Spawn a mock that replies with EngineError (simulates HMAC/token mismatch).
///
/// Returns the `JoinHandle` so callers can detect mock panics via `.await`.
fn spawn_mock_engine_reject_with_error(listener: TcpListener) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        let (tcp, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(tcp).await.unwrap();

        let _ = ws.next().await; // consume Hello

        let err = serde_json::json!({
            "event": "EngineError",
            "code": "auth_failed",
            "message": "invalid token"
        });
        ws.send(Message::Text(err.to_string().into())).await.ok();
    })
}

/// Spawn a mock that replies Ready with an intentionally wrong SCHEMA_MAJOR.
///
/// Returns the `JoinHandle` so callers can detect mock panics via `.await`.
fn spawn_mock_engine_wrong_schema(listener: TcpListener) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        let (tcp, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(tcp).await.unwrap();

        let _ = ws.next().await; // consume Hello

        let ready = serde_json::json!({
            "event": "Ready",
            "schema_major": 99u16, // deliberately wrong
            "schema_minor": 0u16,
            "engine_version": "mock",
            "engine_session_id": "00000000-0000-0000-0000-000000000002",
            "capabilities": {}
        });
        ws.send(Message::Text(ready.to_string().into())).await.ok();
    })
}

// ── tests ─────────────────────────────────────────────────────────────────────

/// 1. Probe succeeds → attach without spawning Python.
#[tokio::test]
async fn probe_success_attaches_without_spawn() {
    let (listener, addr) = bind_loopback().await;
    // TcpListener is already in listen state — no sleep needed before connecting.
    let mock_handle = spawn_mock_engine_accept_any(listener, 500);

    let pm = ProcessManager::new("false"); // non-existent python cmd
    let probe_url = format!("ws://{addr}/");
    let result = pm
        .try_attach_or_spawn(9_u16, &probe_url, "test-token")
        .await;

    assert!(result.is_ok(), "attach should succeed: {:?}", result.err());
    assert_eq!(
        pm.spawn_count(),
        0,
        "Python spawn must NOT be called when attach succeeds"
    );

    mock_handle.abort(); // mock is still sleeping; abort it cleanly
}

/// 2. Nothing at probe URL → connection refused → fall back to spawn.
#[tokio::test]
async fn probe_refused_falls_back_to_spawn() {
    // Bind a free port then drop so nothing listens there.
    let addr = {
        let l = TcpListener::bind("127.0.0.1:0").await.unwrap();
        l.local_addr().unwrap()
        // drop here → port released
    };

    let pm = ProcessManager::new("false");
    let probe_url = format!("ws://{addr}/");
    // Ignore the spawn error (no real Python); we only care about spawn_count.
    let _ = pm.try_attach_or_spawn(9_u16, &probe_url, "any-token").await;

    assert_eq!(
        pm.spawn_count(),
        1,
        "spawn must be attempted when probe is refused"
    );
}

/// 3. Mock replies with EngineError (token mismatch) → fall back to spawn.
#[tokio::test]
async fn token_mismatch_falls_back_to_spawn() {
    let (listener, addr) = bind_loopback().await;
    let mock_handle = spawn_mock_engine_reject_with_error(listener);

    let pm = ProcessManager::new("false");
    let probe_url = format!("ws://{addr}/");
    let _ = pm
        .try_attach_or_spawn(9_u16, &probe_url, "wrong-token")
        .await;

    assert_eq!(
        pm.spawn_count(),
        1,
        "spawn must be attempted after token-mismatch EngineError"
    );
    mock_handle.await.expect("mock server panicked");
}

/// 4. Mock returns Ready with wrong SCHEMA_MAJOR → SchemaMismatch → fall back.
#[tokio::test]
async fn schema_major_mismatch_falls_back_to_spawn() {
    let (listener, addr) = bind_loopback().await;
    let mock_handle = spawn_mock_engine_wrong_schema(listener);

    let pm = ProcessManager::new("false");
    let probe_url = format!("ws://{addr}/");
    let _ = pm.try_attach_or_spawn(9_u16, &probe_url, "any-token").await;

    assert_eq!(
        pm.spawn_count(),
        1,
        "spawn must be attempted after SCHEMA_MAJOR mismatch"
    );
    mock_handle.await.expect("mock server panicked");
}

/// 5. Empty token → probe URL is never contacted → straight to spawn.
#[tokio::test]
async fn empty_token_skips_probe() {
    let (listener, addr) = bind_loopback().await;

    let pm = ProcessManager::new("false");
    let probe_url = format!("ws://{addr}/");
    let _ = pm.try_attach_or_spawn(9_u16, &probe_url, "").await;

    assert_eq!(
        pm.spawn_count(),
        1,
        "spawn must be attempted when token is empty"
    );

    // The mock listener must NOT have received any connection.
    let no_connection = tokio::time::timeout(Duration::from_millis(50), listener.accept())
        .await
        .is_err();
    assert!(
        no_connection,
        "probe URL must NOT be contacted when token is empty"
    );
}
