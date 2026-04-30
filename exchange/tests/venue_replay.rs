//! Pin tests for `Venue::Replay` / `Exchange::ReplayStock` (§4a).
//!
//! These verify that the enum variants, string round-trips, capability flags,
//! and metadata are all coherent before the Rust-side wiring (§4b/§4c) builds
//! on top of them.

use flowsurface_exchange::adapter::{Exchange, MarketKind, Venue};
use flowsurface_exchange::{QuoteCurrency, Timeframe};
use std::str::FromStr;

// ── Venue round-trip ──────────────────────────────────────────────────────────

#[test]
fn venue_from_str_replay_lowercase() {
    assert_eq!(Venue::from_str("replay").unwrap(), Venue::Replay);
}

#[test]
fn venue_from_str_replay_uppercase() {
    assert_eq!(Venue::from_str("REPLAY").unwrap(), Venue::Replay);
}

#[test]
fn venue_from_str_replay_mixed_case() {
    assert_eq!(Venue::from_str("Replay").unwrap(), Venue::Replay);
}

#[test]
fn venue_display_replay() {
    assert_eq!(Venue::Replay.to_string(), "Replay");
}

#[test]
fn venue_all_contains_replay() {
    assert!(
        Venue::ALL.contains(&Venue::Replay),
        "Venue::ALL must include Venue::Replay"
    );
}

// ── Exchange::ReplayStock ─────────────────────────────────────────────────────

#[test]
fn exchange_replay_stock_from_venue_and_market() {
    let result = Exchange::from_venue_and_market(Venue::Replay, MarketKind::Stock);
    assert_eq!(
        result,
        Some(Exchange::ReplayStock),
        "from_venue_and_market(Replay, Stock) must return ReplayStock"
    );
}

#[test]
fn replay_stock_market_type_is_stock() {
    assert_eq!(Exchange::ReplayStock.market_type(), MarketKind::Stock);
}

#[test]
fn replay_stock_venue_is_replay() {
    assert_eq!(Exchange::ReplayStock.venue(), Venue::Replay);
}

#[test]
fn replay_stock_default_quote_currency_is_jpy() {
    assert_eq!(
        Exchange::ReplayStock.default_quote_currency(),
        QuoteCurrency::Jpy
    );
}

#[test]
fn exchange_all_contains_replay_stock() {
    assert!(
        Exchange::ALL.contains(&Exchange::ReplayStock),
        "Exchange::ALL must include Exchange::ReplayStock"
    );
}

// ── kline timeframe support ───────────────────────────────────────────────────

#[test]
fn replay_stock_supports_d1() {
    assert!(
        Exchange::ReplayStock.supports_kline_timeframe(Timeframe::D1),
        "ReplayStock must support D1 (Daily bar from NautilusTrader)"
    );
}

#[test]
fn replay_stock_supports_m1() {
    assert!(
        Exchange::ReplayStock.supports_kline_timeframe(Timeframe::M1),
        "ReplayStock must support M1 (Minute bar from NautilusTrader)"
    );
}

#[test]
fn replay_stock_does_not_support_h1() {
    assert!(
        !Exchange::ReplayStock.supports_kline_timeframe(Timeframe::H1),
        "ReplayStock must NOT support H1 (sub-minute/hour aggregation not emitted)"
    );
}

#[test]
fn replay_stock_does_not_support_m5() {
    assert!(
        !Exchange::ReplayStock.supports_kline_timeframe(Timeframe::M5),
        "ReplayStock must NOT support M5"
    );
}
