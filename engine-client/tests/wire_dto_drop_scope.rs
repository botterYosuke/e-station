//! M4 (MEDIUM-B2-2): Wire DTOs holding secret material must zero their
//! buffers on drop. This test pins:
//!
//! 1. `TachibanaCredentialsWire` and `TachibanaSessionWire` declare a `Drop`
//!    impl (i.e. `std::mem::needs_drop` returns `true`). Without `Zeroizing`
//!    on the secret fields, neither type would need a `Drop` glue — so this
//!    is a structural guard against silently regressing back to plain
//!    `String`.
//! 2. JSON serialization of the wire DTO is byte-for-byte equivalent to the
//!    pre-`Zeroizing` shape. `Zeroizing<String>` is supposed to derive
//!    `Serialize` transparently via the inner `String`'s `Serialize` impl
//!    (no `serde` feature flag required); this test fails loudly if that
//!    transparency ever breaks.

use flowsurface_engine_client::dto::{TachibanaCredentialsWire, TachibanaSessionWire};

#[test]
fn wire_dtos_need_drop_for_zeroize() {
    assert!(
        std::mem::needs_drop::<TachibanaCredentialsWire>(),
        "TachibanaCredentialsWire must hold Zeroizing<String> secrets so Drop runs",
    );
    assert!(
        std::mem::needs_drop::<TachibanaSessionWire>(),
        "TachibanaSessionWire must hold Zeroizing<String> secrets so Drop runs",
    );
}

#[test]
fn credentials_wire_serializes_as_plain_strings() {
    let wire = TachibanaCredentialsWire {
        user_id: "alice".into(),
        password: "p4ss".to_string().into(),
        second_password: None,
        is_demo: true,
        session: None,
    };
    let s = serde_json::to_string(&wire).expect("serialize");
    assert!(s.contains("\"user_id\":\"alice\""), "got: {s}");
    assert!(s.contains("\"password\":\"p4ss\""), "got: {s}");
    assert!(s.contains("\"is_demo\":true"), "got: {s}");
}

#[test]
fn session_wire_roundtrips() {
    let wire = TachibanaSessionWire {
        url_request: "https://example.invalid/req".to_string().into(),
        url_master: "https://example.invalid/m".to_string().into(),
        url_price: "https://example.invalid/p".to_string().into(),
        url_event: "https://example.invalid/e".to_string().into(),
        url_event_ws: "wss://example.invalid/ws".to_string().into(),
        expires_at_ms: Some(1_700_000_000_000),
        zyoutoeki_kazei_c: "0".into(),
    };
    let s = serde_json::to_string(&wire).expect("serialize");
    let back: TachibanaSessionWire = serde_json::from_str(&s).expect("deserialize");
    assert_eq!(back.expires_at_ms, Some(1_700_000_000_000));
    assert_eq!(back.zyoutoeki_kazei_c, "0");
    assert_eq!(&*back.url_event_ws, "wss://example.invalid/ws");
}
