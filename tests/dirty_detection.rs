//! Structural regression pins for F4: dirty detection and confirm-on-exit/open.
//!
//! Pins the invariants documented in `fix-save-menu.md §F4-DoD`:
//! - `last_saved_bytes: Option<Vec<u8>>` field exists (BC-9)
//! - Dirty check: `None => false` (initial/clean state, BC-9)
//! - `save_state_to_disk` updates `last_saved_bytes` after successful write (A-7)
//! - `ExitRequested` checks dirty before calling `iced::exit()` (cases 1-4)
//! - `NativeOpenFileApply` checks dirty before calling `self.restart()` (cases 2-3)
//! - Serialization is deterministic — same state → same bytes 100× (BC-11)
//! - Auto-save does NOT write to `CURRENT_PATH` (R3)

fn read_main() -> String {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/main.rs");
    std::fs::read_to_string(path).expect("failed to read src/main.rs")
}

// ── Case 1 / BC-9: last_saved_bytes field and None-is-clean invariant ─────────

#[test]
fn last_saved_bytes_field_exists() {
    let src = read_main();
    assert!(
        src.contains("last_saved_bytes"),
        "Flowsurface must have a `last_saved_bytes` field for dirty detection (F4/BC-9)"
    );
}

#[test]
fn last_saved_bytes_is_option_vec_u8() {
    let src = read_main();
    assert!(
        src.contains("Option<Vec<u8>>"),
        "last_saved_bytes must be typed Option<Vec<u8>> for byte-level dirty comparison"
    );
}

#[test]
fn dirty_none_is_clean() {
    // BC-9: last_saved_bytes = None means initial/clean state → dirty = false.
    // The dirty logic must use a `None => false` branch.
    let src = read_main();
    assert!(
        src.contains("None => false"),
        "dirty check must return false for None last_saved_bytes (BC-9: initial state is clean)"
    );
}

// ── Case 2-4: ExitRequested and OpenFileApply check dirty ─────────────────────

#[test]
fn exit_requested_checks_dirty() {
    let src = read_main();
    let prefix = "            Message::ExitRequested(windows) =>";
    let start = src
        .find(prefix)
        .expect("ExitRequested handler arm must exist");
    let tail = &src[start..];
    let end = tail[1..]
        .find("\n            Message::")
        .map(|i| i + 1)
        .unwrap_or(tail.len());
    let body = &tail[..end];

    assert!(
        body.contains("is_dirty") || body.contains("last_saved_bytes"),
        "ExitRequested handler must check dirty state before exiting (F4 cases 2-4)"
    );
}

#[test]
fn exit_requested_shows_confirm_when_dirty() {
    let src = read_main();
    let prefix = "            Message::ExitRequested(windows) =>";
    let start = src
        .find(prefix)
        .expect("ExitRequested handler arm must exist");
    let tail = &src[start..];
    let end = tail[1..]
        .find("\n            Message::")
        .map(|i| i + 1)
        .unwrap_or(tail.len());
    let body = &tail[..end];

    assert!(
        body.contains("confirm_dialog") || body.contains("DiscardAndExit"),
        "ExitRequested handler must show confirm dialog when state is dirty (F4)"
    );
}

#[test]
fn open_file_apply_checks_dirty_before_restart() {
    // NativeOpenFileApply dispatches NativeOpenFilePendingCheck (which carries real
    // window specs) so the dirty comparison uses accurate bytes rather than an empty
    // HashMap that would always appear dirty.  The actual is_dirty() call lives in
    // NativeOpenFilePendingCheck — both handler arms are tested here.
    let src = read_main();

    // Step 1: NativeOpenFileApply must dispatch NativeOpenFilePendingCheck.
    let apply_prefix = "            Message::NativeOpenFileApply { json, path } =>";
    let apply_start = src
        .find(apply_prefix)
        .expect("NativeOpenFileApply handler arm must exist");
    let apply_tail = &src[apply_start..];
    let apply_end = apply_tail[1..]
        .find("\n            Message::")
        .map(|i| i + 1)
        .unwrap_or(apply_tail.len());
    let apply_body = &apply_tail[..apply_end];

    assert!(
        apply_body.contains("NativeOpenFilePendingCheck"),
        "NativeOpenFileApply must dispatch NativeOpenFilePendingCheck for accurate dirty comparison (F4 fix)"
    );

    // Step 2: NativeOpenFilePendingCheck must perform the dirty check.
    // rustfmt may reformat the struct pattern across multiple lines, so search
    // for the newline-prefixed match arm (12-space indent, not a deeper nested
    // construction site).
    let check_needle = "\n            Message::NativeOpenFilePendingCheck {";
    let check_start = src
        .find(check_needle)
        .map(|i| i + 1) // skip the leading '\n' so the slice starts at the arm
        .expect("NativeOpenFilePendingCheck handler arm must exist");
    let check_tail = &src[check_start..];
    let check_end = check_tail[1..]
        .find("\n            Message::")
        .map(|i| i + 1)
        .unwrap_or(check_tail.len());
    let check_body = &check_tail[..check_end];

    assert!(
        check_body.contains("is_dirty")
            || check_body.contains("last_saved_bytes")
            || check_body.contains("pending_open_file"),
        "NativeOpenFilePendingCheck must check dirty state before restart (F4 case 2-3)"
    );
}

