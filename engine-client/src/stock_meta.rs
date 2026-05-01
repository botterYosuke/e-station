//! Stock ticker metadata — fields that ride along with the standard
//! `TickerInfo` but are kept *out* of `Ticker`'s `Hash` impl per Q16
//! (Japanese display name, lot size, yobine code, sizyou code).
//!
//! The Rust UI maintains a side-channel `HashMap<Ticker, TickerDisplayMeta>`
//! that the ticker selector consults for incremental search (display name
//! prefix match) and for resolving `min_ticksize` from the `CLMYobine`
//! table when a snapshot price is available. None of these values are
//! safe to fold into `Ticker` itself: ASCII symbols are still the
//! canonical identity, and the Hash impl must stay stable across cache
//! reloads (F13).
//!
//! Phase D: renamed from `tachibana_meta` to `stock_meta`. The parser
//! (`parse_stock_ticker_entry`) is now venue-non-specific and returns
//! `None` when `min_ticksize` is absent — Python guarantees the resolved
//! value (Phase C IPC contract).

use exchange::{Ticker, TickerInfo, adapter::Exchange};
use serde_json::Value;

/// Side-channel ticker metadata kept off `Ticker` per Q16. Populated by
/// the `EngineEvent::TickerInfo` receive path for stock venues.
///
/// Fields are `pub(crate)` so the crate-internal parser
/// (`parse_stock_ticker_entry`) and filter (`matches_tachibana_filter`)
/// can construct/inspect values directly while keeping the in-memory
/// layout an implementation detail of `engine-client`. External callers
/// (UI, integration tests) read via the `display_name_ja()` /
/// `yobine_code()` / `sizyou_c()` accessors below; tests outside this
/// crate may construct instances via the `#[cfg(test)] for_test` helper.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TickerDisplayMeta {
    /// Japanese long name (`sIssueName` from `CLMIssueMstKabu`).
    pub(crate) display_name_ja: Option<String>,
    /// `CLMYobine` table key — Rust resolves the snapshot tick value
    /// via the master table when a price is in hand (B5 follow-up).
    pub(crate) yobine_code: Option<String>,
    /// `sSizyouC` from `CLMIssueSizyouMstKabu` — required when issuing
    /// price/history requests for the right exchange (e.g. "00" =
    /// 東証, "02" = 名証).
    pub(crate) sizyou_c: Option<String>,
}

impl TickerDisplayMeta {
    /// Borrow the Japanese long name, if present.
    pub fn display_name_ja(&self) -> Option<&str> {
        self.display_name_ja.as_deref()
    }

    /// Borrow the `CLMYobine` table key, if present.
    pub fn yobine_code(&self) -> Option<&str> {
        self.yobine_code.as_deref()
    }

    /// Borrow the `sSizyouC` exchange code, if present.
    pub fn sizyou_c(&self) -> Option<&str> {
        self.sizyou_c.as_deref()
    }

    /// Test-only constructor for integration tests outside this crate
    /// that need to fabricate a `TickerDisplayMeta` without going
    /// through `parse_stock_ticker_entry`. Production code MUST NOT
    /// use this — the only legitimate construction path is the parser.
    #[cfg(test)]
    pub fn for_test(
        display_name_ja: Option<String>,
        yobine_code: Option<String>,
        sizyou_c: Option<String>,
    ) -> Self {
        Self {
            display_name_ja,
            yobine_code,
            sizyou_c,
        }
    }
}

