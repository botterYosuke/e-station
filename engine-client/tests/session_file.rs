/// Unit tests for `session_file::EngineSession` — write_atomic, delete, and
/// secure Debug formatting.
///
/// These tests do NOT require a live Python engine: they exercise only the
/// file-system primitives and trait impls in the session_file module.
use flowsurface_engine_client::session_file::EngineSession;

#[test]
fn write_and_delete() {
    let tmp = std::env::temp_dir().join("test-engine-session-a.json");
    let session = EngineSession::new(19876, "test-token-abc".to_string(), std::process::id(), 3);
    session.write_atomic(&tmp).unwrap();

    // File must exist and be valid JSON.
    let content = std::fs::read_to_string(&tmp).unwrap();
    let parsed: serde_json::Value = serde_json::from_str(&content).unwrap();
    assert_eq!(parsed["port"], 19876);
    assert_eq!(parsed["pid"], std::process::id());
    assert_eq!(parsed["schema_major"], 3);
    // token must be a non-empty string (we do not log or assert the value).
    assert!(parsed["token"].is_string());
    assert!(!parsed["token"].as_str().unwrap().is_empty());
    // started_at must be present (C7) and a non-empty ISO-8601 string.
    assert!(parsed["started_at"].is_string());
    assert!(!parsed["started_at"].as_str().unwrap().is_empty());

    // delete must remove the file.
    EngineSession::delete(&tmp);
    assert!(!tmp.exists());
}

#[test]
fn write_creates_parent_dir() {
    let tmp = std::env::temp_dir()
        .join("test-flowsurface-session-dir")
        .join("engine-session.json");
    // Ensure the parent does not exist so we verify that write_atomic creates it.
    if let Some(parent) = tmp.parent() {
        let _ = std::fs::remove_dir_all(parent);
    }

    let session = EngineSession::new(19876, "test-token".to_string(), std::process::id(), 3);
    session.write_atomic(&tmp).unwrap();
    assert!(tmp.exists());
    EngineSession::delete(&tmp);
}

#[test]
fn write_is_atomic_via_rename() {
    // Write twice to the same path — the second write must succeed (rename
    // over an existing file is atomic on all supported platforms).
    let tmp = std::env::temp_dir().join("test-engine-session-overwrite.json");
    let session = EngineSession::new(12345, "first".to_string(), std::process::id(), 3);
    session.write_atomic(&tmp).unwrap();

    let session2 = EngineSession::new(54321, "second".to_string(), std::process::id(), 4);
    session2.write_atomic(&tmp).unwrap();

    let content = std::fs::read_to_string(&tmp).unwrap();
    let parsed: serde_json::Value = serde_json::from_str(&content).unwrap();
    assert_eq!(parsed["port"], 54321);
    assert_eq!(parsed["schema_major"], 4);

    EngineSession::delete(&tmp);
}

/// C7 regression: tokens containing JSON metacharacters (e.g. quote, backslash,
/// newline) must round-trip through write_atomic without corrupting the file.
/// The previous hand-rolled `format!()` writer broke on `"` characters.
#[test]
fn write_handles_token_with_special_characters() {
    let tmp = std::env::temp_dir().join("test-engine-session-special.json");
    let weird_token = r#"abc"def\nghi\\jkl"#;
    let session = EngineSession::new(19876, weird_token.to_string(), std::process::id(), 3);
    session.write_atomic(&tmp).unwrap();

    let content = std::fs::read_to_string(&tmp).unwrap();
    let parsed: serde_json::Value = serde_json::from_str(&content)
        .expect("session file must be valid JSON even for tricky tokens");
    assert_eq!(parsed["token"].as_str().unwrap(), weird_token);

    EngineSession::delete(&tmp);
}

/// C3: `reap_stale` must remove session files whose recorded pid is no longer live.
#[test]
fn reap_stale_removes_dead_pid_file() {
    let tmp = std::env::temp_dir().join("test-reap-stale-dead-pid.json");
    // Pid 0 is treated as not-live (sentinel for unknown). We could also
    // pick a high unlikely pid, but 0 is deterministic across platforms.
    let session = EngineSession::new(19876, "tok".to_string(), 0, 3);
    session.write_atomic(&tmp).unwrap();
    assert!(tmp.exists());

    EngineSession::reap_stale(&tmp);
    assert!(
        !tmp.exists(),
        "reap_stale must delete files whose pid is not live"
    );
}

/// C3: `reap_stale` must keep the file when its pid points to a live process.
#[test]
fn reap_stale_keeps_live_pid_file() {
    let tmp = std::env::temp_dir().join("test-reap-stale-live-pid.json");
    let session = EngineSession::new(19876, "tok".to_string(), std::process::id(), 3);
    session.write_atomic(&tmp).unwrap();

    EngineSession::reap_stale(&tmp);
    assert!(
        tmp.exists(),
        "reap_stale must NOT delete files whose pid is live"
    );

    EngineSession::delete(&tmp);
}

