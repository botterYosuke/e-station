/// Unit tests for `session_file::EngineSession` — write_atomic and delete.
///
/// These tests do NOT require a live Python engine: they exercise only the
/// file-system primitives in the session_file module.
use flowsurface_engine_client::session_file::EngineSession;

#[test]
fn write_and_delete() {
    let tmp = std::env::temp_dir().join("test-engine-session-a.json");
    let session = EngineSession {
        port: 19876,
        token: "test-token-abc".to_string(),
        pid: std::process::id(),
        schema_major: 3,
    };
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
    // started_at must be a non-empty string.
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

    let session = EngineSession {
        port: 19876,
        token: "test-token".to_string(),
        pid: std::process::id(),
        schema_major: 3,
    };
    session.write_atomic(&tmp).unwrap();
    assert!(tmp.exists());
    EngineSession::delete(&tmp);
}

#[test]
fn write_is_atomic_via_rename() {
    // Write twice to the same path — the second write must succeed (rename
    // over an existing file is atomic on all supported platforms).
    let tmp = std::env::temp_dir().join("test-engine-session-overwrite.json");
    let session = EngineSession {
        port: 12345,
        token: "first".to_string(),
        pid: std::process::id(),
        schema_major: 3,
    };
    session.write_atomic(&tmp).unwrap();

    let session2 = EngineSession {
        port: 54321,
        token: "second".to_string(),
        pid: std::process::id(),
        schema_major: 4,
    };
    session2.write_atomic(&tmp).unwrap();

    let content = std::fs::read_to_string(&tmp).unwrap();
    let parsed: serde_json::Value = serde_json::from_str(&content).unwrap();
    assert_eq!(parsed["port"], 54321);
    assert_eq!(parsed["schema_major"], 4);

    EngineSession::delete(&tmp);
}
