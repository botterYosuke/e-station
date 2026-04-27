//! Tpre.2: schema 1.3 の全新規 IPC variant のラウンドトリップテスト。
//! Rust serialize → JSON → Python が期待する shape であることを確認。
//! Python → Rust 方向は Python 側テスト (test_order_schema_v1_3.py) で検証。

use flowsurface_engine_client::dto::{
    Command, EngineEvent, OrderListFilter, OrderModifyChange, OrderSide, OrderType,
    SubmitOrderRequest, TimeInForce, TriggerType,
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
            request_key: 0,
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
    assert!(json.contains(r#""request_key":0"#), "got: {json}");
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
            request_key: 0,
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
fn rust_deserializes_python_order_accepted() {
    let json = r#"{"event":"OrderAccepted","client_order_id":"abc","venue_order_id":"ORD123","ts_event_ms":1700000000001}"#;
    let ev: EngineEvent = serde_json::from_str(json).unwrap();
    match ev {
        EngineEvent::OrderAccepted {
            client_order_id,
            venue_order_id,
            ..
        } => {
            assert_eq!(client_order_id, "abc");
            assert_eq!(venue_order_id, Some("ORD123".to_string()));
        }
        _ => panic!("expected OrderAccepted, got {:?}", ev),
    }
}

#[test]
fn rust_deserializes_python_order_accepted_with_null_venue_order_id() {
    let json = r#"{"event":"OrderAccepted","client_order_id":"abc","venue_order_id":null,"ts_event_ms":1700000000001}"#;
    let ev: EngineEvent = serde_json::from_str(json).unwrap();
    match ev {
        EngineEvent::OrderAccepted {
            client_order_id,
            venue_order_id,
            ..
        } => {
            assert_eq!(client_order_id, "abc");
            assert_eq!(venue_order_id, None);
        }
        _ => panic!("expected OrderAccepted, got {:?}", ev),
    }
}

#[test]
fn rust_deserializes_python_order_accepted_with_absent_venue_order_id() {
    // `#[serde(default)]` — Python が venue_order_id フィールドを省略した場合も None になること
    let json = r#"{"event":"OrderAccepted","client_order_id":"abc","ts_event_ms":1700000000001}"#;
    let ev: EngineEvent = serde_json::from_str(json).unwrap();
    match ev {
        EngineEvent::OrderAccepted { venue_order_id, .. } => {
            assert_eq!(venue_order_id, None);
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

// ── C-1 (M-3): TriggerType SCREAMING_SNAKE_CASE roundtrip ────────────────────

#[test]
fn trigger_type_screaming_snake_case() {
    let last = serde_json::to_string(&TriggerType::Last).unwrap();
    assert_eq!(last, r#""LAST""#);
    let bid_ask = serde_json::to_string(&TriggerType::BidAsk).unwrap();
    assert_eq!(bid_ask, r#""BID_ASK""#);
    let index = serde_json::to_string(&TriggerType::Index).unwrap();
    assert_eq!(index, r#""INDEX""#);
}

/// Schema 2.0: SCHEMA_MAJOR bumped to 2, SCHEMA_MINOR reset to 0.
/// SetVenueCredentials / VenueCredentialsRefreshed removed; Python autonomous login.
#[test]
fn schema_major_is_2() {
    assert_eq!(
        flowsurface_engine_client::SCHEMA_MAJOR,
        2,
        "SCHEMA_MAJOR must be 2 (schema 2.x autonomous-login series), got {}",
        flowsurface_engine_client::SCHEMA_MAJOR
    );
}

/// H-E: SubmitOrderRequest serializes request_key as a numeric field.
/// Nonzero value round-trips correctly through JSON.
#[test]
fn submit_order_request_key_roundtrips() {
    let req = SubmitOrderRequest {
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
        tags: vec![],
        request_key: 1_234_567_890_u64,
    };
    let json = serde_json::to_string(&req).unwrap();
    assert!(
        json.contains(r#""request_key":1234567890"#),
        "request_key must serialize as numeric: {json}"
    );
    let decoded: SubmitOrderRequest = serde_json::from_str(&json).unwrap();
    assert_eq!(decoded.request_key, 1_234_567_890_u64);
}

/// H-E: SubmitOrderRequest without request_key in JSON defaults to 0 (backward compat).
#[test]
fn submit_order_request_key_defaults_to_zero_when_absent() {
    // Simulate an old Rust sender that does not include request_key
    let json = r#"{
        "client_order_id": "old-cid",
        "instrument_id": "7203.TSE",
        "order_side": "BUY",
        "order_type": "MARKET",
        "quantity": "100",
        "price": null,
        "trigger_price": null,
        "trigger_type": null,
        "time_in_force": "DAY",
        "expire_time_ns": null,
        "post_only": false,
        "reduce_only": false,
        "tags": []
    }"#;
    let req: SubmitOrderRequest = serde_json::from_str(json).unwrap();
    assert_eq!(req.request_key, 0, "absent request_key must default to 0");
}

// ── M-4: SetSecondPassword / ForgetSecondPassword serialize ──────────────────

#[test]
fn rust_serializes_set_second_password() {
    let cmd = Command::SetSecondPassword {
        request_id: RID.to_string(),
        value: "sentinel-value".to_string(),
    };
    let json = serde_json::to_string(&cmd).unwrap();
    // op タグが正しいこと
    assert!(json.contains(r#""op":"SetSecondPassword""#), "got: {json}");
    // value フィールドが存在すること（Debug はマスクするが JSON serialize は平文）
    assert!(json.contains(r#""value""#), "got: {json}");
    // request_id が存在すること
    assert!(json.contains(RID), "got: {json}");
    // Debug でマスクされていることを確認
    let debug_str = format!("{:?}", cmd);
    assert!(
        !debug_str.contains("sentinel-value"),
        "Debug must mask value, got: {debug_str}"
    );
    assert!(
        debug_str.contains("[REDACTED]"),
        "Debug must show [REDACTED], got: {debug_str}"
    );
}

#[test]
fn rust_serializes_forget_second_password() {
    let cmd = Command::ForgetSecondPassword;
    let json = serde_json::to_string(&cmd).unwrap();
    assert!(
        json.contains(r#""op":"ForgetSecondPassword""#),
        "got: {json}"
    );
}

// ── M-5: Python → Rust event deserialize ─────────────────────────────────────

#[test]
fn rust_deserializes_python_second_password_required() {
    let json = r#"{"event":"SecondPasswordRequired","request_id":"req-spw-1"}"#;
    let ev: EngineEvent = serde_json::from_str(json).unwrap();
    match ev {
        EngineEvent::SecondPasswordRequired { request_id } => {
            assert_eq!(request_id, "req-spw-1");
        }
        _ => panic!("expected SecondPasswordRequired, got {:?}", ev),
    }
}

#[test]
fn rust_deserializes_python_order_pending_update() {
    let json =
        r#"{"event":"OrderPendingUpdate","client_order_id":"cid-pu","ts_event_ms":1700000000100}"#;
    let ev: EngineEvent = serde_json::from_str(json).unwrap();
    match ev {
        EngineEvent::OrderPendingUpdate {
            client_order_id,
            ts_event_ms,
        } => {
            assert_eq!(client_order_id, "cid-pu");
            assert_eq!(ts_event_ms, 1_700_000_000_100);
        }
        _ => panic!("expected OrderPendingUpdate, got {:?}", ev),
    }
}

#[test]
fn rust_deserializes_python_order_pending_cancel() {
    let json =
        r#"{"event":"OrderPendingCancel","client_order_id":"cid-pc","ts_event_ms":1700000000200}"#;
    let ev: EngineEvent = serde_json::from_str(json).unwrap();
    match ev {
        EngineEvent::OrderPendingCancel {
            client_order_id,
            ts_event_ms,
        } => {
            assert_eq!(client_order_id, "cid-pc");
            assert_eq!(ts_event_ms, 1_700_000_000_200);
        }
        _ => panic!("expected OrderPendingCancel, got {:?}", ev),
    }
}

#[test]
fn rust_deserializes_python_order_expired() {
    let json = r#"{"event":"OrderExpired","client_order_id":"cid-exp","venue_order_id":"V777","ts_event_ms":1700000000300}"#;
    let ev: EngineEvent = serde_json::from_str(json).unwrap();
    match ev {
        EngineEvent::OrderExpired {
            client_order_id,
            venue_order_id,
            ts_event_ms,
        } => {
            assert_eq!(client_order_id, "cid-exp");
            assert_eq!(venue_order_id, "V777");
            assert_eq!(ts_event_ms, 1_700_000_000_300);
        }
        _ => panic!("expected OrderExpired, got {:?}", ev),
    }
}
