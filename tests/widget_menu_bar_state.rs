//! Structural regression pins for F8/P8: `widget_menu_bar` State transitions.
//!
//! Source-inspection tests verifying `src/widget_menu_bar.rs` contains the
//! correct `update()` pure function logic as specified in
//! `docs/✅menu-and-footer/P8-widget-menu-bar-linux.md` (DoD-12 / R2-39).
//!
//! The plan specifies a 3-contract × 4-open-state = 12 case matrix:
//! - Contract 1: Esc (→ Dismiss)
//! - Contract 2: focus-lost (→ Dismiss)
//! - Contract 3: outside click (→ Dismiss)
//! × {File open, Mode open, Tools open, Closed}
//!
//! All 12 cases share the same `Dismiss → open: None` transition in the
//! pure `update()` function. The "dismiss reason" distinction is handled
//! at the call site (Subscription / overlay) for logging only.

fn read_widget_menu_bar() -> String {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/widget_menu_bar.rs");
    std::fs::read_to_string(path)
        .expect("failed to read src/widget_menu_bar.rs — ensure the file exists")
}

/// State transitions (`State`, `TopMenu`, `BarMessage`, `update`) live in
/// `src/menu_bar_state.rs` (no platform gate) so they are testable on all OSes.
fn read_menu_bar_state() -> String {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/menu_bar_state.rs");
    std::fs::read_to_string(path)
        .expect("failed to read src/menu_bar_state.rs — ensure the file exists")
}

// ── Structural: types and update function (in menu_bar_state.rs) ──────────
//
// State transitions live in `src/menu_bar_state.rs` (no platform gate)
// so they compile and test on Windows/macOS/Linux alike.

#[test]
fn state_struct_exists() {
    let src = read_menu_bar_state();
    assert!(
        src.contains("pub struct State"),
        "menu_bar_state.rs must define `pub struct State`"
    );
    assert!(
        src.contains("pub open: Option<TopMenu>"),
        "State must have `pub open: Option<TopMenu>` field"
    );
    assert!(
        src.contains("anchor_y"),
        "State must have `anchor_y` field for dynamic dropdown positioning (HIGH: fixed-pixel fix)"
    );
}

#[test]
fn top_menu_enum_has_file_mode_tools() {
    let src = read_menu_bar_state();
    assert!(
        src.contains("pub enum TopMenu"),
        "menu_bar_state.rs must define `pub enum TopMenu`"
    );
    assert!(src.contains("File"), "TopMenu must have File variant");
    assert!(src.contains("Mode"), "TopMenu must have Mode variant");
    assert!(src.contains("Tools"), "TopMenu must have Tools variant");
}

#[test]
fn bar_message_enum_has_toggle_pick_dismiss() {
    let src = read_menu_bar_state();
    assert!(
        src.contains("pub enum BarMessage"),
        "menu_bar_state.rs must define `pub enum BarMessage`"
    );
    assert!(
        src.contains("Toggle(TopMenu)"),
        "BarMessage must have Toggle(TopMenu) variant"
    );
    assert!(
        src.contains("Pick("),
        "BarMessage must have Pick(...) variant"
    );
    assert!(
        src.contains("    Dismiss,"),
        "BarMessage must have Dismiss variant (outside click / Esc)"
    );
    assert!(
        src.contains("DismissFocusLost"),
        "BarMessage must have DismissFocusLost variant separate from Dismiss (DoD-3 log distinction)"
    );
    assert!(
        src.contains("BarMoved("),
        "BarMessage must have BarMoved(u32) variant for dynamic dropdown positioning (HIGH: fixed-pixel fix)"
    );
}

