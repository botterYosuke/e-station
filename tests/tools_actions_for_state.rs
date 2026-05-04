//! Structural regression pins for F8/P8: `tools_actions_for_state` 2×2 matrix.
//!
//! Source-inspection tests verifying the content of `src/menu.rs` matches
//! the DoD-10 contract from `P8-widget-menu-bar-linux.md`.
//!
//! Expected matrix (R3-66/69):
//! | auth_state  | buffer_state | expected actions                                     |
//! |-------------|--------------|------------------------------------------------------|
//! | SignedOut   | Empty        | [SignInWandb]                                        |
//! | SignedOut   | HasRuns      | [SignInWandb, OpenSubmissionLog, ClearRunBuffer]      |
//! | SignedIn    | Empty        | [SignOutWandb]                                       |
//! | SignedIn    | HasRuns      | [SubmitToWandb, OpenSubmissionLog, ClearRunBuffer,    |
//! |             |              |  SignOutWandb]                                       |

fn read_menu() -> String {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/menu.rs");
    std::fs::read_to_string(path).expect("failed to read src/menu.rs")
}

// ── Structural: function and type existence ────────────────────────────────

#[test]
fn tools_actions_for_state_function_exported() {
    let src = read_menu();
    assert!(
        src.contains("pub fn tools_actions_for_state"),
        "menu.rs must export `pub fn tools_actions_for_state`"
    );
}

#[test]
fn auth_state_enum_exists() {
    let src = read_menu();
    assert!(
        src.contains("pub enum AuthState"),
        "menu.rs must define `pub enum AuthState`"
    );
    assert!(
        src.contains("SignedIn"),
        "AuthState must have a SignedIn variant"
    );
    assert!(
        src.contains("SignedOut"),
        "AuthState must have a SignedOut variant"
    );
}

#[test]
fn buffer_state_enum_exists() {
    let src = read_menu();
    assert!(
        src.contains("pub enum BufferState"),
        "menu.rs must define `pub enum BufferState`"
    );
    assert!(
        src.contains("HasRuns"),
        "BufferState must have a HasRuns variant"
    );
    assert!(
        src.contains("Empty"),
        "BufferState must have an Empty variant"
    );
}

// ── Matrix contents: all four combinations present ────────────────────────

#[test]
fn tools_actions_handles_signed_out_empty() {
    let src = read_menu();
    // The function body must map (SignedOut, Empty) → [SignInWandb].
    // We verify that the match arms for SignedOut + Empty exist.
    let fn_start = src
        .find("pub fn tools_actions_for_state")
        .expect("tools_actions_for_state must exist");
    let fn_body = &src[fn_start..];
    let fn_end = fn_body.find("\npub fn ").unwrap_or(fn_body.len());
    let body = &fn_body[..fn_end];

    assert!(
        body.contains("SignedOut") && body.contains("Empty"),
        "tools_actions_for_state must handle (SignedOut, Empty) arm"
    );
}

#[test]
fn tools_actions_handles_signed_out_has_runs() {
    let src = read_menu();
    let fn_start = src
        .find("pub fn tools_actions_for_state")
        .expect("tools_actions_for_state must exist");
    let fn_body = &src[fn_start..];
    let fn_end = fn_body.find("\npub fn ").unwrap_or(fn_body.len());
    let body = &fn_body[..fn_end];

    assert!(
        body.contains("SignedOut") && body.contains("HasRuns"),
        "tools_actions_for_state must handle (SignedOut, HasRuns) arm"
    );
}

#[test]
fn tools_actions_handles_signed_in_empty() {
    let src = read_menu();
    let fn_start = src
        .find("pub fn tools_actions_for_state")
        .expect("tools_actions_for_state must exist");
    let fn_body = &src[fn_start..];
    let fn_end = fn_body.find("\npub fn ").unwrap_or(fn_body.len());
    let body = &fn_body[..fn_end];

    assert!(
        body.contains("SignedIn") && body.contains("Empty"),
        "tools_actions_for_state must handle (SignedIn, Empty) arm"
    );
}

#[test]
fn tools_actions_handles_signed_in_has_runs() {
    let src = read_menu();
    let fn_start = src
        .find("pub fn tools_actions_for_state")
        .expect("tools_actions_for_state must exist");
    let fn_body = &src[fn_start..];
    let fn_end = fn_body.find("\npub fn ").unwrap_or(fn_body.len());
    let body = &fn_body[..fn_end];

    assert!(
        body.contains("SignedIn") && body.contains("HasRuns"),
        "tools_actions_for_state must handle (SignedIn, HasRuns) arm"
    );
}

// ── Matrix contents: correct action sets per arm ─────────────────────────

#[test]
fn signed_out_empty_returns_sign_in_only() {
    let src = read_menu();
    let fn_start = src
        .find("pub fn tools_actions_for_state")
        .expect("tools_actions_for_state must exist");
    let fn_body = &src[fn_start..];
    let fn_end = fn_body.find("\npub fn ").unwrap_or(fn_body.len());
    let body = &fn_body[..fn_end];

    // SignedOut + Empty arm must yield only SignInWandb
    assert!(
        body.contains("SignInWandb"),
        "tools_actions_for_state must include Action::SignInWandb for SignedOut arm"
    );
}

#[test]
fn signed_in_has_runs_returns_submit_and_signout() {
    let src = read_menu();
    let fn_start = src
        .find("pub fn tools_actions_for_state")
        .expect("tools_actions_for_state must exist");
    let fn_body = &src[fn_start..];
    let fn_end = fn_body.find("\npub fn ").unwrap_or(fn_body.len());
    let body = &fn_body[..fn_end];

    assert!(
        body.contains("SubmitToWandb"),
        "tools_actions_for_state(SignedIn, HasRuns) must include SubmitToWandb"
    );
    assert!(
        body.contains("SignOutWandb"),
        "tools_actions_for_state(SignedIn, HasRuns) must include SignOutWandb"
    );
    assert!(
        body.contains("OpenSubmissionLog"),
        "tools_actions_for_state(SignedIn, HasRuns) must include OpenSubmissionLog"
    );
    assert!(
        body.contains("ClearRunBuffer"),
        "tools_actions_for_state(SignedIn, HasRuns) must include ClearRunBuffer"
    );
}

#[test]
fn signed_in_empty_returns_signout_only_not_submit() {
    let src = read_menu();
    let fn_start = src
        .find("pub fn tools_actions_for_state")
        .expect("tools_actions_for_state must exist");
    let fn_body = &src[fn_start..];
    let fn_end = fn_body.find("\npub fn ").unwrap_or(fn_body.len());
    let body = &fn_body[..fn_end];

    assert!(
        body.contains("SignOutWandb"),
        "tools_actions_for_state(SignedIn, Empty) must include SignOutWandb"
    );
}
