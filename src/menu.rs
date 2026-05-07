use engine_client::dto::AppMode;

/// Unified menu action enum for cross-platform dispatch.
///
/// `native_menu::Action` covers Win/Mac muda items. This enum is the
/// common vocabulary used by both the Linux iced widget menu bar and
/// any future cross-platform menu infrastructure.
#[derive(Debug, Clone, PartialEq, Eq)]
// reason: variants File/Replay/Quit/SwitchAppMode are consumed by the Linux
// widget menu bar (`src/widget_menu_bar.rs`). The `mod menu` itself is
// cross-platform — exposed on every OS so `tests/menu_actions_cross_platform.rs`
// can compile and assert against the source. Variants therefore look "unused" on
// Win/Mac at the `cargo check` level even though they are reached at runtime
// on Linux. (M6 / F8 R1)
#[allow(dead_code)]
pub enum Action {
    // ── File menu (live mode) ──────────────────────────────────────────────
    Open,
    Save,
    SaveAs,
    // ── Common ─────────────────────────────────────────────────────────────
    Quit,
    // ── Mode switch ────────────────────────────────────────────────────────
    SwitchAppMode(AppMode),
}

/// A menu item entry for submenus that need enabled/tooltip/checked state
/// (Mode submenu).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MenuEntry {
    pub action: Action,
    pub enabled: bool,
    pub tooltip: Option<&'static str>,
    /// Exclusive check mark for Mode submenu — kept as `Option<bool>` (M4 / F8 R1):
    /// - `Some(true)` → currently-selected mode (renders `✓` prefix).
    /// - `Some(false)` → candidate but not selected (renders alignment padding
    ///   so all items in a checkable group line up).
    /// - `None` → item has no check column (normal File items).
    ///
    /// The third state is *load-bearing*: collapsing to `bool` would force every
    /// File entry to claim a check column and would render an extra
    /// `  ` indent on items that should sit flush with their button label.
    /// Keep as `Option<bool>` until a real need to widen it appears.
    pub checked: Option<bool>,
}

/// Returns the ordered File menu actions for the given app mode.
///
/// **Frozen signature (R7-88, M5 / F8 R1)**: the return type stays
/// `Vec<Action>` (not `Vec<MenuEntry>`). File items have no enabled/tooltip/
/// checked state distinct from "always enabled, no tooltip, no check", so the
/// richer `MenuEntry` adds nothing here while breaking the cross-platform
/// `tests/menu_actions_cross_platform.rs` contract that pins this signature.
// reason: called directly from `widget_menu_bar::entries_for_menu` (TopMenu::File arm)
// and by the cross-platform `tests/menu_actions_cross_platform.rs`. The
// `widget_menu_bar::menu_items` wrapper that R1 introduced was removed in
// R2 / M-A. Cross-OS callers reach this via source-inspection tests, so it
// appears unused to `cargo check` on Win/Mac despite being part of the public
// menu contract. (M6 / F8 R1 / R2 / R3)
#[allow(dead_code)]
pub fn actions_for_mode(_mode: &AppMode) -> Vec<Action> {
    vec![Action::Open, Action::Save, Action::SaveAs, Action::Quit]
}

/// State of the footer mode-toggle badge.
///
/// Computed by `mode_toggle_state` and passed to `status_bar` in `main.rs`.
/// `dirty` is NOT a field here — dirty state routes through the existing
/// `SaveAndSwitchMode` confirm dialog, so dirty is never a *disabled* reason.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ModeToggleState {
    pub current: AppMode,
    pub enabled: bool,
    /// Human-readable reason shown in the tooltip when `enabled == false`.
    pub disabled_reason: Option<&'static str>,
}

/// Computes the footer mode-toggle badge state.
///
/// Priority (highest first):
/// 1. `mode_switch_in_progress` → disabled, reason: `"Engine を再起動中…"`
/// 2. `engine_busy` → disabled, reason: `"Engine がビジーです"`
/// 3. otherwise → enabled
///
/// `dirty` is intentionally **not** a parameter — dirty state means "prompt
/// the user before switching", not "prevent switching altogether".
pub fn mode_toggle_state(
    current: AppMode,
    engine_busy: bool,
    mode_switch_in_progress: bool,
) -> ModeToggleState {
    if mode_switch_in_progress {
        return ModeToggleState {
            current,
            enabled: false,
            disabled_reason: Some("Engine を再起動中…"),
        };
    }
    if engine_busy {
        return ModeToggleState {
            current,
            enabled: false,
            disabled_reason: Some("Engine がビジーです"),
        };
    }
    ModeToggleState {
        current,
        enabled: true,
        disabled_reason: None,
    }
}

/// Per-button enable state for the replay control bar.
///
/// Computed by [`replay_control_state`] from four parameters.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[allow(dead_code)]
pub struct ReplayControlState {
    /// ▶ 再生 / Resume — enabled when idle (new session) or paused (resume).
    pub play: bool,
    /// ⏸ 一時停止 — enabled only when running (not paused).
    pub pause: bool,
    /// ⏭ Step+ — enabled only while paused (PAUSED state only; RUNNING → EngineBusy).
    pub step_forward: bool,
    /// ⏮ Step- — enabled while PAUSED AND has snapshot history.
    pub step_backward: bool,
    /// ⏹ 停止 — enabled while running or paused.
    pub stop: bool,
}

