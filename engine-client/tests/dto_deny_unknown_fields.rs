//! Tpre.2 / D3-1: SubmitOrderRequest / OrderModifyChange は deny_unknown_fields を付与し、
//! second_password / secondPassword / p_no / 任意 _extra を含む JSON で deserialize error になることを assert。
//! C-R2-M3 (invariant-tests.md) に対応。

use flowsurface_engine_client::dto::{OrderModifyChange, SubmitOrderRequest};

fn valid_submit_json() -> &'static str {
    r#"{
        "client_order_id": "test-id-001",
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
    }"#
}

#[test]
fn submit_order_request_rejects_second_password_field() {
    let json = r#"{
        "client_order_id": "id-1",
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
        "tags": [],
        "second_password": "should_be_rejected"
    }"#;
    let result: Result<SubmitOrderRequest, _> = serde_json::from_str(json);
    assert!(
        result.is_err(),
        "second_password must be rejected by deny_unknown_fields"
    );
}

#[test]
fn submit_order_request_rejects_camelcase_second_password() {
    let json = r#"{
        "client_order_id": "id-1",
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
        "tags": [],
        "secondPassword": "should_be_rejected"
    }"#;
    let result: Result<SubmitOrderRequest, _> = serde_json::from_str(json);
    assert!(result.is_err(), "secondPassword must be rejected");
}

#[test]
fn submit_order_request_rejects_p_no_field() {
    let json = r#"{
        "client_order_id": "id-1",
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
        "tags": [],
        "p_no": "123456"
    }"#;
    let result: Result<SubmitOrderRequest, _> = serde_json::from_str(json);
    assert!(
        result.is_err(),
        "p_no must be rejected by deny_unknown_fields"
    );
}

#[test]
fn submit_order_request_rejects_arbitrary_extra_field() {
    let json = r#"{
        "client_order_id": "id-1",
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
        "tags": [],
        "_extra": "evil_injection"
    }"#;
    let result: Result<SubmitOrderRequest, _> = serde_json::from_str(json);
    assert!(
        result.is_err(),
        "_extra must be rejected by deny_unknown_fields"
    );
}

#[test]
fn submit_order_request_accepts_valid_json() {
    let result: Result<SubmitOrderRequest, _> = serde_json::from_str(valid_submit_json());
    assert!(
        result.is_ok(),
        "valid JSON must deserialize: {:?}",
        result.err()
    );
}

#[test]
fn order_modify_change_rejects_second_password() {
    let json = r#"{
        "new_quantity": null,
        "new_price": "500",
        "new_trigger_price": null,
        "new_expire_time_ns": null,
        "second_password": "injected"
    }"#;
    let result: Result<OrderModifyChange, _> = serde_json::from_str(json);
    assert!(
        result.is_err(),
        "second_password must be rejected in OrderModifyChange"
    );
}

#[test]
fn order_modify_change_accepts_valid_json() {
    let json = r#"{
        "new_quantity": null,
        "new_price": "500",
        "new_trigger_price": null,
        "new_expire_time_ns": null
    }"#;
    let result: Result<OrderModifyChange, _> = serde_json::from_str(json);
    assert!(
        result.is_ok(),
        "valid OrderModifyChange must deserialize: {:?}",
        result.err()
    );
}
