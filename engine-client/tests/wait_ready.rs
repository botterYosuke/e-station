/// Phase 7 T1.3 — regression: `wait_ready()` resolves once the connection
/// is established, since `connect_grpc()` blocks on the Hello/Ready handshake.
///
/// Catches any future refactor that decouples connect_grpc() from Ready
/// (e.g. moving the handshake to a deferred task), which would silently
/// re-introduce the UI-1 race window where `ListTickers` is sent before
/// the engine is warm.
///
/// G3: Migrated from WS (tokio-tungstenite) to gRPC (tonic MockGrpcEngine).
mod common;

use flowsurface_engine_client::{EngineConnection, dto::AppMode};
use std::time::Duration;

#[tokio::test]
async fn wait_ready_resolves_immediately_post_connect() {
    let mock = common::MockGrpcEngine::start_basic("wait-ready-token").await;

    let conn = EngineConnection::connect_grpc(&mock.target(), "wait-ready-token", AppMode::Live)
        .await
        .expect("handshake should succeed");

    // wait_ready() must not block — connect_grpc() already awaited ReadyResponse.
    tokio::time::timeout(Duration::from_millis(100), conn.wait_ready())
        .await
        .expect("wait_ready should resolve immediately, never block")
        .expect("wait_ready should be Ok");

    mock.shutdown().await;
}
