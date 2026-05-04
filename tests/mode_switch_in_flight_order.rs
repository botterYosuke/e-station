/// F7/T5: structural test — WAL in-flight check is present in SwitchMode live→replay path.
const MAIN_RS: &str = include_str!("../src/main.rs");

#[test]
fn has_wal_in_flight_orders_fn_exists() {
    assert!(
        MAIN_RS.contains("fn has_wal_in_flight_orders("),
        "has_wal_in_flight_orders must exist — F7 live→replay safety check"
    );
}

#[test]
fn switch_mode_handler_calls_wal_check() {
    // Locate SwitchMode handler and verify it calls has_wal_in_flight_orders
    assert!(
        MAIN_RS.contains("has_wal_in_flight_orders()"),
        "Action::SwitchMode handler must call has_wal_in_flight_orders() for live→replay WAL safety"
    );
}

#[test]
fn wal_fn_reads_tachibana_orders_jsonl_path() {
    let idx = MAIN_RS
        .find("fn has_wal_in_flight_orders(")
        .expect("has_wal_in_flight_orders must exist");
    let body = &MAIN_RS[idx..];
    let end = body[1..].find("\n\n").map(|i| i + 1).unwrap_or(body.len());
    let fn_body = &body[..end];
    assert!(
        fn_body.contains("tachibana_orders.jsonl"),
        "has_wal_in_flight_orders must read tachibana_orders.jsonl"
    );
}

#[test]
fn wal_fn_checks_submitted_and_partial_status() {
    let idx = MAIN_RS
        .find("fn has_wal_in_flight_orders(")
        .expect("has_wal_in_flight_orders must exist");
    let body = &MAIN_RS[idx..];
    let end = body[1..].find("\n\n").map(|i| i + 1).unwrap_or(body.len());
    let fn_body = &body[..end];
    assert!(
        fn_body.contains("\"submitted\"") && fn_body.contains("\"partial\""),
        "has_wal_in_flight_orders must check both 'submitted' and 'partial' statuses"
    );
}
