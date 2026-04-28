//! N1.1 / N1.13 (Rust): schema 2.4 の nautilus_trader 統合 IPC variant ラウンドトリップテスト。
//!
//! Phase N1 で追加された nautilus 統合 IPC を対象とする:
//! - `Command::StartEngine` / `StopEngine` / `LoadReplayData`
//! - `EngineEvent::EngineStarted` / `EngineStopped` / `ReplayDataLoaded`
//!   / `PositionOpened` / `PositionClosed`
//! - Hello に `mode` フィールド追加
//! - `EngineKind` / `ReplayGranularity` の wire 表現
//!
//! 互換性: `architecture.md` は `schema_minor=4` を要求。`SCHEMA_MAJOR` は 2 のまま。

use flowsurface_engine_client::dto::{
    Command, EngineEvent, EngineKind, EngineStartConfig, ReplayGranularity,
};

// ── Schema version guard ────────────────────────────────────────────────────

#[test]
fn schema_minor_is_4_for_nautilus() {
    assert_eq!(
        flowsurface_engine_client::SCHEMA_MINOR,
        4,
        "SCHEMA_MINOR must be exactly 4 for nautilus integration (Phase N1)"
    );
    assert_eq!(
        flowsurface_engine_client::SCHEMA_MAJOR,
        2,
        "SCHEMA_MAJOR must remain 2 (architecture.md uses logical 1.x but real code is 2.x)"
    );
}

// ── Hello.mode field (N1.13) ────────────────────────────────────────────────

#[test]
fn hello_includes_mode_field() {
    let cmd = Command::Hello {
        schema_major: 2,
        schema_minor: 4,
        client_version: "test-0.0.0".to_string(),
        token: "tok".to_string(),
        mode: "replay".to_string(),
    };
    let json = serde_json::to_string(&cmd).expect("must serialize");
    let v: serde_json::Value = serde_json::from_str(&json).unwrap();
    assert_eq!(v["op"], "Hello");
    assert_eq!(v["mode"], "replay");
}

// ── Sub-types ───────────────────────────────────────────────────────────────

#[test]
fn engine_kind_serializes_as_pascal_case() {
    let bt = serde_json::to_string(&EngineKind::Backtest).unwrap();
    assert_eq!(bt, "\"Backtest\"");
    let lv = serde_json::to_string(&EngineKind::Live).unwrap();
    assert_eq!(lv, "\"Live\"");
}

#[test]
fn replay_granularity_serializes() {
    assert_eq!(
        serde_json::to_string(&ReplayGranularity::Trade).unwrap(),
        "\"Trade\""
    );
    assert_eq!(
        serde_json::to_string(&ReplayGranularity::Minute).unwrap(),
        "\"Minute\""
    );
    assert_eq!(
        serde_json::to_string(&ReplayGranularity::Daily).unwrap(),
        "\"Daily\""
    );
}

// ── StartEngine / StopEngine / LoadReplayData (N1.1 commands) ───────────────

#[test]
fn start_engine_serializes() {
    let cmd = Command::StartEngine {
        request_id: "req-1".to_string(),
        engine: EngineKind::Backtest,
        strategy_id: "strat-001".to_string(),
        config: EngineStartConfig {
            instrument_id: "1301.TSE".to_string(),
            start_date: "2024-01-04".to_string(),
            end_date: "2024-01-31".to_string(),
            initial_cash: "1000000".to_string(),
            granularity: ReplayGranularity::Trade,
        },
    };
    let json = serde_json::to_string(&cmd).expect("must serialize");
    let v: serde_json::Value = serde_json::from_str(&json).unwrap();
    assert_eq!(v["op"], "StartEngine");
    assert_eq!(v["request_id"], "req-1");
    assert_eq!(v["engine"], "Backtest");
    assert_eq!(v["strategy_id"], "strat-001");
    assert_eq!(v["config"]["instrument_id"], "1301.TSE");
    assert_eq!(v["config"]["granularity"], "Trade");
    assert_eq!(v["config"]["initial_cash"], "1000000");
}

#[test]
fn stop_engine_serializes() {
    let cmd = Command::StopEngine {
        request_id: "req-2".to_string(),
        strategy_id: "strat-001".to_string(),
    };
    let json = serde_json::to_string(&cmd).unwrap();
    let v: serde_json::Value = serde_json::from_str(&json).unwrap();
    assert_eq!(v["op"], "StopEngine");
    assert_eq!(v["strategy_id"], "strat-001");
}

#[test]
fn load_replay_data_serializes() {
    let cmd = Command::LoadReplayData {
        request_id: "req-3".to_string(),
        instrument_id: "1301.TSE".to_string(),
        start_date: "2024-01-04".to_string(),
        end_date: "2024-01-31".to_string(),
        granularity: ReplayGranularity::Minute,
    };
    let json = serde_json::to_string(&cmd).unwrap();
    let v: serde_json::Value = serde_json::from_str(&json).unwrap();
    assert_eq!(v["op"], "LoadReplayData");
    assert_eq!(v["instrument_id"], "1301.TSE");
    assert_eq!(v["granularity"], "Minute");
}

