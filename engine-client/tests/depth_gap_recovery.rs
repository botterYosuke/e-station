/// Regression test: on `DepthGap`, `EngineClientBackend::depth_stream` must
/// self-recover by sending a fresh `RequestDepthSnapshot` and keep the stream
/// open. It must NOT yield `Disconnected` and terminate.
///
/// G3: Migrated from WS (tokio-tungstenite) to gRPC (tonic MockGrpcEngine).
mod common;

use exchange::adapter::{Event, Exchange, venue_backend::VenueBackend};
use exchange::{PushFrequency, Ticker, TickerInfo};
use flowsurface_engine_client::dto::EngineEvent;
use flowsurface_engine_client::{EngineClientBackend, EngineConnection, dto::AppMode};

use common::engine;
use futures::StreamExt;
use std::sync::Arc;
use std::time::Duration;

#[test]
fn depth_gap_event_deserializes() {
    let payload = r#"{"event":"DepthGap","venue":"binance","ticker":"BTCUSDT","market":"linear_perp","stream_session_id":"sess-1"}"#;
    let parsed: EngineEvent = serde_json::from_str(payload).expect("DepthGap should parse");
    match parsed {
        EngineEvent::DepthGap { ticker, .. } => assert_eq!(ticker, "BTCUSDT"),
        other => panic!("expected DepthGap, got {other:?}"),
    }
}

/// Build a DepthSnapshotEvent proto Event.
fn make_snapshot(seq: i64, session_id: &str) -> engine::Event {
    engine::Event {
        payload: Some(engine::event::Payload::DepthSnapshot(
            engine::DepthSnapshotEvent {
                request_id: None,
                venue: "binance".to_string(),
                ticker: "BTCUSDT".to_string(),
                market: "linear_perp".to_string(),
                stream_session_id: session_id.to_string(),
                sequence_id: seq,
                bids: vec![engine::DepthLevel {
                    price: "50000.0".to_string(),
                    qty: "1.0".to_string(),
                }],
                asks: vec![engine::DepthLevel {
                    price: "50001.0".to_string(),
                    qty: "1.0".to_string(),
                }],
                checksum: None,
            },
        )),
    }
}

/// Build a DepthGapEvent proto Event.
fn make_gap(session_id: &str) -> engine::Event {
    engine::Event {
        payload: Some(engine::event::Payload::DepthGap(engine::DepthGapEvent {
            venue: "binance".to_string(),
            ticker: "BTCUSDT".to_string(),
            market: "linear_perp".to_string(),
            stream_session_id: session_id.to_string(),
        })),
    }
}

#[tokio::test]
async fn depth_gap_triggers_snapshot_request_without_closing_stream() {
    let token = "tok";

    // Command interceptor: captures commands sent by the client after the handshake.
    let (cmd_tx, mut cmd_rx) = tokio::sync::mpsc::channel::<engine::Command>(32);

    // Events: snapshot → brief pause → gap → brief pause → second snapshot.
    // We use event_delay_ms so the client has time to subscribe before events arrive.
    let events = vec![
        make_snapshot(100, "sess-1"),
        make_gap("sess-1"),
        make_snapshot(200, "sess-2"),
    ];

    let mock = common::MockGrpcEngine::start(common::MockServicer {
        token: token.to_string(),
        extra_events: events,
        event_delay_ms: 50, // 50 ms between events so the client can process them
        cmd_sink: Some(cmd_tx),
        ..Default::default()
    })
    .await;

    let conn = Arc::new(
        EngineConnection::connect_grpc(&mock.target(), token, AppMode::Live)
            .await
            .unwrap(),
    );

    let backend = EngineClientBackend::new(
        conn,
        "binance",
        std::sync::Arc::new(tokio::sync::RwLock::new(
            flowsurface_engine_client::VenueCapsStore::new(),
        )),
    );
    let ticker = Ticker::new("BTCUSDT", Exchange::BinanceLinear);
    let ticker_info = TickerInfo::new(ticker, 0.1, 0.001, None);

    let mut stream = backend.depth_stream(ticker_info, None, PushFrequency::ServerDefault);

    // Drain at least: Connected, first DepthReceived, second DepthReceived (post-gap).
    let mut connected = false;
    let mut depth_events = 0u32;
    let mut saw_disconnect = false;
    let drain = async {
        while let Some(ev) = stream.next().await {
            match ev {
                Event::Connected(_) => connected = true,
                Event::DepthReceived(_, _, _) => {
                    depth_events += 1;
                    if depth_events >= 2 {
                        break;
                    }
                }
                Event::Disconnected(_, _) => {
                    saw_disconnect = true;
                    break;
                }
                _ => {}
            }
        }
    };
    let _ = tokio::time::timeout(Duration::from_secs(5), drain).await;

    // Check that the client sent RequestDepthSnapshot in response to the gap.
    let mut saw_request = false;
    // Drain cmd_rx briefly to catch any buffered commands.
    let check = tokio::time::timeout(Duration::from_secs(1), async {
        while let Some(cmd) = cmd_rx.recv().await {
            if let Some(engine::command::Payload::RequestDepthSnapshot(r)) = cmd.payload {
                if r.ticker == "BTCUSDT" && r.market == "linear_perp" {
                    saw_request = true;
                    break;
                }
            }
        }
    });
    let _ = check.await;

    mock.shutdown().await;

    assert!(connected, "expected Connected event before depth recovery");
    assert!(
        saw_request,
        "client should send RequestDepthSnapshot in response to DepthGap"
    );
    assert!(
        !saw_disconnect,
        "DepthGap must not yield Disconnected — the stream should self-recover"
    );
    assert!(
        depth_events >= 2,
        "expected pre-gap and post-gap DepthReceived events, got {depth_events}"
    );
}
