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
use iced::widget::{
    Space, button, column, container, mouse_area, opaque, row, stack, text, tooltip,
};
use iced::{Element, Length, Point};

pub use crate::menu_bar_state::{BarMessage, State, TopMenu};
use crate::Message;
use crate::menu::{Action, MenuEntry, actions_for_mode, mode_menu_items, tools_actions_for_state};
use crate::wandb_auth::{RunBufferIndex, WandbAuthState};

/// Fixed width for each top-level menu button.  The dropdown's horizontal
/// position is derived from this value, so there are no magic pixel offsets.
const BTN_WIDTH: f32 = 155.0;

/// Fallback vertical offset (px) used before the user first hovers over the
/// bar.  Once the cursor visits the bar, `State::anchor_y` replaces this.
const ANCHOR_Y_FALLBACK: f32 = 50.0;

/// Returns the menu button row (`File ▼` / `Mode ▼` / `Tools ▼`).
///
/// Each button has an explicit fixed width so the horizontal dropdown positions
/// can be computed exactly from `BTN_WIDTH + spacing`.  A `mouse_area` wrapper
/// tracks the cursor's absolute window-Y on each move and emits `BarMoved(y)`,
/// letting `with_dropdown_overlay` anchor the dropdown without fixed offsets.
///
/// The caller must `.map(Message::MenuBar)` before pushing into the column.
pub fn view<'a>(state: &'a State, _mode: &'a AppMode) -> Element<'a, BarMessage> {
    let mk = |label: &str, top: TopMenu| {
        let active = state.open == Some(top);
        button(text(label))
            .on_press(BarMessage::Toggle(top))
            .width(Length::Fixed(BTN_WIDTH))
            .style(if active {
                button::primary
            } else {
                button::secondary
            })
    };

    let bar_row = row![
        mk("ファイル（File）▼", TopMenu::File),
        mk("モード（Mode）▼", TopMenu::Mode),
        mk("ツール（Tools）▼", TopMenu::Tools),
    ]
    .spacing(2);

    mouse_area(bar_row)
        .on_move(|pt: Point| BarMessage::BarMoved(pt.y as u32))
        .into()
}

/// Wraps the full window `base` in a dropdown overlay when a top-level menu is open.
///
/// Layer structure in `stack!`:
/// - Layer 0 (`base`): full window content including the menu bar button row.
/// - Layer 1 (overlay): a `column![Space(anchor_y), opaque(mouse_area(dismiss_area))]`.
///   The leading `Space` height equals the cursor's last known absolute window-Y,
///   so the dropdown appears just below the bar regardless of HiDPI scale or font size.
///   The `Space` is NOT opaque, so pointer events in the button-row band
///   pass through to layer 0's buttons (allowing toggle / switch / close via the same button).
///   Below the spacer, `opaque(mouse_area(...))` catches outside clicks and fires `Dismiss`.
///   The dropdown panel itself is wrapped in a nested `opaque()` so clicks on items do NOT
///   trigger `Dismiss`.
///
/// Horizontal position is derived from `BTN_WIDTH + spacing` — no magic pixel offsets.
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

    // Horizontal offset derived from fixed button widths — adapts if BTN_WIDTH changes.
    let step = BTN_WIDTH + 2.0; // 2.0 = row spacing
    let left_offset = match open_top {
        TopMenu::File  => 0.0,
        TopMenu::Mode  => step,
        TopMenu::Tools => 2.0 * step,
    };

    // Vertical offset: cursor's absolute Y when last over the bar.  Adapts to
    // HiDPI scaling and header-height changes without hard-coded magic numbers.
    let top_offset = state.anchor_y.map(|y| y as f32).unwrap_or(ANCHOR_Y_FALLBACK);

    let entries = entries_for_menu(open_top, mode, wandb_auth, run_buf);
    let items = build_dropdown(entries);
    let dropdown_panel = opaque(
        container(column(items))
            .padding(4)
            .style(container::rounded_box),
    );

    // Area below the button row: opaque dismiss layer with dropdown panel.
    // `opaque(dropdown_panel)` absorbs clicks so they don't bubble to mouse_area.
    let dismiss_area = opaque(
        mouse_area(
            container(
                row![
                    Space::new(Length::Fixed(left_offset), Length::Shrink),
                    dropdown_panel,
                ],
            )
            .width(Length::Fill)
            .height(Length::Fill),
        )
        .on_press(Message::MenuBar(BarMessage::Dismiss)),
    );

    // The leading Space is NOT wrapped in opaque/mouse_area, so pointer events
    // in the button-row band fall through to layer 0 (base) buttons.
    let overlay = column![
        Space::new(Length::Fill, Length::Fixed(top_offset)),
        dismiss_area,
    ];

    stack![base, overlay].into()
}