// ── Events ──────────────────────────────────────────────────────────────────

#[test]
fn engine_started_deserializes() {
    let json = r#"{
        "event": "EngineStarted",
        "strategy_id": "strat-001",
        "account_id": "SIM-001",
        "ts_event_ms": 1700000000000
    }"#;
    let ev: EngineEvent = serde_json::from_str(json).expect("must deserialize");
    match ev {
        EngineEvent::EngineStarted {
            strategy_id,
            account_id,
            ts_event_ms,
        } => {
            assert_eq!(strategy_id, "strat-001");
            assert_eq!(account_id, "SIM-001");
            assert_eq!(ts_event_ms, 1_700_000_000_000);
        }
        other => panic!("expected EngineStarted, got {other:?}"),
    }
}

#[test]
fn engine_stopped_deserializes() {
    let json = r#"{
        "event": "EngineStopped",
        "strategy_id": "strat-001",
        "final_equity": "1050000.50",
        "ts_event_ms": 1700000000001
    }"#;
    let ev: EngineEvent = serde_json::from_str(json).expect("must deserialize");
    match ev {
        EngineEvent::EngineStopped {
            strategy_id,
            final_equity,
            ts_event_ms,
        } => {
            assert_eq!(strategy_id, "strat-001");
            assert_eq!(final_equity, "1050000.50");
            assert_eq!(ts_event_ms, 1_700_000_000_001);
        }
        other => panic!("expected EngineStopped, got {other:?}"),
    }
}

#[test]
fn replay_data_loaded_deserializes() {
    let json = r#"{
        "event": "ReplayDataLoaded",
        "strategy_id": "strat-001",
        "bars_loaded": 1234,
        "trades_loaded": 56789,
        "ts_event_ms": 1700000000002
    }"#;
    let ev: EngineEvent = serde_json::from_str(json).expect("must deserialize");
    match ev {
        EngineEvent::ReplayDataLoaded {
            strategy_id,
            bars_loaded,
            trades_loaded,
            ts_event_ms,
        } => {
            assert_eq!(strategy_id, "strat-001");
            assert_eq!(bars_loaded, 1234);
            assert_eq!(trades_loaded, 56789);
            assert_eq!(ts_event_ms, 1_700_000_000_002);
        }
        other => panic!("expected ReplayDataLoaded, got {other:?}"),
    }
}

#[test]
fn position_opened_deserializes() {
    let json = r#"{
        "event": "PositionOpened",
        "strategy_id": "strat-001",
        "venue": "SIM",
        "instrument_id": "1301.TSE",
        "position_id": "P-1",
        "side": "LONG",
        "opened_qty": "100",
        "avg_open_price": "1500.5",
        "ts_event_ms": 1700000000003
    }"#;
    let ev: EngineEvent = serde_json::from_str(json).expect("must deserialize");
    match ev {
        EngineEvent::PositionOpened {
            strategy_id,
            venue,
            instrument_id,
            position_id,
            side,
            opened_qty,
            avg_open_price,
            ts_event_ms,
        } => {
            assert_eq!(strategy_id, "strat-001");
            assert_eq!(venue, "SIM");
            assert_eq!(instrument_id, "1301.TSE");
            assert_eq!(position_id, "P-1");
            assert_eq!(side, "LONG");
            assert_eq!(opened_qty, "100");
            assert_eq!(avg_open_price, "1500.5");
            assert_eq!(ts_event_ms, 1_700_000_000_003);
        }
        other => panic!("expected PositionOpened, got {other:?}"),
    }
}

#[test]
fn position_closed_deserializes() {
    let json = r#"{
        "event": "PositionClosed",
        "strategy_id": "strat-001",
        "venue": "SIM",
        "instrument_id": "1301.TSE",
        "position_id": "P-1",
        "realized_pnl": "5000.0",
        "ts_event_ms": 1700000000004
    }"#;
    let ev: EngineEvent = serde_json::from_str(json).expect("must deserialize");
    match ev {
        EngineEvent::PositionClosed {
            strategy_id,
            venue,
            instrument_id,
            position_id,
            realized_pnl,
            ts_event_ms,
        } => {
            assert_eq!(strategy_id, "strat-001");
            assert_eq!(venue, "SIM");
            assert_eq!(instrument_id, "1301.TSE");
            assert_eq!(position_id, "P-1");
            assert_eq!(realized_pnl, "5000.0");
            assert_eq!(ts_event_ms, 1_700_000_000_004);
        }
        other => panic!("expected PositionClosed, got {other:?}"),
    }
}
