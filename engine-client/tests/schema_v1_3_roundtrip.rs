//! Tpre.2: schema 1.3 の全新規 IPC variant のラウンドトリップテスト。
//! Rust serialize → JSON → Python が期待する shape であることを確認。
//! Python → Rust 方向は Python 側テスト (test_order_schema_v1_3.py) で検証。

use flowsurface_engine_client::dto::{
    Command, EngineEvent, OrderListFilter, OrderModifyChange, OrderSide, OrderType,
    SubmitOrderRequest, TimeInForce,
};

const CID: &str = "3e4d5f6a-7b8c-9d0e-1f2a-3b4c5d6e7f80";
const VID: &str = "987654";
const RID: &str = "req-001";

// ── SubmitOrder serialize ─────────────────────────────────────────────────────

#[test]
fn submit_order_serializes_market_buy() {
    let cmd = Command::SubmitOrder {
        request_id: RID.to_string(),
        venue: "tachibana".to_string(),
        order: SubmitOrderRequest {
            client_order_id: CID.to_string(),
            instrument_id: "7203.TSE".to_string(),
            order_side: OrderSide::Buy,
            order_type: OrderType::Market,
            quantity: "100".to_string(),
            price: None,
            trigger_price: None,
            trigger_type: None,
            time_in_force: TimeInForce::Day,
            expire_time_ns: None,
            post_only: false,
            reduce_only: false,
            tags: vec!["cash_margin=cash".to_string()],
        },
    };
    let json = serde_json::to_string(&cmd).unwrap();
    assert!(json.contains(r#""op":"SubmitOrder""#), "got: {json}");
    assert!(json.contains(r#""venue":"tachibana""#), "got: {json}");
    assert!(json.contains(CID), "got: {json}");
    assert!(json.contains(r#""order_side":"BUY""#), "got: {json}");
    assert!(json.contains(r#""order_type":"MARKET""#), "got: {json}");
    assert!(json.contains(r#""time_in_force":"DAY""#), "got: {json}");
    assert!(json.contains(r#""post_only":false"#), "got: {json}");
    assert!(json.contains(r#""cash_margin=cash""#), "got: {json}");
}

#[test]
fn submit_order_serializes_limit_sell() {
    let cmd = Command::SubmitOrder {
        request_id: RID.to_string(),
        venue: "tachibana".to_string(),
        order: SubmitOrderRequest {
            client_order_id: CID.to_string(),
            instrument_id: "9984.TSE".to_string(),
            order_side: OrderSide::Sell,
            order_type: OrderType::Limit,
            quantity: "50".to_string(),
            price: Some("3500".to_string()),
            trigger_price: None,
            trigger_type: None,
            time_in_force: TimeInForce::Day,
            expire_time_ns: None,
            post_only: false,
            reduce_only: false,
            tags: vec![],
        },
    };
    let json = serde_json::to_string(&cmd).unwrap();
    assert!(json.contains(r#""order_side":"SELL""#), "got: {json}");
    assert!(json.contains(r#""order_type":"LIMIT""#), "got: {json}");
    assert!(json.contains(r#""price":"3500""#), "got: {json}");
}

// ── ModifyOrder serialize ─────────────────────────────────────────────────────

#[test]
fn modify_order_serializes() {
    let cmd = Command::ModifyOrder {
        request_id: RID.to_string(),
        venue: "tachibana".to_string(),
        client_order_id: CID.to_string(),
        change: OrderModifyChange {
            new_quantity: None,
            new_price: Some("3600".to_string()),
            new_trigger_price: None,
            new_expire_time_ns: None,
        },
    };
    let json = serde_json::to_string(&cmd).unwrap();
    assert!(json.contains(r#""op":"ModifyOrder""#), "got: {json}");
    assert!(json.contains(r#""new_price":"3600""#), "got: {json}");
}

// ── CancelOrder serialize ─────────────────────────────────────────────────────

#[test]
fn cancel_order_serializes() {
    let cmd = Command::CancelOrder {
        request_id: RID.to_string(),
        venue: "tachibana".to_string(),
        client_order_id: CID.to_string(),
        venue_order_id: VID.to_string(),
    };
    let json = serde_json::to_string(&cmd).unwrap();
    assert!(json.contains(r#""op":"CancelOrder""#), "got: {json}");
    assert!(json.contains(VID), "got: {json}");
}

// ── CancelAllOrders serialize ─────────────────────────────────────────────────

#[test]
fn cancel_all_orders_serializes() {
    let cmd = Command::CancelAllOrders {
        request_id: RID.to_string(),
        venue: "tachibana".to_string(),
        instrument_id: Some("7203.TSE".to_string()),
        order_side: Some(OrderSide::Buy),
    };
    let json = serde_json::to_string(&cmd).unwrap();
    assert!(json.contains(r#""op":"CancelAllOrders""#), "got: {json}");
    assert!(json.contains(r#""order_side":"BUY""#), "got: {json}");
}

// ── GetOrderList serialize ────────────────────────────────────────────────────

#[test]
fn get_order_list_serializes() {
    let cmd = Command::GetOrderList {
        request_id: RID.to_string(),
        venue: "tachibana".to_string(),
        filter: OrderListFilter {
            status: Some("ACCEPTED".to_string()),
            instrument_id: None,
            date: None,
        },
    };
    let json = serde_json::to_string(&cmd).unwrap();
    assert!(json.contains(r#""op":"GetOrderList""#), "got: {json}");
    assert!(json.contains(r#""status":"ACCEPTED""#), "got: {json}");
}

// ── Order Events deserialize ──────────────────────────────────────────────────

#[test]
fn order_submitted_event_deserializes() {
    let json = r#"{"event":"OrderSubmitted","client_order_id":"abc","ts_event_ms":1700000000000}"#;
    let ev: EngineEvent = serde_json::from_str(json).unwrap();
    match ev {
        EngineEvent::OrderSubmitted {
            client_order_id,
            ts_event_ms,
        } => {
            assert_eq!(client_order_id, "abc");
            assert_eq!(ts_event_ms, 1_700_000_000_000);
        }
        _ => panic!("expected OrderSubmitted, got {:?}", ev),
    }
}

#[test]
fn order_accepted_event_deserializes() {
    let json = r#"{"event":"OrderAccepted","client_order_id":"abc","venue_order_id":"V123","ts_event_ms":1700000000001}"#;
    let ev: EngineEvent = serde_json::from_str(json).unwrap();
    match ev {
        EngineEvent::OrderAccepted {
            client_order_id,
            venue_order_id,
            ..
        } => {
            assert_eq!(client_order_id, "abc");
            assert_eq!(venue_order_id, "V123");
        }
        _ => panic!("expected OrderAccepted, got {:?}", ev),
    }
}

#[test]
fn order_rejected_event_deserializes() {
    let json = r#"{"event":"OrderRejected","client_order_id":"abc","reason_code":"SECOND_PASSWORD_REQUIRED","reason_text":"","ts_event_ms":1700000000002}"#;
    let ev: EngineEvent = serde_json::from_str(json).unwrap();
    match ev {
        EngineEvent::OrderRejected { reason_code, .. } => {
            assert_eq!(reason_code, "SECOND_PASSWORD_REQUIRED");
        }
        _ => panic!("expected OrderRejected, got {:?}", ev),
    }
}

#[test]
fn order_filled_event_deserializes() {
    let json = r#"{
        "event": "OrderFilled",
        "client_order_id": "abc",
        "venue_order_id": "V123",
        "trade_id": "T001",
        "last_qty": "100",
        "last_price": "3000",
        "cumulative_qty": "100",
        "leaves_qty": "0",
        "ts_event_ms": 1700000000010
    }"#;
    let ev: EngineEvent = serde_json::from_str(json).unwrap();
    match ev {
        EngineEvent::OrderFilled {
            trade_id,
            leaves_qty,
            ..
        } => {
            assert_eq!(trade_id, "T001");
            assert_eq!(leaves_qty, "0");
        }
        _ => panic!("expected OrderFilled, got {:?}", ev),
    }
}

#[test]
fn order_canceled_event_deserializes() {
    let json = r#"{"event":"OrderCanceled","client_order_id":"abc","venue_order_id":"V123","ts_event_ms":1700000000020}"#;
    let ev: EngineEvent = serde_json::from_str(json).unwrap();
    assert!(
        matches!(ev, EngineEvent::OrderCanceled { .. }),
        "expected OrderCanceled, got {:?}",
        ev
    );
}

// ── Enum SCREAMING_SNAKE_CASE ─────────────────────────────────────────────────

#[test]
fn order_side_serializes_screaming_snake_case() {
    assert_eq!(serde_json::to_string(&OrderSide::Buy).unwrap(), r#""BUY""#);
    assert_eq!(
        serde_json::to_string(&OrderSide::Sell).unwrap(),
        r#""SELL""#
    );
}

#[test]
fn order_type_serializes_screaming_snake_case() {
    assert_eq!(
        serde_json::to_string(&OrderType::Market).unwrap(),
        r#""MARKET""#
    );
    assert_eq!(
        serde_json::to_string(&OrderType::StopLimit).unwrap(),
        r#""STOP_LIMIT""#
    );
    assert_eq!(
        serde_json::to_string(&OrderType::MarketIfTouched).unwrap(),
        r#""MARKET_IF_TOUCHED""#
    );
}

#[test]
fn time_in_force_serializes_screaming_snake_case() {
    assert_eq!(
        serde_json::to_string(&TimeInForce::Day).unwrap(),
        r#""DAY""#
    );
    assert_eq!(
        serde_json::to_string(&TimeInForce::AtTheOpen).unwrap(),
        r#""AT_THE_OPEN""#
    );
    assert_eq!(
        serde_json::to_string(&TimeInForce::AtTheClose).unwrap(),
        r#""AT_THE_CLOSE""#
    );
}

#[test]
fn schema_minor_is_3() {
    assert_eq!(flowsurface_engine_client::SCHEMA_MINOR, 3);
}
