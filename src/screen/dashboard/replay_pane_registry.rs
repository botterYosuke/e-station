/// Registry tracking which replay panes have been auto-generated and which
/// have been manually dismissed by the user.
///
/// The registry enforces `MAX_REPLAY_INSTRUMENTS` distinct instruments per
/// session and prevents re-generating panes that the user has explicitly
/// closed.
use iced::widget::pane_grid;
use std::collections::{HashMap, HashSet};

/// Logical identity of an auto-generated replay pane.
/// `pane_kind` is a `&'static str` so comparisons are zero-cost.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct PaneIdentity {
    pub instrument_id: String,
    pub pane_kind: &'static str,
}

/// Per-session state for auto-generated REPLAY panes.
pub struct ReplayPaneRegistry {
    /// Instruments loaded at least once this session.
    loaded: HashSet<String>,
    /// Panes closed by the user — must never be auto-recreated.
    dismissed: HashSet<PaneIdentity>,
    /// Live pane IDs for auto-generated panes (used to clear data on reload).
    registered: HashMap<PaneIdentity, pane_grid::Pane>,
}

impl ReplayPaneRegistry {
    pub fn new() -> Self {
        Self {
            loaded: HashSet::new(),
            dismissed: HashSet::new(),
            registered: HashMap::new(),
        }
    }

    /// Returns `true` when a pane of this identity should be auto-generated.
    ///
    /// Returns `false` if the user has previously dismissed this pane.
    pub fn should_generate(&self, instrument_id: &str, pane_kind: &'static str) -> bool {
        !self.dismissed.contains(&PaneIdentity {
            instrument_id: instrument_id.to_string(),
            pane_kind,
        })
    }

    /// Mark a pane as dismissed by the user.
    /// After calling this, `should_generate` returns `false` for the same identity.
    pub fn dismiss(&mut self, instrument_id: &str, pane_kind: &'static str) {
        self.dismissed.insert(PaneIdentity {
            instrument_id: instrument_id.to_string(),
            pane_kind,
        });
    }

    /// Mark an instrument as loaded (idempotent).
    pub fn mark_loaded(&mut self, instrument_id: &str) {
        self.loaded.insert(instrument_id.to_string());
    }

    /// Returns `true` if this instrument has already been loaded this session.
    pub fn is_loaded(&self, instrument_id: &str) -> bool {
        self.loaded.contains(instrument_id)
    }

    /// Number of distinct instruments loaded so far.
    pub fn loaded_count(&self) -> usize {
        self.loaded.len()
    }

    pub fn register_pane(
        &mut self,
        instrument_id: &str,
        pane_kind: &'static str,
        pane: pane_grid::Pane,
    ) {
        self.registered.insert(
            PaneIdentity {
                instrument_id: instrument_id.to_string(),
                pane_kind,
            },
            pane,
        );
    }

    pub fn get_registered_pane(
        &self,
        instrument_id: &str,
        pane_kind: &'static str,
    ) -> Option<pane_grid::Pane> {
        self.registered
            .get(&PaneIdentity {
                instrument_id: instrument_id.to_string(),
                pane_kind,
            })
            .copied()
    }

    /// Remove a registered pane from the registry without dismissing it.
    ///
    /// Used when the pane grid is closed (e.g. Trade granularity reload removes
    /// the CandlestickChart pane) so a future first-load can recreate it.
    pub fn remove_registered_pane(&mut self, instrument_id: &str, pane_kind: &'static str) {
        self.registered.remove(&PaneIdentity {
            instrument_id: instrument_id.to_string(),
            pane_kind,
        });
    }
}

impl Default for ReplayPaneRegistry {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn should_generate_returns_true_initially() {
        let registry = ReplayPaneRegistry::new();
        assert!(registry.should_generate("1301.TSE", "TimeAndSales"));
        assert!(registry.should_generate("1301.TSE", "CandlestickChart"));
    }

    #[test]
    fn should_generate_returns_false_after_dismiss() {
        let mut registry = ReplayPaneRegistry::new();
        registry.dismiss("1301.TSE", "TimeAndSales");
        assert!(!registry.should_generate("1301.TSE", "TimeAndSales"));
        // Other pane kinds for same instrument are unaffected
        assert!(registry.should_generate("1301.TSE", "CandlestickChart"));
        // Other instruments are unaffected
        assert!(registry.should_generate("7203.TSE", "TimeAndSales"));
    }

    #[test]
    fn mark_loaded_is_idempotent() {
        let mut registry = ReplayPaneRegistry::new();
        registry.mark_loaded("1301.TSE");
        registry.mark_loaded("1301.TSE");
        registry.mark_loaded("1301.TSE");
        assert_eq!(registry.loaded_count(), 1);
    }

    #[test]
    fn loaded_count_counts_distinct() {
        let mut registry = ReplayPaneRegistry::new();
        registry.mark_loaded("1301.TSE");
        registry.mark_loaded("7203.TSE");
        registry.mark_loaded("6758.TSE");
        assert_eq!(registry.loaded_count(), 3);
        // Re-adding an existing instrument does not increment the count
        registry.mark_loaded("1301.TSE");
        assert_eq!(registry.loaded_count(), 3);
    }

    #[test]
    fn is_loaded_false_before_mark() {
        let registry = ReplayPaneRegistry::new();
        assert!(!registry.is_loaded("1301.TSE"));
    }

    #[test]
    fn is_loaded_true_after_mark() {
        let mut registry = ReplayPaneRegistry::new();
        registry.mark_loaded("1301.TSE");
        assert!(registry.is_loaded("1301.TSE"));
    }
}
