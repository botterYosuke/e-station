/// Integration test: `EngineConnection::connect_grpc` performs the
/// HelloRequest/ReadyResponse handshake.
///
/// A mock gRPC server (tonic MockGrpcEngine) is started in-process.
///
/// G3: Migrated from WS (tokio-tungstenite) to gRPC (tonic MockGrpcEngine).
mod common;

use flowsurface_engine_client::{
    EngineClientError, EngineConnection, SCHEMA_MAJOR, SCHEMA_MINOR, dto::AppMode,
};

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

/// H11: client-side `ReadyResponse.schema_major` cross-check returns
/// `SchemaMismatch` when the server echoes a different major version.
///
/// The mock server is configured to bypass its own schema check
/// (`enforce_schema: false`) so the Hello passes, but it returns a bogus
/// `schema_major` in ReadyResponse.  The client must detect this and error.
#[tokio::test]
async fn ready_schema_major_mismatch_returns_schema_mismatch_error() {
    let bogus_major = SCHEMA_MAJOR.saturating_add(42);
    let mock =
        common::MockGrpcEngine::start_with_ready_schema_major_override("tok-h11", bogus_major)
            .await;

    let result = EngineConnection::connect_grpc(&mock.target(), "tok-h11", AppMode::Live).await;
    assert!(result.is_err(), "expected SchemaMismatch error");
    assert!(
        matches!(
            result.unwrap_err(),
            EngineClientError::SchemaMismatch {
                local_major,
                remote_major,
                ..
            } if local_major == SCHEMA_MAJOR && remote_major == bogus_major
        ),
        "error should be SchemaMismatch with correct fields"
    );

    mock.shutdown().await;
}

/// C2-Rust: `schema_minor` mismatch in ReadyResponse is a warning only —
/// the connection must still succeed.
///
/// Verifies the non-error path: normal connection (minor matches) succeeds.
/// The `schema_minor != SCHEMA_MINOR` log::warn branch is exercised when a
/// real engine or a custom mock returns a different minor.
#[tokio::test]
async fn ready_schema_minor_mismatch_does_not_fail_handshake() {
    // Smoke test: matching minor → no warn → handshake OK.
    let mock = common::MockGrpcEngine::start_basic("tok-c2").await;
    let result = EngineConnection::connect_grpc(&mock.target(), "tok-c2", AppMode::Live).await;
    assert!(
        result.is_ok(),
        "handshake must succeed when schema_minor matches: {:?}",
        result.err()
    );
    mock.shutdown().await;

    // Verify SCHEMA_MINOR is u32 (H4 type-change contract).
    let _: u32 = SCHEMA_MINOR;
}

/// H13: mock returns `schema_minor = SCHEMA_MINOR + 1` → connection must succeed.
///
/// `schema_minor` differences signal backward-compatible additions; the client
/// should emit a log::warn but must NOT return an error.
#[tokio::test]
async fn schema_minor_plus_one_still_connects() {
    let minor_ahead = SCHEMA_MINOR.saturating_add(1);
    let mock = common::MockGrpcEngine::start_schema_minor_override("tok-h13", minor_ahead).await;

    let result = EngineConnection::connect_grpc(&mock.target(), "tok-h13", AppMode::Live).await;
    assert!(
        result.is_ok(),
        "connection must succeed when server schema_minor is SCHEMA_MINOR+1: {:?}",
        result.err()
    );

    mock.shutdown().await;
}
