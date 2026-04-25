//! HIGH-D1 — Rust side of the `dev_tachibana_login_allowed` integration.
//!
//! The stdin payload that `PythonProcess::spawn_with` writes to the
//! engine subprocess must encode `dev_tachibana_login_allowed` as a
//! boolean reflecting the build profile:
//!
//! * debug build (`cfg!(debug_assertions) == true`)  → `true`
//! * release build (`cfg!(debug_assertions) == false`) → `false`
//!
//! This test calls **the production builder** (`build_stdin_payload`)
//! that `spawn_with` itself uses — if a future change drops the
//! `dev_tachibana_login_allowed` field from the payload, this test
//! breaks (no tautology). `cargo test` exercises the debug arm and
//! `cargo test --release` the release arm; both must agree on the
//! contract.
//!
//! The Python-side counterpart of this guard lives in
//! `python/tests/test_tachibana_dev_env_guard.py`.

use flowsurface_engine_client::process::build_stdin_payload;

#[test]
fn stdin_payload_includes_dev_tachibana_login_allowed_matching_build_profile() {
    let line = build_stdin_payload(19876, "abc").expect("must serialize");

    // Newline-terminated for Python's `readline()`.
    assert!(line.ends_with('\n'), "payload must end with LF: {line:?}");

    // Round-trip parse (Python's `json.loads(...)` analogue).
    let parsed: serde_json::Value = serde_json::from_str(line.trim_end()).unwrap();

    // Schema invariants: port + token round-trip.
    assert_eq!(parsed["port"].as_u64(), Some(19876));
    assert_eq!(parsed["token"].as_str(), Some("abc"));

    // The flag must be present (not silently dropped).
    let flag = parsed["dev_tachibana_login_allowed"].as_bool();
    assert!(
        flag.is_some(),
        "dev_tachibana_login_allowed must be in payload (got {parsed:?})"
    );

    // Profile-specific assertion. Cargo executes one of the two arms
    // depending on the active profile; both must agree on the contract.
    if cfg!(debug_assertions) {
        assert_eq!(
            flag,
            Some(true),
            "debug build must allow the dev fast path"
        );
    } else {
        assert_eq!(
            flag,
            Some(false),
            "release build must NOT allow the dev fast path (R10)"
        );
    }
}

#[test]
fn stdin_payload_escapes_token_through_serde_json() {
    // HIGH-B2-1 invariant: the production builder must JSON-escape the
    // token, even when it contains characters that would break a
    // `format!`-based hand-rolled encoder.
    let token = r#"hard"to\escape"#;
    let line = build_stdin_payload(1, token).expect("must serialize");
    let parsed: serde_json::Value = serde_json::from_str(line.trim_end()).unwrap();
    assert_eq!(parsed["token"].as_str(), Some(token));
}
