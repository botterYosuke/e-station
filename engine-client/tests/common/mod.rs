#![allow(dead_code)]

/// Shared gRPC mock server helper for engine-client integration tests.
///
/// Replaces the old tokio-tungstenite / fastwebsockets WS mocks that were
/// removed in G3.  `MockGrpcEngine` spins up a real tonic server on a random
/// loopback port, performs the HelloRequest→ReadyResponse handshake, and then
/// optionally streams pre-programmed events back to the client.
///
/// Usage pattern:
/// ```no_run
/// # use common::MockGrpcEngine;
/// let mock = MockGrpcEngine::start_basic("test-token").await;
/// let conn = EngineConnection::connect_grpc(&mock.target(), "test-token", AppMode::Live)
///     .await
///     .unwrap();
/// // ... use conn ...
/// mock.shutdown().await;
/// ```
use std::{net::SocketAddr, time::Duration};
use tokio::net::TcpListener;
use tokio_stream::wrappers::ReceiverStream;
use tonic::{Request, Response, Status, Streaming};

// Re-export the proto module used by the mock.
pub mod engine {
    tonic::include_proto!("engine");
}

use engine::data_engine_server::{DataEngine, DataEngineServer};
use engine::{Command, Event, ReadyResponse};

/// Convenience constant: `SCHEMA_MINOR + 1`, used in H13 tests.
pub const SCHEMA_MINOR_PLUS_ONE: u32 = flowsurface_engine_client::SCHEMA_MINOR + 1;

/// A controllable mock gRPC engine service.
///
/// After the initial HelloRequest, it sends a ReadyResponse and then
/// optionally streams additional events from the `extra_events` list.
pub struct MockServicer {
    pub token: String,
    /// Events to stream after ReadyResponse (after a short delay). Empty = keep
    /// stream open until client disconnects.
    pub extra_events: Vec<Event>,
    /// Optional delay between events (milliseconds).
    pub event_delay_ms: u64,
    /// If true, close the stream immediately after ReadyResponse.
    pub close_after_ready: bool,
    /// If true, return UNAUTHENTICATED for wrong token.
    pub enforce_token: bool,
    /// If false, skip server-side schema_major validation (for testing client-side H11 check).
    pub enforce_schema: bool,
    /// The schema_major the server expects from the client.  When `None`, defaults to
    /// `SCHEMA_MAJOR` from the crate.  Setting this to a value different from `SCHEMA_MAJOR`
    /// causes `enforce_schema=true` to reject the normal client Hello with FAILED_PRECONDITION.
    pub server_expected_schema_major: Option<u32>,
    /// Schema major to echo back in ReadyResponse (default = SCHEMA_MAJOR from crate).
    pub schema_major_override: Option<u32>,
    /// Schema minor to echo back in ReadyResponse (default = SCHEMA_MINOR from crate).
    pub schema_minor_override: Option<u32>,
    /// Capabilities to include in ReadyResponse (default = None = empty).
    pub capabilities: Option<engine::EngineCapabilities>,
    /// Channel sink to forward received commands to the test. If None, commands
    /// are silently discarded after the session.
    pub cmd_sink: Option<tokio::sync::mpsc::Sender<Command>>,
}

impl Default for MockServicer {
    fn default() -> Self {
        Self {
            token: String::new(),
            extra_events: vec![],
            event_delay_ms: 0,
            close_after_ready: false,
            enforce_token: true,
            enforce_schema: true,
            server_expected_schema_major: None,
            schema_major_override: None,
            schema_minor_override: None,
            capabilities: None,
            cmd_sink: None,
        }
    }
}

#[tonic::async_trait]
impl DataEngine for MockServicer {
    type SessionStream = ReceiverStream<Result<Event, Status>>;

