use engine_client::dto::AppMode;
use iced::Subscription;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Action {
    OpenFile,
    Save,
    SaveAs,
    OpenReplayDialog,
    Quit,
}

/// Returns which menu actions are present for a given app mode.
/// `(has_open_file, has_save, has_save_as, has_open_replay_dialog)`
#[cfg(test)]
pub(crate) fn actions_for_mode(app_mode: AppMode) -> (bool, bool, bool, bool) {
    match app_mode {
        AppMode::Live => (true, true, true, false),
        AppMode::Replay => (false, false, false, true),
    }
}

/// Attach the OS-native menu bar to the main window.
/// On Linux this is a no-op (iced sidebar covers the same ground).
pub fn attach(raw_id: u64, app_mode: AppMode) {
    #[cfg(any(target_os = "windows", target_os = "macos"))]
    platform::attach(raw_id, app_mode);

    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    let _ = (raw_id, app_mode);
}

/// `app_mode` is forwarded to the Linux keyboard subscription so it can
/// suppress live-only shortcuts (Open / Save / Save As) in replay mode.
/// On Windows / macOS, muda accelerators are already scoped by which items
/// the current mode shows, so no extra filtering is needed there.
pub fn subscription(app_mode: AppMode) -> Subscription<Action> {
    #[cfg(any(target_os = "windows", target_os = "macos"))]
    let _ = app_mode;

    #[cfg(any(target_os = "windows", target_os = "macos"))]
    return Subscription::run(platform::event_stream);

    // muda does not work on Linux GTK; replicate accelerators via iced
    // keyboard events instead (C-7: prevents double-dispatch on Win/Mac).
    #[cfg(target_os = "linux")]
    return linux_keyboard_subscription(app_mode);

    #[cfg(not(any(target_os = "windows", target_os = "macos", target_os = "linux")))]
    {
        let _ = app_mode;
        Subscription::none()
    }
}

// Linux-only: replicate muda accelerators via iced keyboard events so the
// same Message::NativeMenuAction(Action) path is used on all platforms.
// Live-only shortcuts (OpenFile / Save / SaveAs) are gated on app_mode to
// avoid JSON dialogs appearing during a replay session (HIGH-1 fix).
#[cfg(target_os = "linux")]
fn linux_keyboard_subscription(app_mode: AppMode) -> Subscription<Action> {
    iced::keyboard::on_key_press(move |key, modifiers| {
        let ctrl = modifiers.control();
        let shift = modifiers.shift();
        let is_live = app_mode == AppMode::Live;
        match key.as_ref() {
            iced::keyboard::Key::Character("o") if ctrl && !shift && is_live => {
                Some(Action::OpenFile)
            }
            iced::keyboard::Key::Character("s") if ctrl && !shift && is_live => Some(Action::Save),
            iced::keyboard::Key::Character("s") if ctrl && shift && is_live => Some(Action::SaveAs),
            // Ctrl+Q quits regardless of mode.
            iced::keyboard::Key::Character("q") if ctrl && !shift => Some(Action::Quit),
            _ => None,
        }
    })
}

#[cfg(any(target_os = "windows", target_os = "macos"))]
mod platform {
    use super::Action;
    use engine_client::dto::AppMode;
    use muda::{
        IsMenuItem, Menu, MenuEvent, MenuId, MenuItem, PredefinedMenuItem, Submenu,
        accelerator::{Accelerator, Code, Modifiers},
    };
    use std::sync::Mutex;

    struct MenuIds {
        open_file: Option<MenuId>,
        save: Option<MenuId>,
        save_as: Option<MenuId>,
        open_replay_dialog: Option<MenuId>,
        /// Windows only: Ctrl+Q quit MenuItem.
        /// macOS uses PredefinedMenuItem::quit whose Cmd+Q is OS-handled.
        quit: Option<MenuId>,
    }

    // `Mutex<Option<_>>` (not `OnceLock`) so that `attach()` called again after
    // `Flowsurface::restart()` can overwrite the IDs. Otherwise the new menu's
    // freshly-generated `MenuId`s would not match the cached ones and clicks
    // would silently do nothing.
    static MENU_IDS: Mutex<Option<MenuIds>> = Mutex::new(None);

