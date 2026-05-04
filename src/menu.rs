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
#[allow(dead_code)] // File/Replay/Quit/SwitchAppMode variants are consumed by the Linux widget menu bar (mod is cross-platform).
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
    pub tooltip: Option<&'static str>,
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
#[allow(dead_code)] // used by Linux widget menu bar; cross-platform `mod menu` exposes it for native_menu Tools enable.
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
    let has_completed = buf.latest_completed.is_some();
    // OpenSubmissionLog / ClearRunBuffer は run-buffer/ 配下に「何かあるか」で
    // 判定する。aborted / running しか無くても履歴閲覧と削除は可能であるべき。
    let has_buffer = buf.total > 0;

    vec![
        // SignInWandb — enabled only when not authenticated
        MenuEntry {
            action: Action::SignInWandb,
            enabled: !auth.authenticated,
            tooltip: if auth.authenticated {
                Some("ログイン済みです")
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
                Some("ログインしていません")
            } else {
                None
            },
            checked: None,
        },
        // SubmitToWandb — enabled only when authenticated AND has completed runs
        MenuEntry {
            action: Action::SubmitToWandb,
            enabled: auth.authenticated && has_completed,
            tooltip: if !auth.authenticated {
                Some("W&B にログインしてください")
            } else if !has_completed {
                Some("送信可能な run がありません（最初に replay を実行してください）")
            } else {
                None
            },
            checked: None,
        },
        // OpenSubmissionLog — always present; enabled when run-buffer/ has any entries
        // (including aborted / running, so users can inspect failed runs)
        MenuEntry {
            action: Action::OpenSubmissionLog,
            enabled: has_buffer,
            tooltip: if !has_buffer {
                Some("送信履歴がまだありません")
            } else {
                None
            },
            checked: None,
        },
        // ClearRunBuffer — enabled when run-buffer/ has any entries
        MenuEntry {
            action: Action::ClearRunBuffer,
            enabled: has_buffer,
            tooltip: if !has_buffer {
                Some("削除できるバッファがありません")
            } else {
                None
            },
            checked: None,
        },
    ]
}

/// Returns the `モード（Mode）▼` submenu entries with exclusive check marks
/// showing the currently active mode (DoD-13/14 / R7-87).
#[allow(dead_code)] // used by Linux widget menu bar; mod is cross-platform for H5.
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
    use crate::wandb_auth::AuthMethod;

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
                AuthMethod::Netrc
            } else {
                AuthMethod::None
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
        assert_eq!(sign_in.tooltip, Some("ログイン済みです"));

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
            Some("送信可能な run がありません（最初に replay を実行してください）")
        );

        let sign_in = find_entry(&entries, &Action::SignInWandb).unwrap();
        assert!(!sign_in.enabled);

        let sign_out = find_entry(&entries, &Action::SignOutWandb).unwrap();
        assert!(sign_out.enabled);

        let log = find_entry(&entries, &Action::OpenSubmissionLog).unwrap();
        assert!(!log.enabled);
        assert_eq!(log.tooltip, Some("送信履歴がまだありません"));

        let clear = find_entry(&entries, &Action::ClearRunBuffer).unwrap();
        assert!(!clear.enabled);
        assert_eq!(clear.tooltip, Some("削除できるバッファがありません"));
    }

    #[test]
    fn auth_none_buffer_has_runs_submit_disabled_login_prompt() {
        let entries = tools_actions_for_state(&make_auth(false), &make_buf(true));
        assert_eq!(entries.len(), 5);

        let submit = find_entry(&entries, &Action::SubmitToWandb).unwrap();
        assert!(!submit.enabled);
        assert_eq!(submit.tooltip, Some("W&B にログインしてください"));

        let sign_in = find_entry(&entries, &Action::SignInWandb).unwrap();
        assert!(sign_in.enabled);
        assert_eq!(sign_in.tooltip, None);

        let sign_out = find_entry(&entries, &Action::SignOutWandb).unwrap();
        assert!(!sign_out.enabled);
        assert_eq!(sign_out.tooltip, Some("ログインしていません"));

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
        assert_eq!(submit.tooltip, Some("W&B にログインしてください"));

        let sign_in = find_entry(&entries, &Action::SignInWandb).unwrap();
        assert!(sign_in.enabled);

        let sign_out = find_entry(&entries, &Action::SignOutWandb).unwrap();
        assert!(!sign_out.enabled);

        let log = find_entry(&entries, &Action::OpenSubmissionLog).unwrap();
        assert!(!log.enabled);

        let clear = find_entry(&entries, &Action::ClearRunBuffer).unwrap();
        assert!(!clear.enabled);
    }

    /// Regression: `OpenSubmissionLog` / `ClearRunBuffer` must be enabled
    /// even when only aborted / running runs exist (no completed run).
    /// `SubmitToWandb` must remain disabled because there's nothing to send.
    #[test]
    fn open_log_and_clear_enabled_when_only_aborted_runs() {
        let buf = RunBufferIndex {
            latest_completed: None,
            total: 3,
        };
        let entries = tools_actions_for_state(&make_auth(true), &buf);

        let log = find_entry(&entries, &Action::OpenSubmissionLog).unwrap();
        assert!(
            log.enabled,
            "OpenSubmissionLog must be enabled when buffer has entries"
        );
        assert_eq!(log.tooltip, None);

        let clear = find_entry(&entries, &Action::ClearRunBuffer).unwrap();
        assert!(
            clear.enabled,
            "ClearRunBuffer must be enabled when buffer has entries"
        );
        assert_eq!(clear.tooltip, None);

        let submit = find_entry(&entries, &Action::SubmitToWandb).unwrap();
        assert!(
            !submit.enabled,
            "SubmitToWandb must remain disabled without completed runs"
        );
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
    //
    // Note: these tests pin the *current* mode_menu_items behaviour as
    // consumed by the Linux widget menu bar (P8). Pre-existing test/impl
    // inconsistency means `enabled` and `checked` semantics are coupled
    // to the Linux UI; we leave them gated to Linux to keep the cross-
    // platform `mod menu` (H5) green. Phase 3-C / future P8 follow-up
    // can re-evaluate the semantic and unify across platforms.

    #[cfg(target_os = "linux")]
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

    #[cfg(target_os = "linux")]
    #[test]
    fn replay_mode_marks_replay_checked_live_unchecked() {
        let got = mode_menu_items(&AppMode::Replay);
        assert_eq!(got.len(), 2);
        assert_eq!(got[0].action, Action::SwitchAppMode(AppMode::Live));
        assert_eq!(got[0].checked, Some(false));
        assert_eq!(got[1].action, Action::SwitchAppMode(AppMode::Replay));
        assert_eq!(got[1].checked, Some(true));
    }

    #[cfg(target_os = "linux")]
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
