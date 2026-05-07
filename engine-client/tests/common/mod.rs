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
    /// Schema major to echo back in ReadyResponse (default = SCHEMA_MAJOR from crate).
    pub schema_major_override: Option<u32>,
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
            schema_major_override: None,
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

        // Schema major check.
        let schema_major = self
            .schema_major_override
            .unwrap_or(flowsurface_engine_client::SCHEMA_MAJOR as u32);
        if hello.schema_major != schema_major {
            return Err(Status::failed_precondition(format!(
                "schema major mismatch: client={} server={}",
                hello.schema_major, schema_major
            )));
        }

        // Build ReadyResponse.
        let ready_event = Event {
            payload: Some(engine::event::Payload::Ready(ReadyResponse {
                schema_major,
                schema_minor: flowsurface_engine_client::SCHEMA_MINOR as u32,
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
pub struct MockGrpcEngine {
    pub addr: SocketAddr,
    shutdown_tx: tokio::sync::oneshot::Sender<()>,
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

    /// Start a mock engine with a custom `MockServicer`.
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

        tokio::spawn(server);
        // Brief pause so tonic is accepting before the test starts.
        tokio::time::sleep(Duration::from_millis(10)).await;

        Self { addr, shutdown_tx }
    }

    /// gRPC `http://` target string, e.g. `"http://127.0.0.1:12345"`.
    pub fn target(&self) -> String {
        format!("http://{}", self.addr)
    }

    /// Signal the mock server to stop.
    pub async fn shutdown(self) {
        let _ = self.shutdown_tx.send(());
    }
}
