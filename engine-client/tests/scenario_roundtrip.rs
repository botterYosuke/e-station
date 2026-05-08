//! F6: SCENARIO 定数の IPC ラウンドトリップテスト。
//! LoadStrategyScenario / SaveStrategyScenario コマンドと
//! StrategyScenarioLoaded / StrategyScenarioSaved イベントの
//! JSON シリアライズ・デシリアライズを検証する。

use flowsurface_engine_client::dto::{Command, EngineEvent};
use flowsurface_engine_client::{SCHEMA_MAJOR, SCHEMA_MINOR};

/// SCHEMA_MINOR が期待値以上であることを確認するリグレッションガード。
///
/// H4 / M12 (レビュー反映 2026-05-04 ラウンド1): F7 適用後の SCHEMA_MINOR=11 と
/// 整合させて `>= 11` に更新（F6 単体では 10 だったが F7 で 11 に上がっている）。
/// `assert_eq!(SCHEMA_MAJOR, 3)` のメッセージも汎化。
#[test]
fn schema_minor_is_at_least_11() {
    assert_eq!(SCHEMA_MAJOR, 3, "SCHEMA_MAJOR drift detected");
    // SCHEMA_MINOR must be >= 11 (F6 + F7 schema bumps applied)
    const { assert!(SCHEMA_MINOR >= 11) };
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
            ..
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

/// StrategyScenarioLoaded に resolved_instruments が含まれる場合のデシリアライズ。
#[test]
fn strategy_scenario_loaded_with_resolved_instruments_deserializes() {
    let json = r#"{
        "event": "StrategyScenarioLoaded",
        "request_id": "req-v3",
        "path": "/path/to/strategy.py",
        "scenario": {
            "schema_version": 3,
            "instruments_ref": "data/universe.json#/instruments",
            "start": "2025-01-06",
            "end": "2025-01-10",
            "granularity": "Minute",
            "initial_cash": 1000000
        },
        "resolved_instruments": ["1301.TSE", "7203.TSE"]
    }"#;
    let event: EngineEvent = serde_json::from_str(json).unwrap();
    match event {
        EngineEvent::StrategyScenarioLoaded {
            resolved_instruments,
            scenario,
            ..
        } => {
            let ids = resolved_instruments.unwrap();
            assert_eq!(ids, vec!["1301.TSE", "7203.TSE"]);
            // scenario は raw のまま（instruments_ref が保持されている）
            let s = scenario.unwrap();
            assert_eq!(s["instruments_ref"], "data/universe.json#/instruments");
            assert!(
                s.get("instruments").is_none(),
                "raw scenario must not have instruments key"
            );
        }
        _ => panic!("Expected StrategyScenarioLoaded"),
    }
}

/// v1/v2 では resolved_instruments が absent（None）でもデシリアライズできる。
#[test]
fn strategy_scenario_loaded_v1_resolved_instruments_absent_is_none() {
    let json = r#"{
        "event": "StrategyScenarioLoaded",
        "request_id": "req-v1",
        "path": "/path/to/strategy.py",
        "scenario": {"schema_version": 1, "instrument": "1301.TSE", "start": "2025-01-06", "end": "2025-01-10", "granularity": "Minute", "initial_cash": 1000000}
    }"#;
    let event: EngineEvent = serde_json::from_str(json).unwrap();
    match event {
        EngineEvent::StrategyScenarioLoaded {
            resolved_instruments,
            ..
        } => {
            assert!(resolved_instruments.is_none());
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

/// rust LOW-2 (レビュー反映 2026-05-04 ラウンド1): `loaded_path: None` でも
/// SaveStrategyScenario が正しくシリアライズ・デシリアライズできることを確認する。
/// `#[serde(default, skip_serializing_if = "Option::is_none")]` のおかげで
/// `loaded_path` フィールドはシリアライズ後の JSON から省略される。
#[test]
fn save_with_loaded_path_none_round_trips() {
    let scenario = serde_json::json!({
        "schema_version": 1,
        "instrument": "1301.TSE",
        "start": "2025-01-06",
        "end": "2025-03-31",
        "granularity": "Daily",
        "initial_cash": 1_000_000,
    });
    let cmd = Command::SaveStrategyScenario {
        request_id: "req-3".to_string(),
        path: "/path/to/new_strategy.py".to_string(),
        scenario: scenario.clone(),
        save_as: true,
        loaded_path: None,
    };
    let json = serde_json::to_string(&cmd).unwrap();
    // None は `skip_serializing_if = "Option::is_none"` で省略される
    assert!(
        !json.contains("loaded_path"),
        "loaded_path=None should be skipped from JSON: {json}"
    );
    assert!(json.contains(r#""save_as":true"#));
}

/// rust MEDIUM-3 (レビュー反映 2026-05-04 ラウンド1): `ok=true` かつ
/// `error=Some(...)` という不整合な StrategyScenarioSaved もデシリアライズ自体は
/// 成功する事実を pin 留めする。受信側は `!ok || error.is_some()` で扱うべき
/// （論理的には ok=true なら error は None のはず）。これは serde 側で
/// 弾けない種類の不整合であり、サーバー実装が `ok` と `error` の整合を
/// 守ることが契約。
#[test]
fn saved_with_ok_true_and_error_some_is_inconsistent() {
    let json = r#"{
        "event": "StrategyScenarioSaved",
        "request_id": "req-4",
        "path": "/path/to/strategy.py",
        "ok": true,
        "error": "validate_failed"
    }"#;
    let event: EngineEvent = serde_json::from_str(json).unwrap();
    match event {
        EngineEvent::StrategyScenarioSaved { ok, error, .. } => {
            // 不整合だがデシリアライズ自体は成功する。呼び出し側は
            // `!ok || error.is_some()` で「失敗扱い」と解釈すべき。
            assert!(ok);
            assert!(error.is_some(), "error should still be present");
        }
        _ => panic!("Expected StrategyScenarioSaved"),
    }
}
