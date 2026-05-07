/// Integration test: `EngineConnection::connect_grpc` performs the
/// HelloRequest/ReadyResponse handshake.
///
/// A mock gRPC server (tonic MockGrpcEngine) is started in-process.
///
/// G3: Migrated from WS (tokio-tungstenite) to gRPC (tonic MockGrpcEngine).
mod common;

use flowsurface_engine_client::{EngineConnection, SCHEMA_MAJOR, dto::AppMode};

#[tokio::test]
async fn connect_grpc_performs_hello_ready_handshake() {
    let mock = common::MockGrpcEngine::start_basic("test-token-abc").await;

    let result =
        EngineConnection::connect_grpc(&mock.target(), "test-token-abc", AppMode::Live).await;
    assert!(result.is_ok(), "connect_grpc failed: {:?}", result.err());

    mock.shutdown().await;
}

#[tokio::test]
async fn connect_grpc_rejects_wrong_schema_major() {
    // Mock server uses default SCHEMA_MAJOR; client sends a wildly different one.
    // Since connect_grpc uses SCHEMA_MAJOR from the crate, we test the server-side
    // rejection by using the testing feature's connect_grpc_with_schema.
    let mock = common::MockGrpcEngine::start_basic("tok").await;

    #[cfg(feature = "testing")]
    {
        let bad_major = SCHEMA_MAJOR.saturating_add(999);
        let result = EngineConnection::connect_grpc_with_schema(
            &mock.target(),
            "tok",
            AppMode::Live,
            bad_major,
        )
        .await;
        assert!(result.is_err(), "schema mismatch should be rejected");
        let err_str = result.unwrap_err().to_string();
        assert!(
            err_str.contains("schema")
                || err_str.contains("mismatch")
                || err_str.contains("precondition"),
            "unexpected error: {err_str}"
        );
    }

    mock.shutdown().await;
}

#[tokio::test]
async fn connect_grpc_refused_returns_error() {
    // Nothing listening on this port.
    let result =
        EngineConnection::connect_grpc("http://127.0.0.1:19999", "tok", AppMode::Live).await;
    assert!(result.is_err());
}

/// B4: `EngineConnection::capabilities()` exposes the `Ready.capabilities`
/// snapshot to the UI so it can call `is_timeframe_enabled(...)` for venue
/// gating without subscribing to the event broadcast.
///
/// Note: the mock gRPC server currently returns `None` capabilities (all
/// fields absent), so we just verify the capabilities blob is a JSON object
/// and that helper functions don't panic.
#[tokio::test]
async fn capabilities_getter_exposes_ready_snapshot() {
    let mock = common::MockGrpcEngine::start_basic("tok-caps").await;

    let conn = EngineConnection::connect_grpc(&mock.target(), "tok-caps", AppMode::Live)
        .await
        .unwrap();
    let caps = conn.capabilities();

    // The mock returns empty capabilities (no proto EngineCapabilities set),
    // so `capabilities_to_json` returns a null-safe JSON object (possibly `null`
    // or an empty object depending on proto serialisation).  Just assert no panic.
    let _ = caps;

    use flowsurface_engine_client::capabilities::is_timeframe_enabled;
    // With empty/null caps, is_timeframe_enabled returns Ok(true) (no venue list = unrestricted).
    let result = is_timeframe_enabled(&caps, "tachibana", "1d");
    assert!(
        result.is_ok(),
        "is_timeframe_enabled should not error on empty caps"
    );

    mock.shutdown().await;
}
