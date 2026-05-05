//! C1 / M-2: cross-language WAL writer/reader contract pin.
//!
//! The Python writer (`python/engine/exchanges/tachibana_orders.py::_audit_log_*`)
//! and the Rust reader (`src/main.rs::has_wal_in_flight_orders_at`) must share
//! the same wire schema:
//!
//! - field name: `phase` (NOT `status`)
//! - terminal phase: `rejected`
//! - in-flight phases: `submit`, `accepted`, anything unknown
//! - id field: `client_order_id`
//!
//! This test executes the real Python writer (`uv run python -c ...`) to emit
//! records into a tmp WAL, then a sibling Python invocation runs the reader
//! and prints the in-flight set. The test asserts the cross-language result
//! matches expectations. The Rust reader is pinned via source inspection in
//! `tests/mode_switch_in_flight_order.rs::wal_fn_uses_phase_key_and_rejected_terminal`.

use std::process::Command;

fn uv_python(script: &str) -> String {
    let output = Command::new("uv")
        .args(["run", "python", "-c", script])
        .current_dir(env!("CARGO_MANIFEST_DIR"))
        .output()
        .expect("failed to spawn `uv run python`");
    if !output.status.success() {
        panic!(
            "uv run python failed: status={:?}\nstdout={}\nstderr={}",
            output.status,
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr),
        );
    }
    String::from_utf8(output.stdout).expect("stdout was not utf-8")
}

#[test]
#[ignore = "requires uv + python toolchain; run explicitly with --ignored"]
fn writer_submit_only_is_detected_as_in_flight() {
    let stdout = uv_python(
        r#"
import json, tempfile, pathlib, sys
sys.path.insert(0, "python")
from engine.exchanges.tachibana_orders import _audit_log_submit
from engine.wal_in_flight import detect_in_flight_orders

with tempfile.TemporaryDirectory() as d:
    wal = pathlib.Path(d) / "tachibana_orders.jsonl"
    with wal.open("a", encoding="utf-8") as f:
        _audit_log_submit(
            f,
            client_order_id="X-CONTRACT-1",
            request_key=1,
            instrument_id="1301.TSE",
            order_side="buy",
            order_type="market",
            quantity="100",
        )
    print(json.dumps(sorted(detect_in_flight_orders(wal))))
"#,
    );
    let parsed: Vec<String> =
        serde_json::from_str(stdout.trim()).expect("python script must print JSON array");
    assert_eq!(
        parsed,
        vec!["X-CONTRACT-1"],
        "writer submit-only must be detected as in-flight by reader"
    );
}

#[test]
#[ignore = "requires uv + python toolchain; run explicitly with --ignored"]
fn writer_submit_then_rejected_is_terminal() {
    let stdout = uv_python(
        r#"
import json, tempfile, pathlib, sys
sys.path.insert(0, "python")
from engine.exchanges.tachibana_orders import (
    _audit_log_rejected,
    _audit_log_submit,
)
from engine.wal_in_flight import detect_in_flight_orders

with tempfile.TemporaryDirectory() as d:
    wal = pathlib.Path(d) / "tachibana_orders.jsonl"
    with wal.open("a", encoding="utf-8") as f:
        _audit_log_submit(
            f, client_order_id="X-CONTRACT-2", request_key=2,
            instrument_id="1301.TSE", order_side="buy",
            order_type="market", quantity="100",
        )
        _audit_log_rejected(
            f, client_order_id="X-CONTRACT-2",
            reason_code="E", reason_text="rej",
        )
    print(json.dumps(sorted(detect_in_flight_orders(wal))))
"#,
    );
    let parsed: Vec<String> =
        serde_json::from_str(stdout.trim()).expect("python script must print JSON array");
    assert!(
        parsed.is_empty(),
        "writer submit→rejected must be terminal (not in-flight); got {:?}",
        parsed
    );
}

/// Source-level pin: ensure the Rust reader uses the same wire-schema constants
/// as the Python writer. This mirrors the Python contract test
/// `TestWalContract::test_uses_real_writer_schema_*`.
#[test]
fn rust_reader_uses_phase_field_and_rejected_terminal() {
    const MAIN_RS: &str = include_str!("../src/main.rs");
    let idx = MAIN_RS
        .find("fn has_wal_in_flight_orders_at(")
        .expect("has_wal_in_flight_orders_at helper must exist");
    let body = &MAIN_RS[idx..];
    let end = body[1..].find("\nfn ").map(|i| i + 1).unwrap_or(body.len());
    let fn_body = &body[..end];

    assert!(
        fn_body.contains(".get(\"phase\")"),
        "Rust reader must read the `phase` field (writer schema)"
    );
    assert!(
        fn_body.contains(".get(\"client_order_id\")"),
        "Rust reader must read the `client_order_id` field (writer schema)"
    );
    assert!(
        fn_body.contains("\"rejected\""),
        "Rust reader must treat `rejected` as the terminal phase"
    );
}
