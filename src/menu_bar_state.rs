use std::path::PathBuf;

use crate::menu::Action;
use crate::modal::replay_form::Granularity;

/// Which top-level menu is currently open (or None = all closed).
// reason: variant is constructed only by the Linux `widget_menu_bar`. The
// enum lives in `mod menu_bar_state` (no platform gate) so that source-
// inspection tests can compile on every OS. (H1 / M6 / F8 R1)
#[allow(dead_code)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TopMenu {
    File,
}

/// Persistent state for the replay control bar (2nd row, replay mode only).
///
/// Mirrors `ReplayFormModal` minus `validation_error` / `submitting` — those are
/// transient per-submission concerns, not bar-persistent concerns.
#[allow(dead_code)]
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ReplayBarState {
    pub instrument_id: String,
    pub start_date: String,
    pub end_date: String,
    pub granularity: Option<Granularity>,
    pub initial_cash: String,
    pub strategy_file: Option<PathBuf>,
    /// Current replay time — updated by `TimeUpdated` (毎足) and `DateChangeMarker` IPC events.
    /// Daily granularity: `%Y-%m-%d`; それ以外: `%H:%M:%S` (JST).
    pub current_day: Option<String>,
    /// True while the engine is in PAUSED state — mirrors `DataEngineServer._replay_paused`.
    /// Updated by `BarMessage::ReplayPauseStateChanged`.
    pub replay_paused: bool,
    /// True when the snapshot ring buffer is non-empty — mirrors `has_history` from
    /// `ReplayHistoryChanged` IPC event. Controls ⏮ Step- button enable state.
    pub replay_has_history: bool,
}

impl ReplayBarState {
    /// Prefill fields from a SCENARIO JSON object (mirrors `ReplayFormModal::prefill_from_scenario`).
    pub fn prefill_from_scenario(
        &mut self,
        path: PathBuf,
        scenario: &serde_json::Value,
        resolved_instruments: Option<&[String]>,
    ) {
        self.strategy_file = Some(path);
        let Some(obj) = scenario.as_object() else {
            return;
        };
        // 優先順位: resolved_instruments (v3+ref) > instruments (v2) > instrument (v1)
        if let Some(ids) = resolved_instruments {
            if !ids.is_empty() {
                self.instrument_id = ids.join(", ");
            }
        } else {
            if let Some(s) = obj.get("instrument").and_then(|v| v.as_str()) {
                self.instrument_id = s.to_string();
            }
            if let Some(arr) = obj.get("instruments").and_then(|v| v.as_array()) {
                let ids: Vec<&str> = arr.iter().filter_map(|v| v.as_str()).collect();
                if !ids.is_empty() {
                    self.instrument_id = ids.join(", ");
                }
            }
        }
        if let Some(s) = obj.get("start").and_then(|v| v.as_str()) {
            self.start_date = s.to_string();
        }
        if let Some(s) = obj.get("end").and_then(|v| v.as_str()) {
            self.end_date = s.to_string();
        }
        if let Some(s) = obj.get("granularity").and_then(|v| v.as_str()) {
            match s {
                "Daily" => self.granularity = Some(Granularity::Daily),
                "Minute" => self.granularity = Some(Granularity::Minute),
                "Trade" => self.granularity = Some(Granularity::Trade),
                _ => {}
            }
        }
        if let Some(n) = obj.get("initial_cash").and_then(|v| v.as_u64()) {
            self.initial_cash = n.to_string();
        }
    }

    pub fn set_strategy_file_only(&mut self, path: PathBuf) {
        self.strategy_file = Some(path);
    }
}

/// N4-live: Live 戦略実行中の 2 段目バー状態。
#[allow(dead_code)]
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct LiveBarState {
    pub strategy_file_stem: Option<String>,
    pub current_time: Option<String>,
    pub live_paused: bool,
}

