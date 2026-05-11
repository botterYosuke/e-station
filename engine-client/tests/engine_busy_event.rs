//! H-WS1 + H-WS2: `EngineBusy`, `ClientConnected`, `ClientDisconnected` の
//! デシリアライズテスト。
//!
//! Python `schemas.py` の wire 形式（`event` タグ付き JSON）から
//! `EngineEvent` に正しく変換できることを確認する。

use flowsurface_engine_client::dto::{AttemptedCommand, BusyKind, CurrentEngineState, EngineEvent};

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
            request_id,
            venue,
            busy_kind,
        } => {
            assert_eq!(current_state, CurrentEngineState::Running);
            assert_eq!(attempted_command, AttemptedCommand::LoadReplayData);
            assert_eq!(reason, "already running");
            // MEDIUM-R2-6: 旧 wire (`request_id` フィールド未送出) は
            // `Option<String>` の `None` にデシリアライズされる (後方互換)。
            assert!(
                request_id.is_none(),
                "old wire format should deserialize as None"
            );
            // issue #42 Phase 3 (schema 3.28): 旧 wire (venue / busy_kind 未送出) は
            // 両フィールドとも `None` にデシリアライズされる (後方互換)。
            assert!(venue.is_none(), "old wire: venue must be None");
            assert!(busy_kind.is_none(), "old wire: busy_kind must be None");
        }
        other => panic!("expected EngineBusy, got {other:?}"),
    }
}

#[test]
fn engine_busy_with_request_id_deserializes() {
    // MEDIUM-R2-6: Python schemas.py に追加された Optional `request_id` を
    // Rust 側でも受領できることを保証する。helper attach client が
    // 「自分の Command への reject」を識別するために使うフィールド。
    let json = r#"{
        "event": "EngineBusy",
        "current_state": "RUNNING",
        "attempted_command": "LoadReplayData",
        "reason": "wrong state",
        "request_id": "rid-abc-123"
    }"#;
    let evt: EngineEvent = serde_json::from_str(json).expect("should deserialize");
    match evt {
        EngineEvent::EngineBusy { request_id, .. } => {
            assert_eq!(request_id.as_deref(), Some("rid-abc-123"));
        }
        other => panic!("expected EngineBusy, got {other:?}"),
    }
}

#[test]
fn engine_busy_without_request_id_is_none() {
    // MEDIUM-R2-6: 旧 wire (request_id 未送出) は `None` にデシリアライズされる。
    let json = r#"{
        "event": "EngineBusy",
        "current_state": "RUNNING",
        "attempted_command": "LoadReplayData",
        "reason": "wrong state"
    }"#;
    let evt: EngineEvent = serde_json::from_str(json).expect("should deserialize");
    match evt {
        EngineEvent::EngineBusy { request_id, .. } => assert!(request_id.is_none()),
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

// ── R2-B H8: BusyKind enum 受け入れ ─────────────────────────────────────────
//
// `EngineBusy.busy_kind` を `Option<String>` から `Option<BusyKind>` へ enum 化。
// 既知の wire 値 ("another_strategy_on_venue") は enum variant にデシリアライズし、
// 未知値 / 不在は `None` にフォールバックする（後方互換）。

#[test]
fn engine_busy_with_known_busy_kind_deserializes_to_enum() {
    let json = r#"{
        "event": "EngineBusy",
        "current_state": "TRADING",
        "attempted_command": "StartEngine",
        "reason": "venue tachibana is hosting another strategy",
        "venue": "tachibana",
        "busy_kind": "another_strategy_on_venue"
    }"#;
    let evt: EngineEvent = serde_json::from_str(json).expect("should deserialize");
    match evt {
        EngineEvent::EngineBusy {
            venue, busy_kind, ..
        } => {
            assert_eq!(venue.as_deref(), Some("tachibana"));
            assert_eq!(busy_kind, Some(BusyKind::AnotherStrategyOnVenue));
        }
        other => panic!("expected EngineBusy, got {other:?}"),
    }
}

#[test]
fn engine_busy_with_unknown_busy_kind_falls_back_none() {
    // R2-B H8: 未知の busy_kind は `None` にフォールバック（forward compat）。
    let json = r#"{
        "event": "EngineBusy",
        "current_state": "TRADING",
        "attempted_command": "StartEngine",
        "reason": "future busy reason",
        "venue": "tachibana",
        "busy_kind": "future_unknown_kind"
    }"#;
    let evt: EngineEvent = serde_json::from_str(json).expect("should deserialize");
    match evt {
        EngineEvent::EngineBusy { busy_kind, .. } => {
            assert!(
                busy_kind.is_none(),
                "未知 busy_kind は None にフォールバックすべき: {busy_kind:?}"
            );
        }
        other => panic!("expected EngineBusy, got {other:?}"),
    }
}

#[test]
fn engine_busy_without_busy_kind_is_none() {
    // R2-B H8: busy_kind 未送出（旧 wire）は `None`。
    let json = r#"{
        "event": "EngineBusy",
        "current_state": "RUNNING",
        "attempted_command": "LoadReplayData",
        "reason": "wrong state"
    }"#;
    let evt: EngineEvent = serde_json::from_str(json).expect("should deserialize");
    match evt {
        EngineEvent::EngineBusy { busy_kind, .. } => assert!(busy_kind.is_none()),
        other => panic!("expected EngineBusy, got {other:?}"),
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
