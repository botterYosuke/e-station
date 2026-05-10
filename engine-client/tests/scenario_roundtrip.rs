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

// ── issue #42 Phase 2 (schema 3.25): LIVE_SCENARIO 抽出の serde round-trip ──

/// SCHEMA_MINOR は P2 完了後 25 以上であること。
#[test]
fn schema_minor_is_at_least_25_after_p2() {
    const { assert!(SCHEMA_MINOR >= 25) };
}

/// LoadLiveStrategyScenario コマンドが正しく JSON シリアライズされる。
#[test]
fn load_live_strategy_scenario_serializes() {
    let cmd = Command::LoadLiveStrategyScenario {
        request_id: "live-req-1".to_string(),
        strategy_path: "/path/to/live.py".to_string(),
    };
    let json = serde_json::to_string(&cmd).unwrap();
    assert!(json.contains(r#""op":"LoadLiveStrategyScenario""#));
    assert!(json.contains(r#""request_id":"live-req-1""#));
    assert!(json.contains(r#""strategy_path":"/path/to/live.py""#));
}

/// LiveStrategyScenarioLoaded イベント（全フィールド設定）がデシリアライズできる。
#[test]
fn live_strategy_scenario_loaded_full_deserializes() {
    let json = r#"{
        "event": "LiveStrategyScenarioLoaded",
        "request_id": "live-req-1",
        "instrument_id": "7203.TSE",
        "max_qty": 100,
        "max_notional_jpy": 1000000,
        "venue": "tachibana",
        "strategy_init_kwargs": {"foo": 1, "bar": "baz"}
    }"#;
    let event: EngineEvent = serde_json::from_str(json).unwrap();
    match event {
        EngineEvent::LiveStrategyScenarioLoaded {
            request_id,
            instrument_id,
            max_qty,
            max_notional_jpy,
            venue,
            strategy_init_kwargs,
        } => {
            assert_eq!(request_id, "live-req-1");
            assert_eq!(instrument_id.as_deref(), Some("7203.TSE"));
            assert_eq!(max_qty, Some(100));
            assert_eq!(max_notional_jpy, Some(1_000_000));
            assert_eq!(venue.as_deref(), Some("tachibana"));
            let kw = strategy_init_kwargs.expect("kwargs should be Some");
            assert_eq!(kw.get("foo").and_then(|v| v.as_i64()), Some(1));
            assert_eq!(kw.get("bar").and_then(|v| v.as_str()), Some("baz"));
        }
        _ => panic!("Expected LiveStrategyScenarioLoaded"),
    }
}

/// LiveStrategyScenarioLoaded イベント（LIVE_SCENARIO 不在時 = 全フィールド省略）も
/// デシリアライズできる（Open Q2: 即時応答 SoT）。
#[test]
fn live_strategy_scenario_loaded_all_absent_deserializes() {
    let json = r#"{
        "event": "LiveStrategyScenarioLoaded",
        "request_id": "live-req-2"
    }"#;
    let event: EngineEvent = serde_json::from_str(json).unwrap();
    match event {
        EngineEvent::LiveStrategyScenarioLoaded {
            request_id,
            instrument_id,
            max_qty,
            max_notional_jpy,
            venue,
            strategy_init_kwargs,
        } => {
            assert_eq!(request_id, "live-req-2");
            assert!(instrument_id.is_none());
            assert!(max_qty.is_none());
            assert!(max_notional_jpy.is_none());
            assert!(venue.is_none());
            assert!(strategy_init_kwargs.is_none());
        }
        _ => panic!("Expected LiveStrategyScenarioLoaded"),
    }
}

/// 旧 server (minor < 25) は LoadLiveStrategyScenario を unknown command として
/// 握り潰す（forward compat）。新 client → 旧 server の wire 上の挙動を検証する。
///
/// proto wire-format では、未知の oneof variant field number は受信側の proto デコード時に
/// 「未知フィールド」として silently 無視されるか、`payload: None` として届く。
/// ここではコマンドが Some(payload) で構築できることだけを確認する（旧 server 側で
/// `WhichOneof("payload") is None` または `_FIELD_TO_OP.get(which) is None` の経路で
/// 無視される実装契約 = 後方互換性）。
#[test]
fn load_live_strategy_scenario_forward_compat() {
    // 新 client: LoadLiveStrategyScenario を構築して wire 用 JSON serialise できる。
    let cmd = Command::LoadLiveStrategyScenario {
        request_id: "forward-1".to_string(),
        strategy_path: "/strat.py".to_string(),
    };
    let json = serde_json::to_string(&cmd).unwrap();
    // tag は `op`、旧 server からは未知の値として扱われる
    assert!(json.contains(r#""op":"LoadLiveStrategyScenario""#));

    // Command は serialise のみで deserialise されないため、wire 形が JSON 値として
    // 取り出せることだけ確認する（旧 server は proto 側で oneof field を unknown と
    // して skip し、`_FIELD_TO_OP.get(which) is None` の経路にも到達しない）。
    let v: serde_json::Value = serde_json::from_str(&json).expect("json must parse");
    assert_eq!(v["op"], "LoadLiveStrategyScenario");
    assert_eq!(v["strategy_path"], "/strat.py");
}

/// LiveStrategyScenarioLoaded のうち kwargs が空の dict であるケースのデシリアライズ。
#[test]
fn live_strategy_scenario_loaded_empty_kwargs_deserializes() {
    let json = r#"{
        "event": "LiveStrategyScenarioLoaded",
        "request_id": "live-req-3",
        "strategy_init_kwargs": {}
    }"#;
    let event: EngineEvent = serde_json::from_str(json).unwrap();
    match event {
        EngineEvent::LiveStrategyScenarioLoaded {
            strategy_init_kwargs,
            ..
        } => {
            let kw = strategy_init_kwargs.expect("empty dict should still be Some");
            assert!(kw.is_empty(), "empty dict should remain empty");
        }
        _ => panic!("Expected LiveStrategyScenarioLoaded"),
    }
}

// ── issue #42 Phase 3 (schema 3.26): LiveStrategyReady の serde round-trip ──

/// SCHEMA_MINOR は P3a 完了後 26 以上であること。
#[test]
fn schema_minor_is_at_least_26_after_p3a() {
    const { assert!(SCHEMA_MINOR >= 26) };
}

/// LiveStrategyReady イベントが正しくデシリアライズされる。
#[test]
fn live_strategy_ready_deserializes() {
    let json = r#"{
        "event": "LiveStrategyReady",
        "strategy_id": "live-strat-1",
        "venue": "tachibana",
        "instrument_id": "7203.TSE",
        "ts_event_ms": 1700000000123
    }"#;
    let event: EngineEvent = serde_json::from_str(json).unwrap();
    match event {
        EngineEvent::LiveStrategyReady {
            strategy_id,
            venue,
            instrument_id,
            ts_event_ms,
        } => {
            assert_eq!(strategy_id, "live-strat-1");
            assert_eq!(venue, "tachibana");
            assert_eq!(instrument_id, "7203.TSE");
            assert_eq!(ts_event_ms, 1_700_000_000_123);
        }
        _ => panic!("Expected LiveStrategyReady"),
    }
}

/// LiveStrategyReady に未知 field があっても tolerated（forward compat）。
#[test]
fn live_strategy_ready_unknown_field_tolerated() {
    let json = r#"{
        "event": "LiveStrategyReady",
        "strategy_id": "live-strat-2",
        "venue": "tachibana",
        "instrument_id": "7203.TSE",
        "ts_event_ms": 1700000000456,
        "future_unknown_field": 999
    }"#;
    let event: EngineEvent = serde_json::from_str(json).unwrap();
    match event {
        EngineEvent::LiveStrategyReady { strategy_id, .. } => {
            assert_eq!(strategy_id, "live-strat-2");
        }
        _ => panic!("Expected LiveStrategyReady"),
    }
}

// ── issue #42 Phase 3 (schema 3.27): LiveStrategyWarmingUp の serde round-trip ──

/// SCHEMA_MINOR は P3b 完了後 27 以上であること。
#[test]
fn schema_minor_is_at_least_27_after_p3b() {
    const { assert!(SCHEMA_MINOR >= 27) };
}

/// LiveStrategyWarmingUp イベントが正しくデシリアライズされる。
#[test]
fn live_strategy_warming_up_deserializes() {
    let json = r#"{
        "event": "LiveStrategyWarmingUp",
        "strategy_id": "live-strat-3",
        "progress": 0.42,
        "message": "warming up..."
    }"#;
    let event: EngineEvent = serde_json::from_str(json).unwrap();
    match event {
        EngineEvent::LiveStrategyWarmingUp {
            strategy_id,
            progress,
            message,
        } => {
            assert_eq!(strategy_id, "live-strat-3");
            assert!((progress - 0.42).abs() < 1e-5, "progress 0.42 expected, got {progress}");
            assert_eq!(message, "warming up...");
        }
        _ => panic!("Expected LiveStrategyWarmingUp"),
    }
}

/// LiveStrategyWarmingUp に未知 field があっても tolerated（forward compat）。
#[test]
fn live_strategy_warming_up_unknown_field_tolerated() {
    let json = r#"{
        "event": "LiveStrategyWarmingUp",
        "strategy_id": "live-strat-4",
        "progress": 0.0,
        "message": "init",
        "future_field": "extra"
    }"#;
    let event: EngineEvent = serde_json::from_str(json).unwrap();
    match event {
        EngineEvent::LiveStrategyWarmingUp { progress, .. } => {
            assert!(progress.abs() < 1e-6);
        }
        _ => panic!("Expected LiveStrategyWarmingUp"),
    }
}
