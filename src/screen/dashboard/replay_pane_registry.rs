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

    /// Returns `true` when at least one pane is currently auto-registered.
    pub fn has_registered_panes(&self) -> bool {
        !self.registered.is_empty()
    }

    /// schema 3.14: drain every registered pane and reset the per-session state.
    ///
    /// Returns the `pane_grid::Pane` IDs the caller must close on the dashboard
    /// (the registry doesn't own the pane grid). Also clears `loaded` and
    /// `dismissed` so a brand-new replay session starts from a clean slate —
    /// `dismissed` deliberately does NOT carry over because "file switch" is
    /// a new session, not a reload.
    pub fn drain_all_registered(&mut self) -> Vec<pane_grid::Pane> {
        let panes: Vec<pane_grid::Pane> = self.registered.values().copied().collect();
        self.loaded.clear();
        self.registered.clear();
        self.dismissed.clear();
        panes
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

    // schema 3.14: drain_all_registered() tests.
    //
    // `pane_grid::Pane` has a `pub(super)` constructor so we acquire valid
    // values through `pane_grid::State::new` / `split`.
    #[test]
    fn drain_all_registered_returns_panes_and_clears_state() {
        let (mut grid, first) = pane_grid::State::<()>::new(());
        let (second, _) = grid
            .split(pane_grid::Axis::Horizontal, first, ())
            .expect("split should succeed");

        let mut registry = ReplayPaneRegistry::new();
        registry.mark_loaded("1301.TSE");
        registry.mark_loaded("7203.TSE");
        registry.dismiss("8306.TSE", "TimeAndSales");
        registry.register_pane("1301.TSE", "TimeAndSales", first);
        registry.register_pane("7203.TSE", "CandlestickChart", second);

        let drained = registry.drain_all_registered();
        assert_eq!(drained.len(), 2);
        assert!(drained.contains(&first));
        assert!(drained.contains(&second));

        // Internal state is fully reset — including `dismissed` so that a new
        // session does not inherit close-decisions from the previous file.
        assert_eq!(registry.loaded_count(), 0);
        assert!(!registry.has_registered_panes());
        assert!(registry.should_generate("8306.TSE", "TimeAndSales"));
    }

    #[test]
    fn drain_all_registered_on_empty_returns_empty() {
        let mut registry = ReplayPaneRegistry::new();
        assert!(registry.drain_all_registered().is_empty());
    }

    #[test]
    fn drain_all_registered_includes_session_level_sentinel_panes() {
        // Review-fix regression: session-level REPLAY panes (OrderList /
        // BuyingPower) are registered with the empty sentinel instrument id.
        // Drain MUST return them too — otherwise a file switch leaves the old
        // OrderList/BuyingPower as zombies alongside the freshly generated ones.
        let (mut grid, first) = pane_grid::State::<()>::new(());
        let (second, _) = grid
            .split(pane_grid::Axis::Horizontal, first, ())
            .expect("split should succeed");

        let mut registry = ReplayPaneRegistry::new();
        registry.mark_loaded("1301.TSE");
        registry.register_pane("1301.TSE", "TimeAndSales", first);
        registry.register_pane("", "OrderList", second);
        registry.register_pane("", "BuyingPower", first); // any valid pane id

        let drained = registry.drain_all_registered();
        assert_eq!(
            drained.len(),
            3,
            "drain must return instrument-keyed AND session-level panes"
        );
        assert!(!registry.has_registered_panes());
    }
}
