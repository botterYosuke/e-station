//! F6: SCENARIO 定数の IPC ラウンドトリップテスト。
//! LoadStrategyScenario / SaveStrategyScenario コマンドと
//! StrategyScenarioLoaded / StrategyScenarioSaved イベントの
//! JSON シリアライズ・デシリアライズを検証する。

use flowsurface_engine_client::dto::{Command, EngineEvent};
use flowsurface_engine_client::{SCHEMA_MAJOR, SCHEMA_MINOR};

/// SCHEMA_MINOR が期待値以上であることを確認するリグレッションガード。
#[test]
fn schema_minor_is_at_least_10() {
    assert_eq!(SCHEMA_MAJOR, 3, "SCHEMA_MAJOR must be 3");
    assert!(
        SCHEMA_MINOR >= 10,
        "SCHEMA_MINOR must be >= 10 (F6 schema bump)"
    );
}

/// LoadStrategyScenario コマンドが正しく JSON シリアライズされる。
#[test]
fn load_strategy_scenario_serializes() {
    let cmd = Command::LoadStrategyScenario {
        request_id: "req-1".to_string(),
        path: "/path/to/strategy.py".to_string(),
    };
    let json = serde_json::to_string(&cmd).unwrap();
    assert!(json.contains(r#""op":"LoadStrategyScenario""#));
    assert!(json.contains(r#""request_id":"req-1""#));
    assert!(json.contains(r#""path":"/path/to/strategy.py""#));
}

/// SaveStrategyScenario コマンドが正しく JSON シリアライズされる。
#[test]
fn save_strategy_scenario_serializes() {
    let scenario = serde_json::json!({
        "schema_version": 1,
        "instrument": "1301.TSE",
        "start": "2025-01-06",
        "end": "2025-03-31",
        "granularity": "1m",
        "initial_cash": 1_000_000,
    });
    let cmd = Command::SaveStrategyScenario {
        request_id: "req-2".to_string(),
        path: "/path/to/strategy.py".to_string(),
        scenario: scenario.clone(),
        save_as: false,
        loaded_path: Some("/path/to/strategy.py".to_string()),
    };
    let json = serde_json::to_string(&cmd).unwrap();
    assert!(json.contains(r#""op":"SaveStrategyScenario""#));
    assert!(json.contains(r#""save_as":false"#));
    assert!(json.contains(r#""instrument":"1301.TSE""#));
}

/// StrategyScenarioLoaded イベントが正しくデシリアライズされる。
#[test]
fn strategy_scenario_loaded_deserializes() {
    let json = r#"{
        "event": "StrategyScenarioLoaded",
        "request_id": "req-1",
        "path": "/path/to/strategy.py",
        "scenario": {
            "schema_version": 1,
            "instrument": "1301.TSE",
            "start": "2025-01-06",
            "end": "2025-03-31",
            "granularity": "1m",
            "initial_cash": 1000000
        }
    }"#;
    let event: EngineEvent = serde_json::from_str(json).unwrap();
    match event {
        EngineEvent::StrategyScenarioLoaded {
            request_id,
            path,
            scenario,
        } => {
            assert_eq!(request_id, "req-1");
            assert_eq!(path, "/path/to/strategy.py");
            let s = scenario.unwrap();
            assert_eq!(s["instrument"], "1301.TSE");
        }
        _ => panic!("Expected StrategyScenarioLoaded"),
    }
}

/// StrategyScenarioLoaded の scenario=None（SCENARIO 不在）が正しくデシリアライズされる。
#[test]
fn strategy_scenario_loaded_no_scenario_deserializes() {
    let json = r#"{
        "event": "StrategyScenarioLoaded",
        "request_id": "req-1",
        "path": "/path/to/strategy.py"
    }"#;
    let event: EngineEvent = serde_json::from_str(json).unwrap();
    match event {
        EngineEvent::StrategyScenarioLoaded { scenario, .. } => {
            assert!(scenario.is_none(), "scenario should be None when absent");
        }
        _ => panic!("Expected StrategyScenarioLoaded"),
    }
}

/// StrategyScenarioLoadFailed イベントが正しくデシリアライズされる。
#[test]
fn strategy_scenario_load_failed_deserializes() {
    let json = r#"{
        "event": "StrategyScenarioLoadFailed",
        "request_id": "req-1",
        "path": "/path/to/strategy.py",
        "reason": "dict literal 以外..."
    }"#;
    let event: EngineEvent = serde_json::from_str(json).unwrap();
    match event {
        EngineEvent::StrategyScenarioLoadFailed { reason, .. } => {
            assert!(reason.contains("dict literal"));
        }
        _ => panic!("Expected StrategyScenarioLoadFailed"),
    }
}

/// StrategyScenarioSaved イベントが正しくデシリアライズされる。
#[test]
fn strategy_scenario_saved_ok_deserializes() {
    let json = r#"{
        "event": "StrategyScenarioSaved",
        "request_id": "req-2",
        "path": "/path/to/strategy.py",
        "ok": true
    }"#;
    let event: EngineEvent = serde_json::from_str(json).unwrap();
    match event {
        EngineEvent::StrategyScenarioSaved { ok, error, .. } => {
            assert!(ok);
            assert!(error.is_none());
        }
        _ => panic!("Expected StrategyScenarioSaved"),
    }
}

/// StrategyScenarioSaved エラー時のデシリアライズ。
#[test]
fn strategy_scenario_saved_error_deserializes() {
    let json = r#"{
        "event": "StrategyScenarioSaved",
        "request_id": "req-2",
        "path": "/path/to/strategy.py",
        "ok": false,
        "error": "path_guard_violation"
    }"#;
    let event: EngineEvent = serde_json::from_str(json).unwrap();
    match event {
        EngineEvent::StrategyScenarioSaved { ok, error, .. } => {
            assert!(!ok);
            assert_eq!(error.as_deref(), Some("path_guard_violation"));
        }
        _ => panic!("Expected StrategyScenarioSaved"),
    }
}
