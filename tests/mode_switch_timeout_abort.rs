//! Structural regression pins for F7: timeout / abort / send-failure behavior.
//!
//! These source-inspection tests verify that the replay→live async error paths
//! in `src/main.rs` maintain the key invariants documented in
//! `docs/architecture/modules/ui-shell/P7-mode-switch-menu.md`:
//!
//! - Stale timeout messages are silently ignored (pending_mode_switch guard).
//! - Error dialogs are shown on ForceStopReplay timeout and send failure.
//! - The ModeSwitchGuard is always released before the abort returns.
//! - ModeSwitchSendFailed aborts immediately without waiting for the timeout.

fn read_main() -> String {
    let base = env!("CARGO_MANIFEST_DIR");
    let main =
        std::fs::read_to_string(format!("{base}/src/main.rs")).expect("failed to read src/main.rs");
    let window =
        std::fs::read_to_string(format!("{base}/src/handlers/window.rs")).unwrap_or_default();
    let replay =
        std::fs::read_to_string(format!("{base}/src/handlers/replay.rs")).unwrap_or_default();
    format!("{main}\n{window}\n{replay}").replace("\r\n", "\n")
}

/// Extract the handler arm body for a given Message::Window(WindowMsg::...) variant.
/// Searches for `Message::Window(WindowMsg::<name>) =>` (the match arm, not a `|_| Message::` mapping)
/// and returns content up to the next top-level `Message::` arm.
fn handler_body(src: &str, variant: &str) -> String {
    let marker = format!("WindowMsg::{variant} =>");
    let start = src
        .find(&marker)
        .unwrap_or_else(|| panic!("handler `{marker}` not found in src/main.rs"));
    let after = &src[start..];
    // Next top-level arm (12-space indent before `Message::`)
    let end = after[1..]
        .find("\n            WindowMsg::")
        .map(|i| i + 1)
        .unwrap_or(after.len());
    after[..end].to_string()
}

/// Safe byte-based substring — avoids UTF-8 boundary panics.
fn safe_contains_near(src: &str, start_marker: &str, needle: &str, window: usize) -> bool {
    let Some(pos) = src.find(start_marker) else {
        return false;
    };
    let end = src.len().min(pos + window);
    // Walk back from end to a char boundary
    let mut safe_end = end;
    while safe_end > pos && !src.is_char_boundary(safe_end) {
        safe_end -= 1;
    }
    src[pos..safe_end].contains(needle)
}

// ── ModeSwitchStopTimeout ─────────────────────────────────────────────────

#[test]
fn stop_timeout_handler_exists() {
    let src = read_main();
    assert!(
        src.contains("WindowMsg::ModeSwitchStopTimeout =>"),
        "WindowMsg::ModeSwitchStopTimeout handler arm must exist"
    );
}

#[test]
fn stop_timeout_ignores_stale_pending_none() {
    let src = read_main();
    let body = handler_body(&src, "ModeSwitchStopTimeout");
    assert!(
        body.contains("mode_switch_state.is_none()"),
        "ModeSwitchStopTimeout must check mode_switch_state.is_none() to ignore stale messages (M13)"
    );
}

#[test]
fn stop_timeout_sends_force_stop_replay() {
    let src = read_main();
    let body = handler_body(&src, "ModeSwitchStopTimeout");
    assert!(
        body.contains("ForceStopReplay"),
        "ModeSwitchStopTimeout must send ForceStopReplay as fallback"
    );
}

// ── ModeSwitchForceStopTimeout ────────────────────────────────────────────

#[test]
fn force_stop_timeout_handler_exists() {
    let src = read_main();
    assert!(
        src.contains("WindowMsg::ModeSwitchForceStopTimeout =>"),
        "WindowMsg::ModeSwitchForceStopTimeout handler arm must exist"
    );
}

#[test]
fn force_stop_timeout_ignores_stale() {
    let src = read_main();
    let body = handler_body(&src, "ModeSwitchForceStopTimeout");
    assert!(
        body.contains("mode_switch_state.take().is_none()"),
        "ModeSwitchForceStopTimeout must use mode_switch_state.take().is_none() to ignore stale (M13)"
    );
}

