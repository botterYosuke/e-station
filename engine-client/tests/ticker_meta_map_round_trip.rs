//! B4 / T4 R1: `EngineClientBackend` exposes a `Ticker -> TickerDisplayMeta`
//! side-channel that the UI's incremental search consults.
//!
//! G3: Migrated from WS (tokio-tungstenite) to gRPC (tonic MockGrpcEngine).
mod common;

use common::engine;
use common::engine::data_engine_server::{DataEngine, DataEngineServer};
use exchange::{
    Ticker,
    adapter::{Exchange, MarketKind, venue_backend::VenueBackend},
};
use flowsurface_engine_client::{EngineClientBackend, EngineConnection, dto::AppMode};

use std::sync::Arc;
use std::time::Duration;
use tokio::net::TcpListener;
use tokio_stream::wrappers::{ReceiverStream, TcpListenerStream};
use tonic::{Request, Response, Status, Streaming};

// ── Inline mock that serves ListTickers with Tachibana data ──────────────────

struct TachibanaTickerMock;

#[tonic::async_trait]
impl DataEngine for TachibanaTickerMock {
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
        let hello = match first.payload {
            Some(engine::command::Payload::Hello(h)) => h,
            _ => {
                return Err(Status::invalid_argument(
                    "first message must be HelloRequest",
                ));
            }
        };
        let schema_major = flowsurface_engine_client::SCHEMA_MAJOR;
        if hello.schema_major != schema_major {
            return Err(Status::failed_precondition("schema mismatch"));
        }

        let (tx, rx) = tokio::sync::mpsc::channel::<Result<engine::Event, Status>>(64);

        tokio::spawn(async move {
            // Send Ready with tachibana capabilities.
            let ready = engine::Event {
                payload: Some(engine::event::Payload::Ready(engine::ReadyResponse {
                    schema_major,
                    schema_minor: flowsurface_engine_client::SCHEMA_MINOR,
                    engine_version: "1.0.0-mock".to_string(),
                    engine_session_id: "00000000-0000-0000-0000-00000000beef".to_string(),
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

            // Wait for the ListTickers command and reply with TickerInfo.
            while let Ok(Some(cmd)) = stream.message().await {
                if let Some(engine::command::Payload::ListTickers(lt)) = cmd.payload {
                    let ticker_event = engine::Event {
                        payload: Some(engine::event::Payload::TickerInfo(
                            engine::TickerInfoEvent {
                                request_id: lt.request_id,
                                venue: "tachibana".to_string(),
                                tickers: vec![engine::TickerEntry {
                                    kind: Some(engine::ticker_entry::Kind::Stock(
                                        engine::StockTickerEntry {
                                            symbol: "7203".to_string(),
                                            display_symbol: Some("TOYOTA".to_string()),
                                            display_name_ja: Some("トヨタ自動車".to_string()),
                                            min_ticksize: 1.0,
                                            lot_size: Some(100),
                                            min_qty: Some(100.0),
                                            quote_currency: Some("JPY".to_string()),
                                            yobine_code: Some("103".to_string()),
                                            sizyou_c: Some("00".to_string()),
                                            venue_caps: Some(engine::VenueCaps {
                                                client_aggr_depth: true,
                                                supports_spread_display: false,
                                                qty_norm_kind: None,
                                            }),
                                        },
                                    )),
                                }],
                            },
                        )),
                    };
                    let _ = tx.send(Ok(ticker_event)).await;
                }
                // Keep the stream open after answering.
            }
        });

        Ok(Response::new(ReceiverStream::new(rx)))
    }
}

#[tokio::test]
async fn ticker_meta_handle_empty_then_populated_then_reset() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let (shutdown_tx, shutdown_rx) = tokio::sync::oneshot::channel::<()>();

    let server = tonic::transport::Server::builder()
        .add_service(DataEngineServer::new(TachibanaTickerMock))
        .serve_with_incoming_shutdown(TcpListenerStream::new(listener), async {
            shutdown_rx.await.ok();
        });
    tokio::spawn(server);
    tokio::time::sleep(Duration::from_millis(10)).await;

    let target = format!("http://{addr}");
    let conn = EngineConnection::connect_grpc(&target, "round-trip-token", AppMode::Live)
        .await
        .expect("handshake should succeed");
    let backend = EngineClientBackend::new(
        Arc::new(conn),
        "tachibana",
        std::sync::Arc::new(tokio::sync::RwLock::new(
            flowsurface_engine_client::VenueCapsStore::new(),
        )),
    );

    // (1) Fresh backend: handle exposes an empty map.
    let handle = backend.ticker_meta_handle();
    assert!(
        handle.lock().await.is_empty(),
        "freshly constructed backend must start with an empty meta map"
    );

    // (2) Drive fetch_ticker_metadata; the mock answers with a Tachibana dict.
    let map = backend
        .fetch_ticker_metadata(&[MarketKind::Stock])
        .await
        .expect("fetch_ticker_metadata should succeed");
    let expected_ticker =
        Ticker::new_with_display("7203", Exchange::TachibanaStock, Some("TOYOTA"));
    assert!(
        map.contains_key(&expected_ticker),
        "TickerMetadataMap must contain the parsed Tachibana ticker"
    );

    // (3) Side-channel handle now carries the display meta.
    {
        let handle2 = backend.ticker_meta_handle();
        let guard = handle2.lock().await;
        let meta = guard
            .get(&expected_ticker)
            .expect("ticker_meta side-channel must hold an entry for 7203");
        assert_eq!(meta.display_name_ja(), Some("トヨタ自動車"));
        assert_eq!(meta.yobine_code(), Some("103"));
        assert_eq!(meta.sizyou_c(), Some("00"));
    }

    // (4) reset_ticker_meta() clears the map.
    backend.reset_ticker_meta().await;
    assert!(
        backend.ticker_meta_handle().lock().await.is_empty(),
        "reset_ticker_meta() must clear the side-channel map"
    );

    let _ = shutdown_tx.send(());
}
