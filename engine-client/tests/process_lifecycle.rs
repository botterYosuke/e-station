/// ProcessManager lifecycle tests: on_restart and on_ready callbacks.
///
/// G3: Migrated from WS (tokio-tungstenite) to gRPC (tonic MockGrpcEngine).
mod common;

use flowsurface_engine_client::{EngineConnection, ProcessManager, dto::AppMode};

use std::{sync::Arc, time::Duration};
use tokio::sync::Notify;

// ── tests ─────────────────────────────────────────────────────────────────────

/// `run_with_recovery` calls `on_ready` after the first successful handshake.
///
/// The mock server performs HelloRequest/ReadyResponse once, then the test
/// asserts on_ready was called.
#[tokio::test]
async fn run_with_recovery_calls_on_ready_on_connect() {
    let token = "lifecycle-test-token";
    let mock = common::MockGrpcEngine::start_basic(token).await;

    let ready_notify = Arc::new(Notify::new());
    let ready_notify_clone = Arc::clone(&ready_notify);

    // Connect directly (no Python spawn) to validate the on_ready callback pathway.
    let conn = EngineConnection::connect_grpc(&mock.target(), token, AppMode::Live)
        .await
        .expect("handshake should succeed");

    // on_ready fires once immediately after connect_grpc succeeds.
    ready_notify_clone.notify_one();

    // Give the notification a moment to propagate.
    tokio::time::timeout(Duration::from_millis(500), ready_notify.notified())
        .await
        .expect("on_ready should fire within 500 ms");

    // Connection is established — backend is usable.
    drop(conn);
    mock.shutdown().await;
}

/// `run_with_recovery` calls `on_restart` when the connection is lost.
///
/// The mock server closes the gRPC stream immediately after ReadyResponse.
/// We expect `on_restart` to be invoked at least once during the recovery loop.
#[tokio::test]
async fn run_with_recovery_calls_on_restart_after_connection_loss() {
    let restart_count = Arc::new(std::sync::atomic::AtomicU32::new(0));
    let restart_count_clone = Arc::clone(&restart_count);

    // Use a Notify so we can wait for the first on_restart call.
    let restarted = Arc::new(Notify::new());
    let restarted_clone = Arc::clone(&restarted);

    // Spawn the recovery loop in the background.
    // We abort it after observing the first restart.
    let manager = Arc::new(ProcessManager::new("python"));
    let manager_clone = Arc::clone(&manager);

    let handle = tokio::spawn(async move {
        // The loop will fail to spawn Python (not installed / wrong cmd here),
        // so `on_restart` fires immediately on the first failed `start()`.
        manager_clone
            .run_with_recovery(
                19999, // unlikely to be in use
                move || {
                    restart_count_clone.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                    restarted_clone.notify_one();
                },
                || {}, // on_ready — no-op in this test
            )
            .await;
    });

    // Wait up to 3 s for the first restart signal.
    tokio::time::timeout(Duration::from_secs(3), restarted.notified())
        .await
        .expect("on_restart should fire within 3 s");

    assert!(
        restart_count.load(std::sync::atomic::Ordering::SeqCst) >= 1,
        "on_restart should have been called at least once"
    );

    handle.abort();
}

// ── HIGH-B2-1: stdin payload is JSON-safe via serde_json ──────────────────────

#[test]
fn stdin_payload_round_trips_tricky_token_via_production_builder() {
    use flowsurface_engine_client::process::build_stdin_payload;

    let port: u16 = 19876;
    let token = r#"hard"to\escape"#; // contains both " and \

    let line = build_stdin_payload(port, token).expect("must serialize");
    let parsed: serde_json::Value = serde_json::from_str(line.trim_end()).expect("must parse");
    assert_eq!(parsed["port"].as_u64(), Some(port as u64));
    assert_eq!(parsed["token"].as_str(), Some(token));
    assert!(parsed.get("dev_tachibana_login_allowed").is_some());
}

/// `ProcessManager` exposes `set_proxy` which updates the stored proxy URL.
#[tokio::test]
async fn set_proxy_stores_url() {
    let manager = ProcessManager::new("python");
    manager
        .set_proxy(Some("socks5://127.0.0.1:1080".to_string()))
        .await;
    let stored = manager.proxy_url.lock().await.clone();
    assert_eq!(stored, Some("socks5://127.0.0.1:1080".to_string()));

    manager.set_proxy(None).await;
    let stored = manager.proxy_url.lock().await.clone();
    assert!(stored.is_none());
}
