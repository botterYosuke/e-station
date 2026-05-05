//! Structural regression pins for F9c: `tools_actions_for_state` R7-86 matrix.
//!
//! Source-inspection tests verifying the content of `src/menu.rs` matches
//! the DoD-10 / R7-86 contract from P9-wandb-submit-menu.md.
//!
//! F9c changes (R7-86): `tools_actions_for_state` now takes
//! `(&WandbAuthState, &RunBufferIndex)` and returns `Vec<MenuEntry>`.
//!
//! Expected matrix:
//! | auth.authenticated | buf.latest_completed | SignInWandb    | SignOutWandb   | SubmitToWandb | OpenSubmissionLog | ClearRunBuffer |
//! |--------------------|----------------------|----------------|----------------|---------------|-------------------|----------------|
//! | true               | Some(_)              | enabled=false  | enabled=true   | enabled=true  | enabled=true      | enabled=true   |
//! | true               | None                 | enabled=false  | enabled=true   | enabled=false | enabled=false     | enabled=false  |
//! | false              | Some(_)              | enabled=true   | enabled=false  | enabled=false | enabled=true      | enabled=true   |
//! | false              | None                 | enabled=true   | enabled=false  | enabled=false | enabled=false     | enabled=false  |

fn read_menu() -> String {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/menu.rs");
    // Normalize CRLF → LF to handle Windows line endings uniformly.
    std::fs::read_to_string(path)
        .expect("failed to read src/menu.rs")
        .replace("\r\n", "\n")
}

fn tools_fn_with_doc(src: &str) -> String {
    // Normalize line endings to handle both CRLF and LF.
    let src_lf = src.replace("\r\n", "\n");

    // Find the function declaration.
    let fn_decl = "pub fn tools_actions_for_state";
    let fn_pos = src_lf
        .find(fn_decl)
        .expect("tools_actions_for_state must exist");

    // Walk backwards from fn_pos to find the start of the preceding doc comment.
    let prefix = &src_lf[..fn_pos];
    // Find the blank line before the doc comment block.
    let doc_block_start = prefix
        .rfind("\n\n")
        .map(|p| p + 2) // skip the blank line itself
        .unwrap_or(0);

    // Extract from doc block start to the next sibling function.
    let from_doc = &src_lf[doc_block_start..];
    let fn_relative = fn_pos - doc_block_start;
    let after_fn = &from_doc[fn_relative..];

    // Find the opening brace to skip into the function body.
    let open_brace = after_fn.find('{').unwrap_or(0);
    let after_brace = &after_fn[open_brace..];

    // TODO(F8-fragile / M9): naive brace counting. This walker treats every
    // `{` / `}` byte the same regardless of whether it lives inside a string
    // literal, char literal, raw string, or `//`/`/* */` comment. The current
    // `tools_actions_for_state` body happens to contain none of those, so the
    // walk terminates at the correct closing brace — but a future edit that
    // introduces e.g. `let s = "}";` or `// closes the }` will silently shift
    // the slice and start matching against the next sibling function. If that
    // happens, switch to a real lexer (e.g. `proc-macro2::TokenStream` or
    // `syn::parse_str::<syn::ItemFn>`) instead of byte-level brace counting.
    // Count braces to find the closing brace of the function.
    let mut depth = 0usize;
    let mut end_of_fn = after_brace.len();
    for (i, ch) in after_brace.char_indices() {
        match ch {
            '{' => depth += 1,
            '}' => {
                if depth == 0 {
                    break;
                }
                depth -= 1;
                if depth == 0 {
                    end_of_fn = i + 1;
                    break;
                }
            }
            _ => {}
        }
    }

    let fn_end_abs = fn_relative + open_brace + end_of_fn;
    from_doc[..fn_end_abs].to_string()
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
        "menu.rs must define `pub enum AuthState` (legacy; kept for backward compat)"
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
        "menu.rs must define `pub enum BufferState` (legacy; kept for backward compat)"
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

// ── R7-86: new signature uses WandbAuthState and RunBufferIndex ───────────

#[test]
fn tools_actions_for_state_takes_wandb_auth_state() {
    let src = read_menu();
    let body = tools_fn_with_doc(&src);
    assert!(
        body.contains("WandbAuthState"),
        "tools_actions_for_state must take WandbAuthState (R7-86 signature update)"
    );
}

#[test]
fn tools_actions_for_state_takes_run_buffer_index() {
    let src = read_menu();
    let body = tools_fn_with_doc(&src);
    assert!(
        body.contains("RunBufferIndex"),
        "tools_actions_for_state must take RunBufferIndex (R7-86 signature update)"
    );
}

#[test]
fn tools_actions_for_state_returns_vec_menu_entry() {
    let src = read_menu();
    let body = tools_fn_with_doc(&src);
    assert!(
        body.contains("Vec<MenuEntry>"),
        "tools_actions_for_state must return Vec<MenuEntry> (R7-86)"
    );
}

// ── Matrix contents: all five actions present in function body ────────────

#[test]
fn tools_actions_includes_all_five_actions() {
    let src = read_menu();
    let body = tools_fn_with_doc(&src);
    for action in &[
        "SignInWandb",
        "SignOutWandb",
        "SubmitToWandb",
        "OpenSubmissionLog",
        "ClearRunBuffer",
    ] {
        assert!(
            body.contains(action),
            "tools_actions_for_state must reference Action::{action}"
        );
    }
}

// ── Invariant: OpenSubmissionLog is always present (source comment check) ─

#[test]
fn open_submission_log_always_present_invariant_documented() {
    let src = read_menu();
    let body = tools_fn_with_doc(&src);
    assert!(
        body.contains("always present"),
        "tools_actions_for_state must document OpenSubmissionLog is always present"
    );
}

// ── Invariant: mutual exclusion of SignIn / SignOut (source comment check) ─

#[test]
fn signin_signout_mutually_exclusive_invariant_documented() {
    let src = read_menu();
    let body = tools_fn_with_doc(&src);
    assert!(
        body.contains("mutually exclusive"),
        "tools_actions_for_state must document SignIn/SignOut mutual exclusion"
    );
}

// ── enabled / tooltip logic references ───────────────────────────────────

#[test]
fn function_body_references_authenticated_field() {
    let src = read_menu();
    let body = tools_fn_with_doc(&src);
    assert!(
        body.contains("authenticated"),
        "tools_actions_for_state must check auth.authenticated"
    );
}

#[test]
fn function_body_references_latest_completed() {
    let src = read_menu();
    let body = tools_fn_with_doc(&src);
    assert!(
        body.contains("latest_completed"),
        "tools_actions_for_state must check buf.latest_completed"
    );
}

#[test]
fn function_body_references_login_tooltip() {
    let src = read_menu();
    let body = tools_fn_with_doc(&src);
    assert!(
        body.contains("W&B にログインしてください"),
        "tools_actions_for_state must include login prompt tooltip for unauthenticated SubmitToWandb"
    );
}

#[test]
fn function_body_references_no_runs_tooltip() {
    let src = read_menu();
    let body = tools_fn_with_doc(&src);
    assert!(
        body.contains("送信可能な run がありません"),
        "tools_actions_for_state must include 'no runs' tooltip for empty buffer SubmitToWandb"
    );
}
