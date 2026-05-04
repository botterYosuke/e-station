//! Structural regression pins for F8/P8: cross-platform menu action contracts.
//!
//! These source-inspection tests verify that `src/menu.rs` contains the
//! expected function definitions and Action variants as specified in
//! `docs/✅menu-and-footer/P8-widget-menu-bar-linux.md`.
//!
//! Key invariants (DoD-7, DoD-11):
//! - `actions_for_mode(Live)` ⊇ {Open, Save, SaveAs, Quit}
//! - `actions_for_mode(Replay)` ⊇ {ReplayStart, ReplayStop, Quit}
//! - `actions_for_mode` does NOT contain Tools submenu actions
//!   (SubmitToWandb / SignInWandb / SignOutWandb / OpenSubmissionLog / ClearRunBuffer)

fn read_menu() -> String {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/menu.rs");
    std::fs::read_to_string(path).expect("failed to read src/menu.rs — run this after creating the file")
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

// ── DoD-11: actions_for_mode excludes Tools submenu actions ───────────────

#[test]
fn actions_for_mode_function_exists() {
    let src = read_menu();
    assert!(
        src.contains("pub fn actions_for_mode"),
        "menu.rs must export `pub fn actions_for_mode`"
    );
}

#[test]
fn tools_actions_are_in_separate_function() {
    let src = read_menu();
    // tools_actions_for_state must exist as a distinct function (not mixed into actions_for_mode).
    assert!(
        src.contains("pub fn tools_actions_for_state"),
        "menu.rs must export `pub fn tools_actions_for_state` as a separate function from actions_for_mode (DoD-11)"
    );
}

#[test]
fn action_enum_has_submit_to_wandb() {
    let src = read_menu();
    assert!(
        src.contains("SubmitToWandb"),
        "Action enum must have SubmitToWandb variant"
    );
}

#[test]
fn action_enum_has_sign_in_wandb() {
    let src = read_menu();
    assert!(
        src.contains("SignInWandb"),
        "Action enum must have SignInWandb variant"
    );
}

#[test]
fn action_enum_has_sign_out_wandb() {
    let src = read_menu();
    assert!(
        src.contains("SignOutWandb"),
        "Action enum must have SignOutWandb variant"
    );
}

#[test]
fn action_enum_has_open_submission_log() {
    let src = read_menu();
    assert!(
        src.contains("OpenSubmissionLog"),
        "Action enum must have OpenSubmissionLog variant"
    );
}

#[test]
fn action_enum_has_clear_run_buffer() {
    let src = read_menu();
    assert!(
        src.contains("ClearRunBuffer"),
        "Action enum must have ClearRunBuffer variant"
    );
}

// ── `actions_for_mode` does NOT mention Tools actions ─────────────────────

#[test]
fn actions_for_mode_body_excludes_submit_to_wandb() {
    let src = read_menu();
    // Extract only the body of `actions_for_mode` to verify it doesn't include Tools variants.
    let start = src
        .find("pub fn actions_for_mode")
        .expect("actions_for_mode must exist");
    // Find the closing brace at function level (simple heuristic: next top-level `pub fn`)
    let after = &src[start..];
    let end = after
        .find("\npub fn ")
        .unwrap_or(after.len());
    let body = &after[..end];

    assert!(
        !body.contains("SubmitToWandb"),
        "actions_for_mode body must NOT contain SubmitToWandb (DoD-11 responsibility separation)"
    );
    assert!(
        !body.contains("SignInWandb"),
        "actions_for_mode body must NOT contain SignInWandb (DoD-11)"
    );
    assert!(
        !body.contains("SignOutWandb"),
        "actions_for_mode body must NOT contain SignOutWandb (DoD-11)"
    );
    assert!(
        !body.contains("OpenSubmissionLog"),
        "actions_for_mode body must NOT contain OpenSubmissionLog (DoD-11)"
    );
    assert!(
        !body.contains("ClearRunBuffer"),
        "actions_for_mode body must NOT contain ClearRunBuffer (DoD-11)"
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
