//! B3 HIGH-U-11 (Rust side): when the engine rejects a non-`"1d"`
//! `FetchKlines` for Tachibana with `code="not_implemented"`, the
//! client backend must surface the error as a typed `AdapterError`.
//!
//! G3: Migrated from WS (tokio-tungstenite) to gRPC (tonic MockGrpcEngine).
mod common;

use common::engine;
use common::engine::data_engine_server::{DataEngine, DataEngineServer};
use exchange::adapter::{AdapterError, MarketKind, venue_backend::VenueBackend};
use exchange::{Ticker, TickerInfo, Timeframe, adapter::Exchange};
use flowsurface_engine_client::{EngineClientBackend, EngineConnection, dto::AppMode};

use std::sync::Arc;
use std::time::Duration;
use tokio::net::TcpListener;
use tokio_stream::wrappers::{ReceiverStream, TcpListenerStream};
use tonic::{Request, Response, Status, Streaming};

// ── Inline mock ───────────────────────────────────────────────────────────────

struct KlineGateMock;

#[tonic::async_trait]
impl DataEngine for KlineGateMock {
    type SessionStream = ReceiverStream<Result<engine::Event, Status>>;

    async fn session(
        &self,
        request: Request<Streaming<engine::Command>>,
    ) -> Result<Response<Self::SessionStream>, Status> {
        let mut stream = request.into_inner();

        // Consume Hello.
        let first = stream
            .message()
            .await
            .ok()
            .flatten()
            .ok_or_else(|| Status::invalid_argument("stream closed before Hello"))?;
        match first.payload {
            Some(engine::command::Payload::Hello(_)) => {}
            _ => {
                return Err(Status::invalid_argument(
                    "first message must be HelloRequest",
                ));
            }
        }

        let schema_major = flowsurface_engine_client::SCHEMA_MAJOR;
        let (tx, rx) = tokio::sync::mpsc::channel::<Result<engine::Event, Status>>(64);

        tokio::spawn(async move {
            // Send Ready with tachibana capabilities (1d only).
            let ready = engine::Event {
                payload: Some(engine::event::Payload::Ready(engine::ReadyResponse {
                    schema_major,
                    schema_minor: flowsurface_engine_client::SCHEMA_MINOR,
                    engine_version: "mock".to_string(),
                    engine_session_id: "00000000-0000-0000-0000-000000000001".to_string(),
                    capabilities: Some(engine::EngineCapabilities {
                        supported_venues: vec!["tachibana".to_string()],
                        supports_bulk_trades: true,
                        supports_depth_binary: false,
                    }),
                })),
            };
            if tx.send(Ok(ready)).await.is_err() {
                return;
            }

            // Wait for FetchKlines command and respond with Error (not_implemented).
            while let Ok(Some(cmd)) = stream.message().await {
                if let Some(engine::command::Payload::FetchKlines(fk)) = cmd.payload {
                    let err_event = engine::Event {
                        payload: Some(engine::event::Payload::Error(engine::ErrorEvent {
                            request_id: Some(fk.request_id),
                            code: "not_implemented".to_string(),
                            message: "tachibana supports 1d only in Phase 1".to_string(),
                        })),
                    };
                    let _ = tx.send(Ok(err_event)).await;
                }
            }
        });

        Ok(Response::new(ReceiverStream::new(rx)))
    }
}

#[tokio::test]
async fn test_restored_pane_with_non_d1_timeframe_does_not_crash() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let (shutdown_tx, shutdown_rx) = tokio::sync::oneshot::channel::<()>();

    let server = tonic::transport::Server::builder()
        .add_service(DataEngineServer::new(KlineGateMock))
        .serve_with_incoming_shutdown(TcpListenerStream::new(listener), async {
            shutdown_rx.await.ok();
        });
    tokio::spawn(server);
    tokio::time::sleep(Duration::from_millis(10)).await;

    let target = format!("http://{addr}");
    let conn = Arc::new(
        EngineConnection::connect_grpc(&target, "tok", AppMode::Live)
            .await
            .unwrap(),
    );
    let backend = EngineClientBackend::new(
        conn,
        "tachibana",
        std::sync::Arc::new(tokio::sync::RwLock::new(
            flowsurface_engine_client::VenueCapsStore::new(),
        )),
    );

    // Construct a stock TickerInfo and request a 5m kline.
    let ticker = Ticker::new("7203", Exchange::TachibanaStock);
    let info = TickerInfo::new_stock(ticker, 1.0, 100.0, 100);

    let result = backend.fetch_klines(info, Timeframe::M5, None).await;
    // The contract: a typed error, NOT a panic.
    let err = result.expect_err("non-1d FetchKlines must surface as Err");
    match err {
        AdapterError::InvalidRequest(msg) => {
            assert!(
                msg.contains("not_implemented"),
                "unexpected InvalidRequest message: {msg}"
            );
        }
        other => panic!("expected InvalidRequest, got {other:?}"),
    }

    let _ = MarketKind::Stock; // keep import
    let _ = shutdown_tx.send(());
}
