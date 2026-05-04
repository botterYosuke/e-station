//! Tests for `src/wandb_submit_proc.rs`.
//!
//! Because `wandb_submit_proc` is not yet declared in `main.rs` (that step
//! belongs to a different agent), tests read the source file directly via
//! `std::fs::read_to_string` for structural assertions.
//! Unit tests for pure functions (`parse_url_from_output`) are compiled via
//! the `flowsurface` crate after it is integrated.  Until then those tests
//! are also source-inspection based.

use std::fs;
use std::path::Path;

fn src_root() -> std::path::PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("src")
}

fn read_source(rel: &str) -> String {
    let path = src_root().join(rel);
    fs::read_to_string(&path).unwrap_or_else(|e| panic!("failed to read {}: {}", path.display(), e))
}

// ---------------------------------------------------------------------------
// Test 1: wandb_submit_proc.rs defines build_submit_command
// ---------------------------------------------------------------------------

#[test]
fn submit_proc_module_exists_and_defines_build_submit_command() {
    let source = read_source("wandb_submit_proc.rs");
    assert!(
        source.contains("fn build_submit_command"),
        "wandb_submit_proc.rs must define `fn build_submit_command`"
    );
}

// ---------------------------------------------------------------------------
// Test 2: argv does NOT contain WANDB_API_KEY
// ---------------------------------------------------------------------------

#[test]
fn command_argv_does_not_contain_api_key_as_arg() {
    let source = read_source("wandb_submit_proc.rs");
    // The API key must NOT be passed as a command-line argument.
    // It must only be available via the inherited environment.
    let has_arg_api_key = source.contains(".arg(\"WANDB_API_KEY\")");
    let has_arg_api_key_var = {
        // Reject patterns like `cmd.arg(&api_key)` unless it's via stdin
        // (stdin piping is an acceptable alternative channel).
        source.contains(".arg(&api_key)") && !source.contains("Stdio::piped")
    };
    assert!(
        !has_arg_api_key,
        "WANDB_API_KEY must not appear as a literal .arg() value"
    );
    assert!(
        !has_arg_api_key_var,
        "api_key variable must not be passed as a plain .arg() without stdin piping"
    );
}

// ---------------------------------------------------------------------------
// Test 3: env_clear() is NOT used (env must be inherited for WANDB_API_KEY)
// ---------------------------------------------------------------------------

#[test]
fn env_clear_not_used() {
    let source = read_source("wandb_submit_proc.rs");
    assert!(
        !source.contains("env_clear()"),
        "env_clear() must not be called — the subprocess must inherit the environment \
         so that WANDB_API_KEY is forwarded automatically"
    );
}

// ---------------------------------------------------------------------------
// Test 4 & 5: parse_url_from_output — source inspection of the function body
// ---------------------------------------------------------------------------

#[test]
fn parse_url_function_extracts_url_prefix() {
    let source = read_source("wandb_submit_proc.rs");
    // The function should use "URL: " prefix to identify the URL line.
    assert!(
        source.contains("URL: "),
        "parse_url_from_output must match lines starting with 'URL: '"
    );
}

#[test]
fn parse_url_function_returns_option() {
    let source = read_source("wandb_submit_proc.rs");
    assert!(
        source.contains("Option<String>"),
        "parse_url_from_output must return Option<String>"
    );
}

// Functional tests for parse_url_from_output via inline re-implementation
// (mirrors the production logic so we can test the algorithm independently).

fn parse_url_from_lines(lines: &[&str]) -> Option<String> {
    lines
        .iter()
        .rev()
        .find_map(|line| line.strip_prefix("URL: ").map(|u| u.trim().to_string()))
}

#[test]
fn parse_url_from_output_extracts_url() {
    let lines = [
        "Uploading...",
        "Done.",
        "URL: https://wandb.ai/test/runs/abc",
    ];
    let url = parse_url_from_lines(&lines);
    assert_eq!(url, Some("https://wandb.ai/test/runs/abc".to_string()));
}

#[test]
fn parse_url_returns_none_when_absent() {
    let lines = ["Uploading...", "Done.", "No URL here"];
    let url = parse_url_from_lines(&lines);
    assert_eq!(url, None);
}

#[test]
fn parse_url_picks_last_occurrence() {
    let lines = [
        "URL: https://wandb.ai/first",
        "URL: https://wandb.ai/second",
    ];
    let url = parse_url_from_lines(&lines);
    assert_eq!(url, Some("https://wandb.ai/second".to_string()));
}

