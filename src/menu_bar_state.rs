use crate::menu::Action;

/// Which top-level menu is currently open (or None = all closed).
// reason: variants are constructed only by the Linux `widget_menu_bar`. The
// enum lives in `mod menu_bar_state` (no platform gate) so that source-
// inspection tests can compile on every OS. (H1 / M6 / F8 R1)
#[allow(dead_code)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TopMenu {
    File,
    Tools,
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
        },
        BarMessage::Pick(_) | BarMessage::Dismiss | BarMessage::DismissFocusLost => {
            State { open: None }
        }
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

    #[test]
    fn dismiss_focus_lost_closes_when_file_open() {
        let s = State {
            open: Some(TopMenu::File),
        };
        let next = update(s, BarMessage::DismissFocusLost);
        assert_eq!(next.open, None);
    }

    #[test]
    fn dismiss_focus_lost_closes_when_tools_open() {
        let s = State {
            open: Some(TopMenu::Tools),
        };
        let next = update(s, BarMessage::DismissFocusLost);
        assert_eq!(next.open, None);
    }

    #[test]
    fn dismiss_focus_lost_is_idempotent_when_already_closed() {
        let s = State { open: None };
        let next = update(s, BarMessage::DismissFocusLost);
        assert_eq!(next.open, None);
    }

    // Sanity: ensure Pick / Dismiss / DismissFocusLost share the same outcome,
    // matching the source-inspection guard in tests/widget_menu_bar_state.rs.
    #[test]
    fn pick_dismiss_focus_lost_all_close_menu() {
        let base = State {
            open: Some(TopMenu::Tools),
        };
        for msg in [
            BarMessage::Pick(Action::SwitchAppMode(AppMode::Live)),
            BarMessage::Dismiss,
            BarMessage::DismissFocusLost,
        ] {
            assert_eq!(update(base.clone(), msg).open, None);
        }
    }
}
