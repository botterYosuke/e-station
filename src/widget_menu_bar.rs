#![cfg(target_os = "linux")]
//! Linux-only iced widget menu bar.
//!
//! On Windows / macOS the OS-native muda menu bar is used (`native_menu.rs`).
//! On Linux, muda does not have GTK support, so this module provides an
//! equivalent in-window menu bar built from iced widgets.
//!
//! **Architecture** (P8-widget-menu-bar-linux.md):
//! - State transitions live in `menu_bar_state::{State, update}` (no cfg gate).
//! - This module owns `view()` (button row) and `with_dropdown_overlay()` (overlay).
//! - Actions dispatch via `Message::NativeMenuAction(native_menu::Action)` through
//!   `to_native_action()` — same handler path as Win/Mac.

use engine_client::dto::AppMode;
use iced::widget::{Space, button, container, mouse_area, opaque, row, stack, text};
use iced::{Element, Length, padding};

use crate::Message;
use crate::menu::{Action, MenuEntry, actions_for_mode, mode_menu_items, tools_actions_for_state};
pub use crate::menu_bar_state::{BarMessage, State, TopMenu};
use crate::wandb_auth::{RunBufferIndex, WandbAuthState};

/// Approximate pixel height from the top of the window to the base of the menu bar buttons.
/// Used to position the dropdown overlay below the buttons.
const DROPDOWN_TOP_OFFSET: f32 = 74.0;

/// Approximate left-edge offsets (px) for each top-level button.
const FILE_LEFT: f32 = 0.0;
const MODE_LEFT: f32 = 142.0;
const TOOLS_LEFT: f32 = 284.0;

/// Returns the menu button row (`File ▼` / `Mode ▼` / `Tools ▼`).
/// The caller must `.map(Message::MenuBar)` before pushing into the column.
pub fn view<'a>(state: &'a State, _mode: &'a AppMode) -> Element<'a, BarMessage> {
    let mk = |label: &str, top: TopMenu| {
        let active = state.open == Some(top);
        button(text(label))
            .on_press(BarMessage::Toggle(top))
            .style(if active {
                button::primary
            } else {
                button::secondary
            })
    };

    row![
        mk("ファイル（File）▼", TopMenu::File),
        mk("モード（Mode）▼", TopMenu::Mode),
        mk("ツール（Tools）▼", TopMenu::Tools),
    ]
    .spacing(2)
    .into()
}

/// Wraps the full window `base` in a dropdown overlay when a top-level menu is open.
///
/// When closed → returns `base` unchanged.
/// When open →  `stack![ base | dismiss_layer | dropdown ]` where:
///   - `dismiss_layer` is a full-size transparent `mouse_area` (Dismiss on click)
///   - `dropdown` is rendered above the dismiss layer (opaque to prevent dismiss)
///
/// `wandb_auth` / `run_buf` drive the Tools submenu enabled/disabled state.
pub fn with_dropdown_overlay<'a>(
    base: Element<'a, Message>,
    state: &'a State,
    mode: &'a AppMode,
    wandb_auth: &'a WandbAuthState,
    run_buf: &'a RunBufferIndex,
) -> Element<'a, Message> {
    let Some(open_top) = state.open else {
        return base;
    };

    let left_offset = match open_top {
        TopMenu::File => FILE_LEFT,
        TopMenu::Mode => MODE_LEFT,
        TopMenu::Tools => TOOLS_LEFT,
    };

    let items = build_dropdown(open_top, mode, wandb_auth, run_buf);

    let dropdown_col = iced::widget::Column::with_children(items);

    let dropdown = opaque(
        container(dropdown_col)
            .padding(4)
            .style(container::rounded_box),
    );

    // Dismiss + dropdown overlay layer.
    // `opaque(dropdown)` absorbs pointer events so only clicks on the
    // transparent surrounding area reach the `mouse_area` and fire Dismiss.
    let overlay = opaque(
        mouse_area(
            container(iced::widget::column![
                Space::new(Length::Fill, Length::Fixed(DROPDOWN_TOP_OFFSET)),
                iced::widget::row![
                    Space::new(Length::Fixed(left_offset), Length::Shrink),
                    dropdown,
                ],
            ])
            .width(Length::Fill)
            .height(Length::Fill),
        )
        .on_press(Message::MenuBar(BarMessage::Dismiss)),
    );

    stack![base, overlay].into()
}

