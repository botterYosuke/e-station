/// Regression test: when Python restarts and issues a new `stream_session_id`,
/// the Rust gap-detector must treat the new session as fresh — DepthDiffs for
/// the new session should be accepted without triggering RequestDepthSnapshot.
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
        EngineEvent::DepthSnapshot { stream_session_id, sequence_id, .. } => {
            assert_eq!(stream_session_id, "session-2:1");
            assert_eq!(sequence_id, 1);
        }
        other => panic!("expected DepthSnapshot, got {other:?}"),
    }
}

/// After a Python restart (new stream_session_id), DepthDiffs for the new
/// session should be accepted by the gap-detector — no RequestDepthSnapshot
/// should be triggered.
#[tokio::test]
async fn new_session_id_resets_gap_detector_and_accepts_diffs() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let token = "tok";

    let (snapshot_request_tx, snapshot_request_rx) =
        tokio::sync::oneshot::channel::<bool>();

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

        // Give the client a beat to subscribe to the broadcast channel.
        tokio::time::sleep(Duration::from_millis(50)).await;

        // Phase 1: initial session-1 snapshot seats the tracker.
        let snapshot1 = serde_json::json!({
            "event": "DepthSnapshot",
            "venue": "tachibana",
            "ticker": "7203",
            "market": "stock",
            "stream_session_id": "session-1:1",
            "sequence_id": 100i64,
            "bids": [{"price": "3000.0", "qty": "100.0"}],
            "asks": [{"price": "3001.0", "qty": "50.0"}],
            "checksum": null,
        });
        ws.send(Message::Text(snapshot1.to_string().into()))
            .await
            .unwrap();
        tokio::time::sleep(Duration::from_millis(50)).await;

        // Phase 2: Python restarts — new stream_session_id with seq starting at 1.
        let snapshot2 = serde_json::json!({
            "event": "DepthSnapshot",
            "venue": "tachibana",
            "ticker": "7203",
            "market": "stock",
            "stream_session_id": "session-2:1",
            "sequence_id": 1i64,
            "bids": [{"price": "2999.0", "qty": "200.0"}],
            "asks": [{"price": "3002.0", "qty": "75.0"}],
            "checksum": null,
        });
        ws.send(Message::Text(snapshot2.to_string().into()))
            .await
            .unwrap();
        tokio::time::sleep(Duration::from_millis(50)).await;

        // Phase 3: DepthDiff for the new session — prev=1, seq=2.
        // The gap-detector should accept this without requesting a snapshot.
        let diff = serde_json::json!({
            "event": "DepthDiff",
            "venue": "tachibana",
            "ticker": "7203",
            "market": "stock",
            "stream_session_id": "session-2:1",
            "sequence_id": 2i64,
            "prev_sequence_id": 1i64,
            "bids": [{"price": "2998.0", "qty": "150.0"}],
            "asks": [],
        });
        ws.send(Message::Text(diff.to_string().into()))
            .await
            .unwrap();

        // Wait briefly to see if the client sends RequestDepthSnapshot.
        let saw_snapshot_request = match tokio::time::timeout(
            Duration::from_millis(300),
            ws.next(),
        )
        .await
        {
            Ok(Some(Ok(msg))) => {
                let text = msg.into_text().unwrap_or_default();
                let v: serde_json::Value =
                    serde_json::from_str(&text).unwrap_or_default();
                v["op"] == "RequestDepthSnapshot"
            }
            _ => false,
        };
        let _ = snapshot_request_tx.send(saw_snapshot_request);

        tokio::time::sleep(Duration::from_millis(100)).await;
    });

    tokio::time::sleep(Duration::from_millis(20)).await;
    let url = format!("ws://{addr}");
    let conn = Arc::new(EngineConnection::connect(&url, token).await.unwrap());

    let backend = EngineClientBackend::new(conn, "tachibana");
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
    let _ = tokio::time::timeout(Duration::from_secs(3), drain).await;

    let saw_snapshot_request = snapshot_request_rx.await.unwrap_or(true);

    assert!(
        !saw_snapshot_request,
        "new session_id with prev_seq=1 must not trigger RequestDepthSnapshot"
    );
    assert!(
        depth_events >= 3,
        "expected DepthReceived for snapshot1, snapshot2, and diff; got {depth_events}"
    );
}
