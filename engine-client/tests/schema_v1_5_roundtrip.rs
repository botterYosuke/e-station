//! T2.5 (Rust): schema 1.5 の IPC variant ラウンドトリップテスト。
//!
//! Phase O2 で追加された EC 約定通知イベントを対象とする:
//! - `OrderFilled` (全約定 / 部分約定) のデシリアライズ
//! - `OrderCanceled` のデシリアライズ
//! - `OrderExpired` のデシリアライズ
//!
//! これらの DTO は schema 1.3 ですでに定義済みだが、
//! Phase O2 で初めて実際に使われる。

use flowsurface_engine_client::dto::EngineEvent;

const CID: &str = "4f5e6d7c-8b9a-0c1d-2e3f-4a5b6c7d8e9f";
const VID: &str = "ORD-2026-001";
const TID: &str = "EDA-001";

// ── Schema version guard ────────────────────────────────────────────────────

// Schema 2.x contains schema 1.5 variants (order events).
#[test]
fn schema_major_is_at_least_2_for_order_events() {
    assert!(
        flowsurface_engine_client::SCHEMA_MAJOR >= 2,
        "SCHEMA_MAJOR must be >= 2 (schema 1.5 order events included in schema 2.x)"
    );
}

// ── OrderFilled (全約定: leaves_qty == "0") ───────────────────────────────────

#[test]
fn order_filled_full_fill_deserializes() {
    let json = format!(
        r#"{{
            "event": "OrderFilled",
            "client_order_id": "{CID}",
            "venue_order_id": "{VID}",
            "trade_id": "{TID}",
            "last_qty": "100",
            "last_price": "3500",
            "cumulative_qty": "100",
            "leaves_qty": "0",
            "ts_event_ms": 1745640000000
        }}"#
    );
    let ev: EngineEvent = serde_json::from_str(&json).unwrap();
    match ev {
        EngineEvent::OrderFilled {
            client_order_id,
            venue_order_id,
            trade_id,
            last_qty,
            last_price,
            cumulative_qty,
            leaves_qty,
            ts_event_ms,
        } => {
            assert_eq!(client_order_id, CID);
            assert_eq!(venue_order_id, VID);
            assert_eq!(trade_id, TID);
            assert_eq!(last_qty, "100");
            assert_eq!(last_price, "3500");
            assert_eq!(cumulative_qty, "100");
            assert_eq!(leaves_qty, "0", "全約定は leaves_qty == 0");
            assert_eq!(ts_event_ms, 1_745_640_000_000);
        }
        other => panic!("expected OrderFilled, got {:?}", other),
    }
}

// ── OrderFilled (部分約定: leaves_qty > 0) ────────────────────────────────────

#[test]
fn order_filled_partial_fill_deserializes() {
    let json = format!(
        r#"{{
            "event": "OrderFilled",
            "client_order_id": "{CID}",
            "venue_order_id": "{VID}",
            "trade_id": "EDA-002",
            "last_qty": "50",
            "last_price": "3500",
            "cumulative_qty": "50",
            "leaves_qty": "50",
            "ts_event_ms": 1745640001000
        }}"#
    );
    let ev: EngineEvent = serde_json::from_str(&json).unwrap();
    match ev {
        EngineEvent::OrderFilled {
            leaves_qty,
            last_qty,
            trade_id,
            ..
        } => {
            assert_ne!(leaves_qty, "0", "部分約定は leaves_qty > 0");
            assert_eq!(leaves_qty, "50");
            assert_eq!(last_qty, "50");
            assert_eq!(trade_id, "EDA-002");
        }
        other => panic!("expected OrderFilled, got {:?}", other),
    }
}

