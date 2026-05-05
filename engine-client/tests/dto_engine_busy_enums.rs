//! Phase 8 review-fix-loop R1 / Phase 1 — M-Type4 RED→GREEN tests.
//!
//! `EngineBusy.current_state` / `attempted_command` を `String` から enum に
//! 格上げしたことで、未知値はデシリアライズ失敗することを保証する。

use flowsurface_engine_client::dto::{AttemptedCommand, CurrentEngineState, EngineEvent};

#[test]
fn engine_busy_unknown_state_fails() {
    let json = r#"{
        "event": "EngineBusy",
        "current_state": "MEGA_LOADED",
        "attempted_command": "LoadReplayData",
        "reason": "x"
    }"#;
    let result: Result<EngineEvent, _> = serde_json::from_str(json);
    assert!(result.is_err(), "unknown state should fail: {result:?}");
}

#[test]
fn engine_busy_unknown_command_fails() {
    let json = r#"{
        "event": "EngineBusy",
        "current_state": "RUNNING",
        "attempted_command": "FooBar",
        "reason": "x"
    }"#;
    let result: Result<EngineEvent, _> = serde_json::from_str(json);
    assert!(result.is_err(), "unknown command should fail: {result:?}");
}

#[test]
fn engine_busy_get_buying_power_command_accepted() {
    let json = r#"{
        "event": "EngineBusy",
        "current_state": "DISCONNECTED",
        "attempted_command": "GetBuyingPower",
        "reason": "not connected"
    }"#;
    let evt: EngineEvent = serde_json::from_str(json).expect("should deserialize");
    match evt {
        EngineEvent::EngineBusy {
            current_state,
            attempted_command,
            ..
        } => {
            assert_eq!(current_state, CurrentEngineState::Disconnected);
            assert_eq!(attempted_command, AttemptedCommand::GetBuyingPower);
        }
        other => panic!("expected EngineBusy, got {other:?}"),
    }
}

#[test]
fn engine_busy_get_positions_command_accepted() {
    let json = r#"{
        "event": "EngineBusy",
        "current_state": "DISCONNECTED",
        "attempted_command": "GetPositions",
        "reason": "x"
    }"#;
    let _: EngineEvent = serde_json::from_str(json).expect("should deserialize");
}

/// L-Rust1: M-Type4 の補強として、未知の `current_state` 文字列を渡すと
/// `serde_json::from_str` が `Err` を返すことを明示的に保証する。
/// `engine_busy_unknown_state_fails` と同じ意図だが、`UNKNOWN_STATE`
/// という露骨な未知値で「未知 = エラー」の契約を pinning する。
#[test]
fn engine_busy_unknown_state_string_rejected() {
    let json = r#"{
        "event": "EngineBusy",
        "current_state": "UNKNOWN_STATE",
        "attempted_command": "LoadReplayData",
        "reason": "x"
    }"#;
    let result: Result<EngineEvent, _> = serde_json::from_str(json);
    assert!(
        result.is_err(),
        "未知 state 文字列は拒否されること: {result:?}"
    );
}

#[test]
fn engine_busy_get_order_list_command_accepted() {
    let json = r#"{
        "event": "EngineBusy",
        "current_state": "DISCONNECTED",
        "attempted_command": "GetOrderList",
        "reason": "x"
    }"#;
    let _: EngineEvent = serde_json::from_str(json).expect("should deserialize");
}
