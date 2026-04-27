//! B3 HIGH-U-9: `EngineEvent::TickerInfo` → `TickerInfo` + `TickerDisplayMeta`
//! mapping for Tachibana stock dicts.
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
//!    resolution path planned for B5.

use exchange::{QuoteCurrency, adapter::Exchange};
use flowsurface_engine_client::tachibana_meta::parse_tachibana_ticker_dict;
use serde_json::json;

#[test]
fn test_tachibana_ticker_info_carries_display_name_ja_and_lot_size() {
    let dict = json!({
        "symbol": "7203",
        "display_name_ja": "トヨタ自動車",
        "display_symbol": "TOYOTA",
        "lot_size": 100,
        "min_qty": 100,
        "quote_currency": "JPY",
        "yobine_code": "103",
        "sizyou_c": "00",
    });
    let (_ticker, info, meta) = parse_tachibana_ticker_dict(&dict, Exchange::TachibanaStock)
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
        "yobine_code": "103",
    });
    let (_ticker, _info, meta) =
        parse_tachibana_ticker_dict(&dict, Exchange::TachibanaStock).unwrap();
    assert_eq!(meta.yobine_code(), Some("103"));
}

#[test]
fn test_parse_tachibana_ticker_uses_min_ticksize_from_dict() {
    // B5: when Python sends `min_ticksize` in the IPC dict, Rust must use
    // it instead of TACHIBANA_MIN_TICKSIZE_PLACEHOLDER_F32 (1.0).
    // Use 0.1 (a valid Power10 value: 10^-1) to distinguish from the 1.0 placeholder.
    let dict = json!({
        "symbol": "7203",
        "lot_size": 100,
        "yobine_code": "103",
        "min_ticksize": 0.1_f64,
    });
    let (_ticker, info, _meta) =
        parse_tachibana_ticker_dict(&dict, Exchange::TachibanaStock).unwrap();
    assert!(
        (info.min_ticksize.as_f32() - 0.1).abs() < 1e-6,
        "min_ticksize should be 0.1 from dict, got {}",
        info.min_ticksize.as_f32()
    );
}

#[test]
fn test_parse_tachibana_ticker_falls_back_to_placeholder_when_min_ticksize_absent() {
    // B5: when `min_ticksize` is absent from the dict, the placeholder is used.
    use flowsurface_engine_client::tachibana_meta::TACHIBANA_MIN_TICKSIZE_PLACEHOLDER_F32;
    let dict = json!({
        "symbol": "7203",
        "lot_size": 100,
    });
    let (_ticker, info, _meta) =
        parse_tachibana_ticker_dict(&dict, Exchange::TachibanaStock).unwrap();
    assert!(
        (info.min_ticksize.as_f32() - TACHIBANA_MIN_TICKSIZE_PLACEHOLDER_F32).abs() < 1e-6,
        "absent min_ticksize should fall back to placeholder"
    );
}
