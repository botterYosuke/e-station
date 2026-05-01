use crate::{
    MinTicksize, Price, serde_util,
    unit::qty::{Qty, QtyNormalization},
};

use serde::Deserializer;
use serde::de::Error as SerdeError;
use serde_json::Value;

use std::{collections::BTreeMap, sync::Arc};

#[derive(Clone, Copy)]
pub struct DeOrder {
    pub price: f32,
    pub qty: f32,
}

impl<'de> serde::Deserialize<'de> for DeOrder {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        // can be either an array like ["price","qty", ...] or an object with keys "0" and "1"
        let value = Value::deserialize(deserializer)?;

        let price = match &value {
            Value::Array(arr) => arr.first().and_then(serde_util::value_as_f32),
            Value::Object(map) => map.get("0").and_then(serde_util::value_as_f32),
            _ => None,
        }
        .ok_or_else(|| SerdeError::custom("Order price not found or invalid"))?;

        let qty = match &value {
            Value::Array(arr) => arr.get(1).and_then(serde_util::value_as_f32),
            Value::Object(map) => map.get("1").and_then(serde_util::value_as_f32),
            _ => None,
        }
        .ok_or_else(|| SerdeError::custom("Order qty not found or invalid"))?;

        Ok(DeOrder { price, qty })
    }
}

pub struct DepthPayload {
    pub last_update_id: u64,
    pub time: u64,
    pub bids: Vec<DeOrder>,
    pub asks: Vec<DeOrder>,
}

pub enum DepthUpdate {
    Snapshot(DepthPayload),
    Diff(DepthPayload),
}

#[derive(Clone, Default)]
pub struct Depth {
    pub bids: BTreeMap<Price, Qty>,
    pub asks: BTreeMap<Price, Qty>,
}

impl std::fmt::Debug for Depth {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("Depth")
            .field("bids", &self.bids.len())
            .field("asks", &self.asks.len())
            .finish()
    }
}

impl Depth {
    // E1/E2: Python normalises price and qty before IPC; Rust is no-op in release,
    // debug_assert only.
    fn diff_price_levels(
        price_map: &mut BTreeMap<Price, Qty>,
        orders: &[DeOrder],
        min_ticksize: MinTicksize,
        qty_norm: Option<QtyNormalization>,
    ) {
        orders.iter().for_each(|order| {
            debug_assert!(
                qty_norm.is_none(),
                "qty_norm should be None; Python normalises qty before IPC"
            );
            let price = Price::from_f32(order.price);
            debug_assert!(
                price.is_at_tick(min_ticksize),
                "price {} is not at tick {:?}; Python should normalise",
                order.price,
                min_ticksize,
            );
            let qty = Qty::from_f32(order.qty);
            if qty.is_zero() {
                price_map.remove(&price);
            } else {
                price_map.insert(price, qty);
            }
        });
    }

    fn replace_all_with_qty_norm(
        &mut self,
        snapshot: &DepthPayload,
        min_ticksize: MinTicksize,
        qty_norm: Option<QtyNormalization>,
    ) {
        debug_assert!(
            qty_norm.is_none(),
            "qty_norm should be None; Python normalises qty before IPC"
        );
        self.bids = snapshot
            .bids
            .iter()
            .map(|de_order| {
                let price = Price::from_f32(de_order.price);
                debug_assert!(
                    price.is_at_tick(min_ticksize),
                    "bid price {} is not at tick {:?}; Python should normalise",
                    de_order.price,
                    min_ticksize,
                );
                (price, Qty::from_f32(de_order.qty))
            })
            .collect::<BTreeMap<Price, Qty>>();
        self.asks = snapshot
            .asks
            .iter()
            .map(|de_order| {
                let price = Price::from_f32(de_order.price);
                debug_assert!(
                    price.is_at_tick(min_ticksize),
                    "ask price {} is not at tick {:?}; Python should normalise",
                    de_order.price,
                    min_ticksize,
                );
                (price, Qty::from_f32(de_order.qty))
            })
            .collect::<BTreeMap<Price, Qty>>();
    }

