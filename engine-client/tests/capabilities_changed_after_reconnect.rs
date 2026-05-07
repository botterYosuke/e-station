//! B4 R3 M3 繰越 — capabilities are updated when the engine reconnects.
//!
//! When a new `EngineConnection` is created after a Python restart, the
//! `capabilities()` snapshot on the **new** connection object must reflect
//! the `Ready.capabilities` that arrived in the **second** handshake — not
//! the stale value from the first connection.
//!
//! G3: Migrated from WS (tokio-tungstenite) to gRPC (tonic MockGrpcEngine).
mod common;

use flowsurface_engine_client::{EngineConnection, dto::AppMode};
use std::sync::Arc;

#[tokio::test]
async fn capabilities_snapshot_updated_on_reconnect() {
    let token = "cap-reconnect-test-token";

    // -- First server: empty capabilities --------------------------------
    let mock1 = common::MockGrpcEngine::start_basic(token).await;

    let conn1 = Arc::new(
        EngineConnection::connect_grpc(&mock1.target(), token, AppMode::Live)
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
    let mock2 = common::MockGrpcEngine::start_with_capabilities(
        token,
        common::engine::EngineCapabilities {
            supported_venues: vec!["tachibana".to_string()],
            supports_bulk_trades: true,
            supports_depth_binary: false,
        },
    )
    .await;

    let conn2 = Arc::new(
        EngineConnection::connect_grpc(&mock2.target(), token, AppMode::Live)
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

    mock1.shutdown().await;
    mock2.shutdown().await;
}