#[test]
fn force_stop_timeout_releases_guard() {
    let src = read_main();
    let body = handler_body(&src, "ModeSwitchForceStopTimeout");
    // M13: guard release happens implicitly via mode_switch_state.take() (Drop on the tuple);
    // assert that the take() pattern is used instead of two separate field clears.
    assert!(
        body.contains("mode_switch_state.take()"),
        "ModeSwitchForceStopTimeout must consume mode_switch_state via take() to release guard (M13)"
    );
}

#[test]
fn force_stop_timeout_shows_error_dialog() {
    let src = read_main();
    let body = handler_body(&src, "ModeSwitchForceStopTimeout");
    assert!(
        body.contains("ConfirmDialog::new"),
        "ModeSwitchForceStopTimeout must show an error ConfirmDialog"
    );
    assert!(
        body.contains("confirm_dialog = Some"),
        "ModeSwitchForceStopTimeout must assign the dialog to self.confirm_dialog"
    );
}

// ── ModeSwitchSendFailed ──────────────────────────────────────────────────

#[test]
fn send_failed_message_variant_exists() {
    let src = read_main();
    assert!(
        src.contains("ModeSwitchSendFailed"),
        "ModeSwitchSendFailed must exist in src/main.rs"
    );
}

#[test]
fn send_failed_handler_exists() {
    let src = read_main();
    assert!(
        src.contains("WindowMsg::ModeSwitchSendFailed =>"),
        "Message::Window(WindowMsg::ModeSwitchSendFailed) handler arm must exist in update()"
    );
}

#[test]
fn stop_replay_send_routes_err_to_send_failed() {
    let src = read_main();
    // Use dto::Command::StopReplay to avoid matching AttemptedCommand::StopReplay.
    assert!(
        safe_contains_near(
            &src,
            "dto::Command::StopReplay",
            "ModeSwitchSendFailed",
            500
        ),
        "StopReplay send task must route Err(_) to Message::ModeSwitchSendFailed"
    );
}

#[test]
fn force_stop_replay_send_routes_err_to_send_failed() {
    let src = read_main();
    // Use dto::Command::ForceStopReplay to avoid matching AttemptedCommand::ForceStopReplay.
    assert!(
        safe_contains_near(
            &src,
            "dto::Command::ForceStopReplay",
            "ModeSwitchSendFailed",
            500
        ),
        "ForceStopReplay send task must route Err(_) to Message::ModeSwitchSendFailed"
    );
}

#[test]
fn send_failed_releases_guard_and_shows_dialog() {
    let src = read_main();
    let body = handler_body(&src, "ModeSwitchSendFailed");
    // M13: guard release implicit via mode_switch_state.take().
    assert!(
        body.contains("mode_switch_state.take()"),
        "ModeSwitchSendFailed must consume mode_switch_state via take() to release guard (M13)"
    );
    assert!(
        body.contains("ConfirmDialog::new"),
        "ModeSwitchSendFailed must show an error ConfirmDialog"
    );
}

// ── ModeSwitchEngineBusy ──────────────────────────────────────────────────

#[test]
fn engine_busy_handler_ignores_stale() {
    let src = read_main();
    // Handler arm is `Message::ModeSwitchEngineBusy(reason) =>` — search with the parameter.
    let body = handler_body(&src, "ModeSwitchEngineBusy(reason)");
    assert!(
        body.contains("mode_switch_state.take().is_some()"),
        "ModeSwitchEngineBusy must use mode_switch_state.take().is_some() to ignore stale events (M13)"
    );
}

