//! Structural regression pins for F5: Save As overwrite confirmation.
//!
//! Pins the invariants documented in `fix-save-menu.md §F5-DoD`:
//! - `NativeSaveAsPath(Some(path))` checks whether the target file already exists
//! - When the file exists, a confirm dialog is shown before overwriting
//! - `Message::ConfirmSaveAsOverwrite` carries the confirm action

fn read_main() -> String {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/main.rs");
    std::fs::read_to_string(path).expect("failed to read src/main.rs")
}

/// Entry-point test matching `cargo test save_as_overwrite_confirm`.
/// Combines all F5 assertions into one discoverable test name.
#[test]
fn save_as_overwrite_confirm() {
    let src = read_main();

    // 1. NativeSaveAsPath(Some(path)) handler must check path existence.
    let arm_prefix = "            Message::NativeSaveAsPath(Some(path)) =>";
    let start = src
        .find(arm_prefix)
        .expect("NativeSaveAsPath(Some(path)) handler must exist");
    let tail = &src[start..];
    let end = tail[1..]
        .find("\n            Message::")
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
    let arm_prefix = "            Message::NativeSaveAsPath(Some(path)) =>";
    let start = src
        .find(arm_prefix)
        .expect("NativeSaveAsPath(Some(path)) handler must exist");
    let tail = &src[start..];
    let end = tail[1..]
        .find("\n            Message::")
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

#[test]
fn confirm_save_as_overwrite_handler_proceeds_with_save() {
    let src = read_main();
    // Find handler arm in update() by finding the => pattern.
    // The variant now carries a path field: ConfirmSaveAsOverwrite { path }.
    let handler_prefix = "            Message::ConfirmSaveAsOverwrite {";
    let hstart = src
        .find(handler_prefix)
        .expect("ConfirmSaveAsOverwrite handler arm must exist in update()");
    let htail = &src[hstart..];
    let hend = htail[1..]
        .find("\n            Message::")
        .map(|i| i + 1)
        .unwrap_or(htail.len());
    let hbody = &htail[..hend];

    assert!(
        hbody.contains("NativeSaveAsWithSpecs") || hbody.contains("collect_window_specs"),
        "ConfirmSaveAsOverwrite handler must proceed with the save operation (F5)"
    );
}
