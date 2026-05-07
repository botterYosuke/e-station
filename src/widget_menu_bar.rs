//! Cross-platform iced widget menu bar (all OS).
//!
//! Replaces the former muda OS-native menu on Windows and macOS, matching the
//! existing Linux implementation. All platforms share a single code path.
//!
//! **Architecture** (widget-menu-bar-impl.md):
//! - State transitions live in `menu_bar_state::{State, update}` (no cfg gate).
//! - This module owns `view()` (button row) and `with_dropdown_overlay()` (overlay).
//! - Actions dispatch via `Message::Menu(MenuMsg::NativeAction(native_menu::Action))` through
//!   `to_native_action()` — single handler path on all platforms.

use engine_client::dto::AppMode;
use iced::widget::{
    Space, button, column, container, mouse_area, opaque, pick_list, row, stack, text, text_input,
    tooltip,
};
use iced::{Element, Length};

use crate::messages::MenuMsg;
use crate::Message;
use crate::menu::{Action, MenuEntry, ReplayControlState, actions_for_mode, replay_control_state};
pub use crate::menu_bar_state::{BarMessage, State, TopMenu};
use crate::menu_bar_state::{LiveBarState, ReplayBarState};
use crate::modal::replay_form::Granularity;

/// Fixed width for each top-level menu button.  The dropdown's horizontal
/// position is derived from this value, so there are no magic pixel offsets.
const BTN_WIDTH: f32 = 155.0;

/// Single row height in logical pixels.
const ROW_HEIGHT: f32 = 32.0;

/// Returns the menu bar height for the given mode.
///
/// - Live with no running strategy: single row (32 px)
/// - Live with strategy running: two rows — menu row + live control row (64 px)
/// - Replay: two rows — menu row + input/control row (64 px)
pub fn bar_height(mode: AppMode, live_strategy_running: bool) -> f32 {
    match mode {
        AppMode::Replay => ROW_HEIGHT * 2.0,
        AppMode::Live if live_strategy_running => ROW_HEIGHT * 2.0,
        AppMode::Live => ROW_HEIGHT,
    }
}

/// Returns the menu bar view — single row in Live mode, two rows in Replay mode.
///
/// The caller must `.map(|m| Message::Menu(MenuMsg::Bar(m)))` before pushing into the column.
pub fn view<'a>(
    state: &'a State,
    mode: AppMode,
    replay_running: bool,
    replay_paused: bool,
    mode_switch_in_progress: bool,
    live_strategy_running: bool,
) -> Element<'a, BarMessage> {
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

    let empty_strip = mouse_area(Space::new().width(Length::Fill).height(Length::Fill))
        .on_press(BarMessage::Dismiss);

    let top_row = row![mk("ファイル（File）▼", TopMenu::File), empty_strip,].spacing(2);

    let top_row_container = container(top_row)
        .height(Length::Fixed(ROW_HEIGHT))
        .width(Length::Fill);

    if mode == AppMode::Replay {
        let ctrl = replay_control_state(
            replay_running,
            replay_paused,
            state.replay_bar.replay_has_history,
            mode_switch_in_progress,
        );
        let col = column![
            mouse_area(top_row_container).on_press(BarMessage::Dismiss),
            replay_input_row(&state.replay_bar, ctrl),
        ];
        container(col)
            .height(Length::Fixed(bar_height(AppMode::Replay, false)))
            .width(Length::Fill)
            .into()
    } else if live_strategy_running {
        let col = column![
            mouse_area(top_row_container).on_press(BarMessage::Dismiss),
            live_control_row(&state.live_bar),
        ];
        container(col)
            .height(Length::Fixed(bar_height(AppMode::Live, true)))
            .width(Length::Fill)
            .into()
    } else {
        mouse_area(top_row_container).into()
    }
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
) -> Element<'a, Message> {
    let Some(open_top) = state.open else {
        return base;
    };

    // Horizontal offset derived from fixed button widths — adapts if BTN_WIDTH changes.
    let left_offset = match open_top {
        TopMenu::File => 0.0,
    };

    // Vertical offset: bar's bottom edge = bar_height(mode). F8 R2 / H3':
    // constant anchor derived from mode — see the rationale on `view()`'s
    // removed `.on_move` handler. In Replay mode the bar is 64 px tall.
    // Vertical offset: bar's bottom edge = bar_height(mode). F8 R2 / H3':
    // constant anchor derived from mode — see the rationale on `view()`'s
    // removed `.on_move` handler. In Replay mode the bar is 64 px tall.
    // For dropdown positioning, Live mode anchors at the single-row height (no strategy running).
    let top_offset = bar_height(mode, false);

    let entries = entries_for_menu(open_top, &mode);
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
                Space::new()
                    .width(Length::Fixed(left_offset))
                    .height(Length::Shrink),
                dropdown_panel,
            ])
            .width(Length::Fill)
            .height(Length::Fill),
        )
        .on_press(Message::Menu(MenuMsg::Bar(BarMessage::Dismiss))),
    );

    // The leading Space is NOT wrapped in opaque/mouse_area, so pointer events
    // in the button-row band fall through to layer 0 (base) buttons.
    let overlay = column![
        Space::new()
            .width(Length::Fill)
            .height(Length::Fixed(top_offset)),
        dismiss_area,
    ];

    stack![base, overlay].into()
}

