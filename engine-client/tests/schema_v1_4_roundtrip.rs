//! T1.6: schema 1.4 の IPC variant ラウンドトリップテスト。
//!
//! - Rust serialize → JSON shape が Python の期待する形であること
//! - Python → Rust deserialize: OrderListUpdated / OrderPendingUpdate / OrderPendingCancel
//!
//! Phase O1 (T1.1–T1.6) で追加された IPC 変更のみを対象とする。

use flowsurface_engine_client::dto::{
    Command, EngineEvent, OrderListFilter, OrderModifyChange, OrderRecordWire, OrderSide,
    OrderType, TimeInForce,
};

const CID: &str = "4f5e6d7c-8b9a-0c1d-2e3f-4a5b6c7d8e9f";
const VID: &str = "ORD-2026-001";
const RID: &str = "req-v1.4-001";

// ── SCHEMA_MINOR version guard ────────────────────────────────────────────────

// NOTE: SCHEMA_MINOR は Phase O2 で 5 に bump された (schema_v1_5_roundtrip.rs を参照)。
// このテストは schema 1.4 の variant shape が正しいことを検証する目的のため、
// バージョン番号の exact match ガードは schema_v1_5_roundtrip.rs に移譲する。
#[test]
fn schema_minor_is_at_least_4() {
    assert!(
        flowsurface_engine_client::SCHEMA_MINOR >= 4,
        "SCHEMA_MINOR must be >= 4 (Phase O1 introduced v1.4 variants)"
    );
}

// ── ModifyOrder serialize ─────────────────────────────────────────────────────