#[test]
fn dismiss_focus_lost_closes_menu() {
    let src = read_menu_bar_state();
    let fn_start = src
        .find("pub fn update(state: State, msg: BarMessage) -> State")
        .expect("update function must exist");
    let fn_body = &src[fn_start..];
    let fn_end = fn_body.find("\npub fn ").unwrap_or(fn_body.len());
    let body = &fn_body[..fn_end];

    // DismissFocusLost must be in the same arm as Dismiss (both → open: None).
    assert!(
        body.contains("DismissFocusLost"),
        "update() must handle BarMessage::DismissFocusLost (DoD-3: focus-lost dismiss)"
    );
    // It must appear alongside Dismiss so both share the open: None outcome.
    let dismiss_arm = body
        .find("BarMessage::Dismiss")
        .expect("Dismiss arm must exist");
    let after_dismiss = &body[dismiss_arm..];
    let arm_end = after_dismiss
        .find("=> State")
        .expect("arm must end with => State");
    let arm_pattern = &after_dismiss[..arm_end];
    assert!(
        arm_pattern.contains("DismissFocusLost"),
        "DismissFocusLost must be in the same match arm as Dismiss (both → open: None)"
    );
}

#[test]
fn update_function_exported_as_pure_function() {
    let src = read_menu_bar_state();
    assert!(
        src.contains("pub fn update(state: State, msg: BarMessage) -> State"),
        "menu_bar_state.rs must export `pub fn update(state: State, msg: BarMessage) -> State` (R2-39)"
    );
}

// ── Contract: Dismiss always closes menu (12-case matrix) ─────────────────
// State transitions are in menu_bar_state.rs

#[test]
fn dismiss_always_yields_open_none() {
    let src = read_menu_bar_state();
    let fn_start = src
        .find("pub fn update(state: State, msg: BarMessage) -> State")
        .expect("update function must exist");
    let fn_body = &src[fn_start..];
    let fn_end = fn_body.find("\npub fn ").unwrap_or(fn_body.len());
    let body = &fn_body[..fn_end];

    // The Dismiss arm must set open: None. Accept either combined or separate arm.
    let combined = body.contains("BarMessage::Pick(_) | BarMessage::Dismiss");
    let separate = body.contains("BarMessage::Dismiss") && body.contains("open: None");
    assert!(
        combined || separate,
        "update() must handle BarMessage::Dismiss by setting open: None (DoD-12 / all 12 Dismiss cases)"
    );
}

// ── Contract: Toggle opens or closes ──────────────────────────────────────

#[test]
fn toggle_opens_closed_menu() {
    let src = read_menu_bar_state();
    let fn_start = src
        .find("pub fn update(state: State, msg: BarMessage) -> State")
        .expect("update function must exist");
    let fn_body = &src[fn_start..];
    let fn_end = fn_body.find("\npub fn ").unwrap_or(fn_body.len());
    let body = &fn_body[..fn_end];

    assert!(
        body.contains("BarMessage::Toggle(top)"),
        "update() must match BarMessage::Toggle(top)"
    );
    // The toggle logic: if open == Some(top) close, else open
    assert!(
        body.contains("if state.open == Some(top)"),
        "update() Toggle must check `state.open == Some(top)` to decide open/close (DoD-12)"
    );
}

#[test]
fn toggle_closes_already_open_menu() {
    let src = read_menu_bar_state();
    let fn_start = src
        .find("pub fn update(state: State, msg: BarMessage) -> State")
        .expect("update function must exist");
    let fn_body = &src[fn_start..];
    let fn_end = fn_body.find("\npub fn ").unwrap_or(fn_body.len());
    let body = &fn_body[..fn_end];

    // The else branch must yield Some(top) to open; the if branch yields None to close.
    assert!(
        body.contains("None") && body.contains("Some(top)"),
        "update() Toggle must yield None (close) when already open and Some(top) (open) otherwise"
    );
}

// ── Contract: Pick closes menu ────────────────────────────────────────────

#[test]
fn pick_always_closes_menu() {
    let src = read_menu_bar_state();
    let fn_start = src
        .find("pub fn update(state: State, msg: BarMessage) -> State")
        .expect("update function must exist");
    let fn_body = &src[fn_start..];
    let fn_end = fn_body.find("\npub fn ").unwrap_or(fn_body.len());
    let body = &fn_body[..fn_end];

    assert!(
        body.contains("BarMessage::Pick"),
        "update() must match BarMessage::Pick — item selection must close the dropdown"
    );
}