/// C3: `reap_stale` must remove files whose contents do not parse as JSON.
#[test]
fn reap_stale_removes_corrupt_json() {
    let tmp = std::env::temp_dir().join("test-reap-stale-corrupt.json");
    std::fs::write(&tmp, b"{not valid json").unwrap();
    assert!(tmp.exists());

    EngineSession::reap_stale(&tmp);
    assert!(
        !tmp.exists(),
        "reap_stale must delete files with corrupt JSON"
    );
}

/// C3: `reap_stale` is a no-op when the file does not exist.
#[test]
fn reap_stale_missing_file_is_noop() {
    let tmp = std::env::temp_dir().join("test-reap-stale-missing-xyz.json");
    let _ = std::fs::remove_file(&tmp);
    assert!(!tmp.exists());

    EngineSession::reap_stale(&tmp);
    assert!(!tmp.exists());
}

/// Group 4b M-4 regression: when `Child::id()` returns `None`, the spawn path
/// must NOT write a session file with `pid=0`. The same path that calls
/// `EngineSession::write_atomic` is responsible for skipping the write — this
/// test pins the behaviour by asserting that `write_atomic` with `pid=0`
/// produces a file that `reap_stale` immediately deletes (the round-trip
/// confirms `pid=0` is never a useful state to persist).
#[test]
fn pid_zero_session_file_is_immediately_reaped() {
    let tmp = std::env::temp_dir().join("test-engine-session-pid-zero.json");
    let session = EngineSession::new(19876, "irrelevant".to_string(), 0, 3);
    session.write_atomic(&tmp).unwrap();
    assert!(tmp.exists());
    EngineSession::reap_stale(&tmp);
    assert!(
        !tmp.exists(),
        "pid=0 must be reaped (it represents an unknown/missing pid sentinel)"
    );
}

/// M-2 regression: `Debug` must NOT leak the raw token value, since it is the
/// IPC bearer secret. We assert that `[REDACTED]` appears and the actual token
/// substring does not.
#[test]
fn debug_redacts_token() {
    let session = EngineSession::new(
        19876,
        "super-secret-token-do-not-leak".to_string(),
        std::process::id(),
        3,
    );
    let dbg = format!("{:?}", session);
    assert!(
        dbg.contains("[REDACTED]"),
        "Debug output missing redaction marker: {dbg}"
    );
    assert!(
        !dbg.contains("super-secret-token-do-not-leak"),
        "Debug leaked the raw token: {dbg}"
    );
}

// ── G0.9: transport フィールド ────────────────────────────────────────────────

/// G0.9: `EngineSession::new()` で作ったセッションは `transport == "ws"` であること。
#[test]
fn transport_field_defaults_to_ws() {
    let session = EngineSession::new(19876, "tok".to_string(), std::process::id(), 3);
    assert_eq!(session.transport, "ws", "new() must default transport to 'ws'");
}

/// G0.9: `EngineSession::with_transport("grpc")` で作ったセッションは
/// `transport == "grpc"` が JSON に書き出されること。
#[test]
fn transport_field_grpc_serializes_to_json() {
    let tmp = std::env::temp_dir().join("test-engine-session-grpc-transport.json");
    let session = EngineSession::with_transport(
        19876,
        "tok".to_string(),
        std::process::id(),
        3,
        "grpc",
    );
    assert_eq!(session.transport, "grpc");
    session.write_atomic(&tmp).unwrap();

    let content = std::fs::read_to_string(&tmp).unwrap();
    let parsed: serde_json::Value = serde_json::from_str(&content).unwrap();
    assert_eq!(
        parsed["transport"].as_str().unwrap(),
        "grpc",
        "transport field must appear as 'grpc' in serialized JSON"
    );

    EngineSession::delete(&tmp);
}

/// G0.9: `transport` フィールドが無い旧形式の JSON は `transport == "ws"` に
/// デシリアライズされること（後方互換性）。
#[test]
fn transport_field_missing_in_old_format_defaults_to_ws() {
    let old_json = r#"{"port":19876,"token":"tok","pid":9999,"schema_major":3,"started_at":"2026-05-08T00:00:00Z"}"#;
    let session: EngineSession = serde_json::from_str(old_json).unwrap();
    assert_eq!(
        session.transport, "ws",
        "old session files without transport field must deserialize as 'ws'"
    );
}

/// G0.9: `Debug` 出力に `transport` フィールドが含まれること。
#[test]
fn debug_shows_transport_field() {
    let session = EngineSession::with_transport(
        19876,
        "tok".to_string(),
        std::process::id(),
        3,
        "grpc",
    );
    let dbg = format!("{:?}", session);
    assert!(
        dbg.contains("transport"),
        "Debug output must include 'transport' field: {dbg}"
    );
}