    async fn session(
        &self,
        request: Request<Streaming<Command>>,
    ) -> Result<Response<Self::SessionStream>, Status> {
        let mut stream = request.into_inner();

        // Read the first command — must be HelloRequest.
        let first = stream
            .message()
            .await
            .map_err(|e| Status::internal(format!("recv error: {e}")))?
            .ok_or_else(|| Status::invalid_argument("stream closed before Hello"))?;

        let hello = match first.payload {
            Some(engine::command::Payload::Hello(h)) => h,
            _ => {
                return Err(Status::invalid_argument(
                    "first message must be HelloRequest",
                ));
            }
        };

        // Optionally validate token.
        if self.enforce_token && !self.token.is_empty() && hello.token != self.token {
            return Err(Status::unauthenticated("invalid token"));
        }

        // Schema major check: only validate when enforce_schema is true (default).
        // `server_expected_schema_major` sets the server's ground truth; defaults to
        // the crate's `SCHEMA_MAJOR`.  A mismatch causes FAILED_PRECONDITION.
        let expected_major = self
            .server_expected_schema_major
            .unwrap_or(flowsurface_engine_client::SCHEMA_MAJOR);
        let ready_schema_major = self
            .schema_major_override
            .unwrap_or(flowsurface_engine_client::SCHEMA_MAJOR);
        if self.enforce_schema && hello.schema_major != expected_major {
            return Err(Status::failed_precondition(format!(
                "schema major mismatch: client={} server={}",
                hello.schema_major, expected_major
            )));
        }

        let ready_schema_minor = self
            .schema_minor_override
            .unwrap_or(flowsurface_engine_client::SCHEMA_MINOR);

        // Build ReadyResponse.
        let ready_event = Event {
            payload: Some(engine::event::Payload::Ready(ReadyResponse {
                schema_major: ready_schema_major,
                schema_minor: ready_schema_minor,
                engine_version: "0.0.1-mock".to_string(),
                engine_session_id: "00000000-0000-0000-0000-000000000001".to_string(),
                capabilities: self.capabilities.clone(),
            })),
        };

        let (tx, rx) = tokio::sync::mpsc::channel::<Result<Event, Status>>(64);

        let extra = self.extra_events.clone();
        let event_delay_ms = self.event_delay_ms;
        let close_after_ready = self.close_after_ready;
        let cmd_sink = self.cmd_sink.clone();

        tokio::spawn(async move {
            // Send Ready first.
            if tx.send(Ok(ready_event)).await.is_err() {
                return;
            }

            if close_after_ready {
                return; // tx dropped → stream closed
            }

            // Send extra events (optionally with a delay between them).
            for ev in extra {
                if event_delay_ms > 0 {
                    tokio::time::sleep(Duration::from_millis(event_delay_ms)).await;
                }
                if tx.send(Ok(ev)).await.is_err() {
                    return;
                }
            }

            // Drain incoming commands, forwarding them to cmd_sink if set.
            loop {
                match stream.message().await {
                    Ok(Some(cmd)) => {
                        if let Some(ref sink) = cmd_sink {
                            // Ignore send errors (test may have already closed the receiver).
                            let _ = sink.send(cmd).await;
                        }
                    }
                    Ok(None) | Err(_) => break,
                }
            }
        });

        Ok(Response::new(ReceiverStream::new(rx)))
    }
}

/// A running mock gRPC engine bound to a random loopback port.
///
/// The `_handle` field retains the `JoinHandle` for the spawned server task so
/// that calling `shutdown()` aborts the task — panics in the server task will not
/// silently disappear.
pub struct MockGrpcEngine {
    pub addr: SocketAddr,
    shutdown_tx: tokio::sync::oneshot::Sender<()>,
    // M10: retain JoinHandle to enable abort() on shutdown().
    _handle: tokio::task::JoinHandle<()>,
}

impl MockGrpcEngine {
    /// Start a mock engine with the given token.
    ///
    /// Sends ReadyResponse after HelloRequest and keeps the stream open.
    pub async fn start_basic(token: &str) -> Self {
        Self::start(MockServicer {
            token: token.to_string(),
            ..Default::default()
        })
        .await
    }

    /// Start a mock engine that closes immediately after sending ReadyResponse.
    pub async fn start_close_after_ready(token: &str) -> Self {
        Self::start(MockServicer {
            token: token.to_string(),
            close_after_ready: true,
            ..Default::default()
        })
        .await
    }

    /// Start a mock engine that streams extra events after ReadyResponse.
    pub async fn start_with_events(token: &str, events: Vec<Event>) -> Self {
        Self::start(MockServicer {
            token: token.to_string(),
            extra_events: events,
            ..Default::default()
        })
        .await
    }

