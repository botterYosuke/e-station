#![cfg(target_os = "linux")]
//! Linux-only iced widget menu bar.
//!
//! On Windows / macOS the OS-native muda menu bar is used (`native_menu.rs`).
//! On Linux, muda does not have GTK support, so this module provides an
//! equivalent in-window menu bar built from iced widgets.
//!
//! **Architecture** (P8-widget-menu-bar-linux.md):
//! - State transitions are handled by the platform-independent
//!   `menu_bar_state::{State, update}` pure function.
//! - This module owns the `view()` rendering function only.
//! - Actions are dispatched via `Message::NativeMenuAction(native_menu::Action)`
//!   using `native_to_menu_action()` conversion (same handler path as Win/Mac).

use engine_client::dto::AppMode;
use iced::Element;

pub use crate::menu_bar_state::{BarMessage, State, TopMenu};

use crate::menu::{Action, MenuEntry, actions_for_mode, mode_menu_items};
use crate::Message;

/// Returns the ordered File menu actions for `mode`.
/// Delegates to `menu::actions_for_mode` for cross-platform consistency.
pub fn menu_items(mode: &AppMode) -> Vec<Action> {
    actions_for_mode(mode)
}

/// Returns the `モード（Mode）▼` submenu entries with exclusive check marks.
/// Delegates to `menu::mode_menu_items`.
pub fn mode_items(current_mode: &AppMode) -> Vec<MenuEntry> {
    mode_menu_items(current_mode)
}

/// Render the Linux widget menu bar.
///
/// # TODO (DoD-1 through DoD-4)
///
/// This stub returns an empty element to allow the crate to compile on
/// Linux.  The full implementation requires:
/// - A top row of `File ▼` / `モード（Mode）▼` / `ツール（Tools）▼` buttons
/// - An overlay dropdown that appears on button press
/// - `mouse_area` capture for outside-click dismiss
/// - `iced::event::Event::Window(WindowEvent::Unfocused)` subscription for
///   focus-lost dismiss
/// - Action dispatch via `Message::NativeMenuAction(native_menu::Action)`
///   after converting `menu::Action` to `native_menu::Action`
pub fn view<'a>(_state: &'a State, _mode: &'a AppMode) -> Element<'a, Message> {
    // Placeholder — replace with real dropdown rendering (DoD-1 through DoD-4).
    iced::widget::text("").into()
}
