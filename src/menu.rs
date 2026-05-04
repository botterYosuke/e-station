use engine_client::dto::AppMode;

/// Unified menu action enum for cross-platform dispatch.
///
/// `native_menu::Action` covers Win/Mac muda items. This enum is the
/// common vocabulary used by both the Linux iced widget menu bar and
/// any future cross-platform menu infrastructure.
///
/// **Invariant R7-88**: `actions_for_mode` returns only File/Mode actions.
/// Tools submenu actions live exclusively in `tools_actions_for_state`.
#[derive(Debug, Clone, PartialEq, Eq)]
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
    // ── Tools / W&B submenu ────────────────────────────────────────────────
    SubmitToWandb,
    SignInWandb,
    SignOutWandb,
    OpenSubmissionLog,
    ClearRunBuffer,
}

/// A menu item entry for submenus that need enabled/tooltip/checked state
/// (Mode submenu, Tools submenu).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MenuEntry {
    pub action: Action,
    pub enabled: bool,
    pub tooltip: Option<String>,
    /// Exclusive check mark for Mode submenu:
    /// `Some(true)` = currently selected, `Some(false)` = available but not
    /// selected, `None` = no check display (normal File-style items).
    pub checked: Option<bool>,
}

/// W&B authentication state — drives Tools submenu composition.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AuthState {
    SignedIn,
    SignedOut,
}

/// Local run-buffer state — drives Tools submenu composition.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BufferState {
    HasRuns,
    Empty,
}

/// Returns the ordered File menu actions for the given app mode.
///
/// **Invariant R7-88 / DoD-11**: This function contains ONLY File/Quit
/// actions. Tools submenu actions must NOT appear here; they belong in
/// `tools_actions_for_state`.
pub fn actions_for_mode(mode: &AppMode) -> Vec<Action> {
    match mode {
        AppMode::Live => vec![Action::Open, Action::Save, Action::SaveAs, Action::Quit],
        AppMode::Replay => vec![Action::ReplayStart, Action::ReplayStop, Action::Quit],
    }
}

/// Returns the ordered Tools submenu actions given the current W&B
/// authentication and run-buffer state (DoD-10 / R3-66/69).
pub fn tools_actions_for_state(auth_state: AuthState, buffer_state: BufferState) -> Vec<Action> {
    use AuthState::*;
    use BufferState::*;
    match (auth_state, buffer_state) {
        (SignedOut, Empty) => vec![Action::SignInWandb],
        (SignedOut, HasRuns) => vec![
            Action::SignInWandb,
            Action::OpenSubmissionLog,
            Action::ClearRunBuffer,
        ],
        (SignedIn, Empty) => vec![Action::SignOutWandb],
        (SignedIn, HasRuns) => vec![
            Action::SubmitToWandb,
            Action::OpenSubmissionLog,
            Action::ClearRunBuffer,
            Action::SignOutWandb,
        ],
    }
}

/// Returns the `モード（Mode）▼` submenu entries with exclusive check marks
/// showing the currently active mode (DoD-13/14 / R7-87).
pub fn mode_menu_items(current_mode: &AppMode) -> Vec<MenuEntry> {
    vec![
        MenuEntry {
            action: Action::SwitchAppMode(AppMode::Live),
            enabled: true,
            tooltip: None,
            checked: Some(matches!(current_mode, AppMode::Live)),
        },
        MenuEntry {
            action: Action::SwitchAppMode(AppMode::Replay),
            enabled: true,
            tooltip: None,
            checked: Some(matches!(current_mode, AppMode::Replay)),
        },
    ]
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

    #[test]
    fn actions_for_mode_excludes_tools_actions() {
        let tools_only = [
            Action::SubmitToWandb,
            Action::SignInWandb,
            Action::SignOutWandb,
            Action::OpenSubmissionLog,
            Action::ClearRunBuffer,
        ];
        for mode in [AppMode::Live, AppMode::Replay] {
            let got = actions_for_mode(&mode);
            for action in &tools_only {
                assert!(
                    !got.contains(action),
                    "Tools action {action:?} must not appear in actions_for_mode({mode:?}) — DoD-11"
                );
            }
        }
    }

    // ── tools_actions_for_state ────────────────────────────────────────────

    #[test]
    fn signed_out_empty_offers_signin_only() {
        assert_eq!(
            tools_actions_for_state(AuthState::SignedOut, BufferState::Empty),
            vec![Action::SignInWandb],
        );
    }

    #[test]
    fn signed_out_has_runs_offers_signin_and_buffer_ops() {
        assert_eq!(
            tools_actions_for_state(AuthState::SignedOut, BufferState::HasRuns),
            vec![
                Action::SignInWandb,
                Action::OpenSubmissionLog,
                Action::ClearRunBuffer,
            ],
        );
    }

    #[test]
    fn signed_in_empty_offers_signout_only() {
        assert_eq!(
            tools_actions_for_state(AuthState::SignedIn, BufferState::Empty),
            vec![Action::SignOutWandb],
        );
    }

    #[test]
    fn signed_in_has_runs_offers_full_submission_flow() {
        assert_eq!(
            tools_actions_for_state(AuthState::SignedIn, BufferState::HasRuns),
            vec![
                Action::SubmitToWandb,
                Action::OpenSubmissionLog,
                Action::ClearRunBuffer,
                Action::SignOutWandb,
            ],
        );
    }

    // ── mode_menu_items ────────────────────────────────────────────────────

    #[test]
    fn live_mode_marks_live_checked_replay_unchecked() {
        let got = mode_menu_items(&AppMode::Live);
        assert_eq!(got.len(), 2);
        assert_eq!(got[0].action, Action::SwitchAppMode(AppMode::Live));
        assert_eq!(got[0].checked, Some(true));
        assert!(got[0].enabled);
        assert_eq!(got[1].action, Action::SwitchAppMode(AppMode::Replay));
        assert_eq!(got[1].checked, Some(false));
        assert!(got[1].enabled);
    }

    #[test]
    fn replay_mode_marks_replay_checked_live_unchecked() {
        let got = mode_menu_items(&AppMode::Replay);
        assert_eq!(got.len(), 2);
        assert_eq!(got[0].action, Action::SwitchAppMode(AppMode::Live));
        assert_eq!(got[0].checked, Some(false));
        assert_eq!(got[1].action, Action::SwitchAppMode(AppMode::Replay));
        assert_eq!(got[1].checked, Some(true));
    }

    #[test]
    fn mode_menu_entries_all_dispatch_switch_app_mode() {
        for current in [AppMode::Live, AppMode::Replay] {
            for entry in mode_menu_items(&current) {
                match entry.action {
                    Action::SwitchAppMode(_) => {}
                    ref other => {
                        panic!("expected SwitchAppMode, got {other:?} — DoD-14");
                    }
                }
            }
        }
    }
}
