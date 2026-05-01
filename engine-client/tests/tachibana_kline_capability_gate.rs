//! B3 HIGH-U-11 (Rust side): when the engine rejects a non-`"1d"`
//! `FetchKlines` for Tachibana with `code="not_implemented"`, the
//! client backend must surface the error as a typed `AdapterError`
//! (no panic, no unwrap), so the UI can clear the pane gracefully.
//!
//! The Python worker enforces the gate (`VenueCapabilityError` →
//! `Error{code:"not_implemented"}`); this test pins the Rust-side
//! contract that the response is propagated as `Err(...)` rather than
//! aborting the process.

use exchange::adapter::{AdapterError, MarketKind, venue_backend::VenueBackend};
use exchange::{Ticker, TickerInfo, Timeframe, adapter::Exchange};
use flowsurface_engine_client::{
    EngineClientBackend, EngineConnection, SCHEMA_MAJOR, SCHEMA_MINOR,
};

use futures_util::{SinkExt, StreamExt};
use std::sync::Arc;
use std::time::Duration;
use tokio::net::TcpListener;
use tokio_tungstenite::{accept_async, tungstenite::Message};

#[tokio::test]
async fn test_restored_pane_with_non_d1_timeframe_does_not_crash() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    let token = "tok";

    // Mock engine: handshake with capabilities advertising tachibana=["1d"],
    // then respond to a non-"1d" FetchKlines with an Error event.
    tokio::spawn(async move {
        let (tcp, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(tcp).await.unwrap();

        let _hello = ws.next().await.unwrap().unwrap();
        let ready = serde_json::json!({
            "event": "Ready",
            "schema_major": SCHEMA_MAJOR,
            "schema_minor": SCHEMA_MINOR,
            "engine_version": "mock",
            "engine_session_id": "00000000-0000-0000-0000-000000000001",
            "capabilities": {
                "supported_venues": ["tachibana"],
                "venue_capabilities": {
                    "tachibana": {"supported_timeframes": ["1d"]},
                },
            },
        });
        ws.send(Message::Text(ready.to_string().into()))
            .await
            .unwrap();

        // Expect FetchKlines, capture request_id, respond with Error.
        let raw = ws.next().await.unwrap().unwrap().into_text().unwrap();
        let parsed: serde_json::Value = serde_json::from_str(&raw).unwrap();
        assert_eq!(parsed["op"], "FetchKlines");
        assert_eq!(parsed["timeframe"], "5m");
        let req_id = parsed["request_id"].as_str().unwrap().to_string();

        let err = serde_json::json!({
            "event": "Error",
            "request_id": req_id,
            "code": "not_implemented",
            "message": "tachibana supports 1d only in Phase 1",
        });
        ws.send(Message::Text(err.to_string().into()))
            .await
            .unwrap();
        // Keep the connection open so the client doesn't disconnect first.
        tokio::time::sleep(Duration::from_secs(2)).await;
    });

    tokio::time::sleep(Duration::from_millis(50)).await;
    let url = format!("ws://{addr}");
    let conn = Arc::new(EngineConnection::connect(&url, token).await.unwrap());
    let backend = EngineClientBackend::new(conn, "tachibana", std::sync::Arc::new(tokio::sync::RwLock::new(flowsurface_engine_client::VenueCapsStore::new())));

    // Construct a stock TickerInfo and request a 5m kline (simulating a
    // restored pane with a stale non-"1d" timeframe).
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

    // Suppress unused warnings on imports needed only by other test
    // setups (kept for consistency with sibling files).
    let _ = MarketKind::Stock;
}
