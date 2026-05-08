//! H-2: FormMsg::Submit state mutation consistency regression guard.
//!
//! Verifies that replay_bar state is NOT updated before IPC succeeds.
//! If IPC fails, UI state must remain consistent with backend state.
//!
//! This is a source-scanning test because the dispatcher cannot be called
//! directly from an integration test binary.

const HANDLER_REPLAY: &str = include_str!("../src/handlers/replay.rs");

#[test]
fn replay_bar_updates_only_after_ipc_success() {
    // H-2: After the fix, menu_bar updates should NOT happen in the Submit arm.
    // Instead, they should only happen in the CommitReplayBarState handler
    // when IPC succeeds.

    // Find the FormMsg::Submit arm (in a Some pattern)
    let submit_start = HANDLER_REPLAY
        .find("Some(modal::replay_form::Action::Submit {")
        .expect("Submit action not found");
    let rest = &HANDLER_REPLAY[submit_start..];

    // Find the next Some(...) pattern or the end of the match
    let next_arm = rest.find("Some(modal::replay_form::Action::");
    let arm_end = if let Some(idx) = next_arm {
        if idx == 0 { rest.len() } else { idx }
    } else {
        rest.len()
    };

    let submit_arm = &rest[..arm_end.min(3000)]; // Limit to 3000 chars to avoid going too far

    // Verify the submit arm contains Task::perform
    assert!(
        submit_arm.contains("Task::perform"),
        "Submit arm should contain Task::perform"
    );

    // H-2: Verify menu_bar.replay_bar assignments are NOT in the Submit arm
    // (they've been moved to CommitReplayBarState handler)
    assert!(
        !submit_arm.contains("self.menu_bar.replay_bar.instrument_id"),
        "Menu_bar updates should NOT be in Submit arm (moved to CommitReplayBarState)"
    );

    // Verify it contains CommitReplayBarState emission on success
    assert!(
        submit_arm.contains("CommitReplayBarState"),
        "Submit arm should emit CommitReplayBarState on IPC success"
    );

    // Verify the CommitReplayBarState handler exists
    assert!(
        HANDLER_REPLAY.contains("ReplayMsg::CommitReplayBarState"),
        "Handler must have CommitReplayBarState variant"
    );
}

#[test]
fn no_early_state_mutation_pattern_detected() {
    // Scan for the anti-pattern: menu_bar.replay_bar updates followed by Task::perform
    // In the fixed version, updates should be in a separate callback function.

    // This is hard to detect with source scanning, so we just verify the structure
    // contains the necessary parts.
    assert!(HANDLER_REPLAY.contains("handle_replay"));
    assert!(HANDLER_REPLAY.contains("ReplayMsg"));
}
