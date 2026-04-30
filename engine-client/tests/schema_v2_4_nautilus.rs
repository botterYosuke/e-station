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
    AppMode, Command, EngineEvent, EngineKind, EngineStartConfig, ReplayGranularity, SignalKind,
};

// ── Schema version guard ────────────────────────────────────────────────────

#[test]
fn schema_minor_is_6_for_nautilus() {
    // R2 review-fix R1b M-8: ReplayDataLoaded.strategy_id を Optional に緩和し
    // SCHEMA_MINOR を 4 → 5 に bump。MAJOR は据え置き (互換維持; minor mismatch は WARN のみ)。
    // レビュー反映 2026-04-29: LoadReplayData/ReplayLoadBody.strategy_init_kwargs を Map 型に
    // 統一したため SCHEMA_MINOR を 5 → 6 に bump。
    assert_eq!(
        flowsurface_engine_client::SCHEMA_MINOR,
        6,
        "SCHEMA_MINOR must be 6 after H-1 (strategy_init_kwargs Map unification)"
    );
    assert_eq!(
        flowsurface_engine_client::SCHEMA_MAJOR,
        2,
        "SCHEMA_MAJOR must remain 2 (architecture.md uses logical 1.x but real code is 2.x)"
    );
}

// ── M-8: ReplayDataLoaded.strategy_id Optional (R1b) ─────────────────────────

#[test]
fn replay_data_loaded_deserializes_with_null_strategy_id() {
    // 単独 LoadReplayData (戦略未起動) では strategy_id = null を許可する。
    let json = r#"{
        "event": "ReplayDataLoaded",
        "strategy_id": null,
        "bars_loaded": 0,
        "trades_loaded": 4,
        "ts_event_ms": 1700000000000
    }"#;
    let ev: EngineEvent = serde_json::from_str(json).expect("must deserialize null strategy_id");
    match ev {
        EngineEvent::ReplayDataLoaded {
            strategy_id,
            bars_loaded,
            trades_loaded,
            ..
        } => {
            assert!(strategy_id.is_none());
            assert_eq!(bars_loaded, 0);
            assert_eq!(trades_loaded, 4);
        }
        other => panic!("expected ReplayDataLoaded, got {other:?}"),
    }
}

#[test]
fn replay_data_loaded_deserializes_when_strategy_id_field_absent() {
    // 旧 Python サーバ (minor=4) が strategy_id を送ってこないケース互換確認。
    let json = r#"{
        "event": "ReplayDataLoaded",
        "bars_loaded": 12,
        "trades_loaded": 0,
        "ts_event_ms": 1700000000000
    }"#;
    let ev: EngineEvent =
        serde_json::from_str(json).expect("must deserialize missing strategy_id (serde default)");
    match ev {
        EngineEvent::ReplayDataLoaded { strategy_id, .. } => {
            assert!(strategy_id.is_none());
        }
        other => panic!("expected ReplayDataLoaded, got {other:?}"),
    }
}

// ── Hello.mode field (N1.13) ────────────────────────────────────────────────

#[test]
fn hello_includes_mode_field() {
    // R1b H-E: Hello.mode は wire 上は "live" / "replay" 文字列のまま (Python と互換)。
    // Rust 内部では ``AppMode`` enum を使い、serde rename_all = "lowercase" で wire 互換。
    let cmd = Command::Hello {
        schema_major: 2,
        schema_minor: 6,
        client_version: "test-0.0.0".to_string(),
        token: "tok".to_string(),
        mode: AppMode::Replay,
    };
    let json = serde_json::to_string(&cmd).expect("must serialize");
    let v: serde_json::Value = serde_json::from_str(&json).unwrap();
    assert_eq!(v["op"], "Hello");
    assert_eq!(v["mode"], "replay");
}

// ── R1b H-E: AppMode enum (wire = "live" / "replay") ─────────────────────────

#[test]
fn app_mode_serializes_lowercase() {
    assert_eq!(
        serde_json::to_string(&AppMode::Live).unwrap(),
        "\"live\"",
        "AppMode::Live wire form must be the lowercase string \"live\""
    );
    assert_eq!(
        serde_json::to_string(&AppMode::Replay).unwrap(),
        "\"replay\"",
        "AppMode::Replay wire form must be the lowercase string \"replay\""
    );
}

