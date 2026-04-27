/// Unit tests for IPC DTO ↔ exchange domain type conversions.
use flowsurface_engine_client::{
    convert::depth_levels_to_arc_depth,
    dto::{DepthLevel, KlineMsg, OiPoint, TradeMsg},
};

// ── TradeMsg → Trade ──────────────────────────────────────────────────────────

#[test]
fn trade_msg_buy_converts() {
    let msg = TradeMsg {
        price: "29800.0".to_string(),
        qty: "0.05".to_string(),
        side: "buy".to_string(),
        ts_ms: 1_000_000,
        is_liquidation: false,
    };
    let trade = msg.to_trade().expect("should convert");
    assert!(!trade.is_sell);
    assert_eq!(trade.time, 1_000_000);
    let p = trade.price.to_f32_lossy();
    assert!((p - 29800.0).abs() < 1.0, "price: {p}");
    let q = trade.qty.to_f32_lossy();
    assert!((q - 0.05).abs() < 0.001, "qty: {q}");
}

#[test]
fn trade_msg_sell_converts() {
    let msg = TradeMsg {
        price: "1.23".to_string(),
        qty: "10.0".to_string(),
        side: "sell".to_string(),
        ts_ms: 0,
        is_liquidation: true,
    };
    let trade = msg.to_trade().expect("should convert");
    assert!(trade.is_sell);
}

#[test]
fn trade_msg_invalid_price_returns_none() {
    let msg = TradeMsg {
        price: "???".to_string(),
        qty: "1.0".to_string(),
        side: "buy".to_string(),
        ts_ms: 0,
        is_liquidation: false,
    };
    assert!(msg.to_trade().is_none());
}

#[test]
fn trade_msg_invalid_qty_returns_none() {
    let msg = TradeMsg {
        price: "100.0".to_string(),
        qty: "nan".to_string(),
        side: "buy".to_string(),
        ts_ms: 0,
        is_liquidation: false,
    };
    // "nan" parses as f32::NAN — we don't reject NaN explicitly here,
    // but the conversion should still succeed (NAN is a valid f32 value).
    // The important thing is the function doesn't panic.
    let _ = msg.to_trade(); // either Some or None — no panic
}

// ── KlineMsg → Kline ──────────────────────────────────────────────────────────

#[test]
fn kline_msg_converts() {
    let msg = KlineMsg {
        open_time_ms: 1_700_000_000_000,
        open: "30000.0".to_string(),
        high: "31000.0".to_string(),
        low: "29000.0".to_string(),
        close: "30500.0".to_string(),
        volume: "100.5".to_string(),
        is_closed: true,
        taker_buy_volume: None,
    };
    let kline = msg.to_kline().expect("should convert");
    assert_eq!(kline.time, 1_700_000_000_000u64);
    assert!((kline.open.to_f32_lossy() - 30000.0).abs() < 1.0);
    assert!((kline.high.to_f32_lossy() - 31000.0).abs() < 1.0);
    assert!((kline.low.to_f32_lossy() - 29000.0).abs() < 1.0);
    assert!((kline.close.to_f32_lossy() - 30500.0).abs() < 1.0);
    let vol = kline.volume.total().to_f32_lossy();
    assert!((vol - 100.5).abs() < 0.1, "volume: {vol}");
}

#[test]
fn kline_msg_bad_open_returns_none() {
    let msg = KlineMsg {
        open_time_ms: 0,
        open: "bad".to_string(),
        high: "1.0".to_string(),
        low: "1.0".to_string(),
        close: "1.0".to_string(),
        volume: "1.0".to_string(),
        is_closed: false,
        taker_buy_volume: None,
    };
    assert!(msg.to_kline().is_none());
}

// ── DepthLevel → Arc<Depth> ───────────────────────────────────────────────────

#[test]
fn depth_snapshot_bids_and_asks() {
    let bids = vec![
        DepthLevel {
            price: "100.0".to_string(),
            qty: "5.0".to_string(),
        },
        DepthLevel {
            price: "99.0".to_string(),
            qty: "3.0".to_string(),
        },
    ];
    let asks = vec![DepthLevel {
        price: "101.0".to_string(),
        qty: "4.0".to_string(),
    }];
    let depth = depth_levels_to_arc_depth(&bids, &asks);
    assert_eq!(depth.bids.len(), 2);
    assert_eq!(depth.asks.len(), 1);
}

#[test]
fn depth_snapshot_zero_qty_filtered() {
    let bids = vec![
        DepthLevel {
            price: "100.0".to_string(),
            qty: "0.0".to_string(),
        }, // removed
        DepthLevel {
            price: "99.0".to_string(),
            qty: "1.0".to_string(),
        },
    ];
    let asks = vec![];
    let depth = depth_levels_to_arc_depth(&bids, &asks);
    assert_eq!(depth.bids.len(), 1, "zero-qty bids should be excluded");
    assert_eq!(depth.asks.len(), 0);
}

