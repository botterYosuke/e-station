//! Structural regression pins for F7: SwitchMode is rejected while a W&B
//! submit is in progress (5-axis matrix axis-5 / 統一決定 61, 68).

fn read_main() -> String {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/main.rs");
    std::fs::read_to_string(path).expect("failed to read src/main.rs")
}

/// Safely slice up to `max_bytes` from `s` at a char boundary.
fn safe_slice(s: &str, max_bytes: usize) -> &str {
    if s.len() <= max_bytes {
        return s;
    }
    let mut end = max_bytes;
    while !s.is_char_boundary(end) {
        end -= 1;
    }
    &s[..end]
}

#[test]
fn submit_in_flight_static_exists() {
    let src = read_main();
    assert!(
        src.contains("static SUBMIT_IN_FLIGHT"),
        "src/main.rs must declare `static SUBMIT_IN_FLIGHT` for axis-5 guard (統一決定 61)"
    );
}

#[test]
fn submit_in_flight_is_atomic_bool() {
    let src = read_main();
    let pos = src
        .find("static SUBMIT_IN_FLIGHT")
        .expect("SUBMIT_IN_FLIGHT must exist");
    let snippet = safe_slice(&src[pos..], 120);
    assert!(
        snippet.contains("AtomicBool"),
        "SUBMIT_IN_FLIGHT must be an AtomicBool, got: {snippet}"
    );
}

#[test]
fn switch_mode_handler_checks_submit_in_flight() {
    let src = read_main();
    let handler_start = src
        .find("Action::SwitchMode(target)")
        .expect("Action::SwitchMode handler must exist");
    let handler_body = safe_slice(&src[handler_start..], 1500);
    assert!(
        handler_body.contains("SUBMIT_IN_FLIGHT"),
        "Action::SwitchMode must check SUBMIT_IN_FLIGHT before switching (統一決定 61, 68)"
    );
}

#[test]
fn submit_in_flight_check_precedes_wal_check() {
    let src = read_main();
    let handler_start = src
        .find("Action::SwitchMode(target)")
        .expect("Action::SwitchMode handler must exist");
    let handler_body = safe_slice(&src[handler_start..], 2000);

    let submit_pos = handler_body
        .find("SUBMIT_IN_FLIGHT")
        .expect("SUBMIT_IN_FLIGHT check must be in SwitchMode handler");
    let wal_pos = handler_body
        .find("has_wal_in_flight_orders")
        .expect("WAL in-flight check must be in SwitchMode handler");
    assert!(
        submit_pos < wal_pos,
        "SUBMIT_IN_FLIGHT check must appear before WAL in-flight check \
         (lock order: MODE_SWITCHING → SUBMIT_IN_FLIGHT → APP_MODE → CURRENT_PATH)"
    );
}

#[test]
fn submit_in_flight_block_shows_wandb_dialog() {
    let src = read_main();
    let handler_start = src
        .find("Action::SwitchMode(target)")
        .expect("Action::SwitchMode handler must exist");
    let handler_body = safe_slice(&src[handler_start..], 2000);

    let submit_pos = handler_body
        .find("SUBMIT_IN_FLIGHT")
        .expect("SUBMIT_IN_FLIGHT check must be in SwitchMode handler");
    let snippet = safe_slice(&handler_body[submit_pos..], 400);
    assert!(
        snippet.contains("W&B"),
        "SUBMIT_IN_FLIGHT block must display a W&B-specific error dialog (統一決定 68)"
    );
}
