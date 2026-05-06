//! Structural regression pins for F8/P8: cross-platform menu action contracts.
//!
//! These source-inspection tests verify that `src/menu.rs` contains the
//! expected function definitions and Action variants as specified in
//! `docs/✅menu-and-footer/P8-widget-menu-bar-linux.md`.
//!
//! Key invariants (DoD-7):
//! - `actions_for_mode(Live)` ⊇ {Open, Save, SaveAs, Quit}
//! - `actions_for_mode(Replay)` ⊇ {ReplayStart, ReplayStop, Quit}

fn read_menu() -> String {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/menu.rs");
    // Normalise CRLF so boundary searches work cross-platform.
    std::fs::read_to_string(path)
        .expect("failed to read src/menu.rs — run this after creating the file")
        .replace("\r\n", "\n")
}

// ── DoD-7: live mode actions ───────────────────────────────────────────────

#[test]
fn live_actions_include_open() {
    let src = read_menu();
    assert!(
        src.contains("Action::Open"),
        "actions_for_mode(Live) must include Action::Open"
    );
}

#[test]
fn live_actions_include_save() {
    let src = read_menu();
    // Use "Action::Save," to avoid matching Action::SaveAs as a substring.
    assert!(
        src.contains("Action::Save,"),
        "actions_for_mode(Live) must include Action::Save"
    );
}

#[test]
fn live_actions_include_save_as() {
    let src = read_menu();
    assert!(
        src.contains("Action::SaveAs"),
        "actions_for_mode(Live) must include Action::SaveAs"
    );
}

#[test]
fn live_actions_include_quit() {
    let src = read_menu();
    assert!(
        src.contains("Action::Quit"),
        "actions_for_mode must include Action::Quit (both modes)"
    );
}

// ── DoD-7: replay mode actions ─────────────────────────────────────────────

#[test]
fn replay_actions_include_replay_start() {
    let src = read_menu();
    assert!(
        src.contains("Action::ReplayStart"),
        "actions_for_mode(Replay) must include Action::ReplayStart"
    );
}

#[test]
fn replay_actions_include_replay_stop() {
    let src = read_menu();
    assert!(
        src.contains("Action::ReplayStop"),
        "actions_for_mode(Replay) must include Action::ReplayStop"
    );
}

// ── actions_for_mode function must exist ──────────────────────────────────

#[test]
fn actions_for_mode_function_exists() {
    let src = read_menu();
    assert!(
        src.contains("pub fn actions_for_mode"),
        "menu.rs must export `pub fn actions_for_mode`"
    );
}

// ── SwitchAppMode variant ──────────────────────────────────────────────────

#[test]
fn action_enum_has_switch_app_mode() {
    let src = read_menu();
    assert!(
        src.contains("SwitchAppMode"),
        "Action enum must have SwitchAppMode(AppMode) variant for mode-switch dispatch"
    );
}
