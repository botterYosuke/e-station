//! `TickerEntry` typed parse tests (Phase F — venue_caps required).

use flowsurface_engine_client::TickerEntry;
use serde_json::json;

fn venue_caps_json() -> serde_json::Value {
    json!({ "client_aggr_depth": false, "supports_spread_display": true })
}

// ── Test 1: kind="stock" parses as TickerEntry::Stock ────────────────────────

#[test]
fn kind_stock_parses_as_ticker_entry_stock() {
    let v = json!({
        "kind": "stock",
        "symbol": "7203",
        "display_symbol": "TOYOTA",
        "display_name_ja": "トヨタ自動車",
        "min_ticksize": 1.0,
        "lot_size": 100,
        "quote_currency": "JPY",
        "yobine_code": "103",
        "sizyou_c": "00",
        "venue_caps": venue_caps_json(),
    });

    let entry: TickerEntry = serde_json::from_value(v).expect("must parse as TickerEntry");
    match entry {
        TickerEntry::Stock(s) => {
            assert_eq!(s.symbol, "7203");
            assert_eq!(s.display_symbol.as_deref(), Some("TOYOTA"));
            assert_eq!(s.display_name_ja.as_deref(), Some("トヨタ自動車"));
            assert!((s.min_ticksize - 1.0).abs() < 1e-6);
            assert_eq!(s.lot_size, Some(100));
            assert_eq!(s.yobine_code.as_deref(), Some("103"));
            assert_eq!(s.sizyou_c.as_deref(), Some("00"));
            assert!(!s.venue_caps.client_aggr_depth);
            assert!(s.venue_caps.supports_spread_display);
        }
        other => panic!("expected Stock, got {other:?}"),
    }
}

// ── Test 2: kind="crypto" parses as TickerEntry::Crypto ──────────────────────

#[test]
fn kind_crypto_parses_as_ticker_entry_crypto() {
    let v = json!({
        "kind": "crypto",
        "symbol": "BTC-USDT",
        "display_symbol": "BTC/USDT",
        "min_ticksize": 0.1,
        "min_qty": 0.001,
        "contract_size": 1.0,
        "venue_caps": venue_caps_json(),
    });

    let entry: TickerEntry = serde_json::from_value(v).expect("must parse as TickerEntry");
    match entry {
        TickerEntry::Crypto(c) => {
            assert_eq!(c.symbol, "BTC-USDT");
            assert_eq!(c.display_symbol.as_deref(), Some("BTC/USDT"));
            assert!((c.min_ticksize - 0.1).abs() < 1e-6);
            assert!((c.min_qty - 0.001).abs() < 1e-9);
            assert_eq!(c.contract_size, Some(1.0));
            assert!(!c.venue_caps.client_aggr_depth);
        }
        other => panic!("expected Crypto, got {other:?}"),
    }
}

// ── Test 3: missing `kind` → parse fails ─────────────────────────────────────

#[test]
fn missing_kind_fails_to_parse_as_ticker_entry() {
    let v = json!({
        "symbol": "7203",
        "min_ticksize": 1.0,
        "lot_size": 100,
        "venue_caps": venue_caps_json(),
    });

    let result = serde_json::from_value::<TickerEntry>(v);
    assert!(
        result.is_err(),
        "JSON without `kind` must fail TickerEntry parse"
    );
}

// ── Test 4: Stock with no min_ticksize → parse fails (Phase F: required) ─────

#[test]
fn stock_without_min_ticksize_fails_to_parse() {
    let v = json!({
        "kind": "stock",
        "symbol": "6758",
        "lot_size": 100,
        "venue_caps": venue_caps_json(),
    });
    let result = serde_json::from_value::<TickerEntry>(v);
    assert!(
        result.is_err(),
        "stock without min_ticksize must fail parse in Phase F"
    );
}

// ── Test 5: Stock with min_ticksize=0.5 → 0.5 ────────────────────────────────

