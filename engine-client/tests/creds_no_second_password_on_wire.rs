//! D2-M5: `Command::SubmitOrder` の IPC フレームに `second_password` が漏洩しないことを検証。
//!
//! `SetSecondPassword` が second_password を送る唯一の合法経路であり、
//! `SubmitOrderRequest` には second_password フィールドが存在しないため、
//! シリアライズ結果にも含まれてはならない。

use flowsurface_engine_client::dto::{
    Command, OrderSide, OrderType, SubmitOrderRequest, TimeInForce,
};

/// SubmitOrder の JSON 直列化に `second_password` / `sSecondPassword` が含まれないことを確認。
#[test]
fn test_submit_order_json_has_no_second_password_field() {
    let req = SubmitOrderRequest {
        client_order_id: "test-cid-001".to_string(),
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
    };
    let cmd = Command::SubmitOrder {
        request_id: "req-d2m5-001".to_string(),
        venue: "tachibana".to_string(),
        order: req,
    };
    let json = serde_json::to_string(&cmd).unwrap();

    assert!(
        !json.contains("second_password"),
        "SubmitOrder JSON must not contain 'second_password'; got: {json}"
    );
    assert!(
        !json.contains("sSecondPassword"),
        "SubmitOrder JSON must not contain 'sSecondPassword'; got: {json}"
    );
}

/// `deny_unknown_fields` により second_password を SubmitOrderRequest に inject できないことを確認。
/// (dto_deny_unknown_fields.rs の補完テスト — wire 受信側の対称性を pin する)
#[test]
fn test_submit_order_request_rejects_second_password_injection() {
    let json_with_secret = r#"{
        "client_order_id": "cid",
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
        "tags": ["cash_margin=cash"],
        "request_key": 0,
        "second_password": "SHOULD_NOT_WORK"
    }"#;

    let result: Result<SubmitOrderRequest, _> = serde_json::from_str(json_with_secret);
    assert!(
        result.is_err(),
        "deny_unknown_fields must reject second_password injection"
    );
}

/// `Command::SetSecondPassword` は second_password を wire に乗せる唯一の合法経路であることを確認。
/// SubmitOrder には存在しないフィールドが SetSecondPassword にはある、という非対称性を pin する。
#[test]
fn test_set_second_password_is_only_legal_path_for_secret() {
    let cmd = Command::SetSecondPassword {
        request_id: "req-001".to_string(),
        value: "secret123".to_string(),
    };
    let json = serde_json::to_string(&cmd).unwrap();

    // SetSecondPassword には value フィールドが存在する (合法経路)
    assert!(
        json.contains("SetSecondPassword") || json.contains("set_second_password"),
        "SetSecondPassword command must serialize to its op variant; got: {json}"
    );

    // SubmitOrder JSON には value も second_password も存在しない
    let req = SubmitOrderRequest {
        client_order_id: "test-cid-002".to_string(),
        instrument_id: "7203.TSE".to_string(),
        order_side: OrderSide::Sell,
        order_type: OrderType::Market,
        quantity: "200".to_string(),
        price: None,
        trigger_price: None,
        trigger_type: None,
        time_in_force: TimeInForce::Day,
        expire_time_ns: None,
        post_only: false,
        reduce_only: false,
        tags: vec![],
        request_key: 0,
    };
    let submit_cmd = Command::SubmitOrder {
        request_id: "req-d2m5-002".to_string(),
        venue: "tachibana".to_string(),
        order: req,
    };
    let submit_json = serde_json::to_string(&submit_cmd).unwrap();

    assert!(
        !submit_json.contains("second_password"),
        "SubmitOrder must not leak second_password onto the wire; got: {submit_json}"
    );
    assert!(
        !submit_json.contains("\"value\""),
        "SubmitOrder must not contain a bare 'value' field that could carry secrets; \
         got: {submit_json}"
    );
}