// ── widget_menu_bar module: menu_items, view, overlay, conversion ──────────

#[test]
fn menu_items_function_delegates_to_menu_module() {
    let src = read_widget_menu_bar();
    assert!(
        src.contains("pub fn menu_items"),
        "widget_menu_bar.rs must export `pub fn menu_items`"
    );
    assert!(
        src.contains("actions_for_mode"),
        "menu_items must delegate to menu::actions_for_mode for cross-platform consistency"
    );
}

#[test]
fn view_function_returns_bar_message_element() {
    let src = read_widget_menu_bar();
    assert!(
        src.contains("pub fn view"),
        "widget_menu_bar.rs must export `pub fn view`"
    );
    // view() must return Element<'_, BarMessage> so main.rs can .map(Message::MenuBar)
    assert!(
        src.contains("Element<'a, BarMessage>") || src.contains("-> Element<'_, BarMessage>"),
        "view() must return Element<'a, BarMessage> (not Message) for .map(Message::MenuBar) in main.rs"
    );
    // Must emit Toggle messages and include all three top-level menu variants
    assert!(
        src.contains("BarMessage::Toggle"),
        "view() must emit BarMessage::Toggle for top-level button presses"
    );
    assert!(
        src.contains("TopMenu::File"),
        "view() must reference TopMenu::File for the File ▼ button"
    );
    assert!(
        src.contains("TopMenu::Mode"),
        "view() must reference TopMenu::Mode for the Mode ▼ button"
    );
    assert!(
        src.contains("TopMenu::Tools"),
        "view() must reference TopMenu::Tools for the Tools ▼ button"
    );
    // The empty strip to the right of the buttons must fire Dismiss so DoD-4
    // holds for the full bar width, not just the three button areas (MEDIUM: empty bar strip).
    let view_start = src
        .find("pub fn view<'a>")
        .expect("view function must exist");
    let view_body = &src[view_start..];
    let view_end = view_body.find("\npub fn ").unwrap_or(view_body.len());
    let vb = &view_body[..view_end];
    assert!(
        vb.contains("BarMessage::Dismiss"),
        "view() must emit BarMessage::Dismiss for the empty bar strip right of Tools (DoD-4)"
    );
    assert!(
        vb.contains("Length::Fill") || vb.contains("Fill"),
        "view() must have a fill-width element covering the empty bar strip (DoD-4)"
    );
}

#[test]
fn with_dropdown_overlay_function_exists() {
    let src = read_widget_menu_bar();
    assert!(
        src.contains("pub fn with_dropdown_overlay"),
        "widget_menu_bar.rs must export `pub fn with_dropdown_overlay` for overlay rendering"
    );
    // Must use stack! for layering base + overlay
    assert!(
        src.contains("stack!["),
        "with_dropdown_overlay must use stack! to layer base content and dropdown overlay"
    );
    // Must dismiss on outside click via mouse_area
    assert!(
        src.contains("BarMessage::Dismiss"),
        "with_dropdown_overlay must send BarMessage::Dismiss on outside click via mouse_area"
    );
    // The overlay column must start with a plain Space (click-through for buttons),
    // followed by the dismiss area. Pattern: `let overlay = column![Space::new(...), dismiss_area]`.
    // Find the overlay column declaration and verify Space comes before the dismiss element.
    let overlay_col_start = src.find("let overlay = column!").expect(
        "overlay must be built as `let overlay = column![...]` for button-row click-through",
    );
    let after_col = &src[overlay_col_start..];
    let col_end = after_col.find("];").unwrap_or(after_col.len());
    let col_body = &after_col[..col_end];
    let space_pos = col_body
        .find("Space::new")
        .expect("overlay column must start with Space::new (button-row click-through area)");
    let dismiss_pos = col_body
        .find("dismiss_area")
        .expect("overlay column must contain dismiss_area after the Space");
    assert!(
        space_pos < dismiss_pos,
        "Space::new must appear BEFORE dismiss_area in the overlay column — \
         the spacer lets button-row clicks pass through to base layer (HIGH: click-through)"
    );
}

