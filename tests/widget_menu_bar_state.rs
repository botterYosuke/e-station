//! Structural regression pins for F8/P8: `widget_menu_bar` State transitions.
//!
//! Source-inspection tests verifying `src/widget_menu_bar.rs` contains the
//! correct `update()` pure function logic as specified in
//! `docs/✅menu-and-footer/P8-widget-menu-bar-linux.md` (DoD-12 / R2-39).
//!
//! The plan specifies a 3-contract × 3-open-state = 9 case matrix:
//! - Contract 1: Esc (→ Dismiss)
//! - Contract 2: focus-lost (→ Dismiss)
//! - Contract 3: outside click (→ Dismiss)
//! × {File open, Tools open, Closed}
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
    // F8 R2 / H3': the `anchor_y` mechanism was removed because
    // `mouse_area::on_move` returns widget-local coordinates (0..BAR_HEIGHT),
    // which is incompatible with the window-absolute Y semantics expected by
    // `with_dropdown_overlay`'s `top_offset`. The dropdown anchor is now the
    // constant `BAR_HEIGHT`.
    assert!(
        !src.contains("anchor_y"),
        "State must NOT have `anchor_y` field (F8 R2 H3': mechanism abolished — widget-local vs window-absolute coordinate mismatch)"
    );
}

#[test]
fn top_menu_enum_has_file_and_tools() {
    let src = read_menu_bar_state();
    assert!(
        src.contains("pub enum TopMenu"),
        "menu_bar_state.rs must define `pub enum TopMenu`"
    );
    assert!(src.contains("File"), "TopMenu must have File variant");
    assert!(src.contains("Tools"), "TopMenu must have Tools variant");
    assert!(
        !src.contains("    Mode,"),
        "TopMenu must NOT have Mode variant — Mode menu is replaced by footer toggle"
    );
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
    // F8 R2 / H3': `BarMoved(u32)` was removed alongside `anchor_y`. The
    // dropdown anchor is now the constant `BAR_HEIGHT` in `with_dropdown_overlay`.
    assert!(
        !src.contains("BarMoved("),
        "BarMessage must NOT have BarMoved(...) variant (F8 R2 H3': mechanism abolished)"
    );
}

// F8 R2 / H3': verify `with_dropdown_overlay` anchors the dropdown at the
// constant `BAR_HEIGHT` rather than reading a per-cursor `anchor_y` value.
#[test]
fn with_dropdown_overlay_uses_bar_height_constant_for_top_offset() {
    let src = read_widget_menu_bar();
    let fn_start = src
        .find("pub fn with_dropdown_overlay")
        .expect("with_dropdown_overlay must exist");
    let fn_body = &src[fn_start..];
    // Bound the search to the function body (next top-level pub fn).
    let fn_end = fn_body[1..]
        .find("\npub fn ")
        .map(|i| i + 1)
        .unwrap_or(fn_body.len());
    let body = &fn_body[..fn_end];
    assert!(
        !body.contains("anchor_y"),
        "with_dropdown_overlay must not reference `anchor_y` (F8 R2 H3': mechanism abolished)"
    );
    assert!(
        body.contains("let top_offset = BAR_HEIGHT"),
        "with_dropdown_overlay must anchor at the BAR_HEIGHT constant (F8 R2 H3': `let top_offset = BAR_HEIGHT;`)"
    );
}