#[test]
fn engine_busy_dispatch_limited_to_stop_replay_commands() {
    let src = read_main();
    let busy_start = src
        .find("EngineEvent::EngineBusy {")
        .expect("EngineEvent::EngineBusy dispatch must exist");
    let after = &src[busy_start..];
    // NOTE: このテストは src/main.rs のテキスト内容を直接検査する source-inspection テスト。
    // map_engine_event_to_message の構造が大幅に変わった場合は終端文字列も更新が必要。
    // End at the closing `}` of the match arm (next `EngineEvent::` or `_ =>` line)
    let end = after[1..]
        .find("\n        EngineEvent::")
        .or_else(|| after[1..].find("\n        _ =>"))
        .map(|i| i + 1)
        .unwrap_or(after.len());
    let body = &after[..end];
    // NOTE: このチェックは `src/main.rs` の EngineBusy dispatch body（`_ =>` までの範囲）内に
    // 各 variant の文字列が存在することを確認する。コメントでの残存リスクを避けるため、
    // body の終端を `_ =>` で区切っている。
    assert!(
        body.contains("AttemptedCommand::StopReplay")
            && body.contains("AttemptedCommand::ForceStopReplay")
            && body.contains("AttemptedCommand::PauseReplay")
            && body.contains("AttemptedCommand::ResumeReplay"),
        "EngineBusy dispatch must match AttemptedCommand::StopReplay | ForceStopReplay | PauseReplay | ResumeReplay"
    );
    assert!(
        !body.contains("MODE_SWITCHING"),
        "EngineBusy dispatch must NOT check MODE_SWITCHING (too broad)"
    );
}

// ── H1: stale early-return must release the guard ────────────────────────

#[test]
fn discard_switch_mode_stale_releases_guard() {
    let src = read_main();
    let body = handler_body(&src, "DiscardAndSwitchMode");
    // H1: the early-return path consumes mode_switch_state via take(), which
    // drops the embedded ModeSwitchGuard and releases MODE_SWITCHING.
    assert!(
        body.contains("mode_switch_state.take()"),
        "DiscardAndSwitchMode must consume mode_switch_state via take() so the \
         guard is released even on the stale early-return path (H1)"
    );
}

#[test]
fn save_switch_mode_stale_releases_guard() {
    let src = read_main();
    let body = handler_body(&src, "SaveAndSwitchMode");
    // H1: SaveAndSwitchMode must reference mode_switch_state to detect a stale
    // dialog firing. The guard is preserved across the async window-spec
    // collection by reading without taking.
    assert!(
        body.contains("mode_switch_state"),
        "SaveAndSwitchMode must read mode_switch_state to detect stale dialog \
         firing (H1)"
    );
}

// ── M4: warn-level logs on timeouts ───────────────────────────────────────

#[test]
fn stop_timeout_emits_warn_log() {
    let src = read_main();
    let body = handler_body(&src, "ModeSwitchStopTimeout");
    assert!(
        body.contains("log::warn!") && body.contains("[F7] StopReplay timed out"),
        "ModeSwitchStopTimeout must log::warn! on entry for ops observability (M4)"
    );
}

#[test]
fn force_stop_timeout_emits_warn_log() {
    let src = read_main();
    let body = handler_body(&src, "ModeSwitchForceStopTimeout");
    assert!(
        body.contains("log::warn!") && body.contains("[F7] ForceStopReplay also timed out"),
        "ModeSwitchForceStopTimeout must log::warn! on entry for ops observability (M4)"
    );
}

// ── M-rust2: SwitchModeWithSpecs save-fail shows modal dialog ─────────────

#[test]
fn switch_mode_with_specs_save_fail_shows_dialog() {
    // M-rust2: parity with `SwitchModeSaveComplete` (M5). The non-dirty path
    // (`SwitchModeWithSpecs` after save_state_to_disk failure) must surface a
    // modal `ConfirmDialog::new` alert, not just a Toast — toasts auto-dismiss
    // and the user can miss the abort cause.
    let src = read_main();
    let body = handler_body(&src, "SwitchModeWithSpecs { target, windows }");
    assert!(
        body.contains("save_state_to_disk"),
        "handler must call save_state_to_disk to detect failure"
    );
    assert!(
        body.contains("ConfirmDialog::new"),
        "save-fail path must surface a modal ConfirmDialog (M-rust2 parity with M5)"
    );
    assert!(
        body.contains("self.confirm_dialog = Some"),
        "save-fail path must assign self.confirm_dialog (M-rust2)"
    );
}

#[test]
fn switch_mode_save_complete_save_fail_shows_dialog() {
    // M5 (existing) — also pinned here so review-fix-loop can scan one file.
    let src = read_main();
    let body = handler_body(&src, "SwitchModeSaveComplete { target, windows }");
    assert!(
        body.contains("save_state_to_disk") && body.contains("ConfirmDialog::new"),
        "SwitchModeSaveComplete save-fail path must surface a modal alert (M5)"
    );
}