#[test]
fn stock_with_min_ticksize_deserializes() {
    let v = json!({
        "kind": "stock",
        "symbol": "6758",
        "min_ticksize": 0.5,
        "venue_caps": venue_caps_json(),
    });

    let entry: TickerEntry = serde_json::from_value(v).expect("must parse");
    let min_ticksize = match entry {
        TickerEntry::Stock(s) => s.min_ticksize,
        other => panic!("expected Stock, got {other:?}"),
    };
    assert!(
        (min_ticksize - 0.5).abs() < 1e-6,
        "min_ticksize=0.5 must round-trip as 0.5, got {min_ticksize}"
    );
}

// ── Test 6: venue_caps absent → parse fails (Phase F: required) ──────────────

#[test]
fn stock_without_venue_caps_fails_to_parse() {
    let v = json!({
        "kind": "stock",
        "symbol": "7203",
        "min_ticksize": 1.0,
        "lot_size": 100,
    });
    let result = serde_json::from_value::<TickerEntry>(v);
    assert!(
        result.is_err(),
        "stock without venue_caps must fail parse in Phase F"
    );
}

#[test]
fn crypto_without_venue_caps_fails_to_parse() {
    let v = json!({
        "kind": "crypto",
        "symbol": "BTC-USDT",
        "min_ticksize": 0.1,
        "min_qty": 0.001,
    });
    let result = serde_json::from_value::<TickerEntry>(v);
    assert!(
        result.is_err(),
        "crypto without venue_caps must fail parse in Phase F"
    );
}

// ── Test 8: Vec<TickerEntry> with one bad entry fails the WHOLE frame ────────
//
// Pins the Phase F "trust Python" design choice: when a `TickerInfo` event is
// deserialized, a single malformed `TickerEntry` causes the entire `Vec` parse
// to fail (and therefore the entire `EngineEvent` parse to fail in
// connection.rs). All other entries in the same frame are dropped with no
// per-element tolerance. Python-side adapters MUST validate each entry before
// emitting (see `python/engine/exchanges/base.py::is_valid_ticker_entry`).
//
// If you change the Vec<TickerEntry> deserialization to be tolerant
// (e.g. via a custom deserializer that filters out bad entries), this test
// will fail and force a fresh design discussion — that's the point.

#[test]
fn ticker_info_vec_with_one_bad_entry_fails_whole_frame() {
    // Two valid entries + one stock missing the required `min_ticksize`.
    let frame = json!({
        "event": "TickerInfo",
        "request_id": "rid-1",
        "venue": "tachibana",
        "tickers": [
            {
                "kind": "stock",
                "symbol": "7203",
                "min_ticksize": 1.0,
                "venue_caps": venue_caps_json(),
            },
            {
                // missing min_ticksize on purpose
                "kind": "stock",
                "symbol": "BAD",
                "venue_caps": venue_caps_json(),
            },
            {
                "kind": "stock",
                "symbol": "6758",
                "min_ticksize": 0.5,
                "venue_caps": venue_caps_json(),
            },
        ]
    });

    let result = serde_json::from_value::<flowsurface_engine_client::dto::EngineEvent>(frame);
    assert!(
        result.is_err(),
        "Phase F design: one malformed TickerEntry must fail the entire \
         Vec<TickerEntry> deserialization. If this test starts passing, the \
         per-element tolerance was reintroduced and the silent-failure mode \
         changed — review the design before updating the test."
    );
}

#[test]
fn ticker_info_vec_with_all_valid_entries_parses_into_typed_vec() {
    let frame = json!({
        "event": "TickerInfo",
        "request_id": "rid-2",
        "venue": "tachibana",
        "tickers": [
            {
                "kind": "stock",
                "symbol": "7203",
                "min_ticksize": 1.0,
                "venue_caps": venue_caps_json(),
            },
            {
                "kind": "stock",
                "symbol": "6758",
                "min_ticksize": 0.5,
                "venue_caps": venue_caps_json(),
            },
        ]
    });

    let event = serde_json::from_value::<flowsurface_engine_client::dto::EngineEvent>(frame)
        .expect("all-valid frame must parse");
    match event {
        flowsurface_engine_client::dto::EngineEvent::TickerInfo { tickers, .. } => {
            assert_eq!(tickers.len(), 2);
        }
        other => panic!("expected TickerInfo, got {other:?}"),
    }
}
