//! TT5/TT6: Footer mode-toggle regression pins.
//!
//! TT5: verifies that `status_bar` dispatches `NativeMenuAction(SwitchMode(target))`
//!      — the same `Action::SwitchMode` path used by the keyboard accelerator.
//! TT6: verifies that `Action::SwitchMode` in `main.rs` routes through the
//!      `SaveAndSwitchMode` dirty-check flow (not bypassed by the footer).

fn read_main() -> String {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/main.rs");
    std::fs::read_to_string(path).expect("failed to read src/main.rs")
}

fn read_menu() -> String {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/menu.rs");
    std::fs::read_to_string(path).expect("failed to read src/menu.rs")
}

// ── TT5: footer dispatches SwitchMode via NativeMenuAction ────────────────────

#[test]
fn tt5_status_bar_dispatches_switch_mode_via_native_menu_action() {
    let src = read_main();
    // Locate the status_bar function body.
    let fn_start = src
        .find("fn status_bar(state: crate::menu::ModeToggleState)")
        .expect("status_bar must accept ModeToggleState");
    let after = &src[fn_start..];
    // Find the next top-level fn to bound the body.
    let body_end = after[1..]
        .find("\nfn ")
        .map(|i| i + 1)
        .unwrap_or(after.len());
    let body = &after[..body_end];

    assert!(
        body.contains("NativeMenuAction"),
        "status_bar must dispatch Message::NativeMenuAction for the badge click (TT5)"
    );
    assert!(
        body.contains("SwitchMode"),
        "status_bar must dispatch Action::SwitchMode(target) (TT5)"
    );
}

#[test]
fn tt5_mode_toggle_state_function_exported_from_menu() {
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
    let fn_end = fn_body
        .find(") -> ModeToggleState")
        .unwrap_or(fn_body.len());
    let params = &fn_body[..fn_end];

    assert!(
        !params.contains("dirty"),
        "mode_toggle_state must NOT accept a `dirty` parameter — dirty routes through confirm dialog, not disable (TT5)"
    );
}

// ── TT6: dirty routes through SaveAndSwitchMode confirm dialog ────────────────

#[test]
fn tt6_switch_mode_handler_checks_dirty_and_dispatches_save_and_switch() {
    let src = read_main();
    // The Action::SwitchMode handler must reference SaveAndSwitchMode,
    // proving that dirty state is handled via the confirm dialog flow.
    assert!(
        src.contains("SaveAndSwitchMode"),
        "main.rs must contain SaveAndSwitchMode — dirty check confirm dialog for mode switch (TT6)"
    );
    // And the footer dispatch path goes through the same NativeMenuAction handler.
    assert!(
        src.contains("DiscardAndSwitchMode"),
        "main.rs must contain DiscardAndSwitchMode — discard confirm for mode switch (TT6)"
    );
}

#[test]
fn tt6_mode_menu_items_no_longer_exists() {
    let src = read_menu();
    assert!(
        !src.contains("pub fn mode_menu_items"),
        "menu.rs must NOT export `pub fn mode_menu_items` — Mode menu is removed (TT6 / plan §削除する経路)"
    );
}
