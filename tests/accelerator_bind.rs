//! Structural regression pins for F2 accelerator bindings.
//!
//! These tests verify that the source code contains the expected accelerator
//! definitions without requiring a live GUI runtime.  They pin the invariants
//! documented in `fix-save-menu.md §F2-DoD`.
//!
//! Key invariants checked:
//! - Ctrl+O → Action::OpenFile (Windows/macOS muda, Linux iced keyboard)
//! - Ctrl+S → Action::Save
//! - Ctrl+Shift+S → Action::SaveAs
//! - Ctrl+Q → Action::Quit (Linux iced keyboard; Windows muda Code::KeyQ)
//! - Linux keyboard subscription is mode-aware (live-only actions gated on is_live)
//! - iced `on_key_press` is ONLY present in the Linux branch (C-7: no double-dispatch
//!   on Windows / macOS where muda handles accelerators)

fn read_native_menu() -> String {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/native_menu.rs");
    std::fs::read_to_string(path).expect("failed to read src/native_menu.rs")
}

// ── Ctrl+O ────────────────────────────────────────────────────────────────────

#[test]
fn accelerator_ctrl_o_present_in_muda_items() {
    let src = read_native_menu();
    assert!(
        src.contains("Code::KeyO"),
        "muda live-mode Open item must bind Code::KeyO"
    );
    assert!(
        src.contains("Modifiers::CONTROL"),
        "Ctrl modifier must be present in accelerator definitions"
    );
}

#[test]
fn linux_ctrl_o_dispatches_open_file() {
    let src = read_native_menu();
    // The linux keyboard handler must map "o" + ctrl to Action::OpenFile.
    assert!(
        src.contains(r#"Character("o")"#),
        "Linux keyboard handler must match character 'o' for OpenFile"
    );
    assert!(
        src.contains("Action::OpenFile"),
        "Linux handler must yield Action::OpenFile for Ctrl+O"
    );
}

// ── Ctrl+S ────────────────────────────────────────────────────────────────────

#[test]
fn accelerator_ctrl_s_present_in_muda_items() {
    let src = read_native_menu();
    assert!(
        src.contains("Code::KeyS"),
        "muda live-mode Save / Save As items must bind Code::KeyS"
    );
}

#[test]
fn linux_ctrl_s_dispatches_save() {
    let src = read_native_menu();
    // M-8: use a delimiter-bounded pattern to avoid matching "Action::SaveAs".
    // "Some(Action::Save)" is unique to the Ctrl+S arm; "Action::SaveAs" yields
    // "Some(Action::SaveAs)" which does NOT contain "Some(Action::Save)".
    assert!(
        src.contains("Some(Action::Save)"),
        "Linux handler must yield Action::Save (not Action::SaveAs) for Ctrl+S"
    );
}

#[test]
fn linux_ctrl_shift_s_dispatches_save_as() {
    let src = read_native_menu();
    assert!(
        src.contains("Action::SaveAs"),
        "Linux handler must yield Action::SaveAs for Ctrl+Shift+S"
    );
}

// ── Ctrl+Q ────────────────────────────────────────────────────────────────────

#[test]
fn accelerator_ctrl_q_present_in_muda_windows() {
    let src = read_native_menu();
    assert!(
        src.contains("Code::KeyQ"),
        "muda Windows quit item must bind Code::KeyQ (Ctrl+Q)"
    );
}

#[test]
fn linux_ctrl_q_dispatches_quit() {
    let src = read_native_menu();
    assert!(
        src.contains(r#"Character("q")"#),
        "Linux keyboard handler must match character 'q' for Quit"
    );
    assert!(
        src.contains("Action::Quit"),
        "Linux handler must yield Action::Quit for Ctrl+Q"
    );
}

// ── C-7: no double-dispatch on Windows / macOS ─────────────────────────────────

#[test]
fn iced_on_key_press_is_linux_only() {
    let src = read_native_menu();
    // `on_key_press` must only appear inside the `cfg(target_os = "linux")` guard.
    // Scan for its usage outside that guard by checking the function is annotated.
    assert!(
        src.contains("#[cfg(target_os = \"linux\")]"),
        "linux_keyboard_subscription must be gated with cfg(target_os = \"linux\")"
    );
    assert!(
        src.contains("on_key_press"),
        "iced::keyboard::on_key_press must be used for Linux keyboard subscription"
    );
    // The platform module (Windows/macOS) must NOT call on_key_press.
    // Structural check: on_key_press must not appear inside `mod platform`.
    let platform_start = src.find("mod platform {").unwrap_or(src.len());
    let platform_src = &src[platform_start..];
    assert!(
        !platform_src.contains("on_key_press"),
        "on_key_press must NOT appear inside mod platform (Windows/macOS) — double-dispatch risk"
    );
}

// ── Linux subscription is mode-aware (HIGH-1 fix) ─────────────────────────────

#[test]
fn linux_subscription_gates_live_only_actions_on_is_live() {
    let src = read_native_menu();
    // The Linux handler must guard OpenFile / Save / SaveAs with is_live.
    assert!(
        src.contains("is_live"),
        "linux_keyboard_subscription must check is_live to suppress live-only shortcuts in replay mode"
    );
    assert!(
        src.contains("app_mode == AppMode::Live"),
        "is_live must be derived from app_mode comparison"
    );
}

#[test]
fn linux_subscription_receives_app_mode_parameter() {
    let src = read_native_menu();
    assert!(
        src.contains("fn linux_keyboard_subscription(app_mode: AppMode)"),
        "linux_keyboard_subscription must accept an app_mode parameter"
    );
    assert!(
        src.contains("pub fn subscription(app_mode: AppMode)"),
        "native_menu::subscription must accept app_mode so callers can pass current mode"
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
fn mode_submenu_label_present() {
    let src = read_native_menu();
    assert!(
        src.contains("モード（Mode）"),
        "attach() must create a mode submenu with Japanese label"
    );
}

#[test]
fn mode_submenu_has_live_and_replay_items() {
    let src = read_native_menu();
    assert!(
        src.contains("ライブ（Live）"),
        "mode submenu must have a live item"
    );
    assert!(
        src.contains("リプレイ（Replay）"),
        "mode submenu must have a replay item"
    );
}

#[test]
fn actions_for_mode_returns_six_tuple() {
    // Structural check: actions_for_mode must now return a 6-tuple.
    let src = read_native_menu();
    assert!(
        src.contains("(bool, bool, bool, bool, bool, bool)"),
        "actions_for_mode must return a 6-tuple after adding switch_live / switch_replay"
    );
}

#[test]
fn linux_mode_switching_suppressed_during_mode_switch() {
    let src = read_native_menu();
    // The linux handler must check MODE_SWITCHING before dispatching SwitchMode.
    assert!(
        src.contains("MODE_SWITCHING.load(Ordering::Acquire)"),
        "Linux keyboard handler must check MODE_SWITCHING.load() before dispatching SwitchMode (統一決定 64)"
    );
}