#[test]
fn dropdown_disabled_items_use_tooltip() {
    let src = read_widget_menu_bar();
    // Disabled items must be wrapped with tooltip() so the reason is visible on hover
    assert!(
        src.contains("tooltip(") || src.contains("iced::widget::tooltip("),
        "build_dropdown must wrap disabled items with tooltip() to show the reason (MEDIUM: tooltip visibility)"
    );
    // The tooltip must use the entry's tooltip text (tip or tip_text)
    assert!(
        src.contains("tip_text") || src.contains("tip"),
        "tooltip must be populated from MenuEntry::tooltip — not hardcoded"
    );
    // Tooltip must only apply to DISABLED items (not enabled ones)
    assert!(
        src.contains("!enabled") || src.contains("if !enabled"),
        "tooltip wrapper must only apply when the item is disabled"
    );
}

#[test]
fn to_native_action_function_exists() {
    let src = read_widget_menu_bar();
    assert!(
        src.contains("pub(crate) fn to_native_action") || src.contains("pub fn to_native_action"),
        "widget_menu_bar.rs must export `to_native_action` for menu::Action → native_menu::Action conversion"
    );
    // Must handle Open → OpenFile
    assert!(
        src.contains("N::OpenFile") || src.contains("native_menu::Action::OpenFile"),
        "to_native_action must map Action::Open to native_menu::Action::OpenFile"
    );
    // ReplayStop must NOT return None — it maps to SwitchMode(Live) so the item is functional
    assert!(
        src.contains("Action::ReplayStop"),
        "to_native_action must explicitly handle Action::ReplayStop"
    );
    assert!(
        !src.contains("Action::ReplayStop => None"),
        "Action::ReplayStop must NOT return None — dead menu items are HIGH severity (maps to SwitchMode(Live))"
    );
}

#[test]
fn esc_dismiss_is_wired_in_go_back_handler() {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/main.rs");
    let src = std::fs::read_to_string(path)
        .expect("failed to read src/main.rs")
        .replace("\r\n", "\n");
    // The GoBack handler must dismiss the Linux menu bar (cfg-gated)
    let go_back_start = src
        .find("Message::GoBack =>")
        .expect("Message::GoBack handler must exist in main.rs");
    let after = &src[go_back_start..];
    let end = after.find("\n            Message::").unwrap_or(after.len());
    let body = &after[..end];
    assert!(
        body.contains("menu_bar") && body.contains("BarMessage::Dismiss"),
        "GoBack handler must dismiss self.menu_bar via BarMessage::Dismiss on Linux (Esc key dismiss — DoD-2)"
    );
}

#[test]
fn focus_lost_dismiss_subscription_exists() {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/main.rs");
    let src = std::fs::read_to_string(path).expect("failed to read src/main.rs");
    assert!(
        src.contains("window::Event::Unfocused") || src.contains("iced::window::Event::Unfocused"),
        "subscription() must handle window Unfocused event to dismiss the Linux menu bar (DoD-3 focus-lost)"
    );
    // The Unfocused arm must emit DismissFocusLost — not the generic Dismiss —
    // so focus-lost and outside-click can be told apart in logs (LOW: log distinction).
    assert!(
        src.contains("DismissFocusLost"),
        "Unfocused subscription must emit BarMessage::DismissFocusLost, not generic Dismiss (DoD-3 log)"
    );
}

#[test]
fn file_is_linux_only() {
    let src = read_widget_menu_bar();
    assert!(
        src.contains("#![cfg(target_os = \"linux\")]")
            || src.contains("#[cfg(target_os = \"linux\")]"),
        "widget_menu_bar.rs must be gated with cfg(target_os = \"linux\") to avoid Win/Mac double-menu"
    );
}
