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
///
/// Fields are `pub(crate)` so the crate-internal parser
/// (`parse_tachibana_ticker_dict`) and filter (`matches_tachibana_filter`)
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
    /// through `parse_tachibana_ticker_dict`. Production code MUST NOT
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

/// Parse one Tachibana ticker dict from `EngineEvent::TickerInfo.tickers`
/// into the `(TickerInfo, TickerDisplayMeta)` pair. Returns `None` when
/// the symbol is missing or fails the same ASCII / length / pipe-char
/// guards `fetch_ticker_metadata` already enforces for crypto venues.
///
/// # Returns
///
/// `Some((Ticker, TickerInfo, TickerDisplayMeta))` on a well-formed dict:
/// - `Ticker` — canonical ASCII identity (Q16 Hash key) plus optional
///   English display string lifted from `display_symbol`.
/// - `TickerInfo` — `min_ticksize` / `min_qty` / `lot_size` / quote
///   currency, with `min_ticksize` the Phase 1 placeholder until the
///   per-ticker resolved tick lands (B5 follow-up).
/// - `TickerDisplayMeta` — the side-channel `display_name_ja` /
///   `yobine_code` / `sizyou_c` triplet kept off `Ticker` per Q16.
///
/// Returns `None` when the `symbol` field is missing, non-ASCII, longer
/// than `Ticker::MAX_LEN`, or contains the reserved `|` separator.
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
        assert_eq!(meta.display_name_ja(), Some("トヨタ自動車"));
        assert_eq!(meta.yobine_code(), Some("103"));
        assert_eq!(meta.sizyou_c(), Some("00"));
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