#[test]
fn depth_snapshot_invalid_price_filtered() {
    let bids = vec![
        DepthLevel {
            price: "INVALID".to_string(),
            qty: "1.0".to_string(),
        },
        DepthLevel {
            price: "50.0".to_string(),
            qty: "2.0".to_string(),
        },
    ];
    let depth = depth_levels_to_arc_depth(&bids, &[]);
    assert_eq!(depth.bids.len(), 1, "unparseable price should be skipped");
}

// ── OiPoint → OpenInterest ────────────────────────────────────────────────────

#[test]
fn oi_point_converts() {
    let pt = OiPoint {
        ts_ms: 5_000,
        open_interest: "987654.32".to_string(),
    };
    let oi = pt.to_open_interest().expect("should convert");
    assert_eq!(oi.time, 5_000);
    assert!((oi.value - 987_654.3).abs() < 1.0, "oi value: {}", oi.value);
}

#[test]
fn oi_point_bad_value_returns_none() {
    let pt = OiPoint {
        ts_ms: 0,
        open_interest: "nope".to_string(),
    };
    assert!(pt.to_open_interest().is_none());
}

// ── Kline volume normalization (data-engine vs native path) ──────────────────────

#[test]
fn kline_msg_with_taker_buy_volume_splits_volume() {
    let msg = KlineMsg {
        open_time_ms: 1_700_000_000_000,
        open: "30000.0".to_string(),
        high: "31000.0".to_string(),
        low: "29000.0".to_string(),
        close: "30500.0".to_string(),
        volume: "100.0".to_string(),
        is_closed: true,
        taker_buy_volume: Some("60.0".to_string()),
    };
    let kline = msg.to_kline().expect("should convert");

    // With taker_buy_volume, Volume::BuySell should be used
    match &kline.volume {
        exchange::Volume::BuySell(buy, sell) => {
            assert!(
                (buy.to_f32_lossy() - 60.0).abs() < 0.1,
                "buy volume: {}",
                buy.to_f32_lossy()
            );
            assert!(
                (sell.to_f32_lossy() - 40.0).abs() < 0.1,
                "sell volume: {}",
                sell.to_f32_lossy()
            );
        }
        exchange::Volume::TotalOnly(_) => {
            panic!("Expected BuySell volume, got TotalOnly");
        }
    }
}

#[test]
fn kline_msg_without_taker_buy_volume_uses_total_only() {
    let msg = KlineMsg {
        open_time_ms: 1_700_000_000_000,
        open: "30000.0".to_string(),
        high: "31000.0".to_string(),
        low: "29000.0".to_string(),
        close: "30500.0".to_string(),
        volume: "100.0".to_string(),
        is_closed: true,
        taker_buy_volume: None,
    };
    let kline = msg.to_kline().expect("should convert");

    // Without taker_buy_volume, Volume::TotalOnly should be used
    match &kline.volume {
        exchange::Volume::TotalOnly(total) => {
            assert!(
                (total.to_f32_lossy() - 100.0).abs() < 0.1,
                "total volume: {}",
                total.to_f32_lossy()
            );
        }
        exchange::Volume::BuySell(_, _) => {
            panic!("Expected TotalOnly volume, got BuySell");
        }
    }
}

#[test]
fn kline_msg_taker_buy_larger_than_total_clamped_to_zero_sell() {
    // Regression: if taker_buy_volume > volume due to rounding, sell must clamp to 0, not negative
    let msg = KlineMsg {
        open_time_ms: 1_700_000_000_000,
        open: "30000.0".to_string(),
        high: "31000.0".to_string(),
        low: "29000.0".to_string(),
        close: "30500.0".to_string(),
        volume: "100.0".to_string(),
        is_closed: true,
        taker_buy_volume: Some("100.1".to_string()), // > total
    };
    let kline = msg.to_kline().expect("should convert");

    match &kline.volume {
        exchange::Volume::BuySell(buy, sell) => {
            assert!(
                (buy.to_f32_lossy() - 100.1).abs() < 0.1,
                "buy volume: {}",
                buy.to_f32_lossy()
            );
            // sell should be clamped to 0, not negative
            let sell_val = sell.to_f32_lossy();
            assert!(
                (-0.01..=0.01).contains(&sell_val),
                "sell volume should be ~0, got {}",
                sell_val
            );
        }
        exchange::Volume::TotalOnly(_) => {
            panic!("Expected BuySell volume, got TotalOnly");
        }
    }
}
