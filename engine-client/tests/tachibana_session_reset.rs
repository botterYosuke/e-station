/// Regression test: when Python restarts and issues a new `stream_session_id`,
/// the Rust gap-detector must treat the new session as fresh — DepthDiffs for
/// the new session should be accepted without triggering RequestDepthSnapshot.
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
fn depth_snapshot_with_new_session_id_deserializes() {
    let payload = r#"{
        "event": "DepthSnapshot",
        "venue": "tachibana",
        "ticker": "7203",
        "market": "stock",
        "stream_session_id": "session-2:1",
        "sequence_id": 1,
        "bids": [],
        "asks": [],
        "checksum": null
    }"#;
    let parsed: EngineEvent = serde_json::from_str(payload).expect("DepthSnapshot should parse");
    match parsed {
        EngineEvent::DepthSnapshot {
            stream_session_id,
            sequence_id,
            ..
        } => {
            assert_eq!(stream_session_id, "session-2:1");
            assert_eq!(sequence_id, 1);
        }
        other => panic!("expected DepthSnapshot, got {other:?}"),
    }
}

/// Build a DepthSnapshotEvent proto Event.
fn make_snapshot(ticker: &str, market: &str, session_id: &str, seq: i64) -> engine::Event {
    engine::Event {
        payload: Some(engine::event::Payload::DepthSnapshot(
            engine::DepthSnapshotEvent {
                request_id: None,
                venue: "tachibana".to_string(),
                ticker: ticker.to_string(),
                market: market.to_string(),
                stream_session_id: session_id.to_string(),
                sequence_id: seq,
                bids: vec![engine::DepthLevel {
                    price: "3000.0".to_string(),
                    qty: "100.0".to_string(),
                }],
                asks: vec![engine::DepthLevel {
                    price: "3001.0".to_string(),
                    qty: "50.0".to_string(),
                }],
                checksum: None,
            },
        )),
    }
}

/// Build a DepthDiffEvent proto Event.
fn make_diff(ticker: &str, market: &str, session_id: &str, seq: i64, prev: i64) -> engine::Event {
    engine::Event {
        payload: Some(engine::event::Payload::DepthDiff(engine::DepthDiffEvent {
            venue: "tachibana".to_string(),
            ticker: ticker.to_string(),
            market: market.to_string(),
            stream_session_id: session_id.to_string(),
            sequence_id: seq,
            prev_sequence_id: prev,
            bids: vec![engine::DepthLevel {
                price: "2998.0".to_string(),
                qty: "150.0".to_string(),
            }],
            asks: vec![],
        })),
    }
}

/// After a Python restart (new stream_session_id), DepthDiffs for the new
/// session should be accepted by the gap-detector — no RequestDepthSnapshot
/// should be triggered.
#[tokio::test]
async fn new_session_id_resets_gap_detector_and_accepts_diffs() {
    let token = "tok";

    // Command interceptor: watch for RequestDepthSnapshot.
    let (cmd_tx, mut cmd_rx) = tokio::sync::mpsc::channel::<engine::Command>(32);

    // Events: snapshot1 (session-1) → snapshot2 (session-2) → diff (session-2).
    let events = vec![
        make_snapshot("7203", "stock", "session-1:1", 100),
        make_snapshot("7203", "stock", "session-2:1", 1),
        make_diff("7203", "stock", "session-2:1", 2, 1),
    ];

    let mock = common::MockGrpcEngine::start(common::MockServicer {
        token: token.to_string(),
        extra_events: events,
        event_delay_ms: 50,
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
        "tachibana",
        std::sync::Arc::new(tokio::sync::RwLock::new(
            flowsurface_engine_client::VenueCapsStore::new(),
        )),
    );
    let ticker = Ticker::new("7203", Exchange::TachibanaStock);
    let ticker_info = TickerInfo::new(ticker, 1.0, 1.0, None);

    let mut stream = backend.depth_stream(ticker_info, None, PushFrequency::ServerDefault);

    // Drain until we see 3 DepthReceived events (snapshot1, snapshot2, diff).
    let mut depth_events = 0u32;
    let drain = async {
        while let Some(ev) = stream.next().await {
            if matches!(ev, Event::DepthReceived(_, _, _)) {
                depth_events += 1;
                if depth_events >= 3 {
                    break;
                }
            }
        }
    };
    let _ = tokio::time::timeout(Duration::from_secs(5), drain).await;

    // Check that the client did NOT send RequestDepthSnapshot.
    let mut saw_snapshot_request = false;
    let check = tokio::time::timeout(Duration::from_millis(300), async {
        while let Some(cmd) = cmd_rx.recv().await {
            if let Some(engine::command::Payload::RequestDepthSnapshot(_)) = cmd.payload {
                saw_snapshot_request = true;
                break;
            }
        }
    });
    let _ = check.await;

    mock.shutdown().await;

    assert!(
        !saw_snapshot_request,
        "new session_id with prev_seq=1 must not trigger RequestDepthSnapshot"
    );
    assert!(
        depth_events >= 3,
        "expected DepthReceived for snapshot1, snapshot2, and diff; got {depth_events}"
    );
}
