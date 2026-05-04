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
#![allow(dead_code)]

// ---------------------------------------------------------------------------
// Inline masking types (mirrors src/mask_secrets.rs for standalone use)
// ---------------------------------------------------------------------------

/// A stdout line that has been processed by `mask_secrets()`.
///
/// The newtype prevents raw (possibly secret-containing) strings from reaching
/// the UI or log without going through the masking step.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MaskedLine(String);

impl MaskedLine {
    pub fn as_str(&self) -> &str {
        &self.0
    }
    pub fn into_string(self) -> String {
        self.0
    }
}

impl std::fmt::Display for MaskedLine {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.0.fmt(f)
    }
}

/// Apply secret masking to a raw stdout line.
///
/// Masks `WANDB_API_KEY=<value>`, `api_key=<value>`, and
/// `Bearer <token>` / `bearer <token>` patterns.
pub fn mask_secrets(line: &str) -> MaskedLine {
    let mut out = line.to_string();

    // WANDB_API_KEY=<value>
    if let Some(pos) = out.find("WANDB_API_KEY=") {
        let start = pos + "WANDB_API_KEY=".len();
        let end = out[start..]
            .find(|c: char| c.is_whitespace() || c == '&' || c == '"' || c == '\'')
            .map(|p| start + p)
            .unwrap_or(out.len());
        if start < end {
            out.replace_range(start..end, "***");
        }
    }

    // api_key=<value>
    if let Some(pos) = out.find("api_key=") {
        let start = pos + "api_key=".len();
        let end = out[start..]
            .find(|c: char| c.is_whitespace() || c == '&' || c == '"' || c == '\'')
            .map(|p| start + p)
            .unwrap_or(out.len());
        if start < end {
            out.replace_range(start..end, "***");
        }
    }

    // Bearer / bearer
    for prefix in &["Bearer ", "bearer "] {
        if let Some(pos) = out.find(prefix) {
            let start = pos + prefix.len();
            let end = out[start..]
                .find(|c: char| c.is_whitespace() || c == '"' || c == '\'')
                .map(|p| start + p)
                .unwrap_or(out.len());
            if start < end {
                out.replace_range(start..end, "***");
            }
        }
    }

    MaskedLine(out)
}

// ---------------------------------------------------------------------------

/// Parameters forwarded to `submit_run.py`.
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
}

/// An event produced by the running `submit_run.py` subprocess.
#[derive(Debug, Clone)]
pub enum SubmitEvent {
    /// One stdout line, already processed by `mask_secrets()`.
    Line(MaskedLine),
    /// Process exited with code 0.  `url` is parsed from the last
    /// `"URL: <url>"` line if present.
    Done { url: Option<String> },
    /// Process exited with a non-zero exit code.
    Failed { stderr: String, exit_code: i32 },
}

/// Build the `std::process::Command` that runs `submit_run.py`.
///
/// # Invariants
/// - The subprocess environment is **never cleared** — it inherits the full
///   environment so that `WANDB_API_KEY` is passed automatically.
/// - `WANDB_API_KEY` is **never** passed as a command-line argument.
pub fn build_submit_command(args: &SubmitRunArgs) -> std::process::Command {
    let mut cmd = std::process::Command::new("uv");
    cmd.args(["run", "--with", "wandb", "python"]);
    cmd.arg(&args.script_path);
    cmd.arg("--run-buffer").arg(&args.run_buffer_dir);
    cmd.arg("--project").arg(&args.project);
    cmd.arg("--run-name").arg(&args.run_name);
    cmd.arg("--tags").arg(&args.tags);
    // stdout/stderr are captured by the caller for streaming.
    cmd.stdout(std::process::Stdio::piped());
    cmd.stderr(std::process::Stdio::piped());
    cmd
}

/// Parse the W&B run URL from the last `"URL: <url>"` line in `lines`.
///
/// Returns `None` when no such line exists.
pub fn parse_url_from_output(lines: &[&str]) -> Option<String> {
    lines
        .iter()
        .rev()
        .find_map(|line| line.strip_prefix("URL: ").map(|u| u.trim().to_string()))
}

/// Run the submit subprocess synchronously and collect all [`SubmitEvent`]s.
///
/// This blocking helper is intended for use in tests and one-shot scripts.
/// In the GUI the caller should spawn this on a background thread.
pub fn run_submit_blocking(args: &SubmitRunArgs) -> Vec<SubmitEvent> {
    use std::io::{BufRead, BufReader};

    let mut cmd = build_submit_command(args);
    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => {
            return vec![SubmitEvent::Failed {
                stderr: e.to_string(),
                exit_code: -1,
            }];
        }
    };

    let stdout = child.stdout.take().expect("stdout must be piped");
    let mut events: Vec<SubmitEvent> = Vec::new();
    let mut all_lines: Vec<String> = Vec::new();

    for raw in BufReader::new(stdout).lines() {
        match raw {
            Ok(line) => {
                let masked = mask_secrets(&line);
                all_lines.push(line);
                events.push(SubmitEvent::Line(masked));
            }
            Err(_) => break,
        }
    }

    let output = match child.wait_with_output() {
        Ok(o) => o,
        Err(e) => {
            return {
                events.push(SubmitEvent::Failed {
                    stderr: e.to_string(),
                    exit_code: -1,
                });
                events
            };
        }
    };

    if output.status.success() {
        let refs: Vec<&str> = all_lines.iter().map(String::as_str).collect();
        let url = parse_url_from_output(&refs);
        events.push(SubmitEvent::Done { url });
    } else {
        let exit_code = output.status.code().unwrap_or(-1);
        let stderr = String::from_utf8_lossy(&output.stderr).into_owned();
        events.push(SubmitEvent::Failed { stderr, exit_code });
    }

    events
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
}
