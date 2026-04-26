//! Tpre.2: Command::SetSecondPassword の Debug 出力が value を [REDACTED] にマスクすることを検証。
//! 他の Command variant の Debug 出力は変わらないことも確認する。

use flowsurface_engine_client::dto::Command;

#[test]
fn set_second_password_debug_redacts_value() {
    let cmd = Command::SetSecondPassword {
        request_id: "req-001".to_string(),
        value: "super_secret_password_123".to_string(),
    };
    let debug_str = format!("{:?}", cmd);
    assert!(
        !debug_str.contains("super_secret_password_123"),
        "value must be redacted, got: {debug_str}"
    );
    assert!(
        debug_str.contains("[REDACTED]"),
        "must show [REDACTED], got: {debug_str}"
    );
    assert!(
        debug_str.contains("req-001"),
        "request_id must remain visible, got: {debug_str}"
    );
}

#[test]
fn other_commands_debug_is_unchanged() {
    let cmd = Command::ForgetSecondPassword;
    let debug_str = format!("{:?}", cmd);
    assert!(
        debug_str.contains("ForgetSecondPassword"),
        "got: {debug_str}"
    );
}

#[test]
fn set_second_password_serializes_with_value() {
    let cmd = Command::SetSecondPassword {
        request_id: "req-abc".to_string(),
        value: "my_password".to_string(),
    };
    let json = serde_json::to_string(&cmd).unwrap();
    assert!(json.contains(r#""op":"SetSecondPassword""#), "got: {json}");
    assert!(
        json.contains("my_password"),
        "wire must carry value: {json}"
    );
    assert!(json.contains("req-abc"), "got: {json}");
}

#[test]
fn forget_second_password_serializes() {
    let cmd = Command::ForgetSecondPassword;
    let json = serde_json::to_string(&cmd).unwrap();
    assert!(
        json.contains(r#""op":"ForgetSecondPassword""#),
        "got: {json}"
    );
}

#[test]
fn second_password_required_event_deserializes() {
    use flowsurface_engine_client::dto::EngineEvent;
    let json = r#"{"event":"SecondPasswordRequired","request_id":"req-xyz"}"#;
    let ev: EngineEvent = serde_json::from_str(json).unwrap();
    match ev {
        EngineEvent::SecondPasswordRequired { request_id } => {
            assert_eq!(request_id, "req-xyz");
        }
        _ => panic!("expected SecondPasswordRequired, got {:?}", ev),
    }
}
