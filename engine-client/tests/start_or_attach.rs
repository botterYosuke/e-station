/// Integration tests for `ProcessManager::start_or_attach`.
///
/// Uses `ProcessManager::try_attach_or_spawn` (the testable seam) so that
/// the probe URL and token can be injected without relying on global env vars
/// or a fixed port 19876.
///
/// # Scenarios covered
///
/// 1. **probe_refused_falls_back_to_spawn**: nothing at probe URL →
///    `spawn_count == 1`
/// 2. **empty_token_skips_probe**: empty token → probe URL never reached,
///    `spawn_count == 1`
/// 3. **non_grpc_probe_url_falls_back_to_spawn**: non-grpc:// probe URL → no probe,
///    `spawn_count == 1`
/// 4. **empty_probe_url_falls_back_to_spawn**: empty probe URL → no probe,
///    `spawn_count == 1`
/// 5. **token_mismatch_falls_back_to_spawn**: gRPC mock replies UNAUTHENTICATED →
///    `spawn_count == 1`
/// 6. **schema_major_mismatch_falls_back_to_spawn**: gRPC mock replies
///    FAILED_PRECONDITION → `spawn_count == 1`
/// 7. **probe_success_attaches_without_spawn** (ignored): gRPC mock at random port → attach,
///    `spawn_count == 0` — covered by grpc_wire_integration
mod common;

use flowsurface_engine_client::ProcessManager;

use std::{net::SocketAddr, time::Duration};
use tokio::net::TcpListener;

// ── helpers ───────────────────────────────────────────────────────────────────

/// Bind a loopback TCP port and return both the listener and its address.
async fn bind_loopback() -> (TcpListener, SocketAddr) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    (listener, addr)
}

// ── tests ─────────────────────────────────────────────────────────────────────

/// 1. Probe refused → fall through to spawn.
///
/// Binds a free port then immediately drops the listener so nothing listens
/// at the probe URL.  The happy-path attach (scenario 7) is covered by
/// grpc_wire_integration.rs::test_grpc_attach_to_already_running_server.
#[tokio::test]
async fn probe_refused_falls_back_to_spawn() {
    // Bind a free port then drop so nothing listens there.
    let addr = {
        let l = TcpListener::bind("127.0.0.1:0").await.unwrap();
        l.local_addr().unwrap()
        // drop here → port released
    };

    let pm = ProcessManager::new("false");
    let probe_url = format!("grpc://127.0.0.1:{}", addr.port());
    // Ignore the spawn error (no real Python); we only care about spawn_count.
    let _ = pm.try_attach_or_spawn(9_u16, &probe_url, "any-token").await;

    assert_eq!(
        pm.spawn_count(),
        1,
        "spawn must be attempted when probe is refused"
    );
}

/// 2. Empty token → probe URL is never contacted → straight to spawn.
#[tokio::test]
async fn empty_token_skips_probe() {
    let (listener, addr) = bind_loopback().await;

    let pm = ProcessManager::new("false");
    let probe_url = format!("grpc://127.0.0.1:{}", addr.port());
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

/// 3. Non-grpc:// probe URL (e.g. legacy ws://) → no probe → fall through to spawn.
#[tokio::test]
async fn non_grpc_probe_url_falls_back_to_spawn() {
    let (listener, addr) = bind_loopback().await;

    let pm = ProcessManager::new("false");
    // ws:// URL is not a valid gRPC probe target after G3.
    let probe_url = format!("ws://{addr}/");
    let _ = pm.try_attach_or_spawn(9_u16, &probe_url, "any-token").await;

    assert_eq!(
        pm.spawn_count(),
        1,
        "spawn must be attempted for non-grpc:// probe URL"
    );

    // The mock listener must NOT have received any connection.
    let no_connection = tokio::time::timeout(Duration::from_millis(50), listener.accept())
        .await
        .is_err();
    assert!(no_connection, "non-grpc:// probe URL must NOT be contacted");
}

/// 4. Empty probe URL → no probe → fall through to spawn.
#[tokio::test]
async fn empty_probe_url_falls_back_to_spawn() {
    let pm = ProcessManager::new("false");
    // Empty string does not start with "grpc://" → ConnectionRefused branch.
    let _ = pm.try_attach_or_spawn(9_u16, "", "any-token").await;

    assert_eq!(
        pm.spawn_count(),
        1,
        "spawn must be attempted when probe URL is empty"
    );
}

/// 5. Token mismatch → gRPC mock returns UNAUTHENTICATED → fall through to spawn.
#[tokio::test]
async fn token_mismatch_falls_back_to_spawn() {
    let mock = common::MockGrpcEngine::start_unauthenticated("correct-token").await;
    let probe_url = format!("grpc://{}", mock.addr);

    let pm = ProcessManager::new("false");
    // Send a different token so the mock rejects with UNAUTHENTICATED.
    let _ = pm
        .try_attach_or_spawn(9_u16, &probe_url, "wrong-token")
        .await;

    assert_eq!(
        pm.spawn_count(),
        1,
        "spawn must be attempted when probe returns UNAUTHENTICATED"
    );

    mock.shutdown().await;
}

/// 6. Schema major mismatch → gRPC mock returns FAILED_PRECONDITION → fall through to spawn.
#[tokio::test]
async fn schema_major_mismatch_falls_back_to_spawn() {
    let mock = common::MockGrpcEngine::start_schema_major_mismatch("any-token").await;
    let probe_url = format!("grpc://{}", mock.addr);

    let pm = ProcessManager::new("false");
    let _ = pm.try_attach_or_spawn(9_u16, &probe_url, "any-token").await;

    assert_eq!(
        pm.spawn_count(),
        1,
        "spawn must be attempted when probe returns FAILED_PRECONDITION"
    );

    mock.shutdown().await;
}

/// 7. Probe succeeds → attach without spawning Python.
///
/// This test requires a running gRPC mock server (real wire handshake).
/// It is marked `#[ignore]` because spinning up a Python engine in unit-test
/// scope is impractical. The actual happy-path is exercised end-to-end in
/// `grpc_wire_integration.rs::test_grpc_attach_to_already_running_server`.
///
/// Skeleton: when a live gRPC mock is present at `probe_url` with a matching
/// `token` and schema, `try_attach_or_spawn` must return `Ok` and
/// `spawn_count() == 0`.
///
/// TODO(grpc_wire_integration): wire test against real Python engine is done
/// in `engine-client/tests/grpc_wire_integration.rs`. The mock-only skeleton
/// here documents the expected behaviour without requiring a Python runtime.
#[tokio::test]
#[ignore = "requires a running Python gRPC engine — covered by grpc_wire_integration"]
async fn probe_success_attaches_without_spawn() {
    // To run this test manually, start the Python gRPC engine first:
    //   uv run python -m engine --port 50051 --token test-token --mode live
    // then run: cargo test probe_success_attaches_without_spawn -- --ignored
    let pm = ProcessManager::new("false");
    let probe_url = "grpc://127.0.0.1:50051";
    let result = pm.try_attach_or_spawn(9_u16, probe_url, "test-token").await;

    assert!(
        result.is_ok(),
        "attach to live gRPC server must succeed: {result:?}"
    );
    assert_eq!(
        pm.spawn_count(),
        0,
        "spawn must NOT be attempted when probe succeeds"
    );
}
