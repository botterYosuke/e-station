//! Tpre.2 / D3-1: SubmitOrderRequest / OrderModifyChange は deny_unknown_fields を付与し、
//! second_password / secondPassword / p_no / 任意 _extra を含む JSON で deserialize error になることを assert。
//! C-R2-M3 (invariant-tests.md) に対応。
//! M-10: OrderListFilter にも deny_unknown_fields を追加し、未知フィールドを拒絶する。

use flowsurface_engine_client::dto::{OrderListFilter, OrderModifyChange, SubmitOrderRequest};

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

// ── M-10: OrderListFilter deny_unknown_fields ─────────────────────────────────

#[test]
fn order_list_filter_rejects_unknown_field() {
    let json = r#"{
        "status": "ACCEPTED",
        "instrument_id": null,
        "date": null,
        "_injected": "evil"
    }"#;
    let result: Result<OrderListFilter, _> = serde_json::from_str(json);
    assert!(
        result.is_err(),
        "OrderListFilter must reject unknown fields, got Ok"
    );
}

#[test]
fn order_list_filter_accepts_valid_json() {
    let json = r#"{
        "status": "ACCEPTED",
        "instrument_id": "7203.TSE",
        "date": "20260426"
    }"#;
    let result: Result<OrderListFilter, _> = serde_json::from_str(json);
    assert!(
        result.is_ok(),
        "valid OrderListFilter must deserialize: {:?}",
        result.err()
    );
}

#[test]
fn order_list_filter_accepts_all_none() {
    let json = r#"{
        "status": null,
        "instrument_id": null,
        "date": null
    }"#;
    let result: Result<OrderListFilter, _> = serde_json::from_str(json);
    assert!(
        result.is_ok(),
        "all-null OrderListFilter must deserialize: {:?}",
        result.err()
    );
}

#[test]
fn order_list_filter_accepts_absent_optional_fields() {
    let json = r#"{}"#;
    let result: Result<OrderListFilter, _> = serde_json::from_str(json);
    assert!(
        result.is_ok(),
        "absent fields must default to None: {:?}",
        result.err()
    );
    let filter = result.unwrap();
    assert!(filter.status.is_none());
    assert!(filter.instrument_id.is_none());
    assert!(filter.date.is_none());
}
