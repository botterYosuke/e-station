//! Structural regression pins for accelerator bindings.
//!
//! These tests verify that the source code contains the expected accelerator
//! definitions without requiring a live GUI runtime. They pin the invariants
//! for the iced widget menu bar keyboard subscription, which now runs on all
//! platforms (Windows, macOS, Linux). muda has been removed.
//!
//! Key invariants checked:
//! - Ctrl/Cmd+O → Action::OpenFile (all platforms via iced keyboard)
//! - Ctrl/Cmd+S → Action::Save
//! - Ctrl/Cmd+Shift+S → Action::SaveAs
//! - Ctrl/Cmd+Q → Action::Quit
//! - Keyboard subscription is mode-aware (live-only actions gated on is_live)
//! - No muda / mod platform present in native_menu.rs

fn read_native_menu() -> String {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/native_menu.rs");
    std::fs::read_to_string(path).expect("failed to read src/native_menu.rs")
}

// ── Ctrl/Cmd+O ───────────────────────────────────────────────────────────────

#[test]
fn ctrl_o_dispatches_open_file() {
    let src = read_native_menu();
    // Match physical key (Code::KeyO) for layout-independent accelerators.
    assert!(
        src.contains("Code::KeyO"),
        "keyboard handler must match Code::KeyO (physical key) for OpenFile"
    );
    assert!(
        src.contains("Action::OpenFile"),
        "handler must yield Action::OpenFile for Ctrl/Cmd+O"
    );
}

// ── Ctrl/Cmd+S ────────────────────────────────────────────────────────────────

#[test]
fn ctrl_s_dispatches_save() {
    let src = read_native_menu();
    // Use a delimiter-bounded pattern to avoid matching "Action::SaveAs".
    assert!(
        src.contains("Some(Action::Save)"),
        "handler must yield Action::Save (not Action::SaveAs) for Ctrl/Cmd+S"
    );
}

#[test]
fn ctrl_shift_s_dispatches_save_as() {
    let src = read_native_menu();
    assert!(
        src.contains("Action::SaveAs"),
        "handler must yield Action::SaveAs for Ctrl/Cmd+Shift+S"
    );
}

// ── Ctrl/Cmd+Q ────────────────────────────────────────────────────────────────

#[test]
fn ctrl_q_dispatches_quit() {
    let src = read_native_menu();
    assert!(
        src.contains("Code::KeyQ"),
        "keyboard handler must match Code::KeyQ (physical key) for Quit"
    );
    assert!(
        src.contains("Action::Quit"),
        "handler must yield Action::Quit for Ctrl/Cmd+Q"
    );
}

// ── Layout-independent: physical_key matching, not Character() ────────────────

#[test]
fn matches_physical_key_not_character() {
    let src = read_native_menu();
    assert!(
        src.contains("physical_key"),
        "subscription must destructure physical_key from KeyPressed for layout-independent matching"
    );
    assert!(
        !src.contains(r#"Character("o")"#)
            && !src.contains(r#"Character("s")"#)
            && !src.contains(r#"Character("q")"#)
            && !src.contains(r#"Character("m")"#),
        "subscription must NOT use Key::Character (layout-dependent / IME-fragile) — use Physical::Code"
    );
}

// ── macOS-only logo() acceptance (Win/Linux Super key must not trigger) ───────

#[test]
fn logo_modifier_macos_only() {
    let src = read_native_menu();
    // logo() must appear inside a macOS cfg gate, not unconditionally.
    let logo_idx = src
        .find("modifiers.logo()")
        .expect("widget_keyboard_subscription must reference modifiers.logo() for macOS Cmd");
    // Look backwards 200 bytes for the cfg gate.
    let start = logo_idx.saturating_sub(200);
    let preceding = &src[start..logo_idx];
    assert!(
        preceding.contains("#[cfg(target_os = \"macos\")]"),
        "modifiers.logo() must be gated with #[cfg(target_os = \"macos\")] — \
         on Win/Linux logo() is the Super/Win key and must NOT trigger app shortcuts"
    );
}

// ── All platforms: on_key_press is used ──────────────────────────────────────

#[test]
fn iced_keyboard_listen_is_used_for_all_platforms() {
    let src = read_native_menu();
    assert!(
        src.contains("keyboard::listen()"),
        "iced::keyboard::listen must be used for keyboard subscription on all platforms"
    );
    // muda platform module must be gone
    assert!(
        !src.contains("mod platform"),
        "native_menu.rs must NOT contain a `mod platform` block — muda has been removed"
    );
}

// ── Subscription is mode-aware (HIGH-1 fix) ──────────────────────────────────

#[test]
fn subscription_gates_live_only_actions_on_is_live() {
    let src = read_native_menu();
    assert!(
        src.contains("is_live"),
        "widget_keyboard_subscription must check is_live to suppress live-only shortcuts in replay mode"
    );
    assert!(
        src.contains("app_mode") && src.contains("AppMode::Live"),
        "is_live must be derived from app_mode and AppMode::Live comparison"
    );
}

#[test]
fn subscription_signature_present() {
    let src = read_native_menu();
    assert!(
        src.contains("fn widget_keyboard_subscription("),
        "widget_keyboard_subscription must exist in native_menu.rs"
    );
    assert!(
        src.contains("pub fn subscription("),
        "native_menu::subscription must be present"
    );
}

// ── Action::Quit exists ───────────────────────────────────────────────────────

#[test]
fn action_quit_variant_exists() {
    let src = read_native_menu();
    assert!(
        src.contains("Quit,"),
        "Action enum must contain a Quit variant"
    );
}

// ── F7/T3: Action::SwitchMode exists ─────────────────────────────────────────

#[test]
fn action_switch_mode_variant_exists() {
    let src = read_native_menu();
    assert!(
        src.contains("SwitchMode(AppMode)"),
        "Action enum must contain a SwitchMode(AppMode) variant"
    );
}

#[test]
fn actions_for_mode_returns_six_tuple() {
    let src = read_native_menu();
    assert!(
        src.contains("(bool, bool, bool, bool, bool, bool)"),
        "actions_for_mode must return a 6-tuple (has_open_file, has_save, has_save_as, \
         has_open_replay_dialog, has_switch_live, has_switch_replay)"
    );
}

// ── Mode switching suppressed while MODE_SWITCHING is true (統一決定 64) ─────

#[test]
fn mode_switching_suppressed_during_mode_switch() {
    let src = read_native_menu();
    assert!(
        src.contains("MODE_SWITCHING.load(Ordering::Acquire)"),
        "widget_keyboard_subscription must check MODE_SWITCHING.load() before dispatching SwitchMode"
    );
}
