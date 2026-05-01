//! Phase D: `EngineEvent::TickerInfo` → `TickerInfo` + `TickerDisplayMeta`
//! mapping for stock dicts via `parse_stock_ticker_entry`.
//!
//! These tests exercise the parser directly (the IPC framing layer is
//! covered by `dto_conversion.rs` / `schema_v1_2_roundtrip.rs`). The
//! invariants asserted here are:
//!
//! 1. `display_name_ja` is captured in the side-channel meta map (Q16:
//!    NOT folded into `Ticker` so the Hash impl stays ASCII-stable).
//! 2. `lot_size: Some(100)` survives through `TickerInfo::new_stock`.
//! 3. `quote_currency` is `Some(QuoteCurrency::Jpy)` immediately after
//!    construction — `normalize_after_load()` is **not** called on the
//!    IPC receive path (T0.2 L82 / HIGH-U-13).
//! 4. `yobine_code` is captured for the Rust-side `min_ticksize`
//!    resolution path.
//! 5. (Phase D) `min_ticksize` absent → `None` (no placeholder fallback).

use exchange::{QuoteCurrency, adapter::Exchange};
use flowsurface_engine_client::stock_meta::parse_stock_ticker_entry;
use serde_json::json;

#[test]
fn test_tachibana_ticker_info_carries_display_name_ja_and_lot_size() {
    let dict = json!({
        "symbol": "7203",
        "display_name_ja": "トヨタ自動車",
        "display_symbol": "TOYOTA",
        "lot_size": 100,
        "min_qty": 100,
        "min_ticksize": 1.0,
        "quote_currency": "JPY",
        "yobine_code": "103",
        "sizyou_c": "00",
    });
    let (_ticker, info, meta) = parse_stock_ticker_entry(&dict, Exchange::TachibanaStock)
        .expect("valid stock dict must parse");

    // (1) display_name_ja in side-channel meta.
    assert_eq!(meta.display_name_ja(), Some("トヨタ自動車"));

    // (2) lot_size survives.
    assert_eq!(info.lot_size, Some(100));

    // (3) quote_currency is JPY directly from new_stock — no normalize.
    assert_eq!(info.quote_currency, Some(QuoteCurrency::Jpy));
}

#[test]
fn test_tachibana_ticker_info_carries_yobine_code() {
    let dict = json!({
        "symbol": "7203",
        "lot_size": 100,
        "min_ticksize": 1.0,
        "yobine_code": "103",
    });
    let (_ticker, _info, meta) = parse_stock_ticker_entry(&dict, Exchange::TachibanaStock).unwrap();
    assert_eq!(meta.yobine_code(), Some("103"));
}

#[test]
fn test_parse_stock_ticker_uses_min_ticksize_from_dict() {
    // Use 0.1 (a valid Power10 value: 10^-1) to verify the exact value passes through.
    let dict = json!({
        "symbol": "7203",
        "lot_size": 100,
        "yobine_code": "103",
        "min_ticksize": 0.1_f64,
    });
    let (_ticker, info, _meta) = parse_stock_ticker_entry(&dict, Exchange::TachibanaStock).unwrap();
    assert!(
        (info.min_ticksize.as_f32() - 0.1).abs() < 1e-6,
        "min_ticksize should be 0.1 from dict, got {}",
        info.min_ticksize.as_f32()
    );
}

#[test]
fn test_parse_stock_ticker_returns_none_when_min_ticksize_absent() {
    // Phase D: Python guarantees min_ticksize; absent means malformed entry → skip.
    let dict = json!({
        "symbol": "7203",
        "lot_size": 100,
    });
    assert!(
        parse_stock_ticker_entry(&dict, Exchange::TachibanaStock).is_none(),
        "absent min_ticksize must return None (Phase D IPC contract)"
    );
}