/// Builds the dropdown item buttons for the given top-level menu.
fn build_dropdown<'a>(
    top: TopMenu,
    mode: &AppMode,
    wandb_auth: &WandbAuthState,
    run_buf: &RunBufferIndex,
) -> Vec<Element<'a, Message>> {
    let entries: Vec<(String, bool)> = match top {
        TopMenu::File => actions_for_mode(mode)
            .into_iter()
            .map(|a| (action_label(&a), true))
            .collect(),

        TopMenu::Mode => mode_menu_items(mode)
            .into_iter()
            .map(|e: MenuEntry| {
                let label = if e.checked == Some(true) {
                    format!("✓ {}", action_label(&e.action))
                } else {
                    format!("  {}", action_label(&e.action))
                };
                (label, e.enabled)
            })
            .collect(),

        TopMenu::Tools => tools_actions_for_state(wandb_auth, run_buf)
            .into_iter()
            .map(|e: MenuEntry| (action_label(&e.action), e.enabled))
            .collect(),
    };

    let actions: Vec<Action> = match top {
        TopMenu::File => actions_for_mode(mode),
        TopMenu::Mode => mode_menu_items(mode)
            .into_iter()
            .map(|e| e.action)
            .collect(),
        TopMenu::Tools => tools_actions_for_state(wandb_auth, run_buf)
            .into_iter()
            .map(|e| e.action)
            .collect(),
    };

    entries
        .into_iter()
        .zip(actions)
        .map(|((label, enabled), action)| {
            let msg = Message::MenuBar(BarMessage::Pick(action));
            let btn = button(text(label)).style(button::text);
            if enabled { btn.on_press(msg) } else { btn }.into()
        })
        .collect()
}

/// Human-readable label for a menu action.
fn action_label(action: &Action) -> String {
    match action {
        Action::Open => "ファイルを開く...（Open）\tCtrl+O".to_string(),
        Action::Save => "上書き保存（Save）\tCtrl+S".to_string(),
        Action::SaveAs => "名前を付けて保存...（Save As）\tCtrl+Shift+S".to_string(),
        Action::ReplayStart => "リプレイを開始...（Replay Start）".to_string(),
        Action::ReplayStop => "リプレイを停止（Replay Stop）".to_string(),
        Action::Quit => "終了（Quit）\tCtrl+Q".to_string(),
        Action::SwitchAppMode(AppMode::Live) => "ライブ（Live）".to_string(),
        Action::SwitchAppMode(AppMode::Replay) => "リプレイ（Replay）".to_string(),
        Action::SubmitToWandb => "W&B に送信（Submit）".to_string(),
        Action::SignInWandb => "W&B にログイン（Sign In）".to_string(),
        Action::SignOutWandb => "W&B からログアウト（Sign Out）".to_string(),
        Action::OpenSubmissionLog => "送信ログを開く（Submission Log）".to_string(),
        Action::ClearRunBuffer => "バッファをクリア（Clear Buffer）".to_string(),
    }
}

/// Maps `menu::Action` to the equivalent `native_menu::Action`, if one exists.
///
/// `ReplayStop` has no muda equivalent and returns `None`.
pub(crate) fn to_native_action(action: &Action) -> Option<crate::native_menu::Action> {
    use crate::native_menu::Action as N;
    match action {
        Action::Open => Some(N::OpenFile),
        Action::Save => Some(N::Save),
        Action::SaveAs => Some(N::SaveAs),
        Action::ReplayStart => Some(N::OpenReplayDialog),
        Action::Quit => Some(N::Quit),
        Action::SwitchAppMode(mode) => Some(N::SwitchMode(*mode)),
        Action::SubmitToWandb => Some(N::SubmitToWandb),
        Action::SignInWandb => Some(N::SignInWandb),
        Action::SignOutWandb => Some(N::SignOutWandb),
        Action::OpenSubmissionLog => Some(N::OpenSubmissionLog),
        Action::ClearRunBuffer => Some(N::ClearRunBuffer),
        Action::ReplayStop => None,
    }
}

/// Returns the ordered File menu actions for `mode`.
pub fn menu_items(mode: &AppMode) -> Vec<Action> {
    actions_for_mode(mode)
}

/// Returns the `モード（Mode）▼` submenu entries with exclusive check marks.
pub fn mode_items(current_mode: &AppMode) -> Vec<MenuEntry> {
    mode_menu_items(current_mode)
}
