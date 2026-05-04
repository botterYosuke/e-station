//! Structural regression pins for F7: timeout / abort / send-failure behavior.
//!
//! These source-inspection tests verify that the replay→live async error paths
//! in `src/main.rs` maintain the key invariants documented in
//! `docs/✅menu-and-footer/P7-mode-switch-menu.md`:
//!
//! - Stale timeout messages are silently ignored (pending_mode_switch guard).
//! - Error dialogs are shown on ForceStopReplay timeout and send failure.
//! - The ModeSwitchGuard is always released before the abort returns.
//! - ModeSwitchSendFailed aborts immediately without waiting for the timeout.

fn read_main() -> String {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/main.rs");
    std::fs::read_to_string(path)
        .expect("failed to read src/main.rs")
        .replace("\r\n", "\n")
}

/// Extract the handler arm body for a given Message variant.
/// Searches for `Message::<name> =>` (the match arm, not a `|_| Message::` mapping)
/// and returns content up to the next top-level `Message::` arm.
fn handler_body(src: &str, variant: &str) -> String {
    let marker = format!("Message::{variant} =>");
    let start = src
        .find(&marker)
        .unwrap_or_else(|| panic!("handler `{marker}` not found in src/main.rs"));
    let after = &src[start..];
    // Next top-level arm (12-space indent before `Message::`)
    let end = after[1..]
        .find("\n            Message::")
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
        src.contains("Message::ModeSwitchStopTimeout =>"),
        "Message::ModeSwitchStopTimeout handler arm must exist"
    );
}

#[test]
fn stop_timeout_ignores_stale_pending_none() {
    let src = read_main();
    let body = handler_body(&src, "ModeSwitchStopTimeout");
    assert!(
        body.contains("pending_mode_switch.is_none()"),
        "ModeSwitchStopTimeout must check pending_mode_switch.is_none() to ignore stale messages"
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
        src.contains("Message::ModeSwitchForceStopTimeout =>"),
        "Message::ModeSwitchForceStopTimeout handler arm must exist"
    );
}

#[test]
fn force_stop_timeout_ignores_stale() {
    let src = read_main();
    let body = handler_body(&src, "ModeSwitchForceStopTimeout");
    assert!(
        body.contains("pending_mode_switch.take().is_none()"),
        "ModeSwitchForceStopTimeout must use pending_mode_switch.take().is_none() to ignore stale"
    );
}

#[test]
fn force_stop_timeout_releases_guard() {
    let src = read_main();
    let body = handler_body(&src, "ModeSwitchForceStopTimeout");
    assert!(
        body.contains("_mode_switch_guard = None"),
        "ModeSwitchForceStopTimeout must release _mode_switch_guard before returning"
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
        src.contains("ModeSwitchSendFailed,"),
        "ModeSwitchSendFailed variant must exist in the Message enum"
    );
}

#[test]
fn send_failed_handler_exists() {
    let src = read_main();
    assert!(
        src.contains("Message::ModeSwitchSendFailed =>"),
        "Message::ModeSwitchSendFailed handler arm must exist in update()"
    );
}

#[test]
fn stop_replay_send_routes_err_to_send_failed() {
    let src = read_main();
    // Use dto::Command::StopReplay to avoid matching AttemptedCommand::StopReplay.
    assert!(
        safe_contains_near(&src, "dto::Command::StopReplay", "ModeSwitchSendFailed", 500),
        "StopReplay send task must route Err(_) to Message::ModeSwitchSendFailed"
    );
}

#[test]
fn force_stop_replay_send_routes_err_to_send_failed() {
    let src = read_main();
    // Use dto::Command::ForceStopReplay to avoid matching AttemptedCommand::ForceStopReplay.
    assert!(
        safe_contains_near(&src, "dto::Command::ForceStopReplay", "ModeSwitchSendFailed", 500),
        "ForceStopReplay send task must route Err(_) to Message::ModeSwitchSendFailed"
    );
}

#[test]
fn send_failed_releases_guard_and_shows_dialog() {
    let src = read_main();
    let body = handler_body(&src, "ModeSwitchSendFailed");
    assert!(
        body.contains("_mode_switch_guard = None"),
        "ModeSwitchSendFailed must release _mode_switch_guard"
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
        body.contains("pending_mode_switch.take().is_some()"),
        "ModeSwitchEngineBusy must use pending_mode_switch.take().is_some() to ignore stale events"
    );
}

#[test]
fn engine_busy_dispatch_limited_to_stop_replay_commands() {
    let src = read_main();
    let busy_start = src
        .find("EngineEvent::EngineBusy {")
        .expect("EngineEvent::EngineBusy dispatch must exist");
    let after = &src[busy_start..];
    // End at the closing `}` of the match arm (next `EngineEvent::` or `_ =>` line)
    let end = after[1..]
        .find("\n        EngineEvent::")
        .or_else(|| after[1..].find("\n        _ =>"))
        .map(|i| i + 1)
        .unwrap_or(after.len());
    let body = &after[..end];
    assert!(
        body.contains("AttemptedCommand::StopReplay")
            && body.contains("AttemptedCommand::ForceStopReplay"),
        "EngineBusy dispatch must match AttemptedCommand::StopReplay | ForceStopReplay"
    );
    assert!(
        !body.contains("MODE_SWITCHING"),
        "EngineBusy dispatch must NOT check MODE_SWITCHING (too broad)"
    );
}
