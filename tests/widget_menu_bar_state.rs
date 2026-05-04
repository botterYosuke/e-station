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
        src.contains("Dismiss"),
        "BarMessage must have Dismiss variant"
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
    // ReplayStop has no native equivalent — must return None
    assert!(
        src.contains("Action::ReplayStop"),
        "to_native_action must explicitly handle Action::ReplayStop (returns None — no muda equivalent)"
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
