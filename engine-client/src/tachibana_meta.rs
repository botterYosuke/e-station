//! Deprecated re-exports — use [`crate::stock_meta`] instead (Phase D rename).
//!
//! This module is kept for backward compatibility during Phase D.
//! Phase F will remove it entirely once all call sites are migrated.

pub use crate::stock_meta::{
    TickerDisplayMeta, matches_tachibana_filter, parse_stock_ticker_entry as parse_tachibana_ticker_dict,
};