/// Messages emitted by the widget menu bar's button/overlay layer.
// reason: variants `Toggle`, `Pick` are constructed only by the Linux widget
// menu bar (`src/widget_menu_bar.rs`). The enum is exposed on every OS
// (H1 / F8 R1) so the source-inspection tests in
// `tests/widget_menu_bar_state.rs` and the unit tests below can compile on
// Win/Mac, but those variants therefore look unused to `cargo check` outside
// Linux. `Dismiss` and `DismissFocusLost` are constructed by main.rs
// regardless of OS.
#[allow(dead_code)]
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BarMessage {
    /// Toggle a top-level menu open/closed.
    Toggle(TopMenu),
    /// User clicked a menu item — dispatch the action and close the dropdown.
    Pick(Action),
    /// Close all menus: Esc key or outside click (bar empty space / below bar area).
    Dismiss,
    /// Close all menus: window lost focus (`Window::Unfocused`).
    /// Kept separate from `Dismiss` so call sites can log the distinct reason.
    DismissFocusLost,
    // ── Replay bar input field changes ────────────────────────────────────────
    StartDateChanged(String),
    EndDateChanged(String),
    GranularityChanged(Granularity),
    InitialCashChanged(String),
    // ── Replay bar button actions (dispatched as Tasks in main.rs) ────────────
    PickStrategyFile,
    PressPlay,
    PressPause,
    PressStepForward,
    /// ⏮ Step backward — sent by the Step- button. `main.rs` dispatches
    /// `Command::StepBackward` IPC; state itself is unchanged here.
    PressStepBackward,
    PressStop,
    /// Python engine confirmed pause/resume state change and history status.
    /// Updates `replay_bar.replay_paused` and `replay_bar.replay_has_history`.
    ReplayPauseStateChanged {
        paused: bool,
        has_history: bool,
    },
    /// ▶（paused → running）
    LivePressPlay,
    /// ⏸（running → paused）
    LivePressPause,
    /// ■（StopEngine を発行）
    LivePressStop,
}

/// Lightweight state for the widget menu bar: which top-level menu is open.
///
/// **F8 R2 / H3'**: the previous dynamic-Y dropdown anchor field was removed.
/// `mouse_area::on_move` returns widget-local coordinates (0..BAR_HEIGHT),
/// not window-absolute Y, so feeding the cursor's Y into
/// `with_dropdown_overlay`'s `top_offset` (which is interpreted as a window-Y
/// offset) was a category error. The dropdown is now anchored at the constant
/// `BAR_HEIGHT`, which is always correct because the menu bar is the very
/// first row of the window.
// reason: instantiated only on Linux. Same rationale as `TopMenu` above.
#[allow(dead_code)]
#[derive(Default, Debug, Clone, PartialEq, Eq)]
pub struct State {
    pub open: Option<TopMenu>,
    pub replay_bar: ReplayBarState,
    pub live_bar: LiveBarState,
}

/// Pure state-transition function (R2-39). No GUI dependencies — fully
/// testable on all platforms.
///
/// Invariants:
/// 1. `Dismiss` → `open: None` (Esc / outside-click)
/// 2. `DismissFocusLost` → `open: None` (window Unfocused)
/// 3. `Pick(_)` → `open: None` (item selected, close dropdown)
/// 4. `Toggle(top)` when `open == Some(top)` → `open: None` (close same)
/// 5. `Toggle(top)` when `open != Some(top)` → `open: Some(top)` (open)
// reason: invoked only by the Linux widget menu bar handler in main.rs. Cross-
// platform exposure is required for source-inspection / unit tests. (H1 / M6)
#[allow(dead_code)]
pub fn update(state: State, msg: BarMessage) -> State {
    match msg {
        BarMessage::Toggle(top) => State {
            open: if state.open == Some(top) {
                None
            } else {
                Some(top)
            },
            ..state
        },
        BarMessage::Pick(_) | BarMessage::Dismiss | BarMessage::DismissFocusLost => State {
            open: None,
            ..state
        },
        // Input field changes: update replay_bar, keep open state unchanged.
        BarMessage::StartDateChanged(s) => State {
            replay_bar: ReplayBarState {
                start_date: s,
                ..state.replay_bar
            },
            ..state
        },
        BarMessage::EndDateChanged(s) => State {
            replay_bar: ReplayBarState {
                end_date: s,
                ..state.replay_bar
            },
            ..state
        },
        BarMessage::GranularityChanged(g) => State {
            replay_bar: ReplayBarState {
                granularity: Some(g),
                ..state.replay_bar
            },
            ..state
        },
        BarMessage::InitialCashChanged(s) => State {
            replay_bar: ReplayBarState {
                initial_cash: s,
                ..state.replay_bar
            },
            ..state
        },
        // Action variants: state is unchanged; main.rs dispatches the IPC task.
        BarMessage::PickStrategyFile
        | BarMessage::PressPlay
        | BarMessage::PressPause
        | BarMessage::PressStepForward
        | BarMessage::PressStepBackward
        | BarMessage::PressStop => state,
        BarMessage::LivePressPause => State {
            live_bar: crate::menu_bar_state::LiveBarState {
                live_paused: true,
                ..state.live_bar
            },
            ..state
        },
        BarMessage::LivePressPlay => State {
            live_bar: crate::menu_bar_state::LiveBarState {
                live_paused: false,
                ..state.live_bar
            },
            ..state
        },
        BarMessage::LivePressStop => state,
        // Pause/history state change from Python engine via IPC.
        BarMessage::ReplayPauseStateChanged {
            paused,
            has_history,
        } => State {
            replay_bar: ReplayBarState {
                replay_paused: paused,
                replay_has_history: has_history,
                ..state.replay_bar
            },
            ..state
        },
    }
}