#[test]
fn app_mode_deserializes_lowercase() {
    let live: AppMode = serde_json::from_str("\"live\"").unwrap();
    assert_eq!(live, AppMode::Live);
    let replay: AppMode = serde_json::from_str("\"replay\"").unwrap();
    assert_eq!(replay, AppMode::Replay);
}

#[test]
fn app_mode_default_is_live_for_backward_compat() {
    // R1b H-E: 旧クライアント / 旧サーバ互換のため default は Live。
    assert_eq!(AppMode::default(), AppMode::Live);
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
            strategy_file: None,
            strategy_init_kwargs: None,
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
    assert!(
        v["config"].get("strategy_file").is_none(),
        "None は省略される"
    );
}

#[test]
fn start_engine_with_strategy_file_serializes() {
    let mut kwargs = serde_json::Map::new();
    kwargs.insert("instrument_id".to_string(), serde_json::json!("1301.TSE"));
    kwargs.insert("lot_size".to_string(), serde_json::json!(100));
    let cmd = Command::StartEngine {
        request_id: "req-sf".to_string(),
        engine: EngineKind::Backtest,
        strategy_id: "user-defined".to_string(),
        config: EngineStartConfig {
            instrument_id: "1301.TSE".to_string(),
            start_date: "2024-01-04".to_string(),
            end_date: "2024-03-31".to_string(),
            initial_cash: "1000000".to_string(),
            granularity: ReplayGranularity::Daily,
            strategy_file: Some("examples/strategies/buy_and_hold.py".to_string()),
            strategy_init_kwargs: Some(kwargs),
        },
    };
    let json = serde_json::to_string(&cmd).expect("must serialize");
    let v: serde_json::Value = serde_json::from_str(&json).unwrap();
    assert_eq!(
        v["config"]["strategy_file"],
        "examples/strategies/buy_and_hold.py"
    );
    assert_eq!(v["config"]["strategy_init_kwargs"]["lot_size"], 100);
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
    assert!(
        v.get("strategy_file").is_none(),
        "strategy_file は LoadReplayData の wire JSON に含まれてはいけない"
    );
    assert!(
        v.get("strategy_init_kwargs").is_none(),
        "strategy_init_kwargs は LoadReplayData の wire JSON に含まれてはいけない"
    );
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
            // M-8: Option<String> へ。strategy_id 文字列付きは Some(...) で来る。
            assert_eq!(strategy_id.as_deref(), Some("strat-001"));
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

// ── N1.11: SetReplaySpeed ────────────────────────────────────────────────────

#[test]
fn set_replay_speed_serializes() {
    let cmd = Command::SetReplaySpeed {
        request_id: "req-speed-1".to_string(),
        multiplier: 10,
    };
    let json = serde_json::to_string(&cmd).expect("must serialize");
    let v: serde_json::Value = serde_json::from_str(&json).unwrap();
    assert_eq!(v["op"], "SetReplaySpeed");
    assert_eq!(v["request_id"], "req-speed-1");
    assert_eq!(v["multiplier"], 10);
}

#[test]
fn set_replay_speed_debug_shows_multiplier() {
    let cmd = Command::SetReplaySpeed {
        request_id: "r1".to_string(),
        multiplier: 5,
    };
    let dbg = format!("{cmd:?}");
    assert!(dbg.contains("SetReplaySpeed"));
    assert!(dbg.contains("multiplier: 5"));
}

// ── N1.12: ExecutionMarker + StrategySignal ──────────────────────────────────

#[test]
fn execution_marker_deserializes() {
    let json = r#"{
        "event": "ExecutionMarker",
        "strategy_id": "buy-and-hold-001",
        "instrument_id": "1301.TSE",
        "side": "BUY",
        "price": "1500.0",
        "ts_event_ms": 1700000000010
    }"#;
    let ev: EngineEvent = serde_json::from_str(json).expect("must deserialize");
    match ev {
        EngineEvent::ExecutionMarker {
            strategy_id,
            instrument_id,
            side,
            price,
            ts_event_ms,
            ..
        } => {
            assert_eq!(strategy_id, "buy-and-hold-001");
            assert_eq!(instrument_id, "1301.TSE");
            assert_eq!(side, "BUY");
            assert_eq!(price, "1500.0");
            assert_eq!(ts_event_ms, 1_700_000_000_010);
        }
        other => panic!("expected ExecutionMarker, got {other:?}"),
    }
}

