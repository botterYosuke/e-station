//! PP1: Positions Phase (schema 2.7) の IPC variant ラウンドトリップテスト。
//!
//! 対象:
//! - `Command::GetPositions` のシリアライズ → デシリアライズ
//! - `Event::PositionsUpdated` のデシリアライズ（空配列 / 混在 / tategyoku_id None/Some）
//! - `PositionRecordWire.market_value` の空文字許容
//! - `SCHEMA_MINOR` が 7 以上であることのアサート

use flowsurface_engine_client::dto::{Command, EngineEvent, PositionRecordWire};

// ── Schema version guard ────────────────────────────────────────────────────

#[test]
fn schema_minor_is_7() {
    const {
        assert!(
            flowsurface_engine_client::SCHEMA_MINOR >= 7,
            "SCHEMA_MINOR must be >= 7 (Positions Phase schema 2.7)"
        )
    };
}

// ── Command::GetPositions ────────────────────────────────────────────────────

#[test]
fn get_positions_command_roundtrip() {
    let cmd = Command::GetPositions {
        request_id: "req-abc-123".to_string(),
        venue: "tachibana".to_string(),
    };

    let json = serde_json::to_string(&cmd).expect("serialize Command::GetPositions");

    // op フィールドが正しく出力されていること
    assert!(json.contains(r#""op":"GetPositions""#));
    assert!(json.contains(r#""request_id":"req-abc-123""#));
    assert!(json.contains(r#""venue":"tachibana""#));
}

// ── Event::PositionsUpdated (empty positions) ────────────────────────────────

#[test]
fn positions_updated_roundtrip_empty_vec() {
    let json = r#"{
        "event": "PositionsUpdated",
        "request_id": "req-001",
        "venue": "tachibana",
        "positions": [],
        "ts_ms": 1746000000000
    }"#;

    let ev: EngineEvent = serde_json::from_str(json).expect("deserialize PositionsUpdated empty");
    match ev {
        EngineEvent::PositionsUpdated {
            request_id,
            venue,
            positions,
            ts_ms,
        } => {
            assert_eq!(request_id, "req-001");
            assert_eq!(venue, "tachibana");
            assert!(positions.is_empty());
            assert_eq!(ts_ms, 1_746_000_000_000);
        }
        other => panic!("expected PositionsUpdated, got {:?}", other),
    }
}

// ── Event::PositionsUpdated (cash + margin mixed) ────────────────────────────

#[test]
fn positions_updated_roundtrip_cash_and_margin() {
    let json = r#"{
        "event": "PositionsUpdated",
        "request_id": "req-002",
        "venue": "tachibana",
        "positions": [
            {
                "instrument_id": "7203.TSE",
                "qty": "100",
                "market_value": "345600",
                "position_type": "cash",
                "tategyoku_id": null,
                "venue": "tachibana"
            },
            {
                "instrument_id": "9984.TSE",
                "qty": "50",
                "market_value": "2134500",
                "position_type": "margin_credit",
                "tategyoku_id": "T-12345",
                "venue": "tachibana"
            }
        ],
        "ts_ms": 1746000001000
    }"#;

    let ev: EngineEvent = serde_json::from_str(json).expect("deserialize PositionsUpdated mixed");
    match ev {
        EngineEvent::PositionsUpdated { positions, .. } => {
            assert_eq!(positions.len(), 2);
            let cash = &positions[0];
            assert_eq!(cash.instrument_id, "7203.TSE");
            assert_eq!(cash.qty, "100");
            assert_eq!(cash.market_value, "345600");
            assert_eq!(cash.position_type, "cash");
            assert_eq!(cash.tategyoku_id, None);
            assert_eq!(cash.venue, "tachibana");

            let margin = &positions[1];
            assert_eq!(margin.instrument_id, "9984.TSE");
            assert_eq!(margin.qty, "50");
            assert_eq!(margin.market_value, "2134500");
            assert_eq!(margin.position_type, "margin_credit");
            assert_eq!(margin.tategyoku_id, Some("T-12345".to_string()));
        }
        other => panic!("expected PositionsUpdated, got {:?}", other),
    }
}

// ── tategyoku_id: None ────────────────────────────────────────────────────────

#[test]
fn positions_updated_roundtrip_tategyoku_id_none() {
    let json = r#"{
        "event": "PositionsUpdated",
        "request_id": "req-003",
        "venue": "tachibana",
        "positions": [
            {
                "instrument_id": "7203.TSE",
                "qty": "200",
                "market_value": "691200",
                "position_type": "cash",
                "tategyoku_id": null,
                "venue": "tachibana"
            }
        ],
        "ts_ms": 1746000002000
    }"#;

    let ev: EngineEvent = serde_json::from_str(json).unwrap();
    match ev {
        EngineEvent::PositionsUpdated { positions, .. } => {
            assert_eq!(positions[0].tategyoku_id, None);
        }
        other => panic!("expected PositionsUpdated, got {:?}", other),
    }
}

// ── tategyoku_id: Some ────────────────────────────────────────────────────────

#[test]
fn positions_updated_roundtrip_tategyoku_id_some() {
    let json = r#"{
        "event": "PositionsUpdated",
        "request_id": "req-004",
        "venue": "tachibana",
        "positions": [
            {
                "instrument_id": "6758.TSE",
                "qty": "50",
                "market_value": "1234500",
                "position_type": "margin_credit",
                "tategyoku_id": "T-12345",
                "venue": "tachibana"
            }
        ],
        "ts_ms": 1746000003000
    }"#;

    let ev: EngineEvent = serde_json::from_str(json).unwrap();
    match ev {
        EngineEvent::PositionsUpdated { positions, .. } => {
            assert_eq!(positions[0].tategyoku_id, Some("T-12345".to_string()));
        }
        other => panic!("expected PositionsUpdated, got {:?}", other),
    }
}

// ── market_value: "" (空文字許容) ────────────────────────────────────────────

#[test]
fn positions_updated_roundtrip_market_value_empty() {
    let json = r#"{
        "event": "PositionsUpdated",
        "request_id": "req-005",
        "venue": "tachibana",
        "positions": [
            {
                "instrument_id": "6758.TSE",
                "qty": "50",
                "market_value": "",
                "position_type": "margin_credit",
                "tategyoku_id": null,
                "venue": "tachibana"
            }
        ],
        "ts_ms": 1746000004000
    }"#;

    let ev: EngineEvent =
        serde_json::from_str(json).expect("市場価値が空文字でもデシリアライズ可能なこと");
    match ev {
        EngineEvent::PositionsUpdated { positions, .. } => {
            assert_eq!(positions[0].market_value, "");
        }
        other => panic!("expected PositionsUpdated, got {:?}", other),
    }
}

// ── PositionRecordWire 直接ラウンドトリップ ────────────────────────────────────

#[test]
fn position_record_wire_roundtrip() {
    let wire = PositionRecordWire {
        instrument_id: "7203.TSE".to_string(),
        qty: "100".to_string(),
        market_value: "345600".to_string(),
        position_type: "cash".to_string(),
        tategyoku_id: None,
        venue: "tachibana".to_string(),
    };

    let json = serde_json::to_string(&wire).unwrap();
    let decoded: PositionRecordWire = serde_json::from_str(&json).unwrap();
    assert_eq!(wire, decoded);
}
