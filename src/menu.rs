use engine_client::dto::AppMode;

use crate::wandb_auth::{RunBufferIndex, WandbAuthState};

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

/// W&B authentication state — legacy enum; kept for backward compatibility.
/// New code uses `WandbAuthState` from `crate::wandb_auth` (R7-86).
#[allow(dead_code)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AuthState {
    SignedIn,
    SignedOut,
}

/// Local run-buffer state — legacy enum; kept for backward compatibility.
/// New code uses `RunBufferIndex` from `crate::wandb_auth` (R7-86).
#[allow(dead_code)]
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

/// Returns the ordered Tools submenu entries given the current W&B
/// authentication and run-buffer state (DoD-10 / R3-66/69 / R7-86).
///
/// Invariants:
/// - `SignInWandb` and `SignOutWandb` are mutually exclusive: exactly one is enabled.
/// - `OpenSubmissionLog` is always present in the returned Vec.
/// - `tooltip` is `None` when `enabled=true`, `Some(reason)` when `enabled=false`.
pub fn tools_actions_for_state(auth: &WandbAuthState, buf: &RunBufferIndex) -> Vec<MenuEntry> {
    let has_runs = buf.latest_completed.is_some();

    vec![
        // SignInWandb — enabled only when not authenticated
        MenuEntry {
            action: Action::SignInWandb,
            enabled: !auth.authenticated,
            tooltip: if auth.authenticated {
                Some("ログイン済みです".to_string())
            } else {
                None
            },
            checked: None,
        },
        // SignOutWandb — enabled only when authenticated
        MenuEntry {
            action: Action::SignOutWandb,
            enabled: auth.authenticated,
            tooltip: if !auth.authenticated {
                Some("ログインしていません".to_string())
            } else {
                None
            },
            checked: None,
        },
        // SubmitToWandb — enabled only when authenticated AND has completed runs
        MenuEntry {
            action: Action::SubmitToWandb,
            enabled: auth.authenticated && has_runs,
            tooltip: if !auth.authenticated {
                Some("W&B にログインしてください".to_string())
            } else if !has_runs {
                Some("送信可能な run がありません（最初に replay を実行してください）".to_string())
            } else {
                None
            },
            checked: None,
        },
        // OpenSubmissionLog — always present; enabled only when has_runs
        MenuEntry {
            action: Action::OpenSubmissionLog,
            enabled: has_runs,
            tooltip: if !has_runs {
                Some("送信履歴がまだありません".to_string())
            } else {
                None
            },
            checked: None,
        },
        // ClearRunBuffer — enabled only when has_runs
        MenuEntry {
            action: Action::ClearRunBuffer,
            enabled: has_runs,
            tooltip: if !has_runs {
                Some("削除できるバッファがありません".to_string())
            } else {
                None
            },
            checked: None,
        },
    ]
}

