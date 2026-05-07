//! Structural regression pins for F5: Save As overwrite confirmation.
//!
//! Pins the invariants documented in `fix-save-menu.md §F5-DoD`:
//! - `NativeSaveAsPath(Some(path))` checks whether the target file already exists
//! - When the file exists, a confirm dialog is shown before overwriting
//! - `Message::ConfirmSaveAsOverwrite` carries the confirm action

fn read_main() -> String {
    let base = env!("CARGO_MANIFEST_DIR");
    let main =
        std::fs::read_to_string(format!("{base}/src/main.rs")).expect("failed to read src/main.rs");
    let window =
        std::fs::read_to_string(format!("{base}/src/handlers/window.rs")).unwrap_or_default();
    format!("{main}\n{window}")
}

/// Entry-point test matching `cargo test save_as_overwrite_confirm`.
/// Combines all F5 assertions into one discoverable test name.
#[test]
fn save_as_overwrite_confirm() {
    let src = read_main();

    // 1. NativeSaveAsPath(Some(path)) handler must check path existence.
    let arm_prefix = "            WindowMsg::NativeSaveAsPath(Some(path)) =>";
    let start = src
        .find(arm_prefix)
        .expect("NativeSaveAsPath(Some(path)) handler must exist");
    let tail = &src[start..];
    let end = tail[1..]
        .find("\n            WindowMsg::")
        .map(|i| i + 1)
        .unwrap_or(tail.len());
    let body = &tail[..end];

    assert!(
        body.contains("path.exists()"),
        "NativeSaveAsPath(Some(path)) handler must check path.exists() to detect overwrite (F5)"
    );

    // 2. A confirm dialog must be shown when the file exists.
    assert!(
        body.contains("confirm_dialog") || body.contains("ConfirmSaveAsOverwrite"),
        "NativeSaveAsPath handler must show a confirm dialog when the target file exists (F5)"
    );

    // 3. ConfirmSaveAsOverwrite message variant must exist.
    assert!(
        src.contains("ConfirmSaveAsOverwrite"),
        "Message::ConfirmSaveAsOverwrite must exist to carry the overwrite-confirmed action (F5)"
    );
}

#[test]
fn save_as_path_checks_file_existence() {
    let src = read_main();
    let arm_prefix = "            WindowMsg::NativeSaveAsPath(Some(path)) =>";
    let start = src
        .find(arm_prefix)
        .expect("NativeSaveAsPath(Some(path)) handler must exist");
    let tail = &src[start..];
    let end = tail[1..]
        .find("\n            WindowMsg::")
        .map(|i| i + 1)
        .unwrap_or(tail.len());
    let body = &tail[..end];

    assert!(
        body.contains("path.exists()"),
        "NativeSaveAsPath handler must call path.exists() to detect existing files (F5)"
    );
}

#[test]
fn confirm_save_as_overwrite_message_exists() {
    let src = read_main();
    assert!(
        src.contains("ConfirmSaveAsOverwrite"),
        "Message::ConfirmSaveAsOverwrite must be defined in the Message enum (F5)"
    );
}

/// Regression pin for the SaveAndOpenFile → Save As → NativeSaveComplete(error) path.
///
/// When Save As fails (CURRENT_PATH=None path), `pending_open_file` is still set but
/// `confirm_dialog` has been cleared. Without restoring the dialog the next Open skips
/// the F4 dirty-check (the condition is `is_dirty() && pending_open_file.is_none()`).
#[test]
fn native_save_complete_error_restores_confirm_dialog_when_pending_open_file_set() {
    let src = read_main();

    // Locate the NativeSaveComplete error arm (Some(kind) branch).
    let arm_prefix = "                    Some(kind) => {";
    let start = src
        .find(arm_prefix)
        .expect("NativeSaveComplete Some(kind) arm must exist");
    let tail = &src[start..];
    // Grab enough context — the arm ends before the next top-level match arm.
    let end = tail
        .find("\n                    None =>")
        .or_else(|| tail.find("\n                }"))
        .unwrap_or(tail.len().min(2000));
    let body = &tail[..end];

    assert!(
        body.contains("pending_open_file.is_some()"),
        "NativeSaveComplete error branch must check pending_open_file.is_some() \
         to detect an in-flight SaveAndOpenFile flow"
    );
    assert!(
        body.contains("confirm_dialog = Some("),
        "NativeSaveComplete error branch must restore confirm_dialog when \
         pending_open_file is set, otherwise the next Open bypasses F4"
    );
    assert!(
        body.contains("DiscardAndOpenFile"),
        "restored confirm dialog must wire the Discard action to DiscardAndOpenFile"
    );
    assert!(
        body.contains("SaveAndOpenFile"),
        "restored confirm dialog must wire the Save action to SaveAndOpenFile"
    );
}

#[test]
fn confirm_save_as_overwrite_handler_proceeds_with_save() {
    let src = read_main();
    // Find handler arm in update() by finding the => pattern.
    // The variant now carries a path field: ConfirmSaveAsOverwrite { path }.
    let handler_prefix = "            WindowMsg::ConfirmSaveAsOverwrite {";
    let hstart = src
        .find(handler_prefix)
        .expect("ConfirmSaveAsOverwrite handler arm must exist in update()");
    let htail = &src[hstart..];
    let hend = htail[1..]
        .find("\n            WindowMsg::")
        .map(|i| i + 1)
        .unwrap_or(htail.len());
    let hbody = &htail[..hend];

    assert!(
        hbody.contains("NativeSaveAsWithSpecs") || hbody.contains("collect_window_specs"),
        "ConfirmSaveAsOverwrite handler must proceed with the save operation (F5)"
    );
}
