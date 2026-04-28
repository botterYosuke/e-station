//! T2.x (Rust): schema 2.1 の IPC variant ラウンドトリップテスト。
//!
//! Phase U3 で追加された余力取得 IPC を対象とする:
//! - `GetBuyingPower` コマンドのシリアライズ
//! - `BuyingPowerUpdated` イベントのデシリアライズ

use flowsurface_engine_client::dto::{Command, EngineEvent};

// ── Schema version guard ────────────────────────────────────────────────────

#[test]
fn schema_minor_is_at_least_2_for_buying_power() {
    assert!(
        flowsurface_engine_client::SCHEMA_MINOR >= 2,
        "SCHEMA_MINOR must be >= 2 for BuyingPower IPC"
    );
}

// ── GetBuyingPower Command ──────────────────────────────────────────────────

#[test]
fn get_buying_power_serializes() {
    let cmd = Command::GetBuyingPower {
        request_id: "req-001".to_string(),
        venue: "tachibana".to_string(),
    };
    let json = serde_json::to_string(&cmd).expect("must serialize");
    let v: serde_json::Value = serde_json::from_str(&json).unwrap();
    assert_eq!(v["op"], "GetBuyingPower");
    assert_eq!(v["request_id"], "req-001");
    assert_eq!(v["venue"], "tachibana");
}

#[test]
fn get_buying_power_debug_shows_fields() {
    let cmd = Command::GetBuyingPower {
        request_id: "req-debug".to_string(),
        venue: "tachibana".to_string(),
    };
    let s = format!("{cmd:?}");
    assert!(s.contains("GetBuyingPower"), "debug must name variant");
    assert!(s.contains("req-debug"));
}

// ── BuyingPowerUpdated Event ────────────────────────────────────────────────

#[test]
fn buying_power_updated_deserializes() {
    let json = r#"{
        "event": "BuyingPowerUpdated",
        "request_id": "req-001",
        "venue": "tachibana",
        "cash_available": 1000000,
        "cash_shortfall": 0,
        "credit_available": 500000,
        "ts_ms": 1745640000000
    }"#;
    let ev: EngineEvent = serde_json::from_str(json).expect("must deserialize");
    match ev {
        EngineEvent::BuyingPowerUpdated {
            request_id,
            venue,
            cash_available,
            cash_shortfall,
            credit_available,
            ts_ms,
        } => {
            assert_eq!(request_id, "req-001");
            assert_eq!(venue, "tachibana");
            assert_eq!(cash_available, 1_000_000);
            assert_eq!(cash_shortfall, 0);
            assert_eq!(credit_available, 500_000);
            assert_eq!(ts_ms, 1_745_640_000_000);
        }
        other => panic!("expected BuyingPowerUpdated, got {:?}", other),
    }
}

#[test]
fn buying_power_updated_with_shortfall_deserializes() {
    let json = r#"{
        "event": "BuyingPowerUpdated",
        "request_id": "req-002",
        "venue": "tachibana",
        "cash_available": 0,
        "cash_shortfall": 50000,
        "credit_available": 0,
        "ts_ms": 1745640001000
    }"#;
    let ev: EngineEvent = serde_json::from_str(json).expect("must deserialize");
    match ev {
        EngineEvent::BuyingPowerUpdated { cash_shortfall, .. } => {
            assert_eq!(cash_shortfall, 50_000, "shortfall must be positive");
        }
        other => panic!("expected BuyingPowerUpdated, got {:?}", other),
    }
}

#[test]
fn buying_power_updated_roundtrip_json_shape() {
    let json = r#"{
        "event": "BuyingPowerUpdated",
        "request_id": "req-003",
        "venue": "tachibana",
        "cash_available": 2000000,
        "cash_shortfall": 0,
        "credit_available": 1000000,
        "ts_ms": 1745640002000
    }"#;
    let ev: EngineEvent = serde_json::from_str(json).expect("BuyingPowerUpdated must deserialize");
    assert!(matches!(ev, EngineEvent::BuyingPowerUpdated { .. }));
}