    /// Start a mock engine with specific capabilities in ReadyResponse.
    pub async fn start_with_capabilities(token: &str, caps: engine::EngineCapabilities) -> Self {
        Self::start(MockServicer {
            token: token.to_string(),
            capabilities: Some(caps),
            ..Default::default()
        })
        .await
    }

    /// Start a mock engine that returns UNAUTHENTICATED for any token other than
    /// `token` (for testing that a token mismatch causes the probe to fall through
    /// to spawn).
    pub async fn start_unauthenticated(token: &str) -> Self {
        // enforce_token=true with a specific token; any other token → UNAUTHENTICATED.
        Self::start(MockServicer {
            token: token.to_string(),
            enforce_token: true,
            ..Default::default()
        })
        .await
    }

    /// Start a mock engine that returns FAILED_PRECONDITION due to schema mismatch
    /// (for testing that a schema major mismatch causes the probe to fall through
    /// to spawn).
    ///
    /// The server declares it expects `SCHEMA_MAJOR + 999` from the client, so any
    /// normal client Hello (which carries the real `SCHEMA_MAJOR`) triggers the
    /// FAILED_PRECONDITION rejection.
    pub async fn start_schema_major_mismatch(token: &str) -> Self {
        let bogus = flowsurface_engine_client::SCHEMA_MAJOR.saturating_add(999);
        Self::start(MockServicer {
            token: token.to_string(),
            enforce_schema: true,
            // The server expects this bogus major; the client sends the real SCHEMA_MAJOR
            // → mismatch → FAILED_PRECONDITION.
            server_expected_schema_major: Some(bogus),
            ..Default::default()
        })
        .await
    }

    /// Start a mock engine that echoes a specific `schema_minor` in ReadyResponse.
    ///
    /// The server accepts any HelloRequest with the real SCHEMA_MAJOR but returns
    /// `override_minor` in ReadyResponse so the client's C2 warn path can be exercised.
    pub async fn start_schema_minor_override(token: &str, override_minor: u32) -> Self {
        Self::start(MockServicer {
            token: token.to_string(),
            schema_minor_override: Some(override_minor),
            ..Default::default()
        })
        .await
    }

    /// Start a mock engine that echoes a different `schema_major` in ReadyResponse,
    /// bypassing the server-side mismatch rejection (for client-side H11 test).
    ///
    /// The server accepts any HelloRequest regardless of schema_major, but
    /// returns `override_major` in ReadyResponse so the client's H11 check fires.
    pub async fn start_with_ready_schema_major_override(token: &str, override_major: u32) -> Self {
        Self::start(MockServicer {
            token: token.to_string(),
            // Disable server-side schema enforcement so HelloRequest passes even
            // with the real SCHEMA_MAJOR — we want to test the client-side H11
            // check on the ReadyResponse body, not the server-side reject path.
            enforce_schema: false,
            schema_major_override: Some(override_major),
            ..Default::default()
        })
        .await
    }

    /// Start a mock engine with a custom `MockServicer`.
    ///
    /// The returned `MockGrpcEngine` holds the server task's `JoinHandle` so that
    /// calling `shutdown()` aborts the task — panics in the server task will not
    /// silently disappear.
    pub async fn start(servicer: MockServicer) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let (shutdown_tx, shutdown_rx) = tokio::sync::oneshot::channel::<()>();

        let server = tonic::transport::Server::builder()
            .add_service(DataEngineServer::new(servicer))
            .serve_with_incoming_shutdown(
                tokio_stream::wrappers::TcpListenerStream::new(listener),
                async {
                    shutdown_rx.await.ok();
                },
            );

        // M10: retain the JoinHandle so shutdown() can abort() the server task,
        // ensuring server panics propagate to the test instead of being silently dropped.
        let handle = tokio::spawn(async move {
            server.await.ok();
        });

        Self {
            addr,
            shutdown_tx,
            _handle: handle,
        }
    }

    /// gRPC `http://` target string, e.g. `"http://127.0.0.1:12345"`.
    pub fn target(&self) -> String {
        format!("http://{}", self.addr)
    }

    /// Signal the mock server to stop and abort the server task.
    pub async fn shutdown(self) {
        // Send the shutdown signal first (graceful stop).
        let _ = self.shutdown_tx.send(());
        // Abort the task to ensure it is cleaned up even if it ignores the signal.
        self._handle.abort();
    }
}
