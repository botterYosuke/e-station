//! Structural regression pins for F8/P8: cross-platform menu action contracts.
//!
//! These source-inspection tests verify that `src/menu.rs` contains the
//! expected function definitions and Action variants as specified in
//! `docs/architecture/modules/ui-shell/P8-widget-menu-bar-linux.md`.
//!
//! Key invariants (DoD-7):
//! - `actions_for_mode(Live)` ⊇ {Open, Save, SaveAs, Quit}
//! - `actions_for_mode(Replay)` == `actions_for_mode(Live)` — replay start/stop moved to control bar

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

// ── DoD-7: replay mode actions — same set as live (Step 7) ────────────────

#[test]
fn replay_actions_include_open() {
    let src = read_menu();
    assert!(
        src.contains("Action::Open"),
        "actions_for_mode(Replay) must include Action::Open"
    );
}

#[test]
fn replay_actions_do_not_include_replay_start_in_function_body() {
    let src = read_menu();
    // `actions_for_mode` must not return ReplayStart — that action was moved to the
    // replay control bar (schema 3.15). The variants ReplayStart/ReplayStop have been
    // removed from the Action enum entirely (schema 3.16).
    let fn_body_start = src.find("pub fn actions_for_mode").unwrap_or(0);
    let fn_body_end = src[fn_body_start..]
        .find("\n}")
        .map(|i| fn_body_start + i + 2)
        .unwrap_or(src.len());
    let fn_body = &src[fn_body_start..fn_body_end];
    assert!(
        !fn_body.contains("ReplayStart"),
        "actions_for_mode must not return Action::ReplayStart"
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
