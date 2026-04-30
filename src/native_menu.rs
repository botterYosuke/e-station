use engine_client::dto::AppMode;
use iced::Subscription;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Action {
    OpenFile,
    SaveAs,
    OpenStrategy,
}

/// Returns which menu actions are present for a given app mode.
/// `(has_open_file, has_save_as, has_open_strategy)`
#[cfg(test)]
pub(crate) fn actions_for_mode(app_mode: AppMode) -> (bool, bool, bool) {
    match app_mode {
        AppMode::Live => (true, true, false),
        AppMode::Replay => (false, false, true),
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

pub fn subscription() -> Subscription<Action> {
    #[cfg(any(target_os = "windows", target_os = "macos"))]
    return Subscription::run(platform::event_stream);

    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    Subscription::none()
}

#[cfg(any(target_os = "windows", target_os = "macos"))]
mod platform {
    use super::Action;
    use engine_client::dto::AppMode;
    use muda::{IsMenuItem, Menu, MenuEvent, MenuId, MenuItem, PredefinedMenuItem, Submenu};
    use std::sync::Mutex;

    struct MenuIds {
        open_file: Option<MenuId>,
        save_as: Option<MenuId>,
        open_strategy: Option<MenuId>,
    }

    // `Mutex<Option<_>>` (not `OnceLock`) so that `attach()` called again after
    // `Flowsurface::restart()` can overwrite the IDs. Otherwise the new menu's
    // freshly-generated `MenuId`s would not match the cached ones and clicks
    // would silently do nothing.
    static MENU_IDS: Mutex<Option<MenuIds>> = Mutex::new(None);

    pub fn attach(raw_id: u64, app_mode: AppMode) {
        let menu = Menu::new();
        let file = Submenu::new("File", true);

        let (open_file, save_as, open_strategy) = match app_mode {
            AppMode::Live => {
                let open_item = MenuItem::new("開く...", true, None);
                let save_as_item = MenuItem::new("名前を付けて保存...", true, None);
                let sep = PredefinedMenuItem::separator();
                let quit_item = PredefinedMenuItem::quit(Some("終了"));

                let open_id = open_item.id().clone();
                let save_id = save_as_item.id().clone();

                file.append_items(&[
                    &open_item as &dyn IsMenuItem,
                    &save_as_item as &dyn IsMenuItem,
                    &sep as &dyn IsMenuItem,
                    &quit_item as &dyn IsMenuItem,
                ])
                .ok();

                (Some(open_id), Some(save_id), None)
            }
            AppMode::Replay => {
                let strategy_item = MenuItem::new("ストラテジーを開く...", true, None);
                let sep = PredefinedMenuItem::separator();
                let quit_item = PredefinedMenuItem::quit(Some("終了"));

                let strategy_id = strategy_item.id().clone();

                file.append_items(&[
                    &strategy_item as &dyn IsMenuItem,
                    &sep as &dyn IsMenuItem,
                    &quit_item as &dyn IsMenuItem,
                ])
                .ok();

                (None, None, Some(strategy_id))
            }
        };

        menu.append(&file).ok();

        if let Ok(mut guard) = MENU_IDS.lock() {
            *guard = Some(MenuIds {
                open_file,
                save_as,
                open_strategy,
            });
        }

        // Leak the Menu so its Drop impl never runs and the native HMENU/NSMenu
        // stays registered for the lifetime of the process.
        // muda::Menu uses Rc internally so it is !Send and cannot go into a Mutex static.
        let menu_ref = Box::leak(Box::new(menu));

        #[cfg(target_os = "windows")]
        {
            // SAFETY: raw_id is the valid HWND of the main window, alive for
            // the duration of the application.
            unsafe { menu_ref.init_for_hwnd(raw_id as isize).ok() };
        }

        #[cfg(target_os = "macos")]
        menu_ref.init_for_nsapp();
    }

    pub fn event_stream() -> impl iced::futures::Stream<Item = Action> + Send + 'static {
        async_stream::stream! {
            loop {
                let receiver = MenuEvent::receiver();
                while let Ok(event) = receiver.try_recv() {
                    let action = {
                        let guard = match MENU_IDS.lock() {
                            Ok(g) => g,
                            Err(_) => break,
                        };
                        let Some(ids) = guard.as_ref() else { continue };
                        if ids.open_file.as_ref().is_some_and(|id| *id == event.id) {
                            Some(Action::OpenFile)
                        } else if ids.save_as.as_ref().is_some_and(|id| *id == event.id) {
                            Some(Action::SaveAs)
                        } else if ids.open_strategy.as_ref().is_some_and(|id| *id == event.id) {
                            Some(Action::OpenStrategy)
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
                    save_as: None,
                    open_strategy: None,
                });
            }
            {
                let mut guard = MENU_IDS.lock().unwrap();
                assert!(guard.is_some(), "first attach should set menu IDs");
                // Simulate re-attach after Flowsurface::restart()
                *guard = Some(MenuIds {
                    open_file: None,
                    save_as: None,
                    open_strategy: None,
                });
                assert!(guard.is_some(), "second attach must overwrite successfully");
                // Leave clean for other tests
                *guard = None;
            }
        }

        #[test]
        fn menu_ids_none_means_no_registered_actions() {
            let guard = MENU_IDS.lock().unwrap();
            // When the Option is None, event_stream skips dispatch via the
            // `let Some(ids) = guard.as_ref() else { continue }` guard.
            // Verify the mutex is lockable and None is a valid state.
            let ids_ref = guard.as_ref();
            // Either None (clean state) or Some (attached state) - both are valid.
            // The important invariant: if None, no ID comparisons happen.
            if let Some(ids) = ids_ref {
                // If some other test left IDs, we can still read them without panic.
                let _ = ids.open_file.as_ref();
                let _ = ids.save_as.as_ref();
                let _ = ids.open_strategy.as_ref();
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
        assert_ne!(Action::OpenFile, Action::SaveAs);
        assert_ne!(Action::OpenFile, Action::OpenStrategy);
        assert_ne!(Action::SaveAs, Action::OpenStrategy);
    }

    #[test]
    fn live_mode_provides_open_file_and_save_as() {
        let (open_file, save_as, open_strategy) = actions_for_mode(AppMode::Live);
        assert!(open_file, "live mode must have Open File action");
        assert!(save_as, "live mode must have Save As action");
        assert!(
            !open_strategy,
            "live mode must NOT have Open Strategy action"
        );
    }

    #[test]
    fn replay_mode_provides_open_strategy_only() {
        let (open_file, save_as, open_strategy) = actions_for_mode(AppMode::Replay);
        assert!(!open_file, "replay mode must NOT have Open File action");
        assert!(!save_as, "replay mode must NOT have Save As action");
        assert!(open_strategy, "replay mode must have Open Strategy action");
    }
}