/// Computes the replay control bar button enable state.
///
/// | state               | ▶ | ⏸ | ⏭ | ⏮          | ⏹ |
/// |---------------------|---|---|---|------------|---|
/// | idle                | ✓ | ✗ | ✗ | ✗          | ✗ |
/// | running             | ✗ | ✓ | ✗ | ✗          | ✓ |
/// | paused              | ✓ | ✗ | ✓ | ✗ (no hist)| ✓ |
/// | paused + history    | ✓ | ✗ | ✓ | ✓          | ✓ |
///
/// Step+ enabled condition: `PAUSED` only (計画書 R1 決定; RUNNING 中は EngineBusy)
/// Step- enabled condition: `paused && replay_has_history` (PAUSED のみ; Python サーバーと整合)
#[allow(dead_code)]
pub fn replay_control_state(
    replay_running: bool,
    replay_paused: bool,
    replay_has_history: bool,
    mode_switch_in_progress: bool,
) -> ReplayControlState {
    if mode_switch_in_progress {
        return ReplayControlState {
            play: false,
            pause: false,
            step_forward: false,
            step_backward: false,
            stop: false,
        };
    }
    let idle = !replay_running;
    let running_not_paused = replay_running && !replay_paused;
    let paused = replay_running && replay_paused;
    ReplayControlState {
        play: idle || paused,
        pause: running_not_paused,
        step_forward: paused,
        step_backward: paused && replay_has_history,
        stop: replay_running,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── actions_for_mode ──────────────────────────────────────────────────

    #[test]
    fn live_actions_are_open_save_save_as_quit() {
        assert_eq!(
            actions_for_mode(&AppMode::Live),
            vec![Action::Open, Action::Save, Action::SaveAs, Action::Quit],
        );
    }

    #[test]
    fn replay_actions_are_same_as_live() {
        assert_eq!(
            actions_for_mode(&AppMode::Replay),
            vec![Action::Open, Action::Save, Action::SaveAs, Action::Quit],
        );
    }

    // ── mode_toggle_state (TT1-TT4) ──────────────────────────────────────

    #[test]
    fn tt1_all_false_yields_enabled() {
        let s = mode_toggle_state(AppMode::Live, false, false);
        assert!(s.enabled);
        assert_eq!(s.disabled_reason, None);
        assert_eq!(s.current, AppMode::Live);
    }

    #[test]
    fn tt2_engine_busy_disables_with_reason() {
        let s = mode_toggle_state(AppMode::Live, true, false);
        assert!(!s.enabled);
        assert_eq!(s.disabled_reason, Some("Engine がビジーです"));
    }

    #[test]
    fn tt4_mode_switch_in_progress_is_highest_priority() {
        let s = mode_toggle_state(AppMode::Replay, false, true);
        assert!(!s.enabled);
        assert_eq!(s.disabled_reason, Some("Engine を再起動中…"));
    }

    #[test]
    fn tt4b_mode_switch_alone_disables() {
        let s = mode_toggle_state(AppMode::Live, false, true);
        assert!(!s.enabled);
        assert_eq!(s.disabled_reason, Some("Engine を再起動中…"));
    }

    #[test]
    fn tt4c_replay_mode_also_disabled_when_switching() {
        let s = mode_toggle_state(AppMode::Replay, false, true);
        assert!(!s.enabled);
        assert_eq!(s.disabled_reason, Some("Engine を再起動中…"));
    }

    // ── replay_control_state ─────────────────────────────────────────────

    #[test]
    fn rcs_idle_only_play_enabled() {
        let s = replay_control_state(false, false, false, false);
        assert!(s.play, "idle: play should be enabled");
        assert!(!s.pause);
        assert!(!s.step_forward);
        assert!(!s.step_backward);
        assert!(!s.stop);
    }

    #[test]
    fn rcs_running_pause_and_stop_enabled() {
        let s = replay_control_state(true, false, false, false);
        assert!(!s.play, "running: play should be disabled");
        assert!(s.pause);
        assert!(!s.step_forward);
        assert!(!s.step_backward);
        assert!(s.stop);
    }

    #[test]
    fn rcs_paused_no_history() {
        let s = replay_control_state(true, true, false, false);
        assert!(s.play, "paused: play (resume) should be enabled");
        assert!(!s.pause);
        assert!(s.step_forward);
        assert!(
            !s.step_backward,
            "no history: step_backward should be disabled"
        );
        assert!(s.stop);
    }

    #[test]
    fn rcs_paused_with_history() {
        let s = replay_control_state(true, true, true, false);
        assert!(s.play, "paused+history: play (resume) should be enabled");
        assert!(!s.pause);
        assert!(s.step_forward);
        assert!(
            s.step_backward,
            "has history: step_backward should be enabled"
        );
        assert!(s.stop);
    }

    #[test]
    fn rcs_mode_switch_in_progress_disables_all() {
        let s = replay_control_state(true, true, true, true);
        assert!(!s.play, "mode switch: play should be disabled");
        assert!(!s.pause);
        assert!(!s.step_forward);
        assert!(!s.step_backward);
        assert!(!s.stop);
    }
}
