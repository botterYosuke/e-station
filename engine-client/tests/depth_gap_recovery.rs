/// Regression test: on `DepthGap`, `EngineClientBackend::depth_stream` must
/// self-recover by sending a fresh `RequestDepthSnapshot` and keep the stream
/// open. It must NOT yield `Disconnected` and terminate, because the UI layer
/// re-uses the same `Subscription::run_with` identity and would not respawn
/// the stream on its own.
use exchange::adapter::{Event, Exchange, venue_backend::VenueBackend};
use exchange::{PushFrequency, Ticker, TickerInfo};
use flowsurface_engine_client::dto::EngineEvent;
use flowsurface_engine_client::{
    EngineClientBackend, EngineConnection, SCHEMA_MAJOR, SCHEMA_MINOR,
};

use futures::StreamExt;
use futures_util::SinkExt;
use std::sync::Arc;
use std::time::Duration;
use tokio::net::TcpListener;
use tokio_tungstenite::{accept_async, tungstenite::Message};

#[test]
fn depth_gap_event_deserializes() {
    let payload = r#"{"event":"DepthGap","venue":"binance","ticker":"BTCUSDT","market":"linear_perp","stream_session_id":"sess-1"}"#;
    let parsed: EngineEvent = serde_json::from_str(payload).expect("DepthGap should parse");
    match parsed {
        EngineEvent::DepthGap { ticker, .. } => assert_eq!(ticker, "BTCUSDT"),
        other => panic!("expected DepthGap, got {other:?}"),
    }
}

#[tokio::test]
async fn depth_gap_triggers_snapshot_request_without_closing_stream() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let token = "tok";

    // Mock engine: handshake, expect Subscribe, push Snapshot + Gap, then
    // capture the next command (must be RequestDepthSnapshot).
    let (saw_request_tx, saw_request_rx) = tokio::sync::oneshot::channel::<bool>();
    tokio::spawn(async move {
        let (tcp, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(tcp).await.unwrap();

        // Handshake: read Hello, send Ready.
        let _hello = ws.next().await.unwrap().unwrap();
        let ready = serde_json::json!({
            "event": "Ready",
            "schema_major": SCHEMA_MAJOR,
            "schema_minor": SCHEMA_MINOR,
            "engine_version": "mock",
            "engine_session_id": "00000000-0000-0000-0000-000000000001",
            "capabilities": {}
        });
        ws.send(Message::Text(ready.to_string().into()))
            .await
            .unwrap();

        // Expect Subscribe (depth).
        let sub = ws.next().await.unwrap().unwrap().into_text().unwrap();
        let parsed: serde_json::Value = serde_json::from_str(&sub).unwrap();
        assert_eq!(parsed["op"], "Subscribe");
        assert_eq!(parsed["stream"], "depth");

        // The client subscribes to the broadcast channel slightly after sending
        // Subscribe (see `EngineClientBackend::depth_stream`). Give it a beat so
        // the test does not race with `connection.subscribe_events()`.
        tokio::time::sleep(Duration::from_millis(50)).await;

        // Send a snapshot to seat the tracker.
        let snapshot = serde_json::json!({
            "event": "DepthSnapshot",
            "venue": "binance",
            "ticker": "BTCUSDT",
            "market": "linear_perp",
            "stream_session_id": "sess-1",
            "sequence_id": 100i64,
            "bids": [{"price": "50000.0", "qty": "1.0"}],
            "asks": [{"price": "50001.0", "qty": "1.0"}],
            "checksum": null,
        });
        ws.send(Message::Text(snapshot.to_string().into()))
            .await
            .unwrap();
        tokio::time::sleep(Duration::from_millis(50)).await;

        // Send a DepthGap — the client must self-recover.
        let gap = serde_json::json!({
            "event": "DepthGap",
            "venue": "binance",
            "ticker": "BTCUSDT",
            "market": "linear_perp",
            "stream_session_id": "sess-1",
        });
        ws.send(Message::Text(gap.to_string().into()))
            .await
            .unwrap();

        // Next command from the client must be RequestDepthSnapshot for BTCUSDT.
        let saw = match tokio::time::timeout(Duration::from_secs(3), ws.next()).await {
            Ok(Some(Ok(msg))) => {
                let text = msg.into_text().unwrap();
                let v: serde_json::Value = serde_json::from_str(&text).unwrap();
                v["op"] == "RequestDepthSnapshot"
                    && v["ticker"] == "BTCUSDT"
                    && v["market"] == "linear_perp"
            }
            _ => false,
        };
        let _ = saw_request_tx.send(saw);

        // After replying with another snapshot, the stream should keep yielding events.
        let snapshot2 = serde_json::json!({
            "event": "DepthSnapshot",
            "venue": "binance",
            "ticker": "BTCUSDT",
            "market": "linear_perp",
            "stream_session_id": "sess-2",
            "sequence_id": 200i64,
            "bids": [{"price": "50000.0", "qty": "2.0"}],
            "asks": [{"price": "50001.0", "qty": "2.0"}],
            "checksum": null,
        });
        let _ = ws.send(Message::Text(snapshot2.to_string().into())).await;

        // Keep the connection open briefly so the client can drain.
        tokio::time::sleep(Duration::from_millis(200)).await;
    });

    tokio::time::sleep(Duration::from_millis(20)).await;
    let url = format!("ws://{addr}");
    let conn = Arc::new(EngineConnection::connect(&url, token).await.unwrap());

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

    // Drain at least: Connected, first DepthReceived, second DepthReceived (post-gap recovery).
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
    let _ = tokio::time::timeout(Duration::from_secs(3), drain).await;

    let saw_request = saw_request_rx.await.unwrap_or(false);

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
