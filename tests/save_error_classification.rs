//! Structural regression pins for F4 BC-5: save error classification.
//!
//! Pins the log-level contracts for the three SaveError variants:
//! - `Cancelled`           → INFO-level (no ERROR, no WARN in the Cancelled path)
//! - `IoError(kind)`       → WARN-level  (not ERROR)
//! - `PathGuardViolation`  → ERROR-level + "BUG:" prefix (bug detection signal)

fn read_main() -> String {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/main.rs");
    std::fs::read_to_string(path).expect("failed to read src/main.rs")
}

// ── SaveError enum variants ────────────────────────────────────────────────────

#[test]
fn save_error_enum_has_cancelled_variant() {
    let src = read_main();
    assert!(
        src.contains("Cancelled"),
        "SaveError enum must have a Cancelled variant (BC-5)"
    );
}

#[test]
fn save_error_enum_has_io_error_variant() {
    let src = read_main();
    assert!(
        src.contains("IoError"),
        "SaveError enum must have an IoError variant (BC-5)"
    );
}

#[test]
fn save_error_enum_has_path_guard_violation_variant() {
    let src = read_main();
    assert!(
        src.contains("PathGuardViolation"),
        "SaveError enum must have a PathGuardViolation variant (BC-5)"
    );
}

// ── Log-level contracts (BC-5) ────────────────────────────────────────────────

#[test]
fn path_guard_violation_emits_error_log_with_bug_prefix() {
    // PathGuardViolation is a bug indicator: always ERROR + "BUG:" prefix.
    let src = read_main();
    assert!(
        src.contains("BUG: path guard violation"),
        "PathGuardViolation handling must emit log::error! with 'BUG: path guard violation' prefix (BC-5)"
    );
}

#[test]
fn io_error_emits_warn_not_error() {
    // IoError (disk full / permission denied) is an operational failure: WARN level.
    // Using ERROR for ordinary OS I/O failures would spam on-call dashboards.
    let src = read_main();

    // Find the save-error handling section.
    // The IoError arm must contain log::warn! and NOT log::error! for its own line.
    assert!(
        src.contains("SaveError::IoError"),
        "SaveError::IoError must be handled explicitly (BC-5)"
    );

    // The canonical pattern: log::warn! appears in the IoError arm.
    // M-6: use next SaveError:: occurrence (without .min(500) hard limit)
    // to avoid silently truncating the arm body check.
    let io_arm_start = src
        .find("SaveError::IoError")
        .expect("SaveError::IoError arm must exist");
    let io_tail = &src[io_arm_start..];
    // Find the end of this arm by locating the next SaveError:: in the remaining text.
    // Fall back to the end of the log_save_error function if no next variant follows.
    let io_arm_end = io_tail[1..]
        .find("SaveError::")
        .map(|i| i + 1)
        .unwrap_or(io_tail.len());
    let io_arm = &io_tail[..io_arm_end];

    assert!(
        io_arm.contains("log::warn!"),
        "IoError arm must use log::warn! (not log::error!) for operational I/O failures (BC-5)"
    );
    assert!(
        !io_arm.contains("log::error!"),
        "IoError arm must NOT use log::error! — this would mis-classify normal OS failures as bugs (BC-5)"
    );
}

#[test]
fn cancelled_does_not_emit_error_or_warn_log() {
    // Cancelled is a user-initiated action — no error signal is appropriate.
    let src = read_main();

    let cancelled_start = src
        .find("SaveError::Cancelled")
        .expect("SaveError::Cancelled must be handled explicitly (BC-5)");
    let cancelled_tail = &src[cancelled_start..];
    // M-10: search for next SaveError:: without hard char limit to avoid
    // order-dependent truncation of the arm body.
    let cancelled_end = cancelled_tail[1..]
        .find("SaveError::")
        .map(|i| i + 1)
        .unwrap_or(cancelled_tail.len());
    let cancelled_arm = &cancelled_tail[..cancelled_end];

    assert!(
        !cancelled_arm.contains("log::error!"),
        "Cancelled arm must NOT use log::error! (user intent, not an error) (BC-5)"
    );
    assert!(
        !cancelled_arm.contains("log::warn!"),
        "Cancelled arm must NOT use log::warn! (user intent, no action needed) (BC-5)"
    );
}

// ── NativeSaveAsWithSpecs: I/O error now uses WARN not ERROR ─────────────────

#[test]
fn save_as_with_specs_io_error_uses_warn() {
    // Previously this used log::error!. BC-5 reclassifies ordinary write
    // failures as WARN (IoError category).
    let src = read_main();

    let arm_prefix = "            Message::NativeSaveAsWithSpecs {";
    let start = src
        .find(arm_prefix)
        .expect("NativeSaveAsWithSpecs handler must exist");
    let tail = &src[start..];
    let end = tail[1..]
        .find("\n            Message::")
        .map(|i| i + 1)
        .unwrap_or(tail.len());
    let body = &tail[..end];

    // Must not have a bare log::error! for the ordinary I/O write failure anymore.
    // (PathGuardViolation may use log::error! but that is a different path.)
    assert!(
        !body.contains("log::error!(\"Failed to write"),
        "NativeSaveAsWithSpecs I/O error must use log::warn! not log::error! (BC-5 IoError reclassification)"
    );
}

// ── M-5: save_state_to_disk must not use log::error! ─────────────────────────

#[test]
fn save_state_to_disk_does_not_use_log_error() {
    // M-5: save_state_to_disk's I/O error path must use log_save_error (WARN level),
    // not log::error! directly.  log::error! inside save_state_to_disk would violate
    // the BC-5 log-level contract (IoError → WARN, not ERROR).
    let src = read_main();

    let fn_start = src
        .find("fn save_state_to_disk(")
        .expect("save_state_to_disk must exist in main.rs");
    let tail = &src[fn_start..];
    // Extract the function body up to the next top-level function definition.
    let end = tail[1..]
        .find("\n    fn ")
        .map(|i| i + 1)
        .unwrap_or(tail.len());
    let body = &tail[..end];

    assert!(
        !body.contains("log::error!"),
        "save_state_to_disk must NOT use log::error! — I/O write failure is IoError (WARN), not a bug (BC-5 / M-5)"
    );
}