    pub fn attach(raw_id: u64, app_mode: AppMode) {
        let menu = Menu::new();
        let file = Submenu::new("File", true);

        // --- Quit item (platform-specific) ---
        // Windows: regular MenuItem with Ctrl+Q — dispatched as Action::Quit.
        // macOS:   PredefinedMenuItem::quit gets Cmd+Q via the OS app menu;
        //          its click events are handled by NSApp directly, so we do
        //          not need to track the menu ID.
        #[cfg(target_os = "windows")]
        let quit_menu_item = MenuItem::new(
            "終了",
            true,
            Some(Accelerator::new(Some(Modifiers::CONTROL), Code::KeyQ)),
        );
        #[cfg(target_os = "windows")]
        let quit_id: Option<MenuId> = Some(quit_menu_item.id().clone());

        #[cfg(target_os = "macos")]
        let quit_menu_item = PredefinedMenuItem::quit(Some("終了"));
        #[cfg(target_os = "macos")]
        let quit_id: Option<MenuId> = None;

        let sep = PredefinedMenuItem::separator();

        let (open_file, save, save_as, open_replay_dialog) = match app_mode {
            AppMode::Live => {
                let open_item = MenuItem::new(
                    "開く\u{2026}（Open）",
                    true,
                    Some(Accelerator::new(Some(Modifiers::CONTROL), Code::KeyO)),
                );
                let save_item = MenuItem::new(
                    "上書き保存（Save）",
                    true,
                    Some(Accelerator::new(Some(Modifiers::CONTROL), Code::KeyS)),
                );
                let save_as_item = MenuItem::new(
                    "名前を付けて保存\u{2026}（Save As）",
                    true,
                    Some(Accelerator::new(
                        Some(Modifiers::CONTROL | Modifiers::SHIFT),
                        Code::KeyS,
                    )),
                );

                let open_id = open_item.id().clone();
                let save_id = save_item.id().clone();
                let save_as_id = save_as_item.id().clone();

                if let Err(e) = file.append_items(&[
                    &open_item as &dyn IsMenuItem,
                    &save_item as &dyn IsMenuItem,
                    &save_as_item as &dyn IsMenuItem,
                    &sep as &dyn IsMenuItem,
                    &quit_menu_item as &dyn IsMenuItem,
                ]) {
                    log::error!("[native_menu] append_items failed: {e:?}");
                    return;
                }

                (Some(open_id), Some(save_id), Some(save_as_id), None)
            }
            AppMode::Replay => {
                let replay_item = MenuItem::new("Replay を開始\u{2026}", true, None);

                let replay_id = replay_item.id().clone();

                if let Err(e) = file.append_items(&[
                    &replay_item as &dyn IsMenuItem,
                    &sep as &dyn IsMenuItem,
                    &quit_menu_item as &dyn IsMenuItem,
                ]) {
                    log::error!("[native_menu] append_items failed: {e:?}");
                    return;
                }

                (None, None, None, Some(replay_id))
            }
        };

        if let Err(e) = menu.append(&file) {
            log::error!("[native_menu] menu.append failed: {e:?}");
            return;
        }

        match MENU_IDS.lock() {
            Ok(mut guard) => {
                *guard = Some(MenuIds {
                    open_file,
                    save,
                    save_as,
                    open_replay_dialog,
                    quit: quit_id,
                });
            }
            Err(poisoned) => {
                log::error!("[native_menu] MENU_IDS poisoned during attach — recovering");
                *poisoned.into_inner() = Some(MenuIds {
                    open_file,
                    save,
                    save_as,
                    open_replay_dialog,
                    quit: quit_id,
                });
            }
        }

        // Leak the Menu so its Drop impl never runs and the native HMENU/NSMenu
        // stays registered for the lifetime of the process.
        // muda::Menu uses Rc internally so it is !Send and cannot go into a Mutex static.
        let menu_ref = Box::leak(Box::new(menu));

        #[cfg(target_os = "windows")]
        {
            // SAFETY: raw_id is the valid HWND of the main window, alive for
            // the duration of the application.
            if let Err(e) = unsafe { menu_ref.init_for_hwnd(raw_id as isize) } {
                log::error!("[native_menu] init_for_hwnd failed: {e:?}");
                // Clear MENU_IDS so event_stream does not treat menu actions as reachable
                // when the HMENU is not actually attached to the window.
                match MENU_IDS.lock() {
                    Ok(mut guard) => *guard = None,
                    Err(poisoned) => *poisoned.into_inner() = None,
                }
            }
        }

        #[cfg(target_os = "macos")]
        menu_ref.init_for_nsapp();
    }

