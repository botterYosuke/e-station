//! H-WS1 + H-WS2: `EngineBusy`, `ClientConnected`, `ClientDisconnected` の
//! デシリアライズテスト。
//!
//! Python `schemas.py` の wire 形式（`event` タグ付き JSON）から
//! `EngineEvent` に正しく変換できることを確認する。

use flowsurface_engine_client::dto::EngineEvent;

// ── EngineBusy ───────────────────────────────────────────────────────────────

#[test]
fn engine_busy_deserializes_correctly() {
    let json = r#"{
        "event": "EngineBusy",
        "current_state": "RUNNING",
        "attempted_command": "LoadReplayData",
        "reason": "already running"
    }"#;
    let evt: EngineEvent = serde_json::from_str(json).expect("EngineBusy should deserialize");
    match evt {
        EngineEvent::EngineBusy {
            current_state,
            attempted_command,
            reason,
        } => {
            assert_eq!(current_state, "RUNNING");
            assert_eq!(attempted_command, "LoadReplayData");
            assert_eq!(reason, "already running");
        }
        other => panic!("expected EngineBusy, got {other:?}"),
    }
}

#[test]
fn engine_busy_all_states_accepted() {
    // schemas.py の Literal union に含まれる全ステート値を受け付けることを確認
    for state in &[
        "IDLE",
        "LOADED",
        "RUNNING",
        "STOPPING",
        "DISCONNECTED",
        "CONNECTING",
        "CONNECTED",
    ] {
        let json = format!(
            r#"{{
                "event": "EngineBusy",
                "current_state": "{state}",
                "attempted_command": "StopEngine",
                "reason": "state guard"
            }}"#
        );
        let evt: EngineEvent =
            serde_json::from_str(&json).expect("EngineBusy state should deserialize");
        assert!(
            matches!(evt, EngineEvent::EngineBusy { .. }),
            "state={state}"
        );
    }
}

// ── ClientConnected ──────────────────────────────────────────────────────────

#[test]
fn client_connected_deserializes_correctly() {
    let json = r#"{
        "event": "ClientConnected",
        "count": 2
    }"#;
    let evt: EngineEvent = serde_json::from_str(json).expect("ClientConnected should deserialize");
    match evt {
        EngineEvent::ClientConnected { count } => {
            assert_eq!(count, 2);
        }
        other => panic!("expected ClientConnected, got {other:?}"),
    }
}

#[test]
fn client_connected_count_zero() {
    let json = r#"{"event": "ClientConnected", "count": 0}"#;
    let evt: EngineEvent = serde_json::from_str(json).unwrap();
    assert!(matches!(evt, EngineEvent::ClientConnected { count: 0 }));
}

// ── ClientDisconnected ───────────────────────────────────────────────────────

#[test]
fn client_disconnected_deserializes_correctly() {
    let json = r#"{
        "event": "ClientDisconnected",
        "count": 1
    }"#;
    let evt: EngineEvent =
        serde_json::from_str(json).expect("ClientDisconnected should deserialize");
    match evt {
        EngineEvent::ClientDisconnected { count } => {
            assert_eq!(count, 1);
        }
        other => panic!("expected ClientDisconnected, got {other:?}"),
    }
}

#[test]
fn client_disconnected_count_zero() {
    let json = r#"{"event": "ClientDisconnected", "count": 0}"#;
    let evt: EngineEvent = serde_json::from_str(json).unwrap();
    assert!(matches!(evt, EngineEvent::ClientDisconnected { count: 0 }));
}
