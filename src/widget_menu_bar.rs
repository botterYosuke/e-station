//! Cross-platform iced widget menu bar (all OS).
//!
//! Replaces the former muda OS-native menu on Windows and macOS, matching the
//! existing Linux implementation. All platforms share a single code path.
//!
//! **Architecture** (widget-menu-bar-impl.md):
//! - State transitions live in `menu_bar_state::{State, update}` (no cfg gate).
//! - This module owns `view()` (button row) and `with_dropdown_overlay()` (overlay).
//! - Actions dispatch via `Message::NativeMenuAction(native_menu::Action)` through
//!   `to_native_action()` — single handler path on all platforms.

use engine_client::dto::AppMode;
use iced::widget::{
    Space, button, column, container, mouse_area, opaque, row, stack, text, tooltip,
};
use iced::{Element, Length};

use crate::Message;
use crate::menu::{Action, MenuEntry, actions_for_mode, mode_menu_items, tools_actions_for_state};
pub use crate::menu_bar_state::{BarMessage, State, TopMenu};
use crate::wandb_auth::{RunBufferIndex, WandbAuthState};

/// Fixed width for each top-level menu button.  The dropdown's horizontal
/// position is derived from this value, so there are no magic pixel offsets.
const BTN_WIDTH: f32 = 155.0;

/// Height of the button row in logical pixels.  Setting this explicitly on the
/// bar container makes the value authoritative: `with_dropdown_overlay` anchors
/// the dropdown at exactly `BAR_HEIGHT` regardless of where inside the bar the
/// cursor last rested.
const BAR_HEIGHT: f32 = 32.0;

/// Returns the menu button row (`File ▼` / `Mode ▼` / `Tools ▼`).
///
/// Each button has an explicit fixed width so the horizontal dropdown positions
/// can be computed exactly from `BTN_WIDTH + spacing`.  The bar itself is
/// wrapped in a `container` with an explicit `BAR_HEIGHT` so the overlay anchor
/// is always the bar's bottom edge, not the cursor's position within the bar.
///
/// A `mouse_area` fills the space to the right of the three buttons and fires
/// `BarMessage::Dismiss` on press, satisfying DoD-4 for the full bar width.
///
/// The caller must `.map(Message::MenuBar)` before pushing into the column.
pub fn view<'a>(state: &'a State, _mode: AppMode) -> Element<'a, BarMessage> {
    let mk = |label: &str, top: TopMenu| {
        let active = state.open == Some(top);
        button(text(label.to_owned()))
            .on_press(BarMessage::Toggle(top))
            .width(Length::Fixed(BTN_WIDTH))
            .style(if active {
                button::primary
            } else {
                button::secondary
            })
    };

    // Rightmost fill-space: clicking the empty bar strip (right of Tools) fires
    // Dismiss so DoD-4 holds across the full bar width.
    let empty_strip = mouse_area(Space::new().width(Length::Fill).height(Length::Fill))
        .on_press(BarMessage::Dismiss);

    let bar_row = row![
        mk("ファイル（File）▼", TopMenu::File),
        mk("モード（Mode）▼", TopMenu::Mode),
        mk("ツール（Tools）▼", TopMenu::Tools),
        empty_strip,
    ]
    .spacing(2);

    // F8 R2 / H3': no `.on_move` handler. The previous `BarMoved(y)` design
    // (added in F8 R1 to anchor the dropdown dynamically) was a category error:
    // `mouse_area::on_move` calls back with `cursor.position_in(layout.bounds())`
    // — i.e. **widget-local** coordinates in the range `0..BAR_HEIGHT` — but
    // `with_dropdown_overlay`'s `top_offset` is interpreted as a **window-Y**
    // offset. Feeding widget-local Y into a window-Y slot drove the dropdown
    // up to the window top whenever the cursor sat near y=0 of the bar, which
    // is precisely the common case. The mechanism is abolished; the dropdown
    // is anchored at the constant `BAR_HEIGHT` in `with_dropdown_overlay`.
    mouse_area(
        container(bar_row)
            .height(Length::Fixed(BAR_HEIGHT))
            .width(Length::Fill),
    )
    .into()
}

