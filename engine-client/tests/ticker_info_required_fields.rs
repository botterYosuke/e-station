//! Phase D: verify that `parse_stock_ticker_entry` treats `min_ticksize` as
//! required and returns `None` for absent / invalid values.

use exchange::adapter::Exchange;
use flowsurface_engine_client::stock_meta::parse_stock_ticker_entry;
use serde_json::json;

#[test]
fn absent_min_ticksize_returns_none() {
    let dict = json!({"symbol": "7203", "lot_size": 100});
    assert!(
        parse_stock_ticker_entry(&dict, Exchange::TachibanaStock).is_none(),
        "absent min_ticksize must return None"
    );
}

#[test]
fn zero_min_ticksize_returns_none() {
    let dict = json!({"symbol": "7203", "lot_size": 100, "min_ticksize": 0.0});
    assert!(parse_stock_ticker_entry(&dict, Exchange::TachibanaStock).is_none());
}

#[test]
fn negative_min_ticksize_returns_none() {
    let dict = json!({"symbol": "7203", "lot_size": 100, "min_ticksize": -0.5});
    assert!(parse_stock_ticker_entry(&dict, Exchange::TachibanaStock).is_none());
}

#[test]
fn nan_min_ticksize_returns_none() {
    let dict = json!({"symbol": "7203", "lot_size": 100, "min_ticksize": f64::NAN});
    assert!(parse_stock_ticker_entry(&dict, Exchange::TachibanaStock).is_none());
}

#[test]
fn valid_min_ticksize_returns_some() {
    // Use 0.1 — a valid Power10 value (10^-1) that round-trips through
    // MinTicksize = Power10<-8, 2> without log10-rounding to a different decade.
    let dict = json!({"symbol": "7203", "lot_size": 100, "min_ticksize": 0.1_f64});
    let result = parse_stock_ticker_entry(&dict, Exchange::TachibanaStock);
    assert!(result.is_some(), "valid min_ticksize must parse");
    let (_, info, _) = result.unwrap();
    assert!(
        (info.min_ticksize.as_f32() - 0.1_f32).abs() < 1e-6,
        "min_ticksize value must match: expected 0.1, got {}",
        info.min_ticksize.as_f32()
    );
}
