use engine_client::dto::AppMode;
use iced::Subscription;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Action {
    OpenFile,
    Save,
    SaveAs,
    Quit,
    /// F7/T3: switch to the given app mode (menu item clicked).
    SwitchMode(AppMode),
    Screenshot,
}

/// Returns which menu actions are present for a given app mode.
///
/// Tuple: `(has_open_file, has_save, has_save_as, has_switch_live, has_switch_replay)`
///
/// `has_switch_live`   = `true` when we are currently in Replay mode.
/// `has_switch_replay` = `true` when we are currently in Live mode.
///
/// File actions (Open / Save / Save As) are available in both modes so that the
/// keyboard shortcuts shown in the File menu actually work in replay mode too.
#[cfg(test)]
pub(crate) fn actions_for_mode(app_mode: AppMode) -> (bool, bool, bool, bool, bool) {
    match app_mode {
        AppMode::Live => (true, true, true, false, true),
        AppMode::Replay => (true, true, true, true, false),
    }
}

/// Attach the menu bar. All platforms now use the iced widget menu bar
/// (see `widget_menu_bar.rs`).
pub fn attach(_raw_id: u64, _app_mode: AppMode) {}

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
                // Ctrl+O / Cmd+O (macOS): open file (both modes — spec: same File menu in replay)
                Physical::Code(Code::KeyO) if ctrl_or_cmd && !shift => Some(Action::OpenFile),
                // Ctrl+S / Cmd+S (macOS): save (both modes)
                Physical::Code(Code::KeyS) if ctrl_or_cmd && !shift => Some(Action::Save),
                // Ctrl+Shift+S / Cmd+Shift+S (macOS): save as (both modes)
                Physical::Code(Code::KeyS) if ctrl_or_cmd && shift => Some(Action::SaveAs),
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
                // F12: capture screenshot (both modes, no modifier required)
                Physical::Code(Code::F12) => Some(Action::Screenshot),
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
        assert_ne!(Action::OpenFile, Action::Quit);
        assert_ne!(Action::Save, Action::SaveAs);
        assert_ne!(Action::Save, Action::Quit);
        assert_ne!(Action::SaveAs, Action::Quit);
        // SwitchMode variants
        assert_ne!(
            Action::SwitchMode(AppMode::Live),
            Action::SwitchMode(AppMode::Replay)
        );
        assert_ne!(Action::OpenFile, Action::SwitchMode(AppMode::Live));
    }

    #[test]
    fn live_mode_provides_open_file_save_and_save_as() {
        let (open_file, save, save_as, switch_live, switch_replay) =
            actions_for_mode(AppMode::Live);
        assert!(open_file, "live mode must have Open File action");
        assert!(save, "live mode must have Save action");
        assert!(save_as, "live mode must have Save As action");
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
    fn replay_mode_has_file_actions() {
        // Spec: replay and live modes share the same File menu (Open/Save/SaveAs/Quit).
        // Keyboard shortcuts must therefore work in both modes.
        let (open_file, save, save_as, switch_live, switch_replay) =
            actions_for_mode(AppMode::Replay);
        assert!(open_file, "replay mode must have Open File action");
        assert!(save, "replay mode must have Save action");
        assert!(save_as, "replay mode must have Save As action");
        assert!(switch_live, "replay mode must offer switch-to-live action");
        assert!(
            !switch_replay,
            "replay mode must NOT offer switch-to-replay (already replay)"
        );
    }
}