/// Second row of the replay control bar: input fields + control buttons.
///
/// Displayed only in Replay mode. Button enable/disable is controlled by
/// `ReplayControlState` computed from the current playback state.
fn replay_input_row<'a>(
    bar: &'a ReplayBarState,
    ctrl: ReplayControlState,
) -> Element<'a, BarMessage> {
    let play_btn = {
        let b = button(text("▶")).style(button::primary);
        if ctrl.play {
            b.on_press(BarMessage::PressPlay)
        } else {
            b
        }
    };
    let pause_btn = {
        let b = button(text("⏸")).style(button::secondary);
        if ctrl.pause {
            b.on_press(BarMessage::PressPause)
        } else {
            b
        }
    };
    let step_fwd_btn = {
        let b = button(text("⏭")).style(button::secondary);
        if ctrl.step_forward {
            b.on_press(BarMessage::PressStepForward)
        } else {
            b
        }
    };
    let step_bwd_btn = {
        let b = button(text("⏮")).style(button::secondary);
        if ctrl.step_backward {
            b.on_press(BarMessage::PressStepBackward)
        } else {
            b
        }
    };
    let stop_btn = {
        let b = button(text("⏹")).style(button::danger);
        if ctrl.stop {
            b.on_press(BarMessage::PressStop)
        } else {
            b
        }
    };

    let strat_label = bar
        .strategy_file
        .as_ref()
        .and_then(|p| p.file_name())
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_else(|| "戦略未選択".to_string());
    let strat_btn = button(text(strat_label)).on_press(BarMessage::PickStrategyFile);

    let day_display = text(bar.current_day.as_deref().unwrap_or("--").to_string());

    row![
        strat_btn,
        day_display,
        text_input("銘柄 (例: 7203)", &bar.instrument_id)
            .on_input(BarMessage::InstrumentChanged)
            .width(Length::Fixed(120.0)),
        text_input("開始 YYYY-MM-DD", &bar.start_date)
            .on_input(BarMessage::StartDateChanged)
            .width(Length::Fixed(110.0)),
        text_input("終了 YYYY-MM-DD", &bar.end_date)
            .on_input(BarMessage::EndDateChanged)
            .width(Length::Fixed(110.0)),
        pick_list(
            Granularity::ALL,
            bar.granularity.as_ref(),
            BarMessage::GranularityChanged
        )
        .width(Length::Fixed(90.0)),
        text_input("初期資金", &bar.initial_cash)
            .on_input(BarMessage::InitialCashChanged)
            .width(Length::Fixed(100.0)),
        Space::new().width(Length::Fill).height(Length::Shrink),
        step_bwd_btn,
        step_fwd_btn,
        pause_btn,
        play_btn,
        stop_btn,
    ]
    .spacing(4)
    .height(Length::Fixed(ROW_HEIGHT))
    .into()
}

/// Second row of the live strategy control bar: status + play/pause/stop buttons.
///
/// Displayed only in Live mode while a strategy is running.
fn live_control_row<'a>(bar: &'a LiveBarState) -> Element<'a, BarMessage> {
    let file_label = bar
        .strategy_file_stem
        .as_deref()
        .unwrap_or("--")
        .to_string();

    let time_display = text(
        bar.current_time
            .as_deref()
            .unwrap_or("--:--:--")
            .to_string(),
    );

    let play_btn = {
        let b = button(text("▶")).style(button::primary);
        if bar.live_paused {
            b.on_press(BarMessage::LivePressPlay)
        } else {
            b
        }
    };

    let pause_btn = {
        let b = button(text("⏸")).style(button::secondary);
        if !bar.live_paused {
            b.on_press(BarMessage::LivePressPause)
        } else {
            b
        }
    };

    let stop_btn = button(text("■"))
        .on_press(BarMessage::LivePressStop)
        .style(button::danger);

    row![
        text(file_label),
        time_display,
        Space::new().width(Length::Fill).height(Length::Shrink),
        pause_btn,
        play_btn,
        stop_btn,
    ]
    .spacing(4)
    .height(Length::Fixed(ROW_HEIGHT))
    .into()
}

/// Normalises all menu types into a `Vec<MenuEntry>` with full label/enabled/tooltip/checked.
fn entries_for_menu(top: TopMenu, mode: &AppMode) -> Vec<MenuEntry> {
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

            let msg = Message::Menu(MenuMsg::Bar(BarMessage::Pick(action)));
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
        Action::Quit => ("終了（Quit）", Some(q)),
        Action::SwitchAppMode(AppMode::Live) => ("ライブ（Live）", None),
        Action::SwitchAppMode(AppMode::Replay) => ("リプレイ（Replay）", None),
    }
}

/// Maps `menu::Action` to the equivalent `native_menu::Action`, if one exists.
pub(crate) fn to_native_action(action: &Action) -> Option<crate::native_menu::Action> {
    use crate::native_menu::Action as N;
    match action {
        Action::Open => Some(N::OpenFile),
        Action::Save => Some(N::Save),
        Action::SaveAs => Some(N::SaveAs),
        Action::Quit => Some(N::Quit),
        Action::SwitchAppMode(mode) => Some(N::SwitchMode(*mode)),
    }
}

// F8 R2 / M-A: the `menu_items` / `mode_items` wrappers were removed because
// they were pure delegation shims over `menu::actions_for_mode` with zero
// external callers after H2 (F8 R1). The Mode menu itself was subsequently
// removed and replaced by the footer toggle (mode-toggle-redesign).

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bar_height_live_no_strategy() {
        assert_eq!(bar_height(AppMode::Live, false), ROW_HEIGHT);
    }

    #[test]
    fn bar_height_live_strategy_running() {
        assert_eq!(bar_height(AppMode::Live, true), ROW_HEIGHT * 2.0);
    }

    #[test]
    fn bar_height_replay() {
        assert_eq!(bar_height(AppMode::Replay, false), ROW_HEIGHT * 2.0);
    }
}