    pub fn event_stream() -> impl iced::futures::Stream<Item = Action> + Send + 'static {
        async_stream::stream! {
            let receiver = MenuEvent::receiver();
            loop {
                while let Ok(event) = receiver.try_recv() {
                    let action = {
                        let guard = match MENU_IDS.lock() {
                            Ok(g) => g,
                            Err(poisoned) => {
                                log::error!(
                                    "[native_menu] MENU_IDS poisoned — recovering"
                                );
                                poisoned.into_inner()
                            }
                        };
                        let Some(ids) = guard.as_ref() else { continue };
                        if ids.open_file.as_ref().is_some_and(|id| *id == event.id) {
                            Some(Action::OpenFile)
                        } else if ids.save.as_ref().is_some_and(|id| *id == event.id) {
                            Some(Action::Save)
                        } else if ids.save_as.as_ref().is_some_and(|id| *id == event.id) {
                            Some(Action::SaveAs)
                        } else if ids
                            .open_replay_dialog
                            .as_ref()
                            .is_some_and(|id| *id == event.id)
                        {
                            Some(Action::OpenReplayDialog)
                        } else if ids.quit.as_ref().is_some_and(|id| *id == event.id) {
                            Some(Action::Quit)
                        } else {
                            None
                        }
                    };
                    if let Some(a) = action {
                        yield a;
                    }
                }
                tokio::time::sleep(std::time::Duration::from_millis(16)).await;
            }
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn menu_ids_mutex_allows_overwrite_on_reattach() {
            // OnceLock would silently ignore a second attach after restart.
            // Mutex<Option<_>> lets the new IDs overwrite the old ones.
            {
                let mut guard = MENU_IDS.lock().unwrap();
                *guard = Some(MenuIds {
                    open_file: None,
                    save: None,
                    save_as: None,
                    open_replay_dialog: None,
                    quit: None,
                });
            }
            {
                let mut guard = MENU_IDS.lock().unwrap();
                assert!(guard.is_some(), "first attach should set menu IDs");
                // Simulate re-attach after Flowsurface::restart()
                *guard = Some(MenuIds {
                    open_file: None,
                    save: None,
                    save_as: None,
                    open_replay_dialog: None,
                    quit: None,
                });
                assert!(guard.is_some(), "second attach must overwrite successfully");
                // Leave clean for other tests
                *guard = None;
            }
        }

        #[test]
        fn menu_ids_none_means_no_registered_actions() {
            let guard = MENU_IDS.lock().unwrap();
            let ids_ref = guard.as_ref();
            if let Some(ids) = ids_ref {
                let _ = ids.open_file.as_ref();
                let _ = ids.save.as_ref();
                let _ = ids.save_as.as_ref();
                let _ = ids.quit.as_ref();
            }
        }
    }
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
    }

    #[test]
    fn live_mode_provides_open_file_save_and_save_as() {
        let (open_file, save, save_as, open_replay_dialog) = actions_for_mode(AppMode::Live);
        assert!(open_file, "live mode must have Open File action");
        assert!(save, "live mode must have Save action");
        assert!(save_as, "live mode must have Save As action");
        assert!(
            !open_replay_dialog,
            "live mode must NOT have Open Replay Dialog action"
        );
    }

    #[test]
    fn replay_mode_provides_open_replay_dialog_only() {
        let (open_file, save, save_as, open_replay_dialog) = actions_for_mode(AppMode::Replay);
        assert!(!open_file, "replay mode must NOT have Open File action");
        assert!(!save, "replay mode must NOT have Save action");
        assert!(!save_as, "replay mode must NOT have Save As action");
        assert!(
            open_replay_dialog,
            "replay mode must have Open Replay Dialog action"
        );
    }
}
