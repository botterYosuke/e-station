//! M2-est: `EngineConnection::capabilities()` MUST never expose sensitive
//! credential-shaped keys to UI/log paths.
//!
//! G3: Migrated from WS (tokio-tungstenite) to gRPC (tonic MockGrpcEngine).
//! Note: the mock gRPC server returns None capabilities (empty proto message);
//! the no-sensitive-key invariant is verified via the positive-control unit test.
mod common;

use flowsurface_engine_client::{EngineConnection, dto::AppMode};
use serde_json::Value;

const SENSITIVE_KEYS: &[&str] = &[
    "password",
    "s_pwd",
    "token",
    "cookie",
    "secret",
    "session_id_secret",
    "api_key",
    "p_no",
    "creds",
];

/// Recursively walk a `serde_json::Value` and return the first sensitive key
/// found (case-insensitive substring of any of [`SENSITIVE_KEYS`]). Returns
/// `None` if the blob is clean.
fn find_sensitive_key(v: &Value) -> Option<String> {
    match v {
        Value::Object(map) => {
            for (k, child) in map {
                let lk = k.to_ascii_lowercase();
                for needle in SENSITIVE_KEYS {
                    if lk.contains(needle) {
                        return Some(k.clone());
                    }
                }
                if let Some(hit) = find_sensitive_key(child) {
                    return Some(hit);
                }
            }
            None
        }
        Value::Array(items) => items.iter().find_map(find_sensitive_key),
        _ => None,
    }
}

#[test]
fn helper_catches_sensitive_key_in_handcrafted_blob() {
    // Positive control: helper must spot a planted leak. If this ever
    // regresses to None, the negative tests below would silently pass for
    // the wrong reason.
    let leaky = serde_json::json!({
        "supported_venues": ["tachibana"],
        "venue_capabilities": {
            "tachibana": {"session": {"token": "secret-abc"}}
        }
    });
    assert_eq!(find_sensitive_key(&leaky).as_deref(), Some("token"));

    // The clean fixture used in real handshake tests must pass.
    let clean = serde_json::json!({
        "supported_venues": ["tachibana"],
        "supports_bulk_trades": true,
        "supports_depth_binary": false,
        "venue_capabilities": {
            "tachibana": {"supported_timeframes": ["1d"]}
        }
    });
    assert_eq!(find_sensitive_key(&clean), None);
}

#[tokio::test]
async fn capabilities_snapshot_carries_no_sensitive_keys() {
    // The mock server returns empty capabilities (no proto fields set).
    // capabilities_to_json() converts None → JSON null or empty object;
    // either way there are no sensitive keys.
    let mock = common::MockGrpcEngine::start_basic("caps-token").await;

    let conn = EngineConnection::connect_grpc(&mock.target(), "caps-token", AppMode::Live)
        .await
        .expect("handshake should succeed");

    let caps = conn.capabilities();
    assert!(
        find_sensitive_key(&caps).is_none(),
        "capabilities() snapshot leaked sensitive key: {:?}\nFull blob: {}",
        find_sensitive_key(&caps),
        serde_json::to_string_pretty(&*caps).unwrap_or_default(),
    );

    mock.shutdown().await;
}
