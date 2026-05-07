//! Roundtrip tests for replay control commands added in schema 3.15:
//! PauseReplay / ResumeReplay / StepReplay.

use flowsurface_engine_client::dto::Command;

// ── Schema version guard ────────────────────────────────────────────────────

#[test]
fn schema_minor_is_at_least_15_for_replay_control() {
    const {
        assert!(
            flowsurface_engine_client::SCHEMA_MINOR >= 15,
            "SCHEMA_MINOR must be >= 15 (replay control commands)"
        )
    };
}

// ── PauseReplay ─────────────────────────────────────────────────────────────

#[test]
fn pause_replay_serializes() {
    let cmd = Command::PauseReplay {
        request_id: "req-pause-01".to_string(),
    };
    let json = serde_json::to_string(&cmd).unwrap();
    assert!(json.contains(r#""op":"PauseReplay""#), "got: {json}");
    assert!(json.contains("req-pause-01"), "got: {json}");
}

#[test]
fn pause_replay_debug_shows_request_id() {
    let cmd = Command::PauseReplay {
        request_id: "req-pause-dbg".to_string(),
    };
    let s = format!("{cmd:?}");
    assert!(s.contains("PauseReplay"), "got: {s}");
    assert!(s.contains("req-pause-dbg"), "got: {s}");
}

// ── ResumeReplay ─────────────────────────────────────────────────────────────

#[test]
fn resume_replay_serializes() {
    let cmd = Command::ResumeReplay {
        request_id: "req-resume-01".to_string(),
    };
    let json = serde_json::to_string(&cmd).unwrap();
    assert!(json.contains(r#""op":"ResumeReplay""#), "got: {json}");
    assert!(json.contains("req-resume-01"), "got: {json}");
}

#[test]
fn resume_replay_debug_shows_request_id() {
    let cmd = Command::ResumeReplay {
        request_id: "req-resume-dbg".to_string(),
    };
    let s = format!("{cmd:?}");
    assert!(s.contains("ResumeReplay"), "got: {s}");
    assert!(s.contains("req-resume-dbg"), "got: {s}");
}

// ── StepReplay ───────────────────────────────────────────────────────────────

#[test]
fn step_replay_serializes() {
    let cmd = Command::StepReplay {
        request_id: "req-step-fwd-01".to_string(),
    };
    let json = serde_json::to_string(&cmd).unwrap();
    assert!(json.contains(r#""op":"StepReplay""#), "got: {json}");
    assert!(json.contains("req-step-fwd-01"), "got: {json}");
}

#[test]
fn step_replay_debug_shows_request_id() {
    let cmd = Command::StepReplay {
        request_id: "req-step-fwd-dbg".to_string(),
    };
    let s = format!("{cmd:?}");
    assert!(s.contains("StepReplay"), "got: {s}");
    assert!(s.contains("req-step-fwd-dbg"), "got: {s}");
}

// ── StepBackward ─────────────────────────────────────────────────────────────

#[test]
fn step_backward_serializes() {
    let cmd = Command::StepBackward {
        request_id: "req-step-bwd-01".to_string(),
    };
    let json = serde_json::to_string(&cmd).unwrap();
    assert!(json.contains(r#""op":"StepBackward""#), "got: {json}");
    assert!(json.contains("req-step-bwd-01"), "got: {json}");
}

#[test]
fn step_backward_debug_shows_request_id() {
    let cmd = Command::StepBackward {
        request_id: "req-step-bwd-dbg".to_string(),
    };
    let s = format!("{cmd:?}");
    assert!(s.contains("StepBackward"), "got: {s}");
    assert!(s.contains("req-step-bwd-dbg"), "got: {s}");
}

// ── AttemptedCommand ─────────────────────────────────────────────────────────

#[test]
fn attempted_command_pause_replay_roundtrips() {
    use flowsurface_engine_client::dto::AttemptedCommand;
    let v = AttemptedCommand::PauseReplay;
    let json = serde_json::to_string(&v).unwrap();
    assert!(json.contains("PauseReplay"), "got: {json}");
    let back: AttemptedCommand = serde_json::from_str(&json).unwrap();
    assert_eq!(back, AttemptedCommand::PauseReplay);
    assert_eq!(v.as_wire_str(), "PauseReplay");
}

#[test]
fn attempted_command_resume_replay_roundtrips() {
    use flowsurface_engine_client::dto::AttemptedCommand;
    let v = AttemptedCommand::ResumeReplay;
    let json = serde_json::to_string(&v).unwrap();
    assert!(json.contains("ResumeReplay"), "got: {json}");
    let back: AttemptedCommand = serde_json::from_str(&json).unwrap();
    assert_eq!(back, AttemptedCommand::ResumeReplay);
    assert_eq!(v.as_wire_str(), "ResumeReplay");
}

#[test]
fn attempted_command_step_replay_roundtrips() {
    use flowsurface_engine_client::dto::AttemptedCommand;
    let v = AttemptedCommand::StepReplay;
    let json = serde_json::to_string(&v).unwrap();
    assert!(json.contains("StepReplay"), "got: {json}");
    let back: AttemptedCommand = serde_json::from_str(&json).unwrap();
    assert_eq!(back, AttemptedCommand::StepReplay);
    assert_eq!(v.as_wire_str(), "StepReplay");
}

#[test]
fn attempted_command_step_backward_roundtrips() {
    use flowsurface_engine_client::dto::AttemptedCommand;
    let v = AttemptedCommand::StepBackward;
    let json = serde_json::to_string(&v).unwrap();
    assert!(json.contains("StepBackward"), "got: {json}");
    let back: AttemptedCommand = serde_json::from_str(&json).unwrap();
    assert_eq!(back, AttemptedCommand::StepBackward);
    assert_eq!(v.as_wire_str(), "StepBackward");
}
