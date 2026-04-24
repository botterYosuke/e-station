/// Conversions between IPC DTOs and `exchange::` domain types.
use exchange::{
    Kline, OpenInterest, Trade, Volume,
    depth::{DeOrder, Depth, DepthPayload},
    unit::{Price, Qty},
};
use std::{collections::BTreeMap, sync::Arc};

use crate::dto::{DepthLevel, KlineMsg, OiPoint, TradeMsg};

// ── TradeMsg → Trade ──────────────────────────────────────────────────────────

impl TradeMsg {
    /// Convert to the `exchange::Trade` domain type.
    ///
    /// Returns `None` when price or qty cannot be parsed — callers should log and skip.
    pub fn to_trade(&self) -> Option<Trade> {
        let price: f32 = self.price.parse().ok()?;
        let qty: f32 = self.qty.parse().ok()?;
        Some(Trade {
            time: self.ts_ms as u64,
            is_sell: self.side == "sell",
            price: Price::from_f32(price),
            qty: Qty::from_f32(qty),
        })
    }
}

// ── KlineMsg → Kline ──────────────────────────────────────────────────────────

impl KlineMsg {
    /// Convert to the `exchange::Kline` domain type.
    ///
    /// Returns `None` when any OHLCV field cannot be parsed.
    ///
    /// Volume priority:
    /// 1. If `taker_buy_volume` + `volume` are both present → `Volume::BuySell` using
    ///    base-asset quantities (buy = taker_buy_volume, sell = volume - taker_buy_volume).
    /// 2. If `quote_volume` is present → `Volume::TotalOnly` using quote-asset quantity.
    /// 3. Fallback → `Volume::TotalOnly` using raw `volume` (base-asset, as before).
    pub fn to_kline(&self) -> Option<Kline> {
        let open: f32 = self.open.parse().ok()?;
        let high: f32 = self.high.parse().ok()?;
        let low: f32 = self.low.parse().ok()?;
        let close: f32 = self.close.parse().ok()?;
        let volume: f32 = self.volume.parse().ok()?;

        let vol = if let Some(buy_base) = self
            .taker_buy_volume
            .as_deref()
            .and_then(|s| s.parse::<f32>().ok())
        {
            let sell_base = volume - buy_base;
            Volume::BuySell(Qty::from_f32(buy_base), Qty::from_f32(sell_base))
        } else if let Some(quote_vol) = self
            .quote_volume
            .as_deref()
            .and_then(|s| s.parse::<f32>().ok())
        {
            Volume::TotalOnly(Qty::from_f32(quote_vol))
        } else {
            Volume::TotalOnly(Qty::from_f32(volume))
        };

        Some(Kline {
            time: self.open_time_ms as u64,
            open: Price::from_f32(open),
            high: Price::from_f32(high),
            low: Price::from_f32(low),
            close: Price::from_f32(close),
            volume: vol,
        })
    }
}

// ── DepthLevel → DeOrder ──────────────────────────────────────────────────────

impl DepthLevel {
    /// Convert to `exchange::depth::DeOrder`.
    ///
    /// Returns `None` when price or qty cannot be parsed.
    pub fn to_de_order(&self) -> Option<DeOrder> {
        let price: f32 = self.price.parse().ok()?;
        let qty: f32 = self.qty.parse().ok()?;
        Some(DeOrder { price, qty })
    }
}

// ── DepthLevel slice → Arc<Depth> ────────────────────────────────────────────

/// Build an `Arc<Depth>` (BTreeMap representation) directly from snapshot levels.
///
/// Levels where qty parses to zero are silently skipped (removes the price level).
pub fn depth_levels_to_arc_depth(bids: &[DepthLevel], asks: &[DepthLevel]) -> Arc<Depth> {
    let parse_levels = |levels: &[DepthLevel]| {
        levels
            .iter()
            .filter_map(|l| {
                let price: f32 = l.price.parse().ok()?;
                let qty: f32 = l.qty.parse().ok()?;
                let p = Price::from_f32(price);
                let q = Qty::from_f32(qty);
                if q.is_zero() { None } else { Some((p, q)) }
            })
            .collect::<BTreeMap<Price, Qty>>()
    };

    Arc::new(Depth {
        bids: parse_levels(bids),
        asks: parse_levels(asks),
    })
}

/// Build a `DepthPayload` (as used by `request_depth_snapshot`) from snapshot levels.
pub fn depth_levels_to_payload(
    sequence_id: i64,
    bids: &[DepthLevel],
    asks: &[DepthLevel],
) -> DepthPayload {
    let to_de_orders = |levels: &[DepthLevel]| {
        levels.iter().filter_map(DepthLevel::to_de_order).collect()
    };

    DepthPayload {
        last_update_id: sequence_id as u64,
        // Python engine does not expose the exchange timestamp in DepthSnapshot;
        // use 0 so the caller can substitute its own wall-clock time if needed.
        time: 0,
        bids: to_de_orders(bids),
        asks: to_de_orders(asks),
    }
}

// ── OiPoint → OpenInterest ────────────────────────────────────────────────────