// ---------------------------------------------------------------------------
// Test 6: WandbSubmitModal struct exists in wandb_submit.rs
// ---------------------------------------------------------------------------

#[test]
fn submit_modal_struct_exists() {
    let source = read_source("modal/wandb_submit.rs");
    assert!(
        source.contains("struct WandbSubmitModal"),
        "modal/wandb_submit.rs must define `struct WandbSubmitModal`"
    );
}

// ---------------------------------------------------------------------------
// Test 7: AuthDisplayState::NotSet maps to "未設定"
// ---------------------------------------------------------------------------

#[test]
fn auth_display_not_set_label_present() {
    let source = read_source("modal/wandb_submit.rs");
    assert!(
        source.contains("未設定"),
        "modal/wandb_submit.rs must contain the label '未設定' for AuthDisplayState::NotSet"
    );
}

// ---------------------------------------------------------------------------
// Test 8: log_lines use masking
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// M6: notes field is propagated from modal to subprocess argv
// ---------------------------------------------------------------------------

#[test]
fn submit_command_includes_notes_when_set() {
    // build_submit_command must accept a `notes` field on SubmitRunArgs and
    // forward it as `--notes <value>` to the subprocess argv.
    let source = read_source("wandb_submit_proc.rs");
    assert!(
        source.contains("notes"),
        "wandb_submit_proc.rs SubmitRunArgs must declare a `notes` field (M6)"
    );
    assert!(
        source.contains("--notes"),
        "build_submit_command must forward `--notes` to argv (M6)"
    );
}

#[test]
fn submit_action_carries_notes_field() {
    // The `Action::Submit` variant emitted by `WandbSubmitModal::update`
    // must carry a `notes: String` field so the user input is not silently
    // dropped (M6).
    let source = read_source("modal/wandb_submit.rs");
    // crude grep for `notes` inside the Action::Submit variant.
    let action_idx = source
        .find("Action::Submit")
        .or_else(|| source.find("pub enum Action"))
        .expect("Action enum must exist in modal/wandb_submit.rs");
    let after = &source[action_idx..];
    // Look at next ~600 chars for the Submit variant body.
    let window = &after[..after.len().min(600)];
    assert!(
        window.contains("notes"),
        "Action::Submit must carry a `notes` field — currently dropped (M6). Source window: {window}"
    );
}

// ---------------------------------------------------------------------------
// H8: 非UTF-8 path silent fallback の禁止
// ---------------------------------------------------------------------------

#[test]
fn submit_run_returns_error_on_non_utf8_path() {
    // Source inspection: `submit_wandb_run` (in src/main.rs) must NOT use
    // `.unwrap_or("")` or `.unwrap_or("examples/...")` for path string
    // conversion. Non-UTF-8 paths must yield `WandbSubmitError::ProcessFailed`
    // with a clear message instead of silently submitting with empty argv.
    let main_src = std::fs::read_to_string(
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src/main.rs"),
    )
    .expect("cannot read src/main.rs");

    // The old patterns must be gone.
    assert!(
        !main_src.contains(".to_str().unwrap_or(\"\")"),
        "H8: src/main.rs must not use `.to_str().unwrap_or(\"\")` (silent fallback)"
    );
    assert!(
        !main_src.contains(".to_str().unwrap_or(\"examples/wandb/submit_run.py\")"),
        "H8: src/main.rs must not silently fallback to a hardcoded script path"
    );

    // The new error path must exist.
    assert!(
        main_src.contains("non-UTF-8"),
        "H8: src/main.rs must produce a non-UTF-8 error message"
    );
    assert!(
        main_src.contains("WandbSubmitError::ProcessFailed"),
        "H8: src/main.rs must raise WandbSubmitError::ProcessFailed for path errors"
    );
}

#[test]
fn log_lines_use_masking() {
    let source = read_source("modal/wandb_submit.rs");
    // The modal must call mask_secrets (or reference MaskedLine) when
    // appending to log_lines.
    let has_mask_call = source.contains("mask_secrets") || source.contains("MaskedLine");
    assert!(
        has_mask_call,
        "modal/wandb_submit.rs must apply mask_secrets() or use MaskedLine when \
         storing lines in log_lines"
    );
}
