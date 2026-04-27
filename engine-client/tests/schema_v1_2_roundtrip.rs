//! HIGH-D2-2: schema 1.2 cross-language compat. The companion Python test
//! [`python/tests/test_schema_compat_v1_2.py`] checks Rust→Python; this file
//! pins Python→Rust roundtripping for the venue lifecycle schema variants.
//!
//! For each variant we feed the JSON shape pydantic produces from
//! ``model_dump_json()`` into Rust ``serde_json::from_str`` and assert the
//! deserialization succeeds. For commands we re-serialize and compare the
//! `op` discriminator survives. For events we spot-check fields.

use flowsurface_engine_client::dto::{Command, EngineEvent};

const VALID_UUID: &str = "11111111-2222-4333-8444-555555555555";

fn python_dump(variant: &str) -> String {
    match variant {
        "RequestVenueLogin" => format!(
            r#"{{"op":"RequestVenueLogin","request_id":"{VALID_UUID}","venue":"tachibana"}}"#,
        ),
        "VenueReady" => {
            format!(r#"{{"event":"VenueReady","venue":"tachibana","request_id":"{VALID_UUID}"}}"#,)
        }
        "VenueError" => format!(
            r#"{{"event":"VenueError","venue":"tachibana","request_id":"{VALID_UUID}","code":"session_expired","message":"再ログインしてください"}}"#,
        ),
        "VenueLoginStarted" => format!(
            r#"{{"event":"VenueLoginStarted","venue":"tachibana","request_id":"{VALID_UUID}"}}"#,
        ),
        "VenueLoginCancelled" => {
            String::from(r#"{"event":"VenueLoginCancelled","venue":"tachibana","request_id":null}"#)
        }
        _ => panic!("unknown variant {variant}"),
    }
}

// ── Events: Python → Rust deserialize ────────────────────────────────────────

#[test]
fn rust_deserializes_python_venue_ready() {
    let json = python_dump("VenueReady");
    let ev: EngineEvent = serde_json::from_str(&json).unwrap();
    match ev {
        EngineEvent::VenueReady { venue, request_id } => {
            assert_eq!(venue, "tachibana");
            assert_eq!(request_id.as_deref(), Some(VALID_UUID));
        }
        _ => panic!("expected VenueReady"),
    }
}

#[test]
fn rust_deserializes_python_venue_error() {
    let json = python_dump("VenueError");
    let ev: EngineEvent = serde_json::from_str(&json).unwrap();
    match ev {
        EngineEvent::VenueError {
            venue,
            request_id,
            code,
            message,
        } => {
            assert_eq!(venue, "tachibana");
            assert_eq!(request_id.as_deref(), Some(VALID_UUID));
            assert_eq!(code, "session_expired");
            assert_eq!(message, "再ログインしてください");
        }
        _ => panic!("expected VenueError"),
    }
}

#[test]
fn rust_deserializes_python_venue_login_started() {
    let json = python_dump("VenueLoginStarted");
    let ev: EngineEvent = serde_json::from_str(&json).unwrap();
    assert!(matches!(ev, EngineEvent::VenueLoginStarted { .. }));
}

#[test]
fn rust_deserializes_python_venue_login_cancelled_with_null_request_id() {
    let json = python_dump("VenueLoginCancelled");
    let ev: EngineEvent = serde_json::from_str(&json).unwrap();
    match ev {
        EngineEvent::VenueLoginCancelled { venue, request_id } => {
            assert_eq!(venue, "tachibana");
            assert!(request_id.is_none());
        }
        _ => panic!("expected VenueLoginCancelled"),
    }
}

// ── Commands: Rust → JSON shape Python expects ───────────────────────────────

#[test]
fn rust_serializes_request_venue_login() {
    let cmd = Command::RequestVenueLogin {
        request_id: VALID_UUID.into(),
        venue: "tachibana".into(),
    };
    let s = serde_json::to_string(&cmd).unwrap();
    assert!(s.contains(r#""op":"RequestVenueLogin""#));
    assert!(s.contains(r#""venue":"tachibana""#));
    assert!(s.contains(VALID_UUID));
}