#[test]
fn strategy_signal_deserializes_full() {
    let json = r#"{
        "event": "StrategySignal",
        "strategy_id": "buy-and-hold-001",
        "instrument_id": "1301.TSE",
        "signal_kind": "EntryLong",
        "side": "BUY",
        "price": "1500.0",
        "tag": "entry",
        "note": "first bar",
        "ts_event_ms": 1700000000020
    }"#;
    let ev: EngineEvent = serde_json::from_str(json).expect("must deserialize");
    match ev {
        EngineEvent::StrategySignal {
            strategy_id,
            instrument_id,
            signal_kind,
            side,
            price,
            tag,
            note,
            ts_event_ms,
        } => {
            assert_eq!(strategy_id, "buy-and-hold-001");
            assert_eq!(instrument_id, "1301.TSE");
            assert_eq!(signal_kind, SignalKind::EntryLong);
            assert_eq!(side, Some("BUY".to_string()));
            assert_eq!(price, Some("1500.0".to_string()));
            assert_eq!(tag, Some("entry".to_string()));
            assert_eq!(note, Some("first bar".to_string()));
            assert_eq!(ts_event_ms, 1_700_000_000_020);
        }
        other => panic!("expected StrategySignal, got {other:?}"),
    }
}

#[test]
fn strategy_signal_deserializes_minimal() {
    // side/price/tag/note are all optional
    let json = r#"{
        "event": "StrategySignal",
        "strategy_id": "strat-001",
        "instrument_id": "1301.TSE",
        "signal_kind": "Annotate",
        "ts_event_ms": 1700000000030
    }"#;
    let ev: EngineEvent = serde_json::from_str(json).expect("must deserialize");
    match ev {
        EngineEvent::StrategySignal {
            signal_kind,
            side,
            price,
            tag,
            note,
            ..
        } => {
            assert_eq!(signal_kind, SignalKind::Annotate);
            assert!(side.is_none());
            assert!(price.is_none());
            assert!(tag.is_none());
            assert!(note.is_none());
        }
        other => panic!("expected StrategySignal, got {other:?}"),
    }
}

#[test]
fn signal_kind_serializes_as_pascal_case() {
    assert_eq!(
        serde_json::to_string(&SignalKind::EntryLong).unwrap(),
        "\"EntryLong\""
    );
    assert_eq!(
        serde_json::to_string(&SignalKind::EntryShort).unwrap(),
        "\"EntryShort\""
    );
    assert_eq!(
        serde_json::to_string(&SignalKind::Exit).unwrap(),
        "\"Exit\""
    );
    assert_eq!(
        serde_json::to_string(&SignalKind::Annotate).unwrap(),
        "\"Annotate\""
    );
}

// ── N1.16: ReplayBuyingPower ─────────────────────────────────────────────────

#[test]
fn replay_buying_power_deserializes() {
    let json = r#"{"event":"ReplayBuyingPower","strategy_id":"buy-and-hold","cash":"980000.00","buying_power":"980000.00","equity":"990000.00","ts_event_ms":1704268800000}"#;
    let event: EngineEvent = serde_json::from_str(json).unwrap();
    match event {
        EngineEvent::ReplayBuyingPower {
            strategy_id,
            cash,
            buying_power,
            equity,
            ts_event_ms,
        } => {
            assert_eq!(strategy_id, "buy-and-hold");
            assert_eq!(cash, "980000.00");
            assert_eq!(buying_power, "980000.00");
            assert_eq!(equity, "990000.00");
            assert_eq!(ts_event_ms, 1_704_268_800_000);
        }
        other => panic!("expected ReplayBuyingPower, got {other:?}"),
    }
}
