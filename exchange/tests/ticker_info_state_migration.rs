//! Regression test for the T0.2 `TickerInfo` field additions
//! (`lot_size`, `quote_currency`).
//!
//! Why this exists: `TickerInfo` is a `#[derive(Hash, Eq)]` value that's
//! both stored as map keys at runtime and persisted into `saved-state.json`
//! via the dashboard layout. A blind field-addition would break older state
//! files. We test the *serde* contract: a state file written before the
//! new fields existed must still deserialize, with `lot_size: None` and
//! `quote_currency: None` defaulting in.

use flowsurface_exchange::adapter::Exchange;
use flowsurface_exchange::{Ticker, TickerInfo};

#[test]
fn old_persisted_ticker_info_deserializes_with_defaulted_new_fields() {
    // Simulates a serialization produced by the v1.1 codepath, before the
    // T0.2 lot_size / quote_currency additions.
    let ticker = Ticker::new("BTCUSDT", Exchange::BinanceLinear);
    let ticker_json = serde_json::to_string(&ticker).unwrap();
    let old_payload = format!(
        r#"{{
            "ticker": {ticker_json},
            "min_ticksize": 0.1,
            "min_qty": 0.001,
            "contract_size": null
        }}"#
    );

    let info: TickerInfo = serde_json::from_str(&old_payload)
        .expect("old TickerInfo payload must round-trip via serde(default)");

    assert_eq!(info.ticker, ticker);
    assert!(
        info.lot_size.is_none(),
        "missing lot_size must default to None"
    );
    assert!(
        info.quote_currency.is_none(),
        "missing quote_currency must default to None"
    );

    // The UI formatter must never see `None` — it folds in the venue's
    // default quote currency when restoring from disk.
    assert_eq!(
        info.resolved_quote_currency(),
        flowsurface_exchange::QuoteCurrency::Usdt,
        "Binance defaults to Usdt"
    );
}

#[test]
fn new_ticker_info_round_trips() {
    let info = TickerInfo::new(
        Ticker::new("BTCUSDT", Exchange::BinanceLinear),
        0.1,
        0.001,
        None,
    );
    let json = serde_json::to_string(&info).unwrap();
    let back: TickerInfo = serde_json::from_str(&json).unwrap();
    assert_eq!(info, back);
}

#[test]
fn tachibana_ticker_info_uses_jpy_quote() {
    let info = TickerInfo::new_stock(
        Ticker::new("7203", Exchange::TachibanaStock),
        1.0,
        100.0,
        100,
    );
    assert_eq!(info.lot_size, Some(100));
    assert_eq!(
        info.resolved_quote_currency(),
        flowsurface_exchange::QuoteCurrency::Jpy
    );
}

#[test]
fn ticker_accepts_alphanumeric_5char_codes() {
    // 立花 venue admits non-numeric tickers like 130A0 (新興市場の優先出資証券).
    // Verify Ticker::new doesn't panic — Phase 1 acceptance criterion (Q14).
    let _ = Ticker::new("130A0", Exchange::TachibanaStock);
    let _ = Ticker::new("7203", Exchange::TachibanaStock);
}

#[test]
fn timeframe_serde_uses_display_form() {
    use flowsurface_exchange::Timeframe;
    let v = serde_json::to_string(&Timeframe::D1).unwrap();
    assert_eq!(v, r#""1d""#);
    let back: Timeframe = serde_json::from_str(r#""1d""#).unwrap();
    assert_eq!(back, Timeframe::D1);
}

#[test]
fn timeframe_serde_accepts_legacy_variant_form() {
    use flowsurface_exchange::Timeframe;
    // Old saved-state.json values used the variant name directly.
    let back: Timeframe = serde_json::from_str(r#""D1""#).unwrap();
    assert_eq!(back, Timeframe::D1);
    let back: Timeframe = serde_json::from_str(r#""M1""#).unwrap();
    assert_eq!(back, Timeframe::M1);
}

#[test]
fn normalize_quote_currency_fills_in_default_for_crypto() {
    // Old persisted payload (pre-T0.2) lacks quote_currency.
    let ticker = Ticker::new("BTCUSDT", Exchange::BinanceLinear);
    let ticker_json = serde_json::to_string(&ticker).unwrap();
    let old_payload = format!(
        r#"{{
            "ticker": {ticker_json},
            "min_ticksize": 0.1,
            "min_qty": 0.001,
            "contract_size": null
        }}"#
    );
    let mut info: TickerInfo = serde_json::from_str(&old_payload).unwrap();
    assert!(info.quote_currency.is_none());

    info.normalize_after_load();

    assert_eq!(
        info.quote_currency,
        Some(flowsurface_exchange::QuoteCurrency::Usdt),
        "Binance must fold in Usdt as the venue default",
    );
}

#[test]
fn normalize_quote_currency_fills_in_default_for_tachibana() {
    let ticker = Ticker::new("7203", Exchange::TachibanaStock);
    let ticker_json = serde_json::to_string(&ticker).unwrap();
    let old_payload = format!(
        r#"{{
            "ticker": {ticker_json},
            "min_ticksize": 1.0,
            "min_qty": 100.0,
            "contract_size": null
        }}"#
    );
    let mut info: TickerInfo = serde_json::from_str(&old_payload).unwrap();
    assert!(info.quote_currency.is_none());

    info.normalize_after_load();

    assert_eq!(
        info.quote_currency,
        Some(flowsurface_exchange::QuoteCurrency::Jpy),
        "Tachibana must fold in Jpy as the venue default",
    );
}

#[test]
fn normalize_quote_currency_preserves_existing_value() {
    let mut info = TickerInfo::new(
        Ticker::new("BTCUSDC", Exchange::BinanceSpot),
        0.1,
        0.001,
        None,
    );
    info.quote_currency = Some(flowsurface_exchange::QuoteCurrency::Usdc);
    info.normalize_after_load();
    assert_eq!(
        info.quote_currency,
        Some(flowsurface_exchange::QuoteCurrency::Usdc),
        "explicit value must not be overwritten",
    );
}

#[test]
fn stock_qty_in_quote_value_ignores_size_in_quote_ccy_flag() {
    use flowsurface_exchange::adapter::MarketKind;
    use flowsurface_exchange::unit::{Price, Qty};

    let qty = Qty::from_f32(100.0);
    let price = Price::from_f32(2500.0);
    // Stock must always return price * qty regardless of the flag.
    let with_flag = MarketKind::Stock.qty_in_quote_value(qty, price, true);
    let without_flag = MarketKind::Stock.qty_in_quote_value(qty, price, false);
    assert!((with_flag - 250_000.0).abs() < 1e-3);
    assert!((without_flag - 250_000.0).abs() < 1e-3);
}