#[cfg(test)]
mod tests {
    //! M1 (F8 R1): four explicit open-state cases for `BarMessage::DismissFocusLost`.
    //!
    //! `tests/widget_menu_bar_state.rs` only verifies via source inspection
    //! that `DismissFocusLost` shares the `Dismiss` arm. These unit tests
    //! exercise the actual `update()` function across all four `state.open`
    //! values to pin the focus-lost contract behaviourally — a regression
    //! that, say, accidentally leaves `open` unchanged on focus-lost would
    //! pass the source-inspection test but fail these.
    use super::*;
    use crate::menu::Action;
    use engine_client::dto::AppMode;

    fn open_file() -> State {
        State {
            open: Some(TopMenu::File),
            ..State::default()
        }
    }

    #[test]
    fn dismiss_focus_lost_closes_when_file_open() {
        let next = update(open_file(), BarMessage::DismissFocusLost);
        assert_eq!(next.open, None);
    }

    #[test]
    fn dismiss_focus_lost_is_idempotent_when_already_closed() {
        let s = State::default();
        let next = update(s, BarMessage::DismissFocusLost);
        assert_eq!(next.open, None);
    }

    // Sanity: ensure Pick / Dismiss / DismissFocusLost share the same outcome,
    // matching the source-inspection guard in tests/widget_menu_bar_state.rs.
    #[test]
    fn pick_dismiss_focus_lost_all_close_menu() {
        for msg in [
            BarMessage::Pick(Action::SwitchAppMode(AppMode::Live)),
            BarMessage::Dismiss,
            BarMessage::DismissFocusLost,
        ] {
            assert_eq!(update(open_file(), msg).open, None);
        }
    }

    // R2-M2: actual update() behavioural tests (not source inspection)
    #[test]
    fn replay_pause_state_changed_sets_paused_and_history() {
        let s = State::default();
        assert!(!s.replay_bar.replay_paused);
        assert!(!s.replay_bar.replay_has_history);
        let next = update(
            s,
            BarMessage::ReplayPauseStateChanged {
                paused: true,
                has_history: true,
            },
        );
        assert!(
            next.replay_bar.replay_paused,
            "replay_paused should be true"
        );
        assert!(
            next.replay_bar.replay_has_history,
            "replay_has_history should be true"
        );
    }

    #[test]
    fn replay_pause_state_changed_clears_paused_and_history() {
        let s = State {
            replay_bar: ReplayBarState {
                replay_paused: true,
                replay_has_history: true,
                ..ReplayBarState::default()
            },
            ..State::default()
        };
        let next = update(
            s,
            BarMessage::ReplayPauseStateChanged {
                paused: false,
                has_history: false,
            },
        );
        assert!(
            !next.replay_bar.replay_paused,
            "replay_paused should be reset to false"
        );
        assert!(
            !next.replay_bar.replay_has_history,
            "replay_has_history should be reset to false"
        );
    }

