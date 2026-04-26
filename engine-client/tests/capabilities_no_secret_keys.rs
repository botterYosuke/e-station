//! M2-est: `EngineConnection::capabilities()` MUST never expose sensitive
//! credential-shaped keys to UI/log paths.
//!
//! The Python side (`schemas.py::Ready.capabilities`) is a free-form `dict`,
//! so a future server-side bug could in principle leak a `password` or
//! `token` field into the snapshot the Rust UI clones for view rendering.
//! This test pins the boundary on the Rust side: after a real handshake,
//! the `Arc<Value>` returned by `capabilities()` is recursively scanned and
//! must contain none of the canonical sensitive key names below.
//!
//! The same scan is also exercised on a hand-crafted positive control to
//! make sure the helper actually catches a leak — otherwise a buggy
//! recursion would silently pass the negative test on every future change.

use flowsurface_engine_client::{EngineConnection, SCHEMA_MAJOR, SCHEMA_MINOR};

use futures_util::{SinkExt, StreamExt};
use serde_json::Value;
use tokio::net::TcpListener;
use tokio_tungstenite::{accept_async, tungstenite::Message};

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
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();

    tokio::spawn(async move {
        let (tcp, _) = listener.accept().await.unwrap();
        let mut ws = accept_async(tcp).await.unwrap();
        let _hello = ws.next().await.unwrap().unwrap();
        let ready = serde_json::json!({
            "event": "Ready",
            "schema_major": SCHEMA_MAJOR,
            "schema_minor": SCHEMA_MINOR,
            "engine_version": "1.0.0-mock",
            "engine_session_id": "00000000-0000-0000-0000-0000000c0ffe",
            "capabilities": {
                "supported_venues": ["tachibana", "binance"],
                "supports_bulk_trades": true,
                "supports_depth_binary": false,
                "venue_capabilities": {
                    "tachibana": {"supported_timeframes": ["1d"]},
                    "binance": {"supported_timeframes": ["1m", "5m"]}
                }
            }
        });
        ws.send(Message::Text(ready.to_string().into())).await.ok();
        tokio::time::sleep(std::time::Duration::from_secs(2)).await;
    });

    tokio::time::sleep(std::time::Duration::from_millis(10)).await;

    let conn = EngineConnection::connect(&format!("ws://{addr}"), "caps-token")
        .await
        .expect("handshake should succeed");

    let caps = conn.capabilities();
    assert!(
        find_sensitive_key(&caps).is_none(),
        "capabilities() snapshot leaked sensitive key: {:?}\n\
         Full blob: {}",
        find_sensitive_key(&caps),
        serde_json::to_string_pretty(&*caps).unwrap_or_default(),
    );
}
