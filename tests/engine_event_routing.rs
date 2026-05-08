//! Structural regression pins: engine.rs arm existence for EngineBusy rollback variants.
//!
//! These source-inspection tests verify that `handle_engine` in `src/handlers/engine.rs`
//! contains the rollback arms for PauseReplayBusy and ResumeReplayBusy introduced in R2.

#[test]
fn engine_busy_pause_resume_replay_rollback_arm_exists_in_handler() {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src/handlers/engine.rs");
    let body = std::fs::read_to_string(&path).expect("failed to read src/handlers/engine.rs");
    assert!(
        body.contains("PauseReplayBusy"),
        "handle_engine must have PauseReplayBusy arm for rollback"
    );
    assert!(
        body.contains("ResumeReplayBusy"),
        "handle_engine must have ResumeReplayBusy arm for rollback"
    );

    // PauseReplayBusy セクション: PauseReplayBusy の位置から ResumeReplayBusy の位置まで
    let pause_busy_pos = body
        .find("PauseReplayBusy")
        .expect("PauseReplayBusy arm not found");
    let resume_busy_pos = body
        .find("ResumeReplayBusy")
        .expect("ResumeReplayBusy arm not found");
    let pause_arm_body = &body[pause_busy_pos..resume_busy_pos];
    assert!(
        pause_arm_body.contains("paused: false"),
        "PauseReplayBusy arm must roll back to paused=false"
    );

    // ResumeReplayBusy セクション: ResumeReplayBusy の位置から EngineMsg::Noop の位置まで
    let noop_pos = body
        .find("EngineMsg::Noop")
        .expect("EngineMsg::Noop arm not found");
    let resume_arm_body = &body[resume_busy_pos..noop_pos];
    assert!(
        resume_arm_body.contains("paused: true"),
        "ResumeReplayBusy arm must roll back to paused=true"
    );
}
