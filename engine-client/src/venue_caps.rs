/// `VenueCapsStore` — sidecar `HashMap<Ticker, VenueCaps>` populated from
/// `EngineEvent::TickerInfo` typed entries during `fetch_ticker_metadata`.
///
/// Keyed by `Ticker` (which embeds the `Exchange`), so one global instance
/// covers all venues without conflict. Lives inside `EngineClientBackend`
/// but is **not** persisted — it is rebuilt from Python on every reconnect.
///
/// **Design (Q6):** callers receive an `Arc<tokio::sync::RwLock<VenueCapsStore>>`
/// clone from `main.rs`. UI render paths MUST use `try_read()` to avoid
/// blocking the Iced event loop.
use std::collections::HashMap;

use exchange::Ticker;

use crate::dto::VenueCaps;

/// Per-venue capability store. Populated by Python `TickerInfo` events.
pub struct VenueCapsStore {
    inner: HashMap<Ticker, VenueCaps>,
}

impl VenueCapsStore {
    pub fn new() -> Self {
        Self {
            inner: HashMap::new(),
        }
    }

    /// Insert or update the caps for a ticker.
    pub fn upsert(&mut self, ticker: Ticker, caps: VenueCaps) {
        self.inner.insert(ticker, caps);
    }

    /// Look up caps for a ticker.
    pub fn get(&self, ticker: &Ticker) -> Option<&VenueCaps> {
        self.inner.get(ticker)
    }

    /// Remove all entries (called on engine reconnect).
    pub fn clear(&mut self) {
        self.inner.clear();
    }

    pub fn len(&self) -> usize {
        self.inner.len()
    }

    pub fn is_empty(&self) -> bool {
        self.inner.is_empty()
    }
}

impl Default for VenueCapsStore {
    fn default() -> Self {
        Self::new()
    }
}