/// Normalises all menu types into a `Vec<MenuEntry>` with full label/enabled/tooltip/checked.
fn entries_for_menu(
    top: TopMenu,
    mode: &AppMode,
    wandb_auth: &WandbAuthState,
    run_buf: &RunBufferIndex,
) -> Vec<MenuEntry> {
    match top {
        TopMenu::File => actions_for_mode(mode)
            .into_iter()
            .map(|action| MenuEntry {
                action,
                enabled: true,
                tooltip: None,
                checked: None,
            })
            .collect(),
        TopMenu::Mode => mode_menu_items(mode),
        TopMenu::Tools => tools_actions_for_state(wandb_auth, run_buf),
    }
}

/// Builds the dropdown item elements from normalised `MenuEntry` values.
///
/// - Enabled items: `button.on_press(Pick(action))`
/// - Disabled items with tooltip: wrapped in `tooltip(..., Position::Right)`
/// - `checked = Some(true)` adds a `✓` prefix; `Some(false)` adds alignment padding.
/// - Keyboard shortcuts are right-aligned via `row![label, Space::Fill, shortcut]`.
fn build_dropdown<'a>(entries: Vec<MenuEntry>) -> Vec<Element<'a, Message>> {
    entries
        .into_iter()
        .map(|entry| {
            let MenuEntry {
                action,
                enabled,
                tooltip: tip,
                checked,
            } = entry;

            let (base_label, shortcut) = action_label_and_shortcut(&action);
            let label = match checked {
                Some(true) => format!("✓ {base_label}"),
                Some(false) => format!("  {base_label}"),
                None => base_label,
            };

            let content: Element<'a, Message> = match shortcut {
                Some(sc) => row![
                    text(label),
                    Space::new(Length::Fill, Length::Shrink),
                    text(sc),
                ]
                .into(),
                None => text(label).into(),
            };

            let msg = Message::MenuBar(BarMessage::Pick(action));
            let btn = button(content).width(Length::Fill).style(button::text);
            let btn_el: Element<'a, Message> = if enabled {
                btn.on_press(msg).into()
            } else {
                btn.into()
            };

            match tip {
                Some(tip_text) if !enabled => tooltip(
                    btn_el,
                    container(text(tip_text)).padding(4),
                    tooltip::Position::Right,
                )
                .into(),
                _ => btn_el,
            }
        })
        .collect()
}

/// Returns the human-readable label and optional keyboard shortcut for a menu action.
/// The shortcut is rendered separately (right-aligned) rather than embedded in the label.
fn action_label_and_shortcut(action: &Action) -> (String, Option<&'static str>) {
    match action {
        Action::Open => ("ファイルを開く...（Open）".to_string(), Some("Ctrl+O")),
        Action::Save => ("上書き保存（Save）".to_string(), Some("Ctrl+S")),
        Action::SaveAs => (
            "名前を付けて保存...（Save As）".to_string(),
            Some("Ctrl+Shift+S"),
        ),
        Action::ReplayStart => ("リプレイを開始...（Replay Start）".to_string(), None),
        Action::ReplayStop => ("リプレイを停止（Replay Stop）".to_string(), None),
        Action::Quit => ("終了（Quit）".to_string(), Some("Ctrl+Q")),
        Action::SwitchAppMode(AppMode::Live) => ("ライブ（Live）".to_string(), None),
        Action::SwitchAppMode(AppMode::Replay) => ("リプレイ（Replay）".to_string(), None),
        Action::SubmitToWandb => ("W&B に送信（Submit）".to_string(), None),
        Action::SignInWandb => ("W&B にログイン（Sign In）".to_string(), None),
        Action::SignOutWandb => ("W&B からログアウト（Sign Out）".to_string(), None),
        Action::OpenSubmissionLog => ("送信ログを開く（Submission Log）".to_string(), None),
        Action::ClearRunBuffer => ("バッファをクリア（Clear Buffer）".to_string(), None),
    }
}

/// Maps `menu::Action` to the equivalent `native_menu::Action`, if one exists.
///
/// `ReplayStop` maps to `SwitchMode(Live)` — stopping replay on Linux goes through
/// the same mode-switch flow as on Win/Mac (F7 guard + StopReplay command).
pub(crate) fn to_native_action(action: &Action) -> Option<crate::native_menu::Action> {
    use crate::native_menu::Action as N;
    match action {
        Action::Open => Some(N::OpenFile),
        Action::Save => Some(N::Save),
        Action::SaveAs => Some(N::SaveAs),
        Action::ReplayStart => Some(N::OpenReplayDialog),
        Action::ReplayStop => Some(N::SwitchMode(AppMode::Live)),
        Action::Quit => Some(N::Quit),
        Action::SwitchAppMode(mode) => Some(N::SwitchMode(*mode)),
        Action::SubmitToWandb => Some(N::SubmitToWandb),
        Action::SignInWandb => Some(N::SignInWandb),
        Action::SignOutWandb => Some(N::SignOutWandb),
        Action::OpenSubmissionLog => Some(N::OpenSubmissionLog),
        Action::ClearRunBuffer => Some(N::ClearRunBuffer),
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
