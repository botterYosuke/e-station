/// Regression test: when `depth_stream` receives `EngineEvent::Error` with
/// `code="unknown_venue"`, it must yield `Event::Disconnected` and terminate
/// the stream — not silently swallow the error with `_ => {}`.
///
/// Issue #28 層3 Fix / Issue #38 追加修正:
/// `code="stream_error"` は kline など兄弟ストリームの死をブロードキャストしたものなので
/// depth_stream は無視して継続しなければならない。
mod common;

use exchange::adapter::{Event, Exchange, venue_backend::VenueBackend};
use exchange::{PushFrequency, Ticker, TickerInfo};
use flowsurface_engine_client::dto::EngineEvent;
use flowsurface_engine_client::{EngineClientBackend, EngineConnection, dto::AppMode};

use common::engine;
use futures::StreamExt;
use std::sync::Arc;
use std::time::Duration;

/// Build an `ErrorEvent` proto Event (field 16 in engine.proto).
fn make_error_event(code: &str, message: &str) -> engine::Event {
    engine::Event {
        payload: Some(engine::event::Payload::Error(engine::ErrorEvent {
            request_id: None,
            code: code.to_string(),
            message: message.to_string(),
        })),
    }
}

#[test]
fn error_event_deserializes() {
    let payload = r#"{"event":"Error","code":"unknown_venue","message":"Subscribe: unknown venue 'kabu_station'"}"#;
    let parsed: EngineEvent = serde_json::from_str(payload).expect("Error should parse");
    match parsed {
        EngineEvent::Error { code, .. } => assert_eq!(code, "unknown_venue"),
        other => panic!("expected Error, got {other:?}"),
    }
}

#[tokio::test]
async fn depth_stream_error_event_yields_disconnected() {
    let token = "tok";

    // After the snapshot, inject an Error{code="unknown_venue"}.
    let events = vec![
        // First send a DepthSnapshot so the stream emits Connected + DepthReceived.
        engine::Event {
            payload: Some(engine::event::Payload::DepthSnapshot(
                engine::DepthSnapshotEvent {
                    request_id: None,
                    venue: "kabu_station".to_string(),
                    ticker: "1234".to_string(),
                    market: "stock".to_string(),
                    stream_session_id: "sess-1".to_string(),
                    sequence_id: 1,
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
        },
        make_error_event("unknown_venue", "Subscribe: unknown venue 'kabu_station'"),
    ];

    let mock = common::MockGrpcEngine::start(common::MockServicer {
        token: token.to_string(),
        extra_events: events,
        event_delay_ms: 50,
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
        "kabu_station",
        std::sync::Arc::new(tokio::sync::RwLock::new(
            flowsurface_engine_client::VenueCapsStore::new(),
        )),
    );
    let ticker = Ticker::new("1234", Exchange::KabuStationStock);
    let ticker_info = TickerInfo::new(ticker, 1.0, 1.0, None);

    let mut stream = backend.depth_stream(ticker_info, None, PushFrequency::ServerDefault);

    let mut connected = false;
    let mut saw_disconnect = false;
    let mut disconnect_message = String::new();

    let drain = async {
        while let Some(ev) = stream.next().await {
            match ev {
                Event::Connected(_) => connected = true,
                Event::Disconnected(_, msg) => {
                    saw_disconnect = true;
                    disconnect_message = msg;
                    break;
                }
                _ => {}
            }
        }
    };
    let _ = tokio::time::timeout(Duration::from_secs(5), drain).await;

    mock.shutdown().await;

    assert!(connected, "expected Connected event");
    assert!(
        saw_disconnect,
        "EngineEvent::Error must yield Event::Disconnected — stream must not hang forever"
    );
    assert!(
        disconnect_message.contains("unknown_venue") || !disconnect_message.is_empty(),
        "Disconnected message should be non-empty, got: {disconnect_message:?}"
    );
}

/// `code="stream_error"` は kline など別ストリームの終了通知なので
/// depth_stream は切断せず継続すること。
#[tokio::test]
async fn depth_stream_ignores_sibling_stream_error() {
    let token = "tok";

    // stream_error → kline died (depth とは無関係)
    // その後は DepthDiff を送って depth が生きていることを確認する
    let events = vec![
        engine::Event {
            payload: Some(engine::event::Payload::DepthSnapshot(
                engine::DepthSnapshotEvent {
                    request_id: None,
                    venue: "kabu_station".to_string(),
                    ticker: "1234".to_string(),
                    market: "stock".to_string(),
                    stream_session_id: "sess-1".to_string(),
                    sequence_id: 1,
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
        },
        // kline が死んだ通知 — depth_stream は無視すること
        make_error_event(
            "stream_error",
            "Stream ('tachibana', '1234', 'stock', 'kline', '1d') terminated unexpectedly: tachibana stream_kline is implemented in T5",
        ),
        // depth 自体は生きており続けてデータが届く
        engine::Event {
            payload: Some(engine::event::Payload::DepthDiff(engine::DepthDiffEvent {
                venue: "kabu_station".to_string(),
                ticker: "1234".to_string(),
                market: "stock".to_string(),
                stream_session_id: "sess-1".to_string(),
                sequence_id: 2,
                prev_sequence_id: 1,
                bids: vec![],
                asks: vec![],
            })),
        },
    ];

    let mock = common::MockGrpcEngine::start(common::MockServicer {
        token: token.to_string(),
        extra_events: events,
        event_delay_ms: 50,
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
        "kabu_station",
        std::sync::Arc::new(tokio::sync::RwLock::new(
            flowsurface_engine_client::VenueCapsStore::new(),
        )),
    );
    let ticker = Ticker::new("1234", Exchange::KabuStationStock);
    let ticker_info = TickerInfo::new(ticker, 1.0, 1.0, None);

    let mut stream = backend.depth_stream(ticker_info, None, PushFrequency::ServerDefault);

    let mut depth_count = 0usize;
    let mut saw_disconnect = false;

    let drain = async {
        while let Some(ev) = stream.next().await {
            match ev {
                Event::DepthReceived(..) => {
                    depth_count += 1;
                    if depth_count >= 2 {
                        break; // snapshot + diff を受信できれば成功
                    }
                }
                Event::Disconnected(..) => {
                    saw_disconnect = true;
                    break;
                }
                _ => {}
            }
        }
    };
    let _ = tokio::time::timeout(Duration::from_secs(5), drain).await;

    mock.shutdown().await;

    assert!(
        !saw_disconnect,
        "stream_error (kline) を受け取って depth_stream が切断してはいけない"
    );
    assert!(
        depth_count >= 2,
        "snapshot + diff の 2 回 DepthReceived が届くはず (got {depth_count})"
    );
}
