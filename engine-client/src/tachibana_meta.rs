//! Tachibana-specific ticker metadata — fields that ride along with the
//! standard `TickerInfo` but are kept *out* of `Ticker`'s `Hash` impl
//! per Q16 (Japanese display name, lot size, yobine code, sizyou code).
//!
//! The Rust UI maintains a side-channel `HashMap<Ticker, TickerDisplayMeta>`
//! that the ticker selector consults for incremental search (display name
//! prefix match) and for resolving `min_ticksize` from the `CLMYobine`
//! table when a snapshot price is available. None of these values are
//! safe to fold into `Ticker` itself: ASCII symbols are still the
//! canonical identity, and the Hash impl must stay stable across cache
//! reloads (F13).
//!
//! Phase 1 plumbing (B3):
//! - Python `list_tickers("stock")` emits one dict per (issue, market)
//!   pair with the keys parsed below.
//! - Rust receives the dict via `EngineEvent::TickerInfo.tickers` and
//!   constructs both a `TickerInfo` (via `new_stock`) and a
//!   `TickerDisplayMeta` (via `parse_tachibana_ticker_dict` below).
//! - `min_ticksize` is **temporarily 1.0** — see B3 §5 design note. The
//!   final resolution path (Python emits a per-ticker resolved tick once
//!   a snapshot price is in hand, B5 follow-up) will drop the literal.

use exchange::{Ticker, TickerInfo, adapter::Exchange};
use serde_json::Value;

/// Phase 1 placeholder for `min_ticksize` — see B3 §5 design note. The
/// actual value will be resolved by the Python worker once a snapshot
/// price is in hand (B5 follow-up); until then we use `1.0` so the
/// `TickerInfo::new_stock` constructor accepts the value and the UI
/// can render the ticker without panicking. JPY equities trade in
/// ¥1 ticks for sub-¥3000 issues anyway, so the conservative default
/// is rarely visibly wrong in B4 UI smoke testing.
pub const TACHIBANA_MIN_TICKSIZE_PLACEHOLDER_F32: f32 = 1.0;

/// Side-channel ticker metadata kept off `Ticker` per Q16. Populated by
/// the `EngineEvent::TickerInfo` receive path for Tachibana stocks.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TickerDisplayMeta {
    /// Japanese long name (`sIssueName` from `CLMIssueMstKabu`).
    pub display_name_ja: Option<String>,
    /// `CLMYobine` table key — Rust resolves the snapshot tick value
    /// via the master table when a price is in hand (B5 follow-up).
    pub yobine_code: Option<String>,
    /// `sSizyouC` from `CLMIssueSizyouMstKabu` — required when issuing
    /// price/history requests for the right exchange (e.g. "00" =
    /// 東証, "02" = 名証).
    pub sizyou_c: Option<String>,
}

/// Parse one Tachibana ticker dict from `EngineEvent::TickerInfo.tickers`
/// into the `(TickerInfo, TickerDisplayMeta)` pair. Returns `None` when
/// the symbol is missing or fails the same ASCII / length / pipe-char
/// guards `fetch_ticker_metadata` already enforces for crypto venues.
pub fn parse_tachibana_ticker_dict(
    t: &Value,
    exchange: Exchange,
) -> Option<(Ticker, TickerInfo, TickerDisplayMeta)> {
    let symbol = t.get("symbol")?.as_str()?;
    if !symbol.is_ascii() || symbol.len() > Ticker::MAX_LEN as usize || symbol.contains('|') {
        return None;
    }
    let display_symbol = t
        .get("display_symbol")
        .and_then(|v| v.as_str())
        .filter(|d| d.is_ascii() && d.len() <= Ticker::MAX_LEN as usize && !d.contains('|'));
    let ticker = Ticker::new_with_display(symbol, exchange, display_symbol);

    // lot_size doubles as min_qty for stocks (one lot is the minimum
    // tradable unit). Default to 100 when absent — the canonical
    // tan-i for Tokyo equities since 2018-10.
    let lot_size: u32 = t
        .get("lot_size")
        .and_then(|v| v.as_u64())
        .map(|n| n as u32)
        .unwrap_or(100);
    let min_qty_f32 = lot_size as f32;

    // Phase 1: see TACHIBANA_MIN_TICKSIZE_PLACEHOLDER_F32 doc above.
    let info = TickerInfo::new_stock(
        ticker,
        TACHIBANA_MIN_TICKSIZE_PLACEHOLDER_F32,
        min_qty_f32,
        lot_size,
    );

    let meta = TickerDisplayMeta {
        display_name_ja: t
            .get("display_name_ja")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .map(|s| s.to_string()),
        yobine_code: t
            .get("yobine_code")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .map(|s| s.to_string()),
        sizyou_c: t
            .get("sizyou_c")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .map(|s| s.to_string()),
    };

    Some((ticker, info, meta))
}

#[cfg(test)]
mod tests {
    use super::*;
    use exchange::QuoteCurrency;
    use serde_json::json;

    fn sample_dict() -> Value {
        json!({
            "symbol": "7203",
            "display_name_ja": "トヨタ自動車",
            "display_symbol": "TOYOTA",
            "lot_size": 100,
            "min_qty": 100,
            "quote_currency": "JPY",
            "yobine_code": "103",
            "sizyou_c": "00",
        })
    }

    #[test]
    fn parses_full_dict_into_ticker_info_and_meta() {
        let dict = sample_dict();
        let (_, info, meta) = parse_tachibana_ticker_dict(&dict, Exchange::TachibanaStock).unwrap();
        assert_eq!(info.lot_size, Some(100));
        assert_eq!(info.quote_currency, Some(QuoteCurrency::Jpy));
        assert_eq!(meta.display_name_ja.as_deref(), Some("トヨタ自動車"));
        assert_eq!(meta.yobine_code.as_deref(), Some("103"));
        assert_eq!(meta.sizyou_c.as_deref(), Some("00"));
    }

    #[test]
    fn rejects_non_ascii_symbol() {
        let dict = json!({"symbol": "トヨタ"});
        assert!(parse_tachibana_ticker_dict(&dict, Exchange::TachibanaStock).is_none());
    }

    #[test]
    fn empty_display_name_ja_becomes_none() {
        let dict = json!({"symbol": "7203", "display_name_ja": ""});
        let (_, _, meta) = parse_tachibana_ticker_dict(&dict, Exchange::TachibanaStock).unwrap();
        assert!(meta.display_name_ja.is_none());
    }
}
