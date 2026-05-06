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
    // ── Replay menu ────────────────────────────────────────────────────────
    ReplayStart,
    ReplayStop,
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
pub fn actions_for_mode(mode: &AppMode) -> Vec<Action> {
    match mode {
        AppMode::Live => vec![Action::Open, Action::Save, Action::SaveAs, Action::Quit],
        AppMode::Replay => vec![Action::ReplayStart, Action::ReplayStop, Action::Quit],
    }
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
    fn replay_actions_are_replay_start_stop_quit() {
        assert_eq!(
            actions_for_mode(&AppMode::Replay),
            vec![Action::ReplayStart, Action::ReplayStop, Action::Quit],
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
}
