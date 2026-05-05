//! Subprocess launcher for `examples/wandb/submit_run.py`.
//!
//! Spawns `uv run --with wandb python submit_run.py ...` and converts
//! stdout into [`SubmitEvent`] values.  The environment is **inherited**
//! so that `WANDB_API_KEY` reaches the subprocess without being visible
//! in Rust log output or command-line arguments.
//!
//! # Reentrancy / submit_in_flight
//!
//! Callers MUST NOT spawn a second subprocess while one is already running.
//! The `WandbSubmitModal.submitting` flag (see `src/modal/wandb_submit.rs`)
//! serves as the `submit_in_flight` guard that enforces this invariant.
//!
//! # Dead-code policy (M2, R1 Phase 3-C)
//!
//! `build_submit_command` / `SubmitRunArgs` / `parse_url_from_output` are
//! currently exercised only by source-inspection tests in
//! `tests/wandb_submit_subprocess.rs` and by the in-file unit tests below.
//! They are retained here because Phase 3-B is scheduled to extend
//! `build_submit_command` with a `--notes` argument and migrate
//! `submit_wandb_run` (`src/main.rs`) onto this builder. Until that
//! migration lands, these items are flagged with a per-item
//! `#[allow(dead_code)]` rather than a module-wide `#![allow(dead_code)]`
//! so future regressions in *new* helpers surface immediately.

// MaskedLine / mask_secrets は src/mask_secrets.rs に集約（C3, R1 Phase 1）。
// ここで再定義せず単一定義を再エクスポートする。
#[allow(unused_imports)]
pub use crate::mask_secrets::{MaskedLine, mask_secrets};

// ---------------------------------------------------------------------------

/// Parameters forwarded to `submit_run.py`.
///
/// Used by [`build_submit_command`]. Currently only exercised by in-file
/// unit tests; Phase 3-B will migrate `submit_wandb_run` onto this builder.
#[allow(dead_code)]
pub struct SubmitRunArgs {
    /// Path to `examples/wandb/submit_run.py`.
    pub script_path: std::path::PathBuf,
    /// Directory that contains the buffered run JSON files.
    pub run_buffer_dir: std::path::PathBuf,
    /// W&B project name (e.g. `"flowsurface-strategies"`).
    pub project: String,
    /// Human-readable run name.
    pub run_name: String,
    /// Comma-separated tags (e.g. `"replay,buy_and_hold"`).
    pub tags: String,
    /// M6: free-form notes forwarded to `wandb.init(notes=...)`. Empty
    /// string suppresses the `--notes` argv entry.
    pub notes: String,
}

/// Build the `std::process::Command` that runs `submit_run.py`.
///
/// # Invariants
/// - The subprocess environment is **never cleared** — it inherits the full
///   environment so that `WANDB_API_KEY` is passed automatically.
/// - `WANDB_API_KEY` is **never** passed as a command-line argument.
#[allow(dead_code)]
pub fn build_submit_command(args: &SubmitRunArgs) -> std::process::Command {
    let mut cmd = std::process::Command::new("uv");
    cmd.args(["run", "--with", "wandb", "python"]);
    cmd.arg(&args.script_path);
    cmd.arg("--run-buffer").arg(&args.run_buffer_dir);
    cmd.arg("--project").arg(&args.project);
    cmd.arg("--run-name").arg(&args.run_name);
    cmd.arg("--tags").arg(&args.tags);
    if !args.notes.is_empty() {
        cmd.arg("--notes").arg(&args.notes);
    }
    // stdout/stderr are captured by the caller for streaming.
    cmd.stdout(std::process::Stdio::piped());
    cmd.stderr(std::process::Stdio::piped());
    cmd
}

