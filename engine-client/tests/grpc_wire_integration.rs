//! G2 wire integration: tonic client ↔ real server_grpc.py subprocess.
//!
//! Tests start a real Python subprocess via `std::process::Command` and use
//! `EngineConnection::connect_grpc()` to establish a session.
//!
//! All tests are `#[ignore]` because they require Python 3.10+ with grpcio
//! installed in the active virtualenv.  Run manually with:
//!
//! ```text
//! cargo test -p flowsurface-engine-client --test grpc_wire_integration -- --include-ignored --nocapture
//! ```
use flowsurface_engine_client::{EngineConnection, SCHEMA_MAJOR, dto::AppMode};
use std::time::Duration;

const TEST_TOKEN: &str = "grpc-integration-test-token";

/// RAII guard: kills the Python subprocess on drop (including on test panic).
struct KillOnDrop(std::process::Child);
impl Drop for KillOnDrop {
    fn drop(&mut self) {
        self.0.kill().ok();
    }
}

/// Bind an ephemeral loopback port and return the port number.
///
/// The listener is dropped immediately so the OS releases the port number
/// before Python binds to it.  On Linux the port is typically not reused
/// within the same process, but on Windows a brief gap is possible; the
/// `wait_for_port` retry loop compensates for that.
fn alloc_ephemeral_port() -> u16 {
    // SAFETY: the std TcpListener bind + local_addr only fails if the OS has
    // no free ports, which would cause the test to fail with a clear message.
    std::net::TcpListener::bind("127.0.0.1:0")
        .expect("failed to allocate ephemeral port")
        .local_addr()
        .expect("local_addr after bind")
        .port()
}

/// Start a Python gRPC server subprocess and return it.
///
/// M13: uses `uv run python -m engine` instead of bare `python` so that the
/// correct virtual environment managed by uv is activated automatically.
fn start_python_server(port: u16, token: &str) -> std::process::Child {
    std::process::Command::new("uv")
        .args([
            "run",
            "python",
            "-m",
            "engine",
            "--transport",
            "grpc",
            "--port",
            &port.to_string(),
            "--token",
            token,
        ])
        .current_dir(
            std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .unwrap(),
        )
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .spawn()
        .expect("failed to spawn uv run python -m engine")
}

/// Wait until the gRPC port is accepting TCP connections (up to 10 s).
async fn wait_for_port(port: u16) {
    let deadline = std::time::Duration::from_secs(10);
    let result = tokio::time::timeout(deadline, async {
        loop {
            if tokio::net::TcpStream::connect(format!("127.0.0.1:{port}"))
                .await
                .is_ok()
            {
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(100)).await;
        }
    })
    .await;
    assert!(
        result.is_ok(),
        "Python gRPC server failed to start on port {port} within 10s"
    );
    // brief additional wait for gRPC handshake readiness
    tokio::time::sleep(std::time::Duration::from_millis(50)).await;
}

#[tokio::test]
#[ignore = "requires Python+grpcio"]
async fn grpc_handshake_succeeds() {
    // M17: ephemeral port allocation — no fixed TEST_PORT constant.
    let port = alloc_ephemeral_port();
    let _child = KillOnDrop(start_python_server(port, TEST_TOKEN));
    wait_for_port(port).await;

    let target = format!("http://127.0.0.1:{port}");
    let conn = EngineConnection::connect_grpc(&target, TEST_TOKEN, AppMode::Live)
        .await
        .expect("gRPC handshake should succeed");
    let caps = conn.capabilities();
    // Capabilities is a JSON object (possibly empty but not null).
    assert!(
        caps.is_object(),
        "capabilities should be a JSON object, got: {caps}"
    );
}

#[tokio::test]
#[ignore = "requires Python+grpcio"]
async fn grpc_ping_pong_roundtrip() {
    // M17: each test allocates its own ephemeral port.
    let port = alloc_ephemeral_port();
    let _child = KillOnDrop(start_python_server(port, TEST_TOKEN));
    wait_for_port(port).await;

    let target = format!("http://127.0.0.1:{port}");
    let conn = EngineConnection::connect_grpc(&target, TEST_TOKEN, AppMode::Live)
        .await
        .expect("handshake should succeed");

    let mut events = conn.subscribe_events();

    conn.send(flowsurface_engine_client::dto::Command::Ping {
        request_id: "ping-1".to_string(),
    })
    .await
    .expect("send Ping");

    let pong = tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            match events.recv().await {
                Ok(flowsurface_engine_client::dto::EngineEvent::Pong { request_id }) => {
                    return request_id;
                }
                Ok(_) => continue,
                Err(_) => break "recv-error".to_string(),
            }
        }
    })
    .await
    .expect("should receive Pong within 5 s");

    assert_eq!(pong, "ping-1");
}

#[tokio::test]
#[ignore = "requires Python+grpcio"]
async fn grpc_wrong_token_rejected() {
    // M17: ephemeral port.
    let port = alloc_ephemeral_port();
    let _child = KillOnDrop(start_python_server(port, TEST_TOKEN));
    wait_for_port(port).await;

    let target = format!("http://127.0.0.1:{port}");
    let result = EngineConnection::connect_grpc(&target, "wrong-token", AppMode::Live).await;
    assert!(
        result.is_err(),
        "wrong token should be rejected, got: {:?}",
        result.ok()
    );
}

#[cfg(feature = "testing")]
#[tokio::test]
#[ignore = "requires Python+grpcio"]
async fn grpc_schema_major_mismatch_rejected() {
    // M17: ephemeral port.
    let port = alloc_ephemeral_port();
    let _child = KillOnDrop(start_python_server(port, TEST_TOKEN));
    wait_for_port(port).await;

    let target = format!("http://127.0.0.1:{port}");
    let bad_schema = SCHEMA_MAJOR.saturating_add(999);
    let result =
        EngineConnection::connect_grpc_with_schema(&target, TEST_TOKEN, AppMode::Live, bad_schema)
            .await;
    let err = result.expect_err("schema mismatch should be rejected");
    let msg = err.to_string();
    assert!(
        msg.contains("schema") || msg.contains("mismatch") || msg.contains("precondition"),
        "expected schema rejection error, got: {msg}"
    );
}
