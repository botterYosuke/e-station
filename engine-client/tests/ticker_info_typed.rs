//! A2/A3: `TickerEntry` typed parse tests (Phase A — IPC schema hardening).
//!
//! Tests the `TickerEntry` discriminated union introduced in Phase A.
//! The `EngineEvent::TickerInfo.tickers: Vec<Value>` field is NOT changed
//! (that happens in Phase F); these tests exercise the parallel typed-parse
//! path that tries `serde_json::from_value::<TickerEntry>` for each element
//! and falls back to the existing `Value` path on failure.
//!
//! Test cases:
//! 1. `kind: "stock"` → `TickerEntry::Stock(StockTickerEntry)`
//! 2. `kind: "crypto"` → `TickerEntry::Crypto(CryptoTickerEntry)`
//! 3. No `kind` field → parse fails (fallback path)
//! 4. `min_ticksize` absent in Stock → `Option::<f32>::None`
//! 5. `min_ticksize` present in Stock → `Some(0.5)`

use flowsurface_engine_client::{CryptoTickerEntry, StockTickerEntry, TickerEntry};
use serde_json::json;

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
    });

    let entry: TickerEntry = serde_json::from_value(v).expect("must parse as TickerEntry");
    match entry {
        TickerEntry::Stock(s) => {
            assert_eq!(s.symbol, "7203");
            assert_eq!(s.display_symbol.as_deref(), Some("TOYOTA"));
            assert_eq!(s.display_name_ja.as_deref(), Some("トヨタ自動車"));
            assert_eq!(s.min_ticksize, Some(1.0));
            assert_eq!(s.lot_size, Some(100));
            assert_eq!(s.yobine_code.as_deref(), Some("103"));
            assert_eq!(s.sizyou_c.as_deref(), Some("00"));
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
    });

    let entry: TickerEntry = serde_json::from_value(v).expect("must parse as TickerEntry");
    match entry {
        TickerEntry::Crypto(c) => {
            assert_eq!(c.symbol, "BTC-USDT");
            assert_eq!(c.display_symbol.as_deref(), Some("BTC/USDT"));
            assert!((c.min_ticksize - 0.1).abs() < 1e-6);
            assert!((c.min_qty - 0.001).abs() < 1e-9);
            assert_eq!(c.contract_size, Some(1.0));
        }
        other => panic!("expected Crypto, got {other:?}"),
    }
}

// ── Test 3: missing `kind` → parse fails (fallback path) ─────────────────────

#[test]
fn missing_kind_fails_to_parse_as_ticker_entry() {
    let v = json!({
        "symbol": "7203",
        "min_ticksize": 1.0,
        "lot_size": 100,
    });

    let result = serde_json::from_value::<TickerEntry>(v);
    assert!(
        result.is_err(),
        "JSON without `kind` must fail TickerEntry parse (fallback path)"
    );
}

// ── Test 4: Stock with no min_ticksize → Option::None ────────────────────────

#[test]
fn stock_without_min_ticksize_deserializes_to_none() {
    let v = json!({
        "kind": "stock",
        "symbol": "6758",
        "lot_size": 100,
    });

    let entry: TickerEntry = serde_json::from_value(v).expect("must parse");
    let StockTickerEntry { min_ticksize, .. } = match entry {
        TickerEntry::Stock(s) => s,
        other => panic!("expected Stock, got {other:?}"),
    };
    assert_eq!(
        min_ticksize, None,
        "absent min_ticksize must deserialize as None"
    );
}

// ── Test 5: Stock with min_ticksize=0.5 → Some(0.5) ─────────────────────────

#[test]
fn stock_with_min_ticksize_deserializes_to_some() {
    let v = json!({
        "kind": "stock",
        "symbol": "6758",
        "min_ticksize": 0.5,
    });

    let entry: TickerEntry = serde_json::from_value(v).expect("must parse");
    let StockTickerEntry { min_ticksize, .. } = match entry {
        TickerEntry::Stock(s) => s,
        other => panic!("expected Stock, got {other:?}"),
    };
    assert!(
        matches!(min_ticksize, Some(v) if (v - 0.5).abs() < 1e-6),
        "min_ticksize=0.5 must round-trip as Some(0.5), got {min_ticksize:?}"
    );
}
