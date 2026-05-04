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
fn wal_fn_uses_phase_key_and_rejected_terminal() {
    // C1: writer (`tachibana_orders.py::_audit_log_*`) uses the `phase` key with
    // values `submit` / `accepted` / `rejected`. The reader must read `phase`
    // (not `status`) and treat only `rejected` as terminal — `submit` and
    // `accepted` (and any unknown value) are conservatively in-flight.
    let idx = MAIN_RS
        .find("fn has_wal_in_flight_orders_at(")
        .expect("has_wal_in_flight_orders_at must exist");
    let body = &MAIN_RS[idx..];
    // Cap at the next top-level `fn ` to delimit the function body.
    let end = body[1..].find("\nfn ").map(|i| i + 1).unwrap_or(body.len());
    let fn_body = &body[..end];
    assert!(
        fn_body.contains("\"phase\""),
        "has_wal_in_flight_orders_at must read the `phase` key, not `status` (C1)"
    );
    assert!(
        fn_body.contains("\"rejected\""),
        "has_wal_in_flight_orders_at must treat `rejected` as the terminal phase (C1)"
    );
    assert!(
        !fn_body.contains("\"filled\"") && !fn_body.contains("\"cancelled\""),
        "after C1 fix: `filled`/`cancelled` are NOT writer phase values; reader \
         must not enumerate them"
    );
}

#[test]
fn wal_fn_logs_io_error() {
    // M6: IO errors must be surfaced via log::warn! instead of being silently
    // treated as "no in-flight orders".
    let idx = MAIN_RS
        .find("fn has_wal_in_flight_orders_at(")
        .expect("has_wal_in_flight_orders_at must exist");
    let body = &MAIN_RS[idx..];
    let end = body[1..].find("\nfn ").map(|i| i + 1).unwrap_or(body.len());
    let fn_body = &body[..end];
    assert!(
        fn_body.contains("log::warn!"),
        "has_wal_in_flight_orders_at must log::warn! on IO errors (M6)"
    );
}