#[test]
fn modify_order_serializes_with_new_price() {
    let cmd = Command::ModifyOrder {
        request_id: RID.to_string(),
        venue: "tachibana".to_string(),
        client_order_id: CID.to_string(),
        change: OrderModifyChange {
            new_quantity: None,
            new_price: Some("3700".to_string()),
            new_trigger_price: None,
            new_expire_time_ns: None,
        },
    };
    let json = serde_json::to_string(&cmd).unwrap();
    assert!(json.contains(r#""op":"ModifyOrder""#), "got: {json}");
    assert!(json.contains(r#""venue":"tachibana""#), "got: {json}");
    assert!(json.contains(CID), "got: {json}");
    assert!(json.contains(r#""new_price":"3700""#), "got: {json}");
    assert!(json.contains(r#""new_quantity":null"#), "got: {json}");
}

#[test]
fn modify_order_serializes_with_new_quantity() {
    let cmd = Command::ModifyOrder {
        request_id: RID.to_string(),
        venue: "tachibana".to_string(),
        client_order_id: CID.to_string(),
        change: OrderModifyChange {
            new_quantity: Some("200".to_string()),
            new_price: None,
            new_trigger_price: None,
            new_expire_time_ns: None,
        },
    };
    let json = serde_json::to_string(&cmd).unwrap();
    assert!(json.contains(r#""new_quantity":"200""#), "got: {json}");
    assert!(json.contains(r#""new_price":null"#), "got: {json}");
}

// ── CancelOrder serialize ─────────────────────────────────────────────────────

#[test]
fn cancel_order_serializes_with_venue_order_id() {
    let cmd = Command::CancelOrder {
        request_id: RID.to_string(),
        venue: "tachibana".to_string(),
        client_order_id: CID.to_string(),
        venue_order_id: VID.to_string(),
    };
    let json = serde_json::to_string(&cmd).unwrap();
    assert!(json.contains(r#""op":"CancelOrder""#), "got: {json}");
    assert!(json.contains(VID), "got: {json}");
    assert!(json.contains(CID), "got: {json}");
}

// ── CancelAllOrders serialize ─────────────────────────────────────────────────

#[test]
fn cancel_all_orders_no_filter_serializes() {
    let cmd = Command::CancelAllOrders {
        request_id: RID.to_string(),
        venue: "tachibana".to_string(),
        instrument_id: None,
        order_side: None,
    };
    let json = serde_json::to_string(&cmd).unwrap();
    assert!(json.contains(r#""op":"CancelAllOrders""#), "got: {json}");
    assert!(json.contains(r#""instrument_id":null"#), "got: {json}");
    assert!(json.contains(r#""order_side":null"#), "got: {json}");
}

#[test]
fn cancel_all_orders_with_filter_serializes() {
    let cmd = Command::CancelAllOrders {
        request_id: RID.to_string(),
        venue: "tachibana".to_string(),
        instrument_id: Some("7203.TSE".to_string()),
        order_side: Some(OrderSide::Sell),
    };
    let json = serde_json::to_string(&cmd).unwrap();
    assert!(json.contains(r#""order_side":"SELL""#), "got: {json}");
    assert!(json.contains(r#""instrument_id":"7203.TSE""#), "got: {json}");
}

// ── GetOrderList serialize ────────────────────────────────────────────────────

#[test]
fn get_order_list_empty_filter_serializes() {
    let cmd = Command::GetOrderList {
        request_id: RID.to_string(),
        venue: "tachibana".to_string(),
        filter: OrderListFilter {
            status: None,
            instrument_id: None,
            date: None,
        },
    };
    let json = serde_json::to_string(&cmd).unwrap();
    assert!(json.contains(r#""op":"GetOrderList""#), "got: {json}");
    assert!(json.contains(r#""filter""#), "got: {json}");
}

#[test]
fn get_order_list_with_instrument_filter_serializes() {
    let cmd = Command::GetOrderList {
        request_id: RID.to_string(),
        venue: "tachibana".to_string(),
        filter: OrderListFilter {
            status: None,
            instrument_id: Some("9984.TSE".to_string()),
            date: None,
        },
    };
    let json = serde_json::to_string(&cmd).unwrap();
    assert!(json.contains(r#""instrument_id":"9984.TSE""#), "got: {json}");
}

// ── OrderListUpdated deserialize (Python → Rust) ──────────────────────────────

#[test]
fn order_list_updated_with_one_record_deserializes() {
    let json = r#"{
        "event": "OrderListUpdated",
        "request_id": "req-list-001",
        "orders": [
            {
                "client_order_id": "cid-001",
                "venue_order_id": "ORD-001",
                "instrument_id": "7203.TSE",
                "order_side": "BUY",
                "order_type": "MARKET",
                "quantity": "100",
                "filled_qty": "0",
                "leaves_qty": "100",
                "price": null,
                "trigger_price": null,
                "time_in_force": "DAY",
                "expire_time_ns": null,
                "status": "SUBMITTED",
                "ts_event_ms": 1745640000000
            }
        ]
    }"#;
    let ev: EngineEvent = serde_json::from_str(json).unwrap();
    match ev {
        EngineEvent::OrderListUpdated { request_id, orders } => {
            assert_eq!(request_id, "req-list-001");
            assert_eq!(orders.len(), 1);
            assert_eq!(orders[0].venue_order_id, "ORD-001");
            assert_eq!(orders[0].client_order_id, Some("cid-001".to_string()));
            assert_eq!(orders[0].quantity, "100");
            assert_eq!(orders[0].status, "SUBMITTED");
        }
        _ => panic!("expected OrderListUpdated, got {:?}", ev),
    }
}

#[test]
fn order_list_updated_empty_list_deserializes() {
    let json = r#"{"event":"OrderListUpdated","request_id":"req-empty","orders":[]}"#;
    let ev: EngineEvent = serde_json::from_str(json).unwrap();
    match ev {
        EngineEvent::OrderListUpdated { orders, .. } => {
            assert!(orders.is_empty());
        }
        _ => panic!("expected OrderListUpdated, got {:?}", ev),
    }
}

#[test]
fn order_list_updated_with_null_client_order_id_deserializes() {
    let json = r#"{
        "event": "OrderListUpdated",
        "request_id": "req-null-cid",
        "orders": [
            {
                "client_order_id": null,
                "venue_order_id": "ORD-002",
                "instrument_id": "9984.TSE",
                "order_side": "SELL",
                "order_type": "LIMIT",
                "quantity": "50",
                "filled_qty": "0",
                "leaves_qty": "50",
                "price": "3500",
                "trigger_price": null,
                "time_in_force": "DAY",
                "expire_time_ns": null,
                "status": "ACCEPTED",
                "ts_event_ms": 1745640001000
            }
        ]
    }"#;
    let ev: EngineEvent = serde_json::from_str(json).unwrap();
    match ev {
        EngineEvent::OrderListUpdated { orders, .. } => {
            assert_eq!(orders.len(), 1);
            assert_eq!(orders[0].client_order_id, None);
            assert_eq!(orders[0].venue_order_id, "ORD-002");
            assert_eq!(orders[0].price, Some("3500".to_string()));
        }
        _ => panic!("expected OrderListUpdated, got {:?}", ev),
    }
}

// ── OrderRecordWire serialize/deserialize roundtrip ───────────────────────────

#[test]
fn order_record_wire_roundtrip() {
    let record = OrderRecordWire {
        client_order_id: Some("cid-rt-001".to_string()),
        venue_order_id: "V-RT-001".to_string(),
        instrument_id: "7203.TSE".to_string(),
        order_side: OrderSide::Buy,
        order_type: OrderType::Limit,
        quantity: "100".to_string(),
        filled_qty: "50".to_string(),
        leaves_qty: "50".to_string(),
        price: Some("3000".to_string()),
        trigger_price: None,
        time_in_force: TimeInForce::Day,
        expire_time_ns: None,
        status: "ACCEPTED".to_string(),
        ts_event_ms: 1_745_640_000_000,
    };

    let json = serde_json::to_string(&record).unwrap();
    let roundtripped: OrderRecordWire = serde_json::from_str(&json).unwrap();

    assert_eq!(roundtripped.client_order_id, record.client_order_id);
    assert_eq!(roundtripped.venue_order_id, record.venue_order_id);
    assert_eq!(roundtripped.instrument_id, record.instrument_id);
    assert_eq!(roundtripped.quantity, record.quantity);
    assert_eq!(roundtripped.filled_qty, record.filled_qty);
    assert_eq!(roundtripped.price, record.price);
    assert_eq!(roundtripped.status, record.status);
    assert_eq!(roundtripped.ts_event_ms, record.ts_event_ms);
}