/// Parse the W&B run URL from the last `"URL: <url>"` line in `lines`.
///
/// Returns `None` when no such line exists.
#[allow(dead_code)]
pub fn parse_url_from_output(lines: &[&str]) -> Option<String> {
    lines
        .iter()
        .rev()
        .find_map(|line| line.strip_prefix("URL: ").map(|u| u.trim().to_string()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_url_extracts_last_url_line() {
        let lines = [
            "Uploading...",
            "Done.",
            "URL: https://wandb.ai/test/runs/abc",
        ];
        let url = parse_url_from_output(&lines);
        assert_eq!(url, Some("https://wandb.ai/test/runs/abc".to_string()));
    }

    #[test]
    fn parse_url_returns_none_when_absent() {
        let lines = ["Uploading...", "Done."];
        let url = parse_url_from_output(&lines);
        assert_eq!(url, None);
    }

    #[test]
    fn parse_url_trims_whitespace() {
        let lines = ["URL:   https://wandb.ai/x/y/z  "];
        let url = parse_url_from_output(&lines);
        assert_eq!(url, Some("https://wandb.ai/x/y/z".to_string()));
    }

    #[test]
    fn parse_url_picks_last_when_multiple() {
        let lines = [
            "URL: https://wandb.ai/first",
            "URL: https://wandb.ai/second",
        ];
        let url = parse_url_from_output(&lines);
        assert_eq!(url, Some("https://wandb.ai/second".to_string()));
    }

    #[test]
    fn build_command_uses_uv() {
        let args = SubmitRunArgs {
            script_path: std::path::PathBuf::from("examples/wandb/submit_run.py"),
            run_buffer_dir: std::path::PathBuf::from("/tmp/runs"),
            project: "test-project".to_string(),
            run_name: "test-run".to_string(),
            tags: "tag1,tag2".to_string(),
            notes: String::new(),
        };
        let cmd = build_submit_command(&args);
        // Command::get_program() returns the executable name
        assert_eq!(cmd.get_program(), "uv");
    }

    #[test]
    fn build_command_includes_project_and_run_name() {
        let args = SubmitRunArgs {
            script_path: std::path::PathBuf::from("examples/wandb/submit_run.py"),
            run_buffer_dir: std::path::PathBuf::from("/tmp/runs"),
            project: "my-project".to_string(),
            run_name: "my-run".to_string(),
            tags: "".to_string(),
            notes: String::new(),
        };
        let cmd = build_submit_command(&args);
        let argv: Vec<_> = cmd.get_args().collect();
        // Check that "--project" and "my-project" appear in argv
        let argv_str: Vec<&str> = argv.iter().map(|a| a.to_str().unwrap_or("")).collect();
        assert!(argv_str.contains(&"--project"));
        assert!(argv_str.contains(&"my-project"));
        assert!(argv_str.contains(&"--run-name"));
        assert!(argv_str.contains(&"my-run"));
    }

    /// M6: notes が `--notes <value>` として argv に含まれること。
    #[test]
    fn build_command_includes_notes_when_set() {
        let args = SubmitRunArgs {
            script_path: std::path::PathBuf::from("examples/wandb/submit_run.py"),
            run_buffer_dir: std::path::PathBuf::from("/tmp/runs"),
            project: "p".to_string(),
            run_name: "r".to_string(),
            tags: String::new(),
            notes: "experiment v2".to_string(),
        };
        let cmd = build_submit_command(&args);
        let argv: Vec<_> = cmd.get_args().collect();
        let argv_str: Vec<&str> = argv.iter().map(|a| a.to_str().unwrap_or("")).collect();
        assert!(argv_str.contains(&"--notes"), "argv must contain --notes");
        assert!(
            argv_str.contains(&"experiment v2"),
            "argv must contain the notes value"
        );
    }

    /// M6: notes が空のとき `--notes` は argv に出さない（cli の余計な引数を抑制）。
    #[test]
    fn build_command_omits_notes_when_empty() {
        let args = SubmitRunArgs {
            script_path: std::path::PathBuf::from("examples/wandb/submit_run.py"),
            run_buffer_dir: std::path::PathBuf::from("/tmp/runs"),
            project: "p".to_string(),
            run_name: "r".to_string(),
            tags: String::new(),
            notes: String::new(),
        };
        let cmd = build_submit_command(&args);
        let argv: Vec<_> = cmd.get_args().collect();
        let argv_str: Vec<&str> = argv.iter().map(|a| a.to_str().unwrap_or("")).collect();
        assert!(
            !argv_str.contains(&"--notes"),
            "argv must NOT contain --notes when notes is empty"
        );
    }
}