/// Parse one stock ticker dict from `EngineEvent::TickerInfo.tickers`
/// into the `(TickerInfo, TickerDisplayMeta)` pair. Returns `None` when
/// the symbol is missing or fails the same ASCII / length / pipe-char
/// guards `fetch_ticker_metadata` already enforces for crypto venues,
/// or when `min_ticksize` is absent or invalid (Phase D: Python guarantees
/// the resolved value; missing means a malformed/legacy entry).
///
/// # Returns
///
/// `Some((Ticker, TickerInfo, TickerDisplayMeta))` on a well-formed dict:
/// - `Ticker` — canonical ASCII identity (Q16 Hash key) plus optional
///   English display string lifted from `display_symbol`.
/// - `TickerInfo` — `min_ticksize` / `min_qty` / `lot_size` / quote
///   currency, with `min_ticksize` resolved by Python (Phase C contract).
/// - `TickerDisplayMeta` — the side-channel `display_name_ja` /
///   `yobine_code` / `sizyou_c` triplet kept off `Ticker` per Q16.
///
/// Returns `None` when `symbol` is missing, non-ASCII, longer than
/// `Ticker::MAX_LEN`, contains the reserved `|` separator, or when
/// `min_ticksize` is absent / non-positive / non-finite.
pub fn parse_stock_ticker_entry(
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

    // Phase D: Python guarantees min_ticksize is always present and resolved.
    // Return None when absent or invalid so the caller skips the entry.
    let min_ticksize = t
        .get("min_ticksize")
        .and_then(|v| v.as_f64())
        .map(|v| v as f32)
        .filter(|v| v.is_finite() && *v > 0.0)?;

    let info = TickerInfo::new_stock(ticker, min_ticksize, min_qty_f32, lot_size);

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

/// B4: incremental ticker filter for the Tachibana selector.
///
/// Returns `true` when `query` is a **prefix** of any of:
/// - the ASCII ticker code (e.g. `"7203"`, `"130A0"`),
/// - the English display symbol (e.g. `"TOYOTA"` derived from `sIssueNameEizi`),
/// - the Japanese display name carried in the side-channel `meta`
///   (e.g. `"トヨタ自動車"`).
///
/// ASCII matches are case-insensitive. Japanese matches are byte-level prefix
/// (UTF-8 is self-synchronising so this is correct for CJK). `meta` may be
/// `None` for tickers loaded before the metadata fetch completed; in that case
/// only code/display-symbol prefixes are considered. An empty `query` matches
/// everything.
pub fn matches_tachibana_filter(
    ticker: &Ticker,
    meta: Option<&TickerDisplayMeta>,
    query: &str,
) -> bool {
    if query.is_empty() {
        return true;
    }

    let q_upper = query.to_ascii_uppercase();
    let (mut code, _) = ticker.to_full_symbol_and_type();
    code.make_ascii_uppercase();
    if code.starts_with(&q_upper) {
        return true;
    }

    let (mut disp, _) = ticker.display_symbol_and_type();
    disp.make_ascii_uppercase();
    if disp.starts_with(&q_upper) {
        return true;
    }

    // Japanese path: byte-level starts_with is correct for UTF-8/CJK.
    if let Some(name) = meta.and_then(|m| m.display_name_ja.as_deref())
        && name.starts_with(query)
    {
        return true;
    }

    false
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
            "min_ticksize": 1.0,
            "quote_currency": "JPY",
            "yobine_code": "103",
            "sizyou_c": "00",
        })
    }

    #[test]
    fn parses_full_dict_into_ticker_info_and_meta() {
        let dict = sample_dict();
        let (_, info, meta) = parse_stock_ticker_entry(&dict, Exchange::TachibanaStock).unwrap();
        assert_eq!(info.lot_size, Some(100));
        assert_eq!(info.quote_currency, Some(QuoteCurrency::Jpy));
        assert_eq!(meta.display_name_ja(), Some("トヨタ自動車"));
        assert_eq!(meta.yobine_code(), Some("103"));
        assert_eq!(meta.sizyou_c(), Some("00"));
    }

    #[test]
    fn rejects_non_ascii_symbol() {
        let dict = json!({"symbol": "トヨタ", "min_ticksize": 1.0});
        assert!(parse_stock_ticker_entry(&dict, Exchange::TachibanaStock).is_none());
    }

    #[test]
    fn rejects_missing_min_ticksize() {
        let dict = json!({"symbol": "7203", "lot_size": 100});
        assert!(
            parse_stock_ticker_entry(&dict, Exchange::TachibanaStock).is_none(),
            "missing min_ticksize must return None (Phase D IPC contract)"
        );
    }

    #[test]
    fn rejects_zero_min_ticksize() {
        let dict = json!({"symbol": "7203", "min_ticksize": 0.0, "lot_size": 100});
        assert!(parse_stock_ticker_entry(&dict, Exchange::TachibanaStock).is_none());
    }

    #[test]
    fn rejects_negative_min_ticksize() {
        let dict = json!({"symbol": "7203", "min_ticksize": -1.0, "lot_size": 100});
        assert!(parse_stock_ticker_entry(&dict, Exchange::TachibanaStock).is_none());
    }

    #[test]
    fn empty_display_name_ja_becomes_none() {
        let dict = json!({"symbol": "7203", "min_ticksize": 1.0, "display_name_ja": ""});
        let (_, _, meta) = parse_stock_ticker_entry(&dict, Exchange::TachibanaStock).unwrap();
        assert!(meta.display_name_ja().is_none());
    }

    fn meta(name_ja: &str) -> TickerDisplayMeta {
        TickerDisplayMeta {
            display_name_ja: Some(name_ja.to_string()),
            yobine_code: None,
            sizyou_c: None,
        }
    }

    fn ticker_with_display(symbol: &str, display: &str) -> Ticker {
        Ticker::new_with_display(symbol, Exchange::TachibanaStock, Some(display))
    }

    #[test]
    fn test_filter_by_code_prefix() {
        let t = ticker_with_display("7203", "TOYOTA");
        let m = meta("トヨタ自動車");
        assert!(matches_tachibana_filter(&t, Some(&m), "7203"));
        assert!(matches_tachibana_filter(&t, Some(&m), "72"));
        assert!(!matches_tachibana_filter(&t, Some(&m), "9999"));
        assert!(matches_tachibana_filter(&t, Some(&m), ""));
    }

    #[test]
    fn test_filter_by_display_name_ja_prefix() {
        let t = ticker_with_display("7203", "TOYOTA");
        let m = meta("トヨタ自動車");
        assert!(matches_tachibana_filter(&t, Some(&m), "ト"));
        assert!(matches_tachibana_filter(&t, Some(&m), "トヨタ"));
        assert!(!matches_tachibana_filter(&t, Some(&m), "自動車"));
        assert!(!matches_tachibana_filter(&t, None, "ト"));
    }

    #[test]
    fn test_filter_by_display_symbol_prefix() {
        let t = ticker_with_display("7203", "TOYOTA");
        let m = meta("トヨタ自動車");
        assert!(matches_tachibana_filter(&t, Some(&m), "TOY"));
        assert!(matches_tachibana_filter(&t, Some(&m), "toy"));
        assert!(!matches_tachibana_filter(&t, Some(&m), "OTA"));
    }

    #[test]
    fn test_filter_alphanumeric_ticker_130a0_visible() {
        let t = ticker_with_display("130A0", "ALPHA NUM CO");
        let m = meta("英数銘柄");
        assert!(matches_tachibana_filter(&t, Some(&m), "130"));
        assert!(matches_tachibana_filter(&t, Some(&m), "130A"));
        assert!(matches_tachibana_filter(&t, Some(&m), "130a"));
        assert!(matches_tachibana_filter(&t, Some(&m), "ALPHA"));
        assert!(matches_tachibana_filter(&t, Some(&m), "英数"));
        assert!(!matches_tachibana_filter(&t, Some(&m), "999"));
    }
}