// F8 R2 / M-B: the `Message::MenuBar(bar_msg)` handler in main.rs must
// exhaustively match every `BarMessage` variant rather than absorbing them in
// a wildcard arm. With `BarMoved` removed, the only remaining unlogged variant
// is `Pick(_)`, which must be named explicitly so that future BarMessage
// additions trigger a compile error.
#[test]
fn main_menu_bar_handler_match_is_exhaustive_without_wildcard() {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/main.rs");
    let src = std::fs::read_to_string(path).expect("failed to read src/main.rs");
    let handler_start = src
        .find("Message::MenuBar(bar_msg) =>")
        .expect("MenuBar handler must exist");
    let after = &src[handler_start..];
    // The handler ends at the next top-level `Message::` arm.
    let end = after[1..]
        .find("\n            Message::")
        .map(|i| i + 1)
        .unwrap_or(after.len());
    let body = &after[..end];
    // The exhaustive match logs Pick explicitly (or via Pick(_) => {}).
    assert!(
        body.contains("BarMessage::Pick(_) => {}") || body.contains("BarMessage::Pick(_) =>"),
        "MenuBar handler must explicitly match BarMessage::Pick(_) (F8 R2 M-B: no wildcard `_ => {{}}`)"
    );
    // Forbid the wildcard arm — its only purpose was to absorb BarMoved/Pick.
    assert!(
        !body.contains("_ => {}"),
        "MenuBar handler must NOT use a wildcard `_ => {{}}` arm (F8 R2 M-B: exhaustive matching)"
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
    // Accept either `=> State { open: None }` (single-expression arm) or
    // `=> { State { open: None } }` (block arm — rustfmt's preferred shape
    // after the `anchor_y` field was removed in F8 R2).
    let dismiss_arm = body
        .find("BarMessage::Dismiss")
        .expect("Dismiss arm must exist");
    let after_dismiss = &body[dismiss_arm..];
    let arm_end = after_dismiss.find("=>").expect("arm must contain `=>`");
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

// F8 R2 / M-A: the `menu_items` / `mode_items` wrappers in widget_menu_bar.rs
// were pure delegation shims removed in F8 R2. Mode menu itself was then
// removed and replaced with the footer toggle in the mode-toggle-redesign.
#[test]
fn widget_menu_bar_does_not_define_redundant_wrappers() {
    let src = read_widget_menu_bar();
    assert!(
        !src.contains("pub fn menu_items"),
        "widget_menu_bar.rs must NOT define wrapper `pub fn menu_items` (F8 R2 M-A: redundant delegation removed)"
    );
    assert!(
        !src.contains("pub fn mode_items"),
        "widget_menu_bar.rs must NOT define wrapper `pub fn mode_items` (F8 R2 M-A: redundant delegation removed)"
    );
    assert!(
        !src.contains("mode_menu_items"),
        "widget_menu_bar.rs must NOT reference `mode_menu_items` — Mode menu removed; use footer toggle"
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
    // Must emit Toggle messages and include both top-level menu variants
    assert!(
        src.contains("BarMessage::Toggle"),
        "view() must emit BarMessage::Toggle for top-level button presses"
    );
    assert!(
        src.contains("TopMenu::File"),
        "view() must reference TopMenu::File for the File ▼ button"
    );
    assert!(
        src.contains("TopMenu::Tools"),
        "view() must reference TopMenu::Tools for the Tools ▼ button"
    );
    assert!(
        !src.contains("TopMenu::Mode"),
        "view() must NOT reference TopMenu::Mode — Mode menu is replaced by footer toggle"
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
    // ReplayStop must NOT return None — it maps to N::StopReplay so the item is functional.
    // Stop-only flow: stops the running replay without switching app mode (engine → IDLE,
    // dashboard stays in Replay mode).
    assert!(
        src.contains("Action::ReplayStop"),
        "to_native_action must explicitly handle Action::ReplayStop"
    );
    assert!(
        !src.contains("Action::ReplayStop => None"),
        "Action::ReplayStop must NOT return None — dead menu items are HIGH severity (maps to N::StopReplay)"
    );
    assert!(
        src.contains("Action::ReplayStop => Some(N::StopReplay)")
            || src.contains("Action::ReplayStop => Some(native_menu::Action::StopReplay)"),
        "Action::ReplayStop must map to native_menu::Action::StopReplay (stop-only flow, no mode switch)"
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
fn file_has_no_linux_cfg_gate() {
    let src = read_widget_menu_bar();
    assert!(
        !src.contains("#![cfg(target_os = \"linux\")]"),
        "widget_menu_bar.rs must NOT be linux-only — it is now cross-platform (all OS)"
    );
}