/// Wraps the full window `base` in a dropdown overlay when a top-level menu is open.
///
/// Layer structure in `stack!`:
/// - Layer 0 (`base`): full window content including the menu bar button row.
/// - Layer 1 (overlay): a `column![Space(BAR_HEIGHT), opaque(mouse_area(dismiss_area))]`.
///   The leading `Space` height equals `BAR_HEIGHT` (the bar's bottom edge in window-Y),
///   so the dropdown always starts immediately below the bar regardless of cursor position.
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
    mode: AppMode,
    wandb_auth: &'a WandbAuthState,
    run_buf: &'a RunBufferIndex,
    replay_running: bool,
) -> Element<'a, Message> {
    let Some(open_top) = state.open else {
        return base;
    };

    // Horizontal offset derived from fixed button widths — adapts if BTN_WIDTH changes.
    let step = BTN_WIDTH + 2.0; // 2.0 = row spacing
    let left_offset = match open_top {
        TopMenu::File => 0.0,
        TopMenu::Mode => step,
        TopMenu::Tools => 2.0 * step,
    };

    // Vertical offset: bar's bottom edge = BAR_HEIGHT (bar is always at y=0
    // in iced's window-content coordinate space). F8 R2 / H3': constant
    // anchor — see the rationale on `view()`'s removed `.on_move` handler.
    let top_offset = BAR_HEIGHT;

    let entries = entries_for_menu(open_top, &mode, wandb_auth, run_buf, replay_running);
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
            container(row![
                Space::new().width(Length::Fixed(left_offset)).height(Length::Shrink),
                dropdown_panel,
            ])
            .width(Length::Fill)
            .height(Length::Fill),
        )
        .on_press(Message::MenuBar(BarMessage::Dismiss)),
    );

    // The leading Space is NOT wrapped in opaque/mouse_area, so pointer events
    // in the button-row band fall through to layer 0 (base) buttons.
    let overlay = column![
        Space::new().width(Length::Fill).height(Length::Fixed(top_offset)),
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
    replay_running: bool,
) -> Vec<MenuEntry> {
    match top {
        TopMenu::File => actions_for_mode(mode)
            .into_iter()
            .map(|action| {
                // "リプレイ停止" is only clickable while a replay is actually
                // running; everything else in the File menu is unconditional.
                let (enabled, tooltip) = match action {
                    Action::ReplayStop if !replay_running => {
                        (false, Some("リプレイは実行されていません"))
                    }
                    _ => (true, None),
                };
                MenuEntry {
                    action,
                    enabled,
                    tooltip,
                    checked: None,
                }
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
            // M7 (F8 R1): no per-entry allocation. The check prefix is one of
            // three fixed `&'static str`s rendered as its own `text(...)` so the
            // base label remains a borrowed static. This keeps `build_dropdown`
            // allocation-free aside from the `Vec` that already aggregates the
            // resulting elements.
            let prefix: &'static str = match checked {
                Some(true) => "✓ ",
                Some(false) => "  ",
                None => "",
            };

            let content: Element<'a, Message> = match shortcut {
                Some(sc) => row![
                    text(prefix),
                    text(base_label),
                    Space::new().width(Length::Fill).height(Length::Shrink),
                    text(sc),
                ]
                .into(),
                None => row![text(prefix), text(base_label)].into(),
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
///
/// M7 (F8 R1): both label and shortcut are `&'static str` — the build_dropdown
/// hot path runs every time a dropdown opens, so allocating a fresh `String`
/// per entry just to copy the same constant text was wasted work. The check
/// prefix (`✓ ` / `  ` / `""`) is the only piece that varies, and we render it
/// with a separate `text(...)` widget below instead of formatting it into the
/// label.
fn action_label_and_shortcut(action: &Action) -> (&'static str, Option<&'static str>) {
    // On macOS the primary modifier is Cmd; on Win/Linux it is Ctrl.
    #[cfg(target_os = "macos")]
    let (o, s, ss, q) = ("Cmd+O", "Cmd+S", "Cmd+Shift+S", "Cmd+Q");
    #[cfg(not(target_os = "macos"))]
    let (o, s, ss, q) = ("Ctrl+O", "Ctrl+S", "Ctrl+Shift+S", "Ctrl+Q");

    match action {
        Action::Open => ("ファイルを開く...（Open）", Some(o)),
        Action::Save => ("上書き保存（Save）", Some(s)),
        Action::SaveAs => ("名前を付けて保存...（Save As）", Some(ss)),
        Action::ReplayStart => ("リプレイを開始...（Replay Start）", None),
        Action::ReplayStop => ("リプレイを停止（Replay Stop）", None),
        Action::Quit => ("終了（Quit）", Some(q)),
        Action::SwitchAppMode(AppMode::Live) => ("ライブ（Live）", None),
        Action::SwitchAppMode(AppMode::Replay) => ("リプレイ（Replay）", None),
        Action::SubmitToWandb => ("W&B に送信（Submit）", None),
        Action::SignInWandb => ("W&B にログイン（Sign In）", None),
        Action::SignOutWandb => ("W&B からログアウト（Sign Out）", None),
        Action::OpenSubmissionLog => ("送信ログを開く（Submission Log）", None),
        Action::ClearRunBuffer => ("バッファをクリア（Clear Buffer）", None),
    }
}

/// Maps `menu::Action` to the equivalent `native_menu::Action`, if one exists.
///
/// `ReplayStop` maps to `StopReplay` — stops the running replay without
/// switching app mode. The dashboard stays in Replay mode and the engine
/// transitions to IDLE (a new replay can then be started via `ReplayStart`).
pub(crate) fn to_native_action(action: &Action) -> Option<crate::native_menu::Action> {
    use crate::native_menu::Action as N;
    match action {
        Action::Open => Some(N::OpenFile),
        Action::Save => Some(N::Save),
        Action::SaveAs => Some(N::SaveAs),
        Action::ReplayStart => Some(N::OpenReplayDialog),
        Action::ReplayStop => Some(N::StopReplay),
        Action::Quit => Some(N::Quit),
        Action::SwitchAppMode(mode) => Some(N::SwitchMode(*mode)),
        Action::SubmitToWandb => Some(N::SubmitToWandb),
        Action::SignInWandb => Some(N::SignInWandb),
        Action::SignOutWandb => Some(N::SignOutWandb),
        Action::OpenSubmissionLog => Some(N::OpenSubmissionLog),
        Action::ClearRunBuffer => Some(N::ClearRunBuffer),
    }
}

// F8 R2 / M-A: the `menu_items` / `mode_items` wrappers were removed because
// they were pure delegation shims over `menu::actions_for_mode` /
// `menu::mode_menu_items` with zero external callers after H2 (F8 R1) added
// `pub use` for the underlying functions. Callers should use those module-
// level functions directly (already imported at the top of this file).
