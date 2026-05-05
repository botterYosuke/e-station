use engine_client::dto::AppMode;
use iced::Subscription;

use crate::wandb_auth::{RunBufferIndex, WandbAuthState};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Action {
    OpenFile,
    Save,
    SaveAs,
    OpenReplayDialog,
    /// Stop the currently running replay without switching app mode.
    /// (Replay モードに留まり、エンジンだけ IDLE に落とす)
    StopReplay,
    Quit,
    /// F7/T3: switch to the given app mode (menu item clicked).
    SwitchMode(AppMode),
    // ── Tools / W&B submenu ────────────────────────────────────────────────
    /// F9c: W&B に送信（Submit to W&B）
    SubmitToWandb,
    /// F9c: W&B にサインイン（Sign In to W&B）
    SignInWandb,
    /// F9c: W&B からサインアウト（Sign Out from W&B）
    SignOutWandb,
    /// F9c: 送信ログを開く（Open Submission Log）
    OpenSubmissionLog,
    /// F9c: バッファを消去（Clear Run Buffer）
    ClearRunBuffer,
}

/// Returns which menu actions are present for a given app mode.
///
/// Tuple: `(has_open_file, has_save, has_save_as, has_open_replay_dialog,
///          has_switch_live, has_switch_replay)`
///
/// `has_switch_live`   = `true` when we are currently in Replay mode (clicking
///                       it switches to Live).
/// `has_switch_replay` = `true` when we are currently in Live mode (clicking
///                       it switches to Replay).
#[cfg(test)]
pub(crate) fn actions_for_mode(app_mode: AppMode) -> (bool, bool, bool, bool, bool, bool) {
    match app_mode {
        AppMode::Live => (true, true, true, false, false, true),
        AppMode::Replay => (false, false, false, true, true, false),
    }
}

/// Attach the menu bar. All platforms now use the iced widget menu bar
/// (see `widget_menu_bar.rs`) which renders the `ツール（Tools）` submenu.
/// Enable/disable state for each item is computed by `tools_actions_for_state`
/// and read via each entry's `.enabled` field. The Tools items are:
/// - `submit_to_wandb`     → `Action::SubmitToWandb`
/// - `sign_in_wandb`       → `Action::SignInWandb`
/// - `sign_out_wandb`      → `Action::SignOutWandb`
/// - `open_submission_log` → `Action::OpenSubmissionLog`
/// - `clear_run_buffer`    → `Action::ClearRunBuffer`
pub fn attach(_raw_id: u64, _app_mode: AppMode, _auth: &WandbAuthState, _buffer: &RunBufferIndex) {}

/// Refresh Tools submenu enable/disable state (no-op on unified system).
/// Tools enable/disable is now computed by `menu_bar_state` and rendered by the widget.
pub fn refresh_tools_enable(_auth: &WandbAuthState, _buffer: &RunBufferIndex) {
    // All platforms use iced widget menu bar; enable/disable is reactive to state.
}

/// All platforms use the unified iced widget keyboard subscription.
/// No platform-specific branching needed.
pub fn subscription(app_mode: AppMode) -> Subscription<Action> {
    widget_keyboard_subscription(app_mode)
}