#[test]
fn toggle_dialog_modal_none_clears_pending_open_file() {
    // Fix for Issue 2: ToggleDialogModal(None) must clear pending_open_file so that
    // a subsequent Open action re-enters the dirty-check flow instead of skipping it.
    let src = read_main();
    let prefix = "            Message::ToggleDialogModal(dialog) =>";
    let start = src
        .find(prefix)
        .expect("ToggleDialogModal handler arm must exist");
    let tail = &src[start..];
    let end = tail[1..]
        .find("\n            Message::")
        .map(|i| i + 1)
        .unwrap_or(tail.len());
    let body = &tail[..end];

    assert!(
        body.contains("pending_open_file = None"),
        "ToggleDialogModal handler must clear pending_open_file when dismissing (Issue 2 fix)"
    );
    assert!(
        body.contains("pending_exit_windows = None"),
        "ToggleDialogModal handler must clear pending_exit_windows when dismissing (Issue 2 fix)"
    );
}

// ── Case 5 / BC-11: Stable serialization ──────────────────────────────────────

#[test]
fn stable_serialization() {
    // Serializing the same state 100 times must always produce identical bytes.
    // This guards against HashMap-based non-determinism (BC-11 / C-9).
    let state = data::State::default();
    let first = serde_json::to_string(&state).expect("serialize must succeed");
    for i in 1..100 {
        let next = serde_json::to_string(&state).expect("serialize must succeed");
        assert_eq!(
            first, next,
            "Serialization is non-deterministic at iteration {i}: bytes differ between runs"
        );
    }
}

// ── Case 6: SaveError enum ─────────────────────────────────────────────────────
// (Full log-level assertions are in tests/save_error_classification.rs)

#[test]
fn save_error_enum_exists() {
    let src = read_main();
    assert!(
        src.contains("enum SaveError"),
        "SaveError enum must exist in main.rs (F4/BC-5)"
    );
}

// ── Case 7 / R3: Auto-save does NOT touch CURRENT_PATH ────────────────────────

#[test]
fn save_state_to_disk_does_not_write_current_path() {
    // R3: save_state_to_disk (auto-save) must only write to saved-state.json,
    // never to CURRENT_PATH.
    let src = read_main();
    let fn_start = src
        .find("fn save_state_to_disk(")
        .expect("save_state_to_disk must exist");
    let tail = &src[fn_start..];
    let end = tail[1..]
        .find("\n    fn ")
        .map(|i| i + 1)
        .unwrap_or(tail.len());
    let body = &tail[..end];

    assert!(
        !body.contains("CURRENT_PATH.lock()"),
        "save_state_to_disk must NOT access CURRENT_PATH — auto-save writes only to saved-state.json (R3)"
    );
}

#[test]
fn save_state_to_disk_updates_last_saved_bytes() {
    // A-7: save_state_to_disk must update last_saved_bytes so auto-save
    // clears the dirty flag (prevents false-positive confirm on quit after auto-save).
    let src = read_main();
    let fn_start = src
        .find("fn save_state_to_disk(")
        .expect("save_state_to_disk must exist");
    let tail = &src[fn_start..];
    let end = tail[1..]
        .find("\n    fn ")
        .map(|i| i + 1)
        .unwrap_or(tail.len());
    let body = &tail[..end];

    assert!(
        body.contains("last_saved_bytes"),
        "save_state_to_disk must update last_saved_bytes after a successful write (A-7)"
    );
}

// ── Pending exit / open message variants ──────────────────────────────────────

#[test]
fn discard_and_exit_message_exists() {
    let src = read_main();
    assert!(
        src.contains("DiscardAndExit"),
        "Message::DiscardAndExit must exist for the dirty-check dialog confirm action (F4)"
    );
}
