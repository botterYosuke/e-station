//! H-2 (+ R1 review): FormMsg::Submit state-consistency regression guards.
//!
//! Source-scanning checks that complement the behavioural unit tests in
//! `src/handlers/replay.rs::tests` (`submit_result_to_message` covers the
//! Load-fail / Start-fail dispatch). These scans guard the *structure* of the
//! Submit arm — the connection-missing toast, the deferred modal clear, the
//! partial-failure differentiation — that pure unit tests cannot reach.

const HANDLER_REPLAY: &str = include_str!("../src/handlers/replay.rs");

fn submit_arm() -> &'static str {
    let submit_start = HANDLER_REPLAY
        .find("Some(modal::replay_form::Action::Submit {")
        .expect("Submit action not found");
    let rest = &HANDLER_REPLAY[submit_start..];
    // Slice to the next Some(...) arm or 3000 chars, whichever comes first.
    let next_arm = rest[1..]
        .find("Some(modal::replay_form::Action::")
        .map(|i| i + 1)
        .unwrap_or(rest.len());
    // The Submit arm grew when partial-failure handling was added; cap at 6000
    // chars (well above the current size, well below the next arm boundary).
    &rest[..next_arm.min(6000)]
}

#[test]
fn replay_bar_updates_only_after_ipc_success() {
    // H-2: menu_bar updates must live in the CommitReplayBarState handler,
    // not in the Submit arm.
    let arm = submit_arm();
    assert!(
        arm.contains("Task::perform"),
        "Submit arm should contain Task::perform"
    );
    assert!(
        !arm.contains("self.menu_bar.replay_bar.instrument_id"),
        "Menu_bar updates should NOT be in Submit arm (moved to CommitReplayBarState)"
    );
    assert!(
        arm.contains("submit_result_to_message"),
        "Submit arm should dispatch via submit_result_to_message on Task completion"
    );
    assert!(
        HANDLER_REPLAY.contains("ReplayMsg::CommitReplayBarState"),
        "Handler must have CommitReplayBarState variant"
    );
}

#[test]
fn missing_engine_connection_emits_toast_and_keeps_modal() {
    // M2 regression: a missing engine connection used to silently fall through
    // (modal cleared, no toast, user input lost). The Submit arm now must:
    //   1. Check `engine_connection` BEFORE clearing the modal
    //   2. Emit an OrderToast in the no-connection branch
    let arm = submit_arm();

    // The let-else / if-let no-connection guard should appear before
    // `self.replay_form_modal = None`.
    let connection_check = arm
        .find("self.engine_connection")
        .expect("Submit arm must reference self.engine_connection");
    let modal_clear = arm
        .find("self.replay_form_modal = None")
        .expect("Submit arm must clear the modal at some point");
    assert!(
        connection_check < modal_clear,
        "engine_connection must be checked BEFORE the modal is cleared (M2). \
         connection_check={connection_check} modal_clear={modal_clear}"
    );

    // The no-connection branch must surface a toast (not silently drop input).
    // We anchor on `Task::done(...OrderToast` appearing before the
    // `self.replay_form_modal = None` line so we know the toast belongs to the
    // no-connection guard rather than some unrelated downstream branch.
    let no_conn_window = &arm[..modal_clear];
    assert!(
        no_conn_window.contains("OrderToast"),
        "No-connection branch must emit an OrderToast before clearing the modal (M2). \
         Window scanned:\n{no_conn_window}"
    );
}

#[test]
fn submit_arm_differentiates_load_and_start_failures() {
    // M1 regression: the async block must distinguish Load-failure from
    // Start-failure so the UI can decide whether to commit the bar state.
    // The pure dispatch logic is unit-tested in
    // `src/handlers/replay.rs::tests::*`; this scan ensures the variants are
    // actually produced inside the async block.
    let arm = submit_arm();
    assert!(
        arm.contains("ReplaySubmitResult::LoadFailed"),
        "Submit async block must produce ReplaySubmitResult::LoadFailed on LoadReplayData error (M1)"
    );
    assert!(
        arm.contains("ReplaySubmitResult::StartFailed"),
        "Submit async block must produce ReplaySubmitResult::StartFailed on StartEngine error (M1)"
    );
    assert!(
        arm.contains("ReplaySubmitResult::BothOk"),
        "Submit async block must produce ReplaySubmitResult::BothOk when both IPC calls succeed (M1)"
    );
}

#[test]
fn commit_replay_bar_state_handler_surfaces_start_error_toast() {
    // M1 regression: CommitReplayBarState carries an optional start_error;
    // when present, the handler must surface a toast so the user is informed
    // that the engine did not start even though the replay was loaded.
    assert!(
        HANDLER_REPLAY.contains("start_error"),
        "CommitReplayBarState handler must reference start_error field"
    );
    // Locate the handler match arm by scanning the production code only
    // (slice off the trailing `#[cfg(test)] mod tests` block). The handler
    // arm is followed by `} => {` and mutates `self.menu_bar.replay_bar`.
    let prod_end = HANDLER_REPLAY
        .find("#[cfg(test)]")
        .unwrap_or(HANDLER_REPLAY.len());
    let production = &HANDLER_REPLAY[..prod_end];
    let handler_idx = production
        .rfind("ReplayMsg::CommitReplayBarState {")
        .expect("CommitReplayBarState handler arm not found in production code");
    let handler_end = handler_idx.saturating_add(2000).min(production.len());
    let handler_window = &production[handler_idx..handler_end];
    assert!(
        handler_window.contains("self.menu_bar.replay_bar.instrument_id"),
        "Located block does not look like the handler arm — it should mutate menu_bar.replay_bar"
    );
    assert!(
        handler_window.contains("if let Some(err) = start_error"),
        "CommitReplayBarState handler must branch on start_error.is_some()"
    );
    assert!(
        handler_window.contains("OrderToast"),
        "CommitReplayBarState handler must emit OrderToast when start_error is Some"
    );
}
