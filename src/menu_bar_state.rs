use crate::menu::Action;

/// Which top-level menu is currently open (or None = all closed).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TopMenu {
    File,
    Mode,
    Tools,
}

/// Messages emitted by the widget menu bar's button/overlay layer.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BarMessage {
    /// Toggle a top-level menu open/closed.
    Toggle(TopMenu),
    /// User clicked a menu item — dispatch the action and close the dropdown.
    Pick(Action),
    /// Close all menus (Esc key / focus-lost / outside click).
    Dismiss,
}

/// Lightweight state for the widget menu bar: only tracks which top-level
/// menu is open.  All transitions go through the pure `update()` function.
#[derive(Default, Debug, Clone, PartialEq, Eq)]
pub struct State {
    pub open: Option<TopMenu>,
}

/// Pure state-transition function (R2-39). No GUI dependencies — fully
/// testable on all platforms.
///
/// Invariants:
/// 1. `Dismiss` → `open: None` (Esc / focus-lost / outside-click)
/// 2. `Pick(_)` → `open: None` (item selected, close dropdown)
/// 3. `Toggle(top)` when `open == Some(top)` → `open: None` (close same)
/// 4. `Toggle(top)` when `open != Some(top)` → `open: Some(top)` (open)
pub fn update(state: State, msg: BarMessage) -> State {
    match msg {
        BarMessage::Toggle(top) => State {
            open: if state.open == Some(top) {
                None
            } else {
                Some(top)
            },
        },
        BarMessage::Pick(_) | BarMessage::Dismiss => State { open: None },
    }
}