    fn apply_diff(&mut self, diff: &DepthPayload, min_ticksize: MinTicksize) {
        Self::diff_price_levels(&mut self.bids, &diff.bids, min_ticksize, None);
        Self::diff_price_levels(&mut self.asks, &diff.asks, min_ticksize, None);
    }

    pub fn mid_price(&self) -> Option<Price> {
        match (self.asks.first_key_value(), self.bids.last_key_value()) {
            (Some((ask_price, _)), Some((bid_price, _))) => Some((*ask_price + *bid_price) / 2),
            _ => None,
        }
    }
}

#[derive(Default)]
pub struct LocalDepthCache {
    pub last_update_id: u64,
    pub time: u64,
    pub depth: Arc<Depth>,
}

impl LocalDepthCache {
    pub fn update(&mut self, new_depth: DepthUpdate, min_ticksize: MinTicksize) {
        self.update_inner(new_depth, min_ticksize, None);
    }

    /// Deprecated: Python normalises qty before IPC; pass `qty_norm = None` or use `update()`.
    /// Will be removed in Phase F.
    #[deprecated(note = "Python normalises qty before IPC; use update() instead")]
    pub fn update_with_qty_norm(
        &mut self,
        new_depth: DepthUpdate,
        min_ticksize: MinTicksize,
        qty_norm: Option<QtyNormalization>,
    ) {
        self.update_inner(new_depth, min_ticksize, qty_norm);
    }

    fn update_inner(
        &mut self,
        new_depth: DepthUpdate,
        min_ticksize: MinTicksize,
        qty_norm: Option<QtyNormalization>,
    ) {
        // Hard assert (all builds): callers that pass Some(qty_norm) via the deprecated
        // update_with_qty_norm API would be silently no-op'd in release, so we make the
        // breakage explicit rather than silent.
        assert!(
            qty_norm.is_none(),
            "qty_norm is no longer applied; Python normalises qty before IPC. \
             Use update() instead of the deprecated update_with_qty_norm()."
        );
        match new_depth {
            DepthUpdate::Snapshot(snapshot) => {
                self.last_update_id = snapshot.last_update_id;
                self.time = snapshot.time;

                let depth = Arc::make_mut(&mut self.depth);
                depth.replace_all_with_qty_norm(&snapshot, min_ticksize, qty_norm);
            }
            DepthUpdate::Diff(diff) => {
                self.last_update_id = diff.last_update_id;
                self.time = diff.time;

                let depth = Arc::make_mut(&mut self.depth);
                depth.apply_diff(&diff, min_ticksize);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        unit::qty::{QtyNormalization, RawQtyUnit},
        Exchange, MinTicksize, Ticker, TickerInfo,
    };

    fn btc_ticker_info() -> TickerInfo {
        TickerInfo::new(Ticker::new("BTCUSDT", Exchange::BinanceSpot), 0.01, 0.001, None)
    }

    fn min_tick(power: i8) -> MinTicksize {
        MinTicksize::new(power)
    }

    fn normalised_snapshot() -> DepthUpdate {
        DepthUpdate::Snapshot(DepthPayload {
            last_update_id: 1,
            time: 0,
            bids: vec![DeOrder { price: 100.0, qty: 10.0 }],
            asks: vec![DeOrder { price: 101.0, qty: 5.0 }],
        })
    }

    #[test]
    #[should_panic(expected = "qty_norm is no longer applied")]
    fn update_with_some_qty_norm_panics_in_all_builds() {
        let qty_norm = QtyNormalization::with_raw_qty_unit(false, btc_ticker_info(), RawQtyUnit::Base);
        let mut cache = LocalDepthCache::default();
        #[allow(deprecated)]
        cache.update_with_qty_norm(normalised_snapshot(), min_tick(0), Some(qty_norm));
    }

    #[test]
    fn update_with_none_qty_norm_does_not_panic() {
        let mut cache = LocalDepthCache::default();
        #[allow(deprecated)]
        cache.update_with_qty_norm(normalised_snapshot(), min_tick(0), None);
    }
}