    #[test]
    fn press_step_backward_does_not_mutate_state() {
        let s = State::default();
        let next = update(s.clone(), BarMessage::PressStepBackward);
        assert_eq!(next.replay_bar.instrument_id, s.replay_bar.instrument_id);
        assert_eq!(next.replay_bar.replay_paused, s.replay_bar.replay_paused);
    }

    #[test]
    fn live_press_stop_does_not_mutate_state() {
        let s = State::default();
        let next = update(s.clone(), BarMessage::LivePressStop);
        assert_eq!(next.open, s.open);
        assert_eq!(next.live_bar, s.live_bar);
    }

    #[test]
    fn live_press_pause_sets_live_paused() {
        let s = State::default();
        assert!(!s.live_bar.live_paused);
        let next = update(s.clone(), BarMessage::LivePressPause);
        assert_eq!(next.open, s.open);
        assert!(
            next.live_bar.live_paused,
            "LivePressPause should set live_paused = true"
        );
    }

    #[test]
    fn press_play_rollback_preserves_has_history() {
        // Simulates: was paused with has_history=true, IPC failed → rollback to paused=true
        let mut s = State::default();
        s.replay_bar.replay_has_history = true;
        s.replay_bar.replay_paused = false; // optimistic update already done

        let next = update(
            s,
            BarMessage::ReplayPauseStateChanged {
                paused: true, // rollback
                has_history: true,
            },
        );
        assert!(
            next.replay_bar.replay_paused,
            "should be paused after rollback"
        );
        assert!(
            next.replay_bar.replay_has_history,
            "has_history must be preserved during rollback"
        );
    }

    #[test]
    fn press_pause_rollback_preserves_has_history() {
        // Simulates: was playing with has_history=true, IPC failed → rollback to paused=false
        let mut s = State::default();
        s.replay_bar.replay_has_history = true;
        s.replay_bar.replay_paused = true; // optimistic update already done

        let next = update(
            s,
            BarMessage::ReplayPauseStateChanged {
                paused: false, // rollback
                has_history: true,
            },
        );
        assert!(
            !next.replay_bar.replay_paused,
            "should not be paused after rollback"
        );
        assert!(
            next.replay_bar.replay_has_history,
            "has_history must be preserved during rollback"
        );
    }

    #[test]
    fn live_press_play_clears_live_paused() {
        let s = State {
            live_bar: LiveBarState {
                live_paused: true,
                ..LiveBarState::default()
            },
            ..State::default()
        };
        let next = update(s.clone(), BarMessage::LivePressPlay);
        assert_eq!(next.open, s.open);
        assert!(
            !next.live_bar.live_paused,
            "LivePressPlay should set live_paused = false"
        );
    }

    #[test]
    fn replay_bar_state_prefill_v3_resolved() {
        let mut replay_bar = ReplayBarState::default();
        let scenario = serde_json::json!({
            "schema_version": 3,
            "instruments_ref": "data/universe.json#/instruments",
            "instruments": ["FROM_INSTRUMENTS", "IGNORED"],
            "start": "2025-01-06",
            "end": "2025-01-10",
            "granularity": "Minute",
            "initial_cash": 1_000_000_u64,
        });
        let resolved = vec!["1301.TSE".to_string(), "7203.TSE".to_string()];
        replay_bar.prefill_from_scenario(
            PathBuf::from("/tmp/s.py"),
            &scenario,
            Some(resolved.as_slice()),
        );
        assert_eq!(replay_bar.instrument_id, "1301.TSE, 7203.TSE");
    }

    #[test]
    fn replay_bar_state_prefill_v3_resolved_empty_keeps_existing() {
        let mut replay_bar = ReplayBarState {
            instrument_id: "EXISTING".to_string(),
            ..Default::default()
        };
        let scenario = serde_json::json!({"schema_version": 3});
        replay_bar.prefill_from_scenario(PathBuf::from("/tmp/s.py"), &scenario, Some(&[]));
        assert_eq!(replay_bar.instrument_id, "EXISTING");
    }

    #[test]
    fn state_default_live_bar_is_default() {
        let s = State::default();
        assert_eq!(s.live_bar, LiveBarState::default());
        assert!(s.live_bar.strategy_file_stem.is_none());
        assert!(s.live_bar.current_time.is_none());
        assert!(!s.live_bar.live_paused);
    }
}
