//! TT5/TT6: Footer mode-toggle regression pins.
//!
//! TT5: verifies that `status_bar` body dispatches through `BarMessage::Pick`
//!      (→ `to_native_action` → `NativeMenuAction` → `Action::SwitchMode` handler)
//!      and that the click message wraps `SwitchAppMode`.
//! TT6: verifies that the `Action::SwitchMode` handler in `main.rs` contains
//!      the dirty-check flow (`SaveAndSwitchMode` / `DiscardAndSwitchMode`)
//!      within its own body, so the footer dispatch path cannot bypass it.

fn read_main() -> String {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/main.rs");
    std::fs::read_to_string(path).expect("failed to read src/main.rs")
}

fn read_menu() -> String {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/menu.rs");
    std::fs::read_to_string(path).expect("failed to read src/menu.rs")
}

/// Bound a function body by scanning for the next `\nfn ` at the same
/// indentation level (works for top-level and 4-space-indented fns).
fn fn_body(src: &str, fn_start: usize) -> &str {
    let after = &src[fn_start..];
    let end = after[1..]
        .find("\nfn ")
        .map(|i| i + 1)
        .unwrap_or(after.len());
    &after[..end]
}

// ── TT5: status_bar body dispatches via BarMessage::Pick(SwitchAppMode) ───────

#[test]
fn tt5_status_bar_body_contains_bar_message_pick() {
    let src = read_main();
    let start = src
        .find("fn status_bar(state: crate::menu::ModeToggleState)")
        .expect("status_bar must accept ModeToggleState");
    let body = fn_body(&src, start);

    assert!(
        body.contains("BarMessage::Pick"),
        "status_bar body must dispatch via BarMessage::Pick — not NativeMenuAction directly — \
         so the Action::SwitchMode handler's guard chain is preserved (TT5)"
    );
    assert!(
        body.contains("SwitchAppMode"),
        "status_bar body must wrap Action::SwitchAppMode(target) in the Pick message (TT5)"
    );
    // Ensure the body does NOT use NativeMenuAction directly for the badge press,
    // which would skip the BarMessage::Pick → to_native_action routing chain.
    let on_press_region = body
        .find("on_press(")
        .and_then(|i| body[i..].find(')').map(|j| &body[i..i + j + 1]))
        .unwrap_or("");
    assert!(
        !on_press_region.contains("NativeMenuAction"),
        "status_bar on_press must NOT call NativeMenuAction directly — must use BarMessage::Pick \
         so to_native_action routing chain is preserved (TT5)"
    );
}

#[test]
fn tt5_mode_toggle_state_exported_from_menu() {
    let src = read_menu();
    assert!(
        src.contains("pub fn mode_toggle_state("),
        "menu.rs must export `pub fn mode_toggle_state` for footer toggle (TT5)"
    );
}

#[test]
fn tt5_mode_toggle_state_does_not_accept_dirty_param() {
    let src = read_menu();
    let fn_start = src
        .find("pub fn mode_toggle_state(")
        .expect("mode_toggle_state must exist");
    let fn_body = &src[fn_start..];
    let params_end = fn_body
        .find(") -> ModeToggleState")
        .unwrap_or(fn_body.len());
    let params = &fn_body[..params_end];

    assert!(
        !params.contains("dirty"),
        "mode_toggle_state must NOT accept a `dirty` param — \
         dirty routes through confirm dialog, not disable (TT5)"
    );
}

// ── TT6: SwitchMode handler body contains SaveAndSwitchMode (dirty check) ─────

#[test]
fn tt6_switch_mode_handler_body_contains_dirty_check_flow() {
    let src = read_main();
    let handler_start = src
        .find("Action::SwitchMode(target) =>")
        .expect("Action::SwitchMode handler must exist");
    // Bound the handler to ~6000 bytes — enough to cover the full match arm.
    let handler_body = &src[handler_start..];
    let end = handler_body.len().min(6000);
    let body = &handler_body[..end];

    assert!(
        body.contains("SaveAndSwitchMode"),
        "Action::SwitchMode handler must reference SaveAndSwitchMode — \
         dirty check confirm dialog is in this path (TT6)"
    );
    assert!(
        body.contains("DiscardAndSwitchMode"),
        "Action::SwitchMode handler must reference DiscardAndSwitchMode — \
         discard confirm is in this path (TT6)"
    );
}

#[test]
fn tt6_mode_menu_items_no_longer_exists() {
    let src = read_menu();
    assert!(
        !src.contains("pub fn mode_menu_items"),
        "menu.rs must NOT export `pub fn mode_menu_items` — Mode menu removed (TT6)"
    );
}