/// Unified keyboard subscription for all platforms (Windows / macOS / Linux).
/// Replaces the old muda-based Win/Mac and iced-based Linux implementations.
///
/// Match against `physical_key` (`Code::KeyO` etc.) rather than the produced
/// character so accelerators are layout-independent and survive IME state /
/// non-Latin keyboard layouts. The Cmd (logo) modifier is accepted only on
/// macOS — on Windows/Linux `logo()` is the Super/Win key, which must NOT
/// trigger app shortcuts (it conflicts with WM-level bindings).
///
/// Live-only shortcuts (OpenFile / Save / SaveAs) are gated on app_mode to avoid
/// JSON dialogs during replay (HIGH-1 fix).
/// SwitchMode dispatch is suppressed while MODE_SWITCHING is true (統一決定 64).
fn widget_keyboard_subscription(app_mode: AppMode) -> Subscription<Action> {
    use iced::keyboard::Event as KbEvent;
    use iced::keyboard::key::{Code, Physical};
    use std::sync::atomic::Ordering;

    let is_live = app_mode == AppMode::Live;
    iced::keyboard::listen()
        .with(is_live)
        .filter_map(|(is_live, event): (bool, KbEvent)| {
            let KbEvent::KeyPressed {
                physical_key,
                modifiers,
                ..
            } = event
            else {
                return None;
            };
            // macOS: Cmd (logo) is the standard primary modifier — accept it.
            // Win/Linux: logo() is Super/Win key — DO NOT accept (WM conflict).
            #[cfg(target_os = "macos")]
            let ctrl_or_cmd = modifiers.control() || modifiers.logo();
            #[cfg(not(target_os = "macos"))]
            let ctrl_or_cmd = modifiers.control();
            let shift = modifiers.shift();

            match physical_key {
                // Ctrl+O / Cmd+O (macOS): open file (live only)
                Physical::Code(Code::KeyO) if ctrl_or_cmd && !shift && is_live => {
                    Some(Action::OpenFile)
                }
                // Ctrl+S / Cmd+S (macOS): save (live only)
                Physical::Code(Code::KeyS) if ctrl_or_cmd && !shift && is_live => {
                    Some(Action::Save)
                }
                // Ctrl+Shift+S / Cmd+Shift+S (macOS): save as (live only)
                Physical::Code(Code::KeyS) if ctrl_or_cmd && shift && is_live => {
                    Some(Action::SaveAs)
                }
                // Ctrl+Q / Cmd+Q (macOS): quit (both modes)
                Physical::Code(Code::KeyQ) if ctrl_or_cmd && !shift => Some(Action::Quit),
                // Ctrl+M / Cmd+M (macOS): switch mode (Live ↔ Replay)
                // Suppressed while a mode-switch is already in progress (統一決定 64)
                Physical::Code(Code::KeyM) if ctrl_or_cmd && !shift => {
                    if crate::MODE_SWITCHING.load(Ordering::Acquire) {
                        None
                    } else {
                        let target = if is_live {
                            AppMode::Replay
                        } else {
                            AppMode::Live
                        };
                        Some(Action::SwitchMode(target))
                    }
                }
                _ => None,
            }
        })
}

#[cfg(test)]
mod tests {
    use super::*;
    use engine_client::dto::AppMode;

    #[test]
    fn action_variants_are_distinct() {
        assert_ne!(Action::OpenFile, Action::Save);
        assert_ne!(Action::OpenFile, Action::SaveAs);
        assert_ne!(Action::OpenFile, Action::OpenReplayDialog);
        assert_ne!(Action::OpenFile, Action::Quit);
        assert_ne!(Action::Save, Action::SaveAs);
        assert_ne!(Action::Save, Action::OpenReplayDialog);
        assert_ne!(Action::Save, Action::Quit);
        assert_ne!(Action::SaveAs, Action::OpenReplayDialog);
        assert_ne!(Action::SaveAs, Action::Quit);
        assert_ne!(Action::OpenReplayDialog, Action::Quit);
        // SwitchMode variants
        assert_ne!(
            Action::SwitchMode(AppMode::Live),
            Action::SwitchMode(AppMode::Replay)
        );
        assert_ne!(Action::OpenFile, Action::SwitchMode(AppMode::Live));
    }

    #[test]
    fn live_mode_provides_open_file_save_and_save_as() {
        let (open_file, save, save_as, open_replay_dialog, switch_live, switch_replay) =
            actions_for_mode(AppMode::Live);
        assert!(open_file, "live mode must have Open File action");
        assert!(save, "live mode must have Save action");
        assert!(save_as, "live mode must have Save As action");
        assert!(
            !open_replay_dialog,
            "live mode must NOT have Open Replay Dialog action"
        );
        assert!(
            !switch_live,
            "live mode must NOT offer switch-to-live (already live)"
        );
        assert!(
            switch_replay,
            "live mode must offer switch-to-replay action"
        );
    }

    #[test]
    fn replay_mode_provides_open_replay_dialog_only() {
        let (open_file, save, save_as, open_replay_dialog, switch_live, switch_replay) =
            actions_for_mode(AppMode::Replay);
        assert!(!open_file, "replay mode must NOT have Open File action");
        assert!(!save, "replay mode must NOT have Save action");
        assert!(!save_as, "replay mode must NOT have Save As action");
        assert!(
            open_replay_dialog,
            "replay mode must have Open Replay Dialog action"
        );
        assert!(switch_live, "replay mode must offer switch-to-live action");
        assert!(
            !switch_replay,
            "replay mode must NOT offer switch-to-replay (already replay)"
        );
    }
}
