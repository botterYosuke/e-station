/// Fix 1: EngineConnection::wait_closed() resolves when the remote side closes.
///
/// The mock gRPC server sends ReadyResponse then closes its stream.
/// `wait_closed()` must resolve within a generous timeout.
///
/// G3: Migrated from WS (tokio-tungstenite) to gRPC (tonic MockGrpcEngine).
mod common;

use flowsurface_engine_client::{EngineConnection, dto::AppMode};
use std::time::Duration;

#[tokio::test]
async fn wait_closed_resolves_when_stream_drops() {
    // close_after_ready=true → server sends ReadyResponse then drops the stream.
    let mock = common::MockGrpcEngine::start_close_after_ready("close-test-token").await;

    let conn = EngineConnection::connect_grpc(&mock.target(), "close-test-token", AppMode::Live)
        .await
        .expect("connect should succeed");

    // wait_closed() should resolve once the server drops the gRPC stream.
    tokio::time::timeout(Duration::from_secs(5), conn.wait_closed())
        .await
        .expect("wait_closed() should resolve within 5 s after remote stream close");

    mock.shutdown().await;
}

#[tokio::test]
async fn wait_closed_resolves_after_second_connect() {
    // Second independent connection also satisfies the contract.
    let mock = common::MockGrpcEngine::start_close_after_ready("close-test-2").await;

    let conn = EngineConnection::connect_grpc(&mock.target(), "close-test-2", AppMode::Live)
        .await
        .expect("connect should succeed");

    tokio::time::timeout(Duration::from_secs(5), conn.wait_closed())
        .await
        .expect("wait_closed() should resolve after gRPC stream close");

    mock.shutdown().await;
}
