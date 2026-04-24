/// State machine that tracks depth sequence numbers and detects gaps.
///
/// Each `(ticker, session_id)` pair maintains the last applied `sequence_id`.
/// A gap occurs when a diff's `prev_sequence_id` does not match the last applied id.
use std::collections::HashMap;

pub struct DepthTracker {
    /// Last applied sequence_id per (ticker_symbol, stream_session_id).
    state: HashMap<(String, String), i64>,
}

impl DepthTracker {
    pub fn new() -> Self {
        Self { state: HashMap::new() }
    }

    /// Record a snapshot for `(ticker, session_id)` and return `true` (always accepted).
    ///
    /// Snapshots reset the sequence baseline for the session; a new session_id will
    /// also implicitly replace any previous session's state for that ticker.
    pub fn on_snapshot(&mut self, ticker: &str, session_id: &str, seq: i64) -> bool {
        self.state.insert((ticker.to_owned(), session_id.to_owned()), seq);
        true
    }

    /// Validate a depth diff for `(ticker, session_id)`.
    ///
    /// Returns `true` when the diff is contiguous (prev_seq matches last applied seq).
    /// Returns `false` when a gap is detected — the caller should request a new snapshot.
    pub fn on_diff(
        &mut self,
        ticker: &str,
        session_id: &str,
        seq: i64,
        prev_seq: i64,
    ) -> bool {
        let key = (ticker.to_owned(), session_id.to_owned());
        match self.state.get(&key) {
            Some(&last) if last == prev_seq => {
                self.state.insert(key, seq);
                true
            }
            Some(&last) => {
                log::warn!(
                    "depth gap detected for {ticker}/{session_id}: \
                     expected prev_seq={last}, got prev_seq={prev_seq} seq={seq}"
                );
                false
            }
            None => {
                // No snapshot yet for this session — treat as a gap.
                log::warn!(
                    "depth diff for {ticker}/{session_id} before snapshot: \
                     seq={seq} prev_seq={prev_seq}"
                );
                false
            }
        }
    }

    /// Remove all tracked state for every session of `ticker`.
    ///
    /// Call when the Python engine signals a session change (`DepthGap` / reconnect).
    pub fn reset_ticker(&mut self, ticker: &str) {
        self.state.retain(|(t, _), _| t != ticker);
    }

    /// Remove all tracked state (e.g. after engine restart).
    pub fn reset_all(&mut self) {
        self.state.clear();
    }
}

impl Default for DepthTracker {
    fn default() -> Self {
        Self::new()
    }
}

// ── Unit tests ────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn snapshot_then_sequential_diffs_ok() {
        let mut tracker = DepthTracker::new();
        assert!(tracker.on_snapshot("BTCUSDT", "sess1", 100));
        assert!(tracker.on_diff("BTCUSDT", "sess1", 101, 100));
        assert!(tracker.on_diff("BTCUSDT", "sess1", 102, 101));
    }

    #[test]
    fn gap_detected_on_non_sequential_diff() {
        let mut tracker = DepthTracker::new();
        tracker.on_snapshot("BTCUSDT", "sess1", 100);
        // Simulate a missed message: prev_seq=102 but last=100
        assert!(!tracker.on_diff("BTCUSDT", "sess1", 103, 102));
    }

    #[test]
    fn diff_before_snapshot_is_gap() {
        let mut tracker = DepthTracker::new();
        assert!(!tracker.on_diff("ETHUSDT", "sess1", 1, 0));
    }

    #[test]
    fn new_session_id_starts_fresh() {
        let mut tracker = DepthTracker::new();
        tracker.on_snapshot("BTCUSDT", "sess1", 50);
        // New session_id — snapshot resets the baseline for that session.
        assert!(tracker.on_snapshot("BTCUSDT", "sess2", 1));
        // sess1 is still there but sess2 is independent
        assert!(tracker.on_diff("BTCUSDT", "sess2", 2, 1));
    }

    #[test]
    fn reset_ticker_clears_state() {
        let mut tracker = DepthTracker::new();
        tracker.on_snapshot("BTCUSDT", "sess1", 100);
        tracker.on_snapshot("ETHUSDT", "sess1", 200);
        tracker.reset_ticker("BTCUSDT");
        // BTCUSDT is gone → diff without snapshot → gap
        assert!(!tracker.on_diff("BTCUSDT", "sess1", 101, 100));
        // ETHUSDT is unaffected
        assert!(tracker.on_diff("ETHUSDT", "sess1", 201, 200));
    }

    #[test]
    fn reset_all_clears_everything() {
        let mut tracker = DepthTracker::new();
        tracker.on_snapshot("BTCUSDT", "sess1", 100);
        tracker.reset_all();
        assert!(!tracker.on_diff("BTCUSDT", "sess1", 101, 100));
    }
}
