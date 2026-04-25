//! HIGH-D2-2: schema 1.2 cross-language compat. The companion Python test
//! [`python/tests/test_schema_compat_v1_2.py`] checks Rust→Python; this file
//! pins Python→Rust roundtripping for the 7 schema-1.2 variants.
//!
//! For each variant we feed the JSON shape pydantic produces from
//! ``model_dump_json()`` into Rust ``serde_json::from_str`` and assert the
//! deserialization succeeds. For commands we re-serialize and compare the
//! `op` discriminator survives. For events we spot-check fields.

use flowsurface_engine_client::dto::{Command, EngineEvent, VenueCredentialsPayload};

const VALID_UUID: &str = "11111111-2222-4333-8444-555555555555";

fn python_dump(variant: &str) -> String {
    match variant {
        "SetVenueCredentials" => format!(
            r#"{{"op":"SetVenueCredentials","request_id":"{VALID_UUID}","payload":{{"venue":"tachibana","user_id":"alice","password":"p4ss","second_password":null,"is_demo":true,"session":null}}}}"#,
        ),
        "RequestVenueLogin" => format!(
            r#"{{"op":"RequestVenueLogin","request_id":"{VALID_UUID}","venue":"tachibana"}}"#,
        ),
        "VenueReady" => {
            format!(r#"{{"event":"VenueReady","venue":"tachibana","request_id":"{VALID_UUID}"}}"#,)
        }
        "VenueError" => format!(
            r#"{{"event":"VenueError","venue":"tachibana","request_id":"{VALID_UUID}","code":"session_expired","message":"再ログインしてください"}}"#,
        ),
        "VenueCredentialsRefreshed" => String::from(
            r#"{"event":"VenueCredentialsRefreshed","venue":"tachibana","session":{"url_request":"https://example.invalid/req","url_master":"https://example.invalid/m","url_price":"https://example.invalid/p","url_event":"https://example.invalid/e","url_event_ws":"wss://example.invalid/ws","expires_at_ms":1700000000000,"zyoutoeki_kazei_c":"0"}}"#,
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
fn rust_deserializes_python_venue_credentials_refreshed() {
    let json = python_dump("VenueCredentialsRefreshed");
    let ev: EngineEvent = serde_json::from_str(&json).unwrap();
    match ev {
        EngineEvent::VenueCredentialsRefreshed { venue, session, .. } => {
            assert_eq!(venue, "tachibana");
            assert_eq!(session.expires_at_ms, Some(1_700_000_000_000));
            assert_eq!(&*session.url_event_ws, "wss://example.invalid/ws");
            assert_eq!(session.zyoutoeki_kazei_c, "0");
        }
        _ => panic!("expected VenueCredentialsRefreshed"),
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
fn rust_serializes_set_venue_credentials_with_tachibana_tag() {
    use flowsurface_engine_client::dto::{TachibanaCredentialsWire, VenueCredentialsPayload};
    let cmd = Command::SetVenueCredentials {
        request_id: VALID_UUID.into(),
        payload: VenueCredentialsPayload::Tachibana(TachibanaCredentialsWire {
            user_id: "alice".into(),
            password: "p4ss".to_string().into(),
            second_password: None,
            is_demo: true,
            session: None,
        }),
    };
    let s = serde_json::to_string(&cmd).unwrap();
    // op + venue tag must both be present so pydantic Discriminator routes it
    assert!(s.contains(r#""op":"SetVenueCredentials""#), "got: {s}");
    assert!(s.contains(r#""venue":"tachibana""#), "got: {s}");
    assert!(s.contains(r#""user_id":"alice""#), "got: {s}");
    assert!(s.contains(r#""password":"p4ss""#), "got: {s}");
}

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

// ── Smoke: VenueCredentialsPayload tag round-trip ────────────────────────────

#[test]
fn payload_serializes_with_tachibana_venue_tag() {
    use flowsurface_engine_client::dto::TachibanaCredentialsWire;
    let p = VenueCredentialsPayload::Tachibana(TachibanaCredentialsWire {
        user_id: "u".into(),
        password: "p".to_string().into(),
        second_password: None,
        is_demo: true,
        session: None,
    });
    assert_eq!(p.venue_tag(), "tachibana");
    let s = serde_json::to_string(&p).unwrap();
    assert!(s.contains(r#""venue":"tachibana""#), "got: {s}");
}
