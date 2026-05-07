//! Structural regression pins for F4 BC-5: save error classification.
//!
//! Pins the log-level contracts for the three SaveError variants:
//! - `Cancelled`           → INFO-level (no ERROR, no WARN in the Cancelled path)
//! - `IoError(kind)`       → WARN-level  (not ERROR)
//! - `PathGuardViolation`  → ERROR-level + "BUG:" prefix (bug detection signal)

fn read_main() -> String {
    let base = env!("CARGO_MANIFEST_DIR");
    let main =
        std::fs::read_to_string(format!("{base}/src/main.rs")).expect("failed to read src/main.rs");
    let window =
        std::fs::read_to_string(format!("{base}/src/handlers/window.rs")).unwrap_or_default();
    let menu = std::fs::read_to_string(format!("{base}/src/handlers/menu.rs")).unwrap_or_default();
    format!("{main}\n{window}\n{menu}")
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

    let arm_prefix = "            WindowMsg::NativeSaveAsWithSpecs {";
    let start = src
        .find(arm_prefix)
        .expect("NativeSaveAsWithSpecs handler must exist");
    let tail = &src[start..];
    let end = tail[1..]
        .find("\n            WindowMsg::")
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

// ── F-H1 (R6): SaveAndExit updates last_saved_bytes on CURRENT_PATH write ────

#[test]
fn save_and_exit_updates_last_saved_bytes_on_current_path_write() {
    // F-H1: when SaveAndExit successfully writes to CURRENT_PATH, it MUST update
    // `last_saved_bytes` immediately, before attempting the saved-state.json mirror.
    // Otherwise a subsequent saved-state.json failure leaves dirty state intact
    // even though iced::exit() is still invoked (current_path.is_some() branch),
    // violating A-7's "明示 Save 直後に last_saved_bytes 更新" contract.
    let src = read_main();

    // Use newline-prefix to skip occurrences inside string literals in test code.
    let needle = "\n            WindowMsg::SaveAndExit =>";
    let start = src
        .find(needle)
        .map(|i| i + 1)
        .expect("WindowMsg::SaveAndExit handler must exist");
    let tail = &src[start..];
    let end = tail[1..]
        .find("\n            WindowMsg::")
        .map(|i| i + 1)
        .unwrap_or(tail.len());
    let body = &tail[..end];

    assert!(
        body.contains("last_saved_bytes = Some(json.as_bytes().to_vec())")
            || body.contains("last_saved_bytes = Some(json.clone().into_bytes())")
            || body.contains("last_saved_bytes = Some(json.into_bytes())"),
        "SaveAndExit must update last_saved_bytes after a successful CURRENT_PATH write (F-H1 / A-7)"
    );
}

// ── F-M1 (R6): SaveAndExit guards against missing pending_exit_windows ───────

#[test]
fn save_and_exit_logs_warn_when_pending_exit_windows_is_none() {
    // F-M1: if SaveAndExit is dispatched without a prior ExitRequested dirty check
    // (i.e. pending_exit_windows is None), `unwrap_or_default()` silently produces
    // an empty HashMap and `build_state_json` saves a corrupted layout. Replace
    // with `let-else` + `log::warn!` + `return Task::none()`.
    let src = read_main();

    // Use newline-prefix to skip occurrences inside string literals in test code.
    let needle = "\n            WindowMsg::SaveAndExit =>";
    let start = src
        .find(needle)
        .map(|i| i + 1)
        .expect("WindowMsg::SaveAndExit handler must exist");
    let tail = &src[start..];
    let end = tail[1..]
        .find("\n            WindowMsg::")
        .map(|i| i + 1)
        .unwrap_or(tail.len());
    let body = &tail[..end];

    assert!(
        body.contains("let Some(windows) = self.pending_exit_windows.take()"),
        "SaveAndExit must use `let Some(windows) = self.pending_exit_windows.take()` (F-M1)"
    );
    assert!(
        body.contains("log::warn!"),
        "SaveAndExit must `log::warn!` when pending_exit_windows is None (F-M1 silent corruption guard)"
    );
    assert!(
        !body.contains("self.pending_exit_windows.take().unwrap_or_default()"),
        "SaveAndExit must NOT use `unwrap_or_default()` — it silently corrupts saved state (F-M1)"
    );
}

// ── F-M2 (R6): Action::Save / Action::SaveAs guard against existing dialog ────

#[test]
fn action_save_and_save_as_guard_against_existing_dialog() {
    // F-M2: Ctrl+S / Ctrl+Shift+S can fire while a confirm_dialog is visible,
    // multi-launching rfd's save_file() dialog. Each handler must short-circuit
    // when `confirm_dialog.is_some()`.
    let src = read_main();

    for prefix in [
        "                    Action::Save =>",
        "                    Action::SaveAs =>",
    ] {
        let start = src
            .find(prefix)
            .unwrap_or_else(|| panic!("{prefix} handler must exist"));
        let tail = &src[start..];
        // Action arms end at the next "                    Action::" sibling.
        let end = tail[1..]
            .find("\n                    Action::")
            .map(|i| i + 1)
            .unwrap_or(tail.len());
        let body = &tail[..end];

        assert!(
            body.contains("confirm_dialog.is_some()"),
            "{prefix} body must check confirm_dialog.is_some() to prevent dialog re-entrancy (F-M2)"
        );
    }
}

// ── R2-M1: SaveAndOpenFile aborts and keeps CURRENT_PATH consistent on saved-state failure ─

#[test]
fn save_and_open_file_aborts_and_keeps_current_path_when_saved_state_write_fails() {
    // R2-M1 (revert of R1 F-M4): when `SaveAndOpenFile` reaches the saved-state.json
    // mirror write and that write fails, the handler MUST:
    //   1. NOT update CURRENT_PATH (otherwise next Ctrl+S overwrites the new file
    //      with the old layout still loaded by Flowsurface::new())
    //   2. NOT call self.restart() (restart would reload the OLD saved-state.json
    //      while CURRENT_PATH points to the NEW path — silent corruption)
    //   3. Emit a warn log + toast and return Task::none() to abort cleanly
    //
    // Ok branch only: update CURRENT_PATH then restart().
    let src = read_main();

    // Use newline-prefix to skip occurrences inside string literals in test code.
    let needle = "\n            WindowMsg::SaveAndOpenFile =>";
    let start = src
        .find(needle)
        .map(|i| i + 1)
        .expect("WindowMsg::SaveAndOpenFile handler must exist");
    let tail = &src[start..];
    let end = tail[1..]
        .find("\n            WindowMsg::")
        .map(|i| i + 1)
        .unwrap_or(tail.len());
    let body = &tail[..end];

    // Locate the saved-state.json mirror write call (after the named-doc save).
    let write_idx = body
        .find("data::write_json_to_file(&json, data::SAVED_STATE_PATH)")
        .expect("SaveAndOpenFile must write the imported state to SAVED_STATE_PATH");
    let post_write = &body[write_idx..];

    // The handler must branch on the Result of the write (match Ok/Err) so the
    // Err arm can return Task::none() without touching CURRENT_PATH or restart().
    assert!(
        post_write.contains("Ok(()) =>") || post_write.contains("Ok(_) =>"),
        "SaveAndOpenFile saved-state mirror must be a `match` with an Ok arm \
         (R2-M1: required to gate CURRENT_PATH update + restart() on success)"
    );
    assert!(
        post_write.contains("Err(e) =>") || post_write.contains("Err(_) =>"),
        "SaveAndOpenFile saved-state mirror must have an Err arm (R2-M1)"
    );

    // The Err arm must abort with Task::none() and must NOT reach self.restart()
    // or the CURRENT_PATH = Some(new_path) assignment. We verify by extracting
    // the Err arm body.
    let err_idx = post_write
        .find("Err(e) =>")
        .or_else(|| post_write.find("Err(_) =>"))
        .expect("Err arm must exist");
    let err_tail = &post_write[err_idx..];
    // Err arm ends at the closing of the match — find the next "}\n" at the
    // match-arm indent level. As a structural proxy, look up to the enclosing
    // match's closing brace by scanning until the first occurrence of a line
    // that begins with "            }" (12-space indent of the handler arm).
    // For simplicity, take the next 600 chars which comfortably covers the arm.
    let err_arm_window = &err_tail[..err_tail.len().min(600)];

    assert!(
        err_arm_window.contains("Task::none()"),
        "SaveAndOpenFile saved-state-mirror Err arm must return Task::none() to abort \
         (R2-M1: prevent silent corruption from restart() + new CURRENT_PATH mismatch)"
    );
    assert!(
        !err_arm_window.contains("self.restart()"),
        "SaveAndOpenFile saved-state-mirror Err arm must NOT call self.restart() — \
         restart() reloads the OLD saved-state.json and corrupts the next save (R2-M1)"
    );
    assert!(
        !err_arm_window.contains("*guard = Some(new_path)")
            && !err_arm_window.contains("*poisoned.into_inner() = Some(new_path)"),
        "SaveAndOpenFile saved-state-mirror Err arm must NOT update CURRENT_PATH — \
         updating it while loading old saved-state silently corrupts the next save (R2-M1)"
    );
    assert!(
        err_arm_window.contains("log::warn!"),
        "SaveAndOpenFile saved-state-mirror Err arm must emit log::warn! (R2-M1 user visibility)"
    );
    assert!(
        err_arm_window.contains("Toast::error"),
        "SaveAndOpenFile saved-state-mirror Err arm must push a Toast::error (R2-M1 user visibility)"
    );

    // The Ok arm must update CURRENT_PATH and call restart().
    let ok_idx = post_write
        .find("Ok(()) =>")
        .or_else(|| post_write.find("Ok(_) =>"))
        .expect("Ok arm must exist");
    let ok_tail = &post_write[ok_idx..];
    // Ok arm precedes Err arm; cap at err_idx relative position.
    let ok_arm = if ok_idx < err_idx {
        &post_write[ok_idx..err_idx]
    } else {
        &ok_tail[..ok_tail.len().min(600)]
    };
    assert!(
        ok_arm.contains("self.restart()"),
        "SaveAndOpenFile saved-state-mirror Ok arm must call self.restart() to load new file (R2-M1)"
    );
    assert!(
        ok_arm.contains("Some(new_path)"),
        "SaveAndOpenFile saved-state-mirror Ok arm must update CURRENT_PATH to new_path (R2-M1)"
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
