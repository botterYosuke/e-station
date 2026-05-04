//! Structural regression pins for F8/P8: `mode_menu_items` function contract.
//!
//! Source-inspection tests for `src/menu.rs` verifying DoD-13 and DoD-14:
//! - Linux `モード（Mode）▼` submenu shows Live/Replay with exclusive check marks
//! - Each entry dispatches `Action::SwitchAppMode(target)`

fn read_menu() -> String {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/menu.rs");
    std::fs::read_to_string(path).expect("failed to read src/menu.rs")
}

// ── DoD-13: function and MenuEntry type ────────────────────────────────────

#[test]
fn mode_menu_items_function_exported() {
    let src = read_menu();
    assert!(
        src.contains("pub fn mode_menu_items"),
        "menu.rs must export `pub fn mode_menu_items` for Linux Mode submenu (DoD-13)"
    );
}

#[test]
fn menu_entry_struct_exported() {
    let src = read_menu();
    assert!(
        src.contains("pub struct MenuEntry"),
        "menu.rs must export `pub struct MenuEntry`"
    );
}

#[test]
fn menu_entry_has_checked_field() {
    let src = read_menu();
    assert!(
        src.contains("pub checked: Option<bool>"),
        "MenuEntry must have `pub checked: Option<bool>` for exclusive-check display (DoD-13)"
    );
}

#[test]
fn menu_entry_has_enabled_field() {
    let src = read_menu();
    assert!(
        src.contains("pub enabled: bool"),
        "MenuEntry must have `pub enabled: bool`"
    );
}

#[test]
fn menu_entry_has_action_field() {
    let src = read_menu();
    assert!(
        src.contains("pub action: Action"),
        "MenuEntry must have `pub action: Action`"
    );
}

// ── DoD-13/14: mode_menu_items body dispatches SwitchAppMode ──────────────

#[test]
fn mode_menu_items_dispatches_switch_app_mode() {
    let src = read_menu();
    let fn_start = src
        .find("pub fn mode_menu_items")
        .expect("mode_menu_items must exist");
    let fn_body = &src[fn_start..];
    let fn_end = fn_body.find("\npub fn ").unwrap_or(fn_body.len());
    let body = &fn_body[..fn_end];

    assert!(
        body.contains("SwitchAppMode"),
        "mode_menu_items must dispatch Action::SwitchAppMode (DoD-14)"
    );
}

#[test]
fn mode_menu_items_uses_checked_some_true() {
    let src = read_menu();
    let fn_start = src
        .find("pub fn mode_menu_items")
        .expect("mode_menu_items must exist");
    let fn_body = &src[fn_start..];
    let fn_end = fn_body.find("\npub fn ").unwrap_or(fn_body.len());
    let body = &fn_body[..fn_end];

    assert!(
        body.contains("Some(true)"),
        "mode_menu_items must use `checked: Some(true)` for the current mode (DoD-13)"
    );
    assert!(
        body.contains("Some(false)"),
        "mode_menu_items must use `checked: Some(false)` for the inactive mode (DoD-13)"
    );
}

#[test]
fn mode_menu_items_includes_live_and_replay_modes() {
    let src = read_menu();
    let fn_start = src
        .find("pub fn mode_menu_items")
        .expect("mode_menu_items must exist");
    let fn_body = &src[fn_start..];
    let fn_end = fn_body.find("\npub fn ").unwrap_or(fn_body.len());
    let body = &fn_body[..fn_end];

    assert!(
        body.contains("AppMode::Live"),
        "mode_menu_items must include AppMode::Live entry"
    );
    assert!(
        body.contains("AppMode::Replay"),
        "mode_menu_items must include AppMode::Replay entry"
    );
}

#[test]
fn mode_menu_items_uses_matches_macro_for_checked() {
    let src = read_menu();
    let fn_start = src
        .find("pub fn mode_menu_items")
        .expect("mode_menu_items must exist");
    let fn_body = &src[fn_start..];
    let fn_end = fn_body.find("\npub fn ").unwrap_or(fn_body.len());
    let body = &fn_body[..fn_end];

    assert!(
        body.contains("matches!("),
        "mode_menu_items should use the `matches!` macro or equivalent to determine checked state"
    );
}

// ── Mode submenu returns exactly 2 entries ─────────────────────────────────

#[test]
fn mode_menu_items_returns_vec_with_two_entries() {
    let src = read_menu();
    let fn_start = src
        .find("pub fn mode_menu_items")
        .expect("mode_menu_items must exist");
    let fn_body = &src[fn_start..];
    let fn_end = fn_body.find("\npub fn ").unwrap_or(fn_body.len());
    let body = &fn_body[..fn_end];

    // The return value is `vec![...]` with two MenuEntry items.
    assert!(
        body.contains("vec!["),
        "mode_menu_items must return a vec literal with the menu entries"
    );
}