#[test]
fn order_filled_roundtrip_json_shape() {
    // Python が emit する JSON の shape を検証
    let json = format!(
        r#"{{
            "event": "OrderFilled",
            "client_order_id": "{CID}",
            "venue_order_id": "{VID}",
            "trade_id": "{TID}",
            "last_qty": "100",
            "last_price": "3500",
            "cumulative_qty": "100",
            "leaves_qty": "0",
            "ts_event_ms": 1745640000000
        }}"#
    );
    // デシリアライズできることが保証されれば OK
    let ev: EngineEvent = serde_json::from_str(&json).expect("OrderFilled must deserialize");
    assert!(matches!(ev, EngineEvent::OrderFilled { .. }));
}

// ── OrderCanceled ─────────────────────────────────────────────────────────────

#[test]
fn order_canceled_deserializes() {
    let json = format!(
        r#"{{
            "event": "OrderCanceled",
            "client_order_id": "{CID}",
            "venue_order_id": "{VID}",
            "ts_event_ms": 1745640002000
        }}"#
    );
    let ev: EngineEvent = serde_json::from_str(&json).unwrap();
    match ev {
        EngineEvent::OrderCanceled {
            client_order_id,
            venue_order_id,
            ts_event_ms,
        } => {
            assert_eq!(client_order_id, CID);
            assert_eq!(venue_order_id, VID);
            assert_eq!(ts_event_ms, 1_745_640_002_000);
        }
        other => panic!("expected OrderCanceled, got {:?}", other),
    }
}

// ── OrderExpired ──────────────────────────────────────────────────────────────

#[test]
fn order_expired_deserializes() {
    let json = format!(
        r#"{{
            "event": "OrderExpired",
            "client_order_id": "{CID}",
            "venue_order_id": "{VID}",
            "ts_event_ms": 1745640003000
        }}"#
    );
    let ev: EngineEvent = serde_json::from_str(&json).unwrap();
    match ev {
        EngineEvent::OrderExpired {
            client_order_id,
            venue_order_id,
            ts_event_ms,
        } => {
            assert_eq!(client_order_id, CID);
            assert_eq!(venue_order_id, VID);
            assert_eq!(ts_event_ms, 1_745_640_003_000);
        }
        other => panic!("expected OrderExpired, got {:?}", other),
    }
}

// ── trade_id フィールド存在確認（重複検知キー）────────────────────────────────

#[test]
fn order_filled_has_trade_id_field() {
    // trade_id は重複検知キー (venue_order_id, trade_id) の一部
    let json = r#"{
        "event": "OrderFilled",
        "client_order_id": "cid-dedup",
        "venue_order_id": "ORD-DEDUP",
        "trade_id": "TRADE-UNIQUE-001",
        "last_qty": "100",
        "last_price": "3500",
        "cumulative_qty": "100",
        "leaves_qty": "0",
        "ts_event_ms": 1745640000000
    }"#;
    let ev: EngineEvent = serde_json::from_str(json).unwrap();
    match ev {
        EngineEvent::OrderFilled { trade_id, .. } => {
            assert_eq!(trade_id, "TRADE-UNIQUE-001");
        }
        other => panic!("expected OrderFilled, got {:?}", other),
    }
}

// ── leaves_qty == "0" で全約定の判定（nautilus 流）───────────────────────────

#[test]
fn leaves_qty_zero_means_full_fill() {
    let json = r#"{
        "event": "OrderFilled",
        "client_order_id": "cid-full",
        "venue_order_id": "ORD-FULL",
        "trade_id": "TRADE-FULL",
        "last_qty": "100",
        "last_price": "4000",
        "cumulative_qty": "100",
        "leaves_qty": "0",
        "ts_event_ms": 1745640000000
    }"#;
    let ev: EngineEvent = serde_json::from_str(json).unwrap();
    match ev {
        EngineEvent::OrderFilled { leaves_qty, .. } => {
            // nautilus 流: leaves_qty == "0" → 全約定
            assert_eq!(leaves_qty, "0");
        }
        other => panic!("expected OrderFilled, got {:?}", other),
    }
}
