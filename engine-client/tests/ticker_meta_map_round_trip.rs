//! B4 / T4 R1: `EngineClientBackend` exposes a `Ticker -> TickerDisplayMeta`
//! side-channel that the UI's incremental search consults.
//!
//! This integration test goes through the real handshake + IPC path:
//! 1. Spin up a mock WS server (tokio-tungstenite) that completes
//!    `Hello`/`Ready` and answers `ListTickers` with a Tachibana-shaped
//!    `TickerInfo` event carrying one stock dict.
//! 2. Wrap the resulting `EngineConnection` in `EngineClientBackend` and
//!    assert that `ticker_meta_handle()` returns an Arc<Mutex<_>> whose
//!    initial map is empty.
//! 3. Drive `fetch_ticker_metadata(&[MarketKind::Stock])` and assert the
//!    handle now contains the parsed `TickerDisplayMeta`.
//! 4. Call `reset_ticker_meta()` and assert the map is cleared (T4 H1
//!    reconnect-reset pin).

use exchange::{
    Ticker,
    adapter::{Exchange, MarketKind, venue_backend::VenueBackend},
};
use flowsurface_engine_client::{
    EngineClientBackend, EngineConnection, SCHEMA_MAJOR, SCHEMA_MINOR,
};

use futures_util::{SinkExt, StreamExt};
use std::sync::Arc;
use tokio::net::TcpListener;
use tokio_tungstenite::{accept_async, tungstenite::Message};

/// Spawn a mock engine server that completes the handshake then answers
/// the next `ListTickers` request with a single Tachibana stock dict.
async fn spawn_tachibana_mock(listener: TcpListener) {
    tokio::spawn(async move {
        let (tcp, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(tcp).await.unwrap();

        // Hello
        let _hello = ws.next().await.unwrap().unwrap();

        // Ready
        let ready = serde_json::json!({
            "event": "Ready",
            "schema_major": SCHEMA_MAJOR,
            "schema_minor": SCHEMA_MINOR,
            "engine_version": "1.0.0-mock",
            "engine_session_id": "00000000-0000-0000-0000-00000000beef",
            "capabilities": {
                "supported_venues": ["tachibana"],
                "venue_capabilities": {"tachibana": {"supported_timeframes": ["1d"]}}
            }
        });
        ws.send(Message::Text(ready.to_string().into()))
            .await
            .unwrap();

        // Next inbound frame must be a `ListTickers` for tachibana/stock.
        let cmd_msg = ws.next().await.unwrap().unwrap();
        let cmd_text = cmd_msg.into_text().unwrap();
        let parsed: serde_json::Value = serde_json::from_str(&cmd_text).unwrap();
        assert_eq!(parsed["op"], "ListTickers");
        assert_eq!(parsed["venue"], "tachibana");
        assert_eq!(parsed["market"], "stock");
        let request_id = parsed["request_id"].as_str().unwrap().to_owned();

        // TickerInfo response with one Tachibana dict.
        // Phase D: min_ticksize is required; absent entries are skipped.
        let resp = serde_json::json!({
            "event": "TickerInfo",
            "request_id": request_id,
            "venue": "tachibana",
            "tickers": [{
                "kind": "stock",
                "symbol": "7203",
                "display_symbol": "TOYOTA",
                "display_name_ja": "トヨタ自動車",
                "lot_size": 100,
                "min_qty": 100,
                "min_ticksize": 1.0,
                "quote_currency": "JPY",
                "yobine_code": "103",
                "sizyou_c": "00",
            }]
        });
        ws.send(Message::Text(resp.to_string().into()))
            .await
            .unwrap();

        // Hold the socket open so the client doesn't EOF mid-test.
        tokio::time::sleep(std::time::Duration::from_secs(2)).await;
    });
}

#[tokio::test]
async fn ticker_meta_handle_empty_then_populated_then_reset() {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    spawn_tachibana_mock(listener).await;

    // Give the spawned task a tick to start accepting.
    tokio::time::sleep(std::time::Duration::from_millis(10)).await;

    let url = format!("ws://{addr}");
    let conn = EngineConnection::connect(&url, "round-trip-token")
        .await
        .expect("handshake should succeed");
    let backend = EngineClientBackend::new(Arc::new(conn), "tachibana", std::sync::Arc::new(tokio::sync::RwLock::new(flowsurface_engine_client::VenueCapsStore::new())));

    // (1) Fresh backend: handle exposes an empty map.
    let handle = backend.ticker_meta_handle();
    assert!(
        handle.lock().await.is_empty(),
        "freshly constructed backend must start with an empty meta map"
    );

    // (2) Drive fetch_ticker_metadata; the mock answers with a Tachibana dict
    //     and the parser stages it into the side-channel.
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

    // (3) Side-channel handle now carries the display meta — UI reads via this Arc.
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

    // (4) reset_ticker_meta() clears the map (T4 H1 reconnect pin).
    backend.reset_ticker_meta().await;
    assert!(
        backend.ticker_meta_handle().lock().await.is_empty(),
        "reset_ticker_meta() must clear the side-channel map"
    );
}