impl OiPoint {
    /// Convert to the `exchange::OpenInterest` domain type.
    ///
    /// Returns `None` when `open_interest` cannot be parsed as `f32`.
    pub fn to_open_interest(&self) -> Option<OpenInterest> {
        let value: f32 = self.open_interest.parse().ok()?;
        Some(OpenInterest {
            time: self.ts_ms as u64,
            value,
        })
    }
}

// ── Unit tests ────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn trade_msg_buy_side() {
        let msg = TradeMsg {
            price: "50000.5".to_string(),
            qty: "0.001".to_string(),
            side: "buy".to_string(),
            ts_ms: 1_700_000_000_000,
            is_liquidation: false,
        };
        let trade = msg.to_trade().expect("should parse");
        assert!(!trade.is_sell);
        assert_eq!(trade.time, 1_700_000_000_000u64);
        // Price round-trips through f32 → i64 atomic units; check approximate value
        let p: f32 = trade.price.to_f32_lossy();
        assert!((p - 50000.5).abs() < 1.0, "price mismatch: {p}");
    }

    #[test]
    fn trade_msg_sell_side() {
        let msg = TradeMsg {
            price: "100.0".to_string(),
            qty: "2.5".to_string(),
            side: "sell".to_string(),
            ts_ms: 0,
            is_liquidation: false,
        };
        let trade = msg.to_trade().expect("should parse");
        assert!(trade.is_sell);
    }

    #[test]
    fn trade_msg_bad_price_returns_none() {
        let msg = TradeMsg {
            price: "notanumber".to_string(),
            qty: "1.0".to_string(),
            side: "buy".to_string(),
            ts_ms: 0,
            is_liquidation: false,
        };
        assert!(msg.to_trade().is_none());
    }

    #[test]
    fn kline_msg_converts_fallback_to_base_volume() {
        let msg = KlineMsg {
            open_time_ms: 1_000,
            open: "100.0".to_string(),
            high: "110.0".to_string(),
            low: "90.0".to_string(),
            close: "105.0".to_string(),
            volume: "50.0".to_string(),
            is_closed: true,
            quote_volume: None,
            taker_buy_volume: None,
            taker_buy_quote_volume: None,
        };
        let kline = msg.to_kline().expect("should parse");
        assert_eq!(kline.time, 1_000);
        let v: f32 = kline.volume.total().to_f32_lossy();
        assert!((v - 50.0).abs() < 0.01, "volume mismatch: {v}");
    }

    #[test]
    fn kline_msg_uses_quote_volume_when_present() {
        let msg = KlineMsg {
            open_time_ms: 1_000,
            open: "100.0".to_string(),
            high: "110.0".to_string(),
            low: "90.0".to_string(),
            close: "105.0".to_string(),
            volume: "50.0".to_string(),
            is_closed: true,
            quote_volume: Some("5000.0".to_string()),
            taker_buy_volume: None,
            taker_buy_quote_volume: None,
        };
        let kline = msg.to_kline().expect("should parse");
        let v: f32 = kline.volume.total().to_f32_lossy();
        assert!((v - 5000.0).abs() < 0.1, "quote volume mismatch: {v}");
    }

    #[test]
    fn kline_msg_uses_buy_sell_split_when_taker_buy_volume_present() {
        let msg = KlineMsg {
            open_time_ms: 1_000,
            open: "100.0".to_string(),
            high: "110.0".to_string(),
            low: "90.0".to_string(),
            close: "105.0".to_string(),
            volume: "100.0".to_string(),
            is_closed: true,
            quote_volume: Some("10000.0".to_string()),
            taker_buy_volume: Some("60.0".to_string()),
            taker_buy_quote_volume: Some("6300.0".to_string()),
        };
        let kline = msg.to_kline().expect("should parse");
        // buy/sell split takes priority over quote_volume
        let total: f32 = kline.volume.total().to_f32_lossy();
        assert!((total - 100.0).abs() < 0.1, "total volume mismatch: {total}");
        assert!(
            matches!(kline.volume, Volume::BuySell(_, _)),
            "expected BuySell variant"
        );
    }

    #[test]
    fn depth_snapshot_roundtrip() {
        let bids = vec![
            DepthLevel { price: "100.0".to_string(), qty: "1.5".to_string() },
            DepthLevel { price: "99.0".to_string(), qty: "0.0".to_string() }, // zero → removed
        ];
        let asks = vec![DepthLevel { price: "101.0".to_string(), qty: "2.0".to_string() }];
        let depth = depth_levels_to_arc_depth(&bids, &asks);
        // zero-qty bid is filtered out
        assert_eq!(depth.bids.len(), 1);
        assert_eq!(depth.asks.len(), 1);
    }

    #[test]
    fn oi_point_converts() {
        let pt = OiPoint { ts_ms: 9_000, open_interest: "1234.5".to_string() };
        let oi = pt.to_open_interest().expect("should parse");
        assert_eq!(oi.time, 9_000);
        assert!((oi.value - 1234.5).abs() < 0.1, "oi mismatch: {}", oi.value);
    }
}