/// Returns the `モード（Mode）▼` submenu entries with exclusive check marks
/// showing the currently active mode (DoD-13/14 / R7-87).
pub fn mode_menu_items(current_mode: &AppMode) -> Vec<MenuEntry> {
    vec![
        MenuEntry {
            action: Action::SwitchAppMode(AppMode::Live),
            enabled: !matches!(current_mode, AppMode::Live),
            tooltip: None,
            checked: Some(matches!(current_mode, AppMode::Live)),
        },
        MenuEntry {
            action: Action::SwitchAppMode(AppMode::Replay),
            enabled: !matches!(current_mode, AppMode::Replay),
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

    // ── tools_actions_for_state (R7-86: Vec<MenuEntry>) ──────────────────

    fn make_auth(authenticated: bool) -> WandbAuthState {
        WandbAuthState {
            authenticated,
            method: if authenticated {
                "netrc".to_string()
            } else {
                "none".to_string()
            },
            username: None,
            error: None,
        }
    }

    fn make_buf(has_runs: bool) -> RunBufferIndex {
        if has_runs {
            RunBufferIndex {
                latest_completed: Some("run-1".to_string()),
                total: 1,
            }
        } else {
            RunBufferIndex::empty()
        }
    }

    fn find_entry(entries: &[MenuEntry], action: &Action) -> Option<MenuEntry> {
        entries.iter().find(|e| &e.action == action).cloned()
    }

    #[test]
    fn auth_ok_buffer_has_runs_submit_enabled() {
        let entries = tools_actions_for_state(&make_auth(true), &make_buf(true));
        assert_eq!(entries.len(), 5);

        let submit = find_entry(&entries, &Action::SubmitToWandb).unwrap();
        assert!(submit.enabled);
        assert_eq!(submit.tooltip, None);

        let sign_in = find_entry(&entries, &Action::SignInWandb).unwrap();
        assert!(!sign_in.enabled);
        assert_eq!(sign_in.tooltip, Some("ログイン済みです".to_string()));

        let sign_out = find_entry(&entries, &Action::SignOutWandb).unwrap();
        assert!(sign_out.enabled);
        assert_eq!(sign_out.tooltip, None);

        let log = find_entry(&entries, &Action::OpenSubmissionLog).unwrap();
        assert!(log.enabled);
        assert_eq!(log.tooltip, None);

        let clear = find_entry(&entries, &Action::ClearRunBuffer).unwrap();
        assert!(clear.enabled);
        assert_eq!(clear.tooltip, None);
    }

    #[test]
    fn auth_ok_buffer_empty_submit_disabled() {
        let entries = tools_actions_for_state(&make_auth(true), &make_buf(false));
        assert_eq!(entries.len(), 5);

        let submit = find_entry(&entries, &Action::SubmitToWandb).unwrap();
        assert!(!submit.enabled);
        assert_eq!(
            submit.tooltip,
            Some("送信可能な run がありません（最初に replay を実行してください）".to_string())
        );

        let sign_in = find_entry(&entries, &Action::SignInWandb).unwrap();
        assert!(!sign_in.enabled);

        let sign_out = find_entry(&entries, &Action::SignOutWandb).unwrap();
        assert!(sign_out.enabled);

        let log = find_entry(&entries, &Action::OpenSubmissionLog).unwrap();
        assert!(!log.enabled);
        assert_eq!(log.tooltip, Some("送信履歴がまだありません".to_string()));

        let clear = find_entry(&entries, &Action::ClearRunBuffer).unwrap();
        assert!(!clear.enabled);
        assert_eq!(
            clear.tooltip,
            Some("削除できるバッファがありません".to_string())
        );
    }

    #[test]
    fn auth_none_buffer_has_runs_submit_disabled_login_prompt() {
        let entries = tools_actions_for_state(&make_auth(false), &make_buf(true));
        assert_eq!(entries.len(), 5);

        let submit = find_entry(&entries, &Action::SubmitToWandb).unwrap();
        assert!(!submit.enabled);
        assert_eq!(
            submit.tooltip,
            Some("W&B にログインしてください".to_string())
        );

        let sign_in = find_entry(&entries, &Action::SignInWandb).unwrap();
        assert!(sign_in.enabled);
        assert_eq!(sign_in.tooltip, None);

        let sign_out = find_entry(&entries, &Action::SignOutWandb).unwrap();
        assert!(!sign_out.enabled);
        assert_eq!(sign_out.tooltip, Some("ログインしていません".to_string()));

        let log = find_entry(&entries, &Action::OpenSubmissionLog).unwrap();
        assert!(log.enabled);

        let clear = find_entry(&entries, &Action::ClearRunBuffer).unwrap();
        assert!(clear.enabled);
    }

    #[test]
    fn auth_none_buffer_empty_all_disabled_appropriately() {
        let entries = tools_actions_for_state(&make_auth(false), &make_buf(false));
        assert_eq!(entries.len(), 5);

        let submit = find_entry(&entries, &Action::SubmitToWandb).unwrap();
        assert!(!submit.enabled);
        assert_eq!(
            submit.tooltip,
            Some("W&B にログインしてください".to_string())
        );

        let sign_in = find_entry(&entries, &Action::SignInWandb).unwrap();
        assert!(sign_in.enabled);

        let sign_out = find_entry(&entries, &Action::SignOutWandb).unwrap();
        assert!(!sign_out.enabled);

        let log = find_entry(&entries, &Action::OpenSubmissionLog).unwrap();
        assert!(!log.enabled);

        let clear = find_entry(&entries, &Action::ClearRunBuffer).unwrap();
        assert!(!clear.enabled);
    }

    #[test]
    fn signin_signout_mutually_exclusive_all_combinations() {
        for authenticated in [true, false] {
            for has_runs in [true, false] {
                let entries =
                    tools_actions_for_state(&make_auth(authenticated), &make_buf(has_runs));
                let sign_in = find_entry(&entries, &Action::SignInWandb).unwrap();
                let sign_out = find_entry(&entries, &Action::SignOutWandb).unwrap();
                assert_ne!(
                    sign_in.enabled, sign_out.enabled,
                    "SignInWandb and SignOutWandb must be mutually exclusive \
                     (auth={authenticated}, has_runs={has_runs})"
                );
            }
        }
    }

    #[test]
    fn open_submission_log_always_present_all_combinations() {
        for authenticated in [true, false] {
            for has_runs in [true, false] {
                let entries =
                    tools_actions_for_state(&make_auth(authenticated), &make_buf(has_runs));
                assert!(
                    find_entry(&entries, &Action::OpenSubmissionLog).is_some(),
                    "OpenSubmissionLog must always be present \
                     (auth={authenticated}, has_runs={has_runs})"
                );
            }
        }
    }

    #[test]
    fn enabled_true_always_has_none_tooltip() {
        for authenticated in [true, false] {
            for has_runs in [true, false] {
                let entries =
                    tools_actions_for_state(&make_auth(authenticated), &make_buf(has_runs));
                for entry in &entries {
                    if entry.enabled {
                        assert_eq!(
                            entry.tooltip, None,
                            "enabled entry {:?} must have tooltip=None \
                             (auth={authenticated}, has_runs={has_runs})",
                            entry.action
                        );
                    } else {
                        assert!(
                            entry.tooltip.is_some(),
                            "disabled entry {:?} must have tooltip=Some(...) \
                             (auth={authenticated}, has_runs={has_runs})",
                            entry.action
                        );
                    }
                }
            }
        }
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
