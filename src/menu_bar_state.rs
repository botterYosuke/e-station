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
    /// Close all menus: Esc key or outside click (bar empty space / below bar area).
    Dismiss,
    /// Close all menus: window lost focus (`Window::Unfocused`).
    /// Kept separate from `Dismiss` so call sites can log the distinct reason.
    DismissFocusLost,
    /// Cursor moved over the button row at absolute window-Y `y` (pixels).
    /// Used to anchor the dropdown position dynamically instead of fixed offsets.
    BarMoved(u32),
}

/// Lightweight state for the widget menu bar: which top-level menu is open,
/// and where the bar sits vertically so the dropdown can be positioned
/// without hard-coded pixel offsets.
#[derive(Default, Debug, Clone, PartialEq, Eq)]
pub struct State {
    pub open: Option<TopMenu>,
    /// Last known absolute window-Y of the cursor while over the button row.
    /// `None` before the user first hovers over the bar.
    pub anchor_y: Option<u32>,
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
/// 6. `BarMoved(y)` → update `anchor_y` only (open unchanged)
pub fn update(state: State, msg: BarMessage) -> State {
    match msg {
        BarMessage::Toggle(top) => State {
            open: if state.open == Some(top) {
                None
            } else {
                Some(top)
            },
            anchor_y: state.anchor_y,
        },
        BarMessage::Pick(_) | BarMessage::Dismiss | BarMessage::DismissFocusLost => State {
            open: None,
            anchor_y: state.anchor_y,
        },
        BarMessage::BarMoved(y) => State {
            open: state.open,
            anchor_y: Some(y),
        },
    }
}
