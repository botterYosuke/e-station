//! Structural regression pins for F3: `CURRENT_PATH` static.
//!
//! Pins the invariants documented in `fix-save-menu.md §F3-DoD`:
//! - `CURRENT_PATH` is a `static Mutex<Option<PathBuf>>`
//! - Poison recovery via `into_inner()` (not `unwrap()`)
//! - `NativeOpenFileApply` handler sets CURRENT_PATH on success
//! - `NativeSaveAsWithSpecs` handler updates CURRENT_PATH after a successful write
//! - `NativeSaveAsWithSpecs` calls both `std::fs::write` (user path) and
//!   `save_state_to_disk` (saved-state.json), satisfying A-3 "both paths" rule
//!
//! `Flowsurface` is a GUI-heavy struct; full instantiation in a unit test
//! is impractical.  Source-inspection tests (same approach as other `tests/*.rs`
//! files in this workspace) provide structural guarantees without a live runtime.

fn read_main() -> String {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/main.rs");
    std::fs::read_to_string(path).expect("failed to read src/main.rs")
}

// ── CURRENT_PATH static ───────────────────────────────────────────────────────

#[test]
fn current_path_static_exists() {
    let src = read_main();
    assert!(
        src.contains("static CURRENT_PATH"),
        "CURRENT_PATH static must exist in main.rs"
    );
}

#[test]
fn current_path_uses_mutex_option_pathbuf() {
    let src = read_main();
    assert!(
        src.contains("Mutex<Option<std::path::PathBuf>>"),
        "CURRENT_PATH must be declared as Mutex<Option<std::path::PathBuf>>"
    );
}

// ── Poison recovery ───────────────────────────────────────────────────────────

#[test]
fn current_path_uses_into_inner_for_poison_recovery() {
    let src = read_main();
    // Every CURRENT_PATH.lock() call site must handle poison via into_inner().
    let occurrences = src.matches("CURRENT_PATH.lock()").count();
    let recoveries = src
        .lines()
        .filter(|l| !l.trim_start().starts_with("//"))
        .filter(|l| l.contains("into_inner()"))
        .count();
    assert!(
        occurrences > 0,
        "CURRENT_PATH.lock() must be called at least once"
    );
    assert!(
        recoveries >= occurrences,
        "every CURRENT_PATH.lock() call must have a corresponding into_inner() poison recovery"
    );
}

// ── NativeOpenFilePendingCheck sets CURRENT_PATH ─────────────────────────────
// (F4 two-step fix: NativeOpenFileApply now dispatches NativeOpenFilePendingCheck
// so dirty comparison uses real window specs; CURRENT_PATH is set there.)

#[test]
fn open_file_apply_sets_current_path() {
    let src = read_main();

    // NativeOpenFileApply must dispatch NativeOpenFilePendingCheck.
    let apply_prefix = "            Message::NativeOpenFileApply { json, path } =>";
    let apply_start = src
        .find(apply_prefix)
        .expect("NativeOpenFileApply handler must exist");
    let apply_tail = &src[apply_start..];
    let apply_end = apply_tail[1..]
        .find("\n            Message::")
        .map(|i| i + 1)
        .unwrap_or(apply_tail.len());
    let apply_body = &apply_tail[..apply_end];
    assert!(
        apply_body.contains("NativeOpenFilePendingCheck"),
        "NativeOpenFileApply must dispatch NativeOpenFilePendingCheck (F4 two-step fix)"
    );

    // NativeOpenFilePendingCheck sets CURRENT_PATH and calls restart.
    // rustfmt may reformat the struct pattern across multiple lines, so search
    // for the newline-prefixed match arm (12-space indent, not a deeper nested
    // construction site with 28 spaces).
    let check_needle = "\n            Message::NativeOpenFilePendingCheck {";
    let check_start = src
        .find(check_needle)
        .map(|i| i + 1) // skip the leading '\n' so the slice starts at the arm
        .expect("NativeOpenFilePendingCheck handler must exist");
    let check_tail = &src[check_start..];
    let check_end = check_tail[1..]
        .find("\n            Message::")
        .map(|i| i + 1)
        .unwrap_or(check_tail.len());
    let check_body = &check_tail[..check_end];

    assert!(
        check_body.contains("CURRENT_PATH"),
        "NativeOpenFilePendingCheck success branch must write path to CURRENT_PATH"
    );
    assert!(
        check_body.contains("self.restart()"),
        "NativeOpenFilePendingCheck success branch must call self.restart()"
    );
}

// ── NativeSaveAsWithSpecs updates CURRENT_PATH (A-3) ─────────────────────────

#[test]
fn save_as_with_specs_sets_current_path() {
    let src = read_main();
    let arm_prefix = "Message::NativeSaveAsWithSpecs(windows)";
    let start = src
        .find(arm_prefix)
        .expect("NativeSaveAsWithSpecs handler must exist");
    let tail = &src[start..];
    let end = tail[1..]
        .find("\n            Message::")
        .map(|i| i + 1)
        .unwrap_or(tail.len());
    let body = &tail[..end];

    assert!(
        body.contains("CURRENT_PATH"),
        "NativeSaveAsWithSpecs must update CURRENT_PATH after a successful write"
    );
}

// ── A-3: explicit Save / Save As writes to both paths ─────────────────────────

#[test]
fn save_as_with_specs_double_writes_a3() {
    let src = read_main();
    let arm_prefix = "Message::NativeSaveAsWithSpecs(windows)";
    let start = src
        .find(arm_prefix)
        .expect("NativeSaveAsWithSpecs handler must exist");
    let tail = &src[start..];
    let end = tail[1..]
        .find("\n            Message::")
        .map(|i| i + 1)
        .unwrap_or(tail.len());
    let body = &tail[..end];

    assert!(
        body.contains("std::fs::write"),
        "NativeSaveAsWithSpecs must write to the user-specified path (explicit Save/Save As)"
    );
    assert!(
        body.contains("save_state_to_disk"),
        "NativeSaveAsWithSpecs must also call save_state_to_disk (A-3: both paths)"
    );
}

// ── Action::Save falls back to Save As when CURRENT_PATH is None ─────────────

#[test]
fn action_save_handler_reads_current_path() {
    let src = read_main();
    // The Save handler must read CURRENT_PATH to decide between direct-write
    // and Save As dialog fallback.
    assert!(
        src.contains("Action::Save =>"),
        "NativeMenuAction handler must have an Action::Save arm"
    );
    let save_start = src.find("Action::Save =>").expect("checked above");
    let tail = &src[save_start..];
    // Look ahead up to Action::SaveAs arm to stay within the Save arm body.
    let end = tail.find("Action::SaveAs =>").unwrap_or(tail.len());
    let body = &tail[..end];

    assert!(
        body.contains("CURRENT_PATH"),
        "Action::Save arm must read CURRENT_PATH to determine the target path"
    );
}
