//! Regression test for Findings #2 — `VenueCredentialsRefreshed` events
//! emitted during the in-`start()` `VenueReady` wait must reach the
//! installed hook synchronously, AND must update the in-memory creds
//! store so the next restart re-injects the refreshed session.
//!
//! Calls the **production** helper `ProcessManager::patch_in_memory_session`
//! directly so a future regression to a different field name (or to a
//! different visibility scope that breaks the hook chain) is caught here.

// MEDIUM-9 (ラウンド 6): no live mock WS in this file, but the
// production refresh path interacts with the same WS layer audited
// in process_send_failure_skips_subscribe.rs. Keep
// `WebSocketConfig::default()` audited on tungstenite version bumps.
use flowsurface_engine_client::{
    ProcessManager,
    dto::{TachibanaCredentialsWire, TachibanaSessionWire, VenueCredentialsPayload},
};
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};

fn dummy_session() -> TachibanaSessionWire {
    TachibanaSessionWire {
        url_request: "https://demo/req/SES1/".to_string().into(),
        url_master: "https://demo/mst/SES2/".to_string().into(),
        url_price: "https://demo/prc/SES3/".to_string().into(),
        url_event: "https://demo/evt/SES4/".to_string().into(),
        url_event_ws: "wss://demo/evt/SES5/".to_string().into(),
        expires_at_ms: None,
        zyoutoeki_kazei_c: "1".to_string(),
    }
}

fn cold_creds_no_session() -> VenueCredentialsPayload {
    VenueCredentialsPayload::Tachibana(TachibanaCredentialsWire {
        user_id: "u".to_string(),
        password: "p".to_string().into(),
        second_password: None,
        is_demo: true,
        session: None,
    })
}

#[tokio::test]
async fn patch_in_memory_session_replaces_session_field() {
    // This pins the contract that `start()` relies on inside its
    // VenueReady wait: when a refresh arrives, the in-memory store
    // must reflect it before the next restart cycle.
    let manager = Arc::new(ProcessManager::new("python"));
    manager.set_venue_credentials(cold_creds_no_session()).await;

    {
        let store = manager.venue_credentials.lock().await;
        let VenueCredentialsPayload::Tachibana(c) = &store[0];
        assert!(c.session.is_none());
    }

    // Production helper — same code path `start()` runs.
    ProcessManager::patch_in_memory_session(&manager.venue_credentials, &dummy_session()).await;

    let store = manager.venue_credentials.lock().await;
    let VenueCredentialsPayload::Tachibana(c) = &store[0];
    let s = c
        .session
        .as_ref()
        .expect("session populated by patch helper");
    assert_eq!(s.url_event_ws.as_str(), "wss://demo/evt/SES5/");
    assert_eq!(s.url_request.as_str(), "https://demo/req/SES1/");
}

#[tokio::test]
async fn refresh_hook_callback_fires_with_session() {
    let manager = Arc::new(ProcessManager::new("python"));
    let fired = Arc::new(AtomicUsize::new(0));
    let captured_url_event_ws: Arc<std::sync::Mutex<Option<String>>> =
        Arc::new(std::sync::Mutex::new(None));

    let fired_clone = Arc::clone(&fired);
    let captured_clone = Arc::clone(&captured_url_event_ws);
    // HIGH-5 (ラウンド 6 強制修正 / Group F): callback signature is
    // `Fn(&VenueCredentialsRefresh)` — pinned here so a regression to
    // by-value (which would force a per-dispatch
    // `Zeroizing<String>` heap clone of the password) breaks the
    // build. The closure clones only the field it actually needs.
    manager
        .set_on_venue_credentials_refreshed(Box::new(move |refresh| {
            fired_clone.fetch_add(1, Ordering::SeqCst);
            *captured_clone.lock().unwrap() = Some(refresh.session().url_event_ws.to_string());
        }))
        .await;

    // M11: drive the **production** entry point used by `start()` —
    // `handle_credentials_refreshed` — instead of dereffing the hook
    // mutex directly. A test that pokes the lock guard would not
    // catch a regression where `start()` stops calling
    // `handle_credentials_refreshed` and reverts to invoking the hook
    // inline (skipping in-memory patching). This way a refactor that
    // moves the dispatch wins or loses on a single observable contract.
    manager.set_venue_credentials(cold_creds_no_session()).await;
    let refresh = flowsurface_engine_client::process::VenueCredentialsRefresh::SessionOnly {
        session: dummy_session(),
    };
    ProcessManager::handle_credentials_refreshed(
        &manager.venue_credentials,
        &manager.on_venue_credentials_refreshed,
        &refresh,
    )
    .await;

    assert_eq!(fired.load(Ordering::SeqCst), 1);
    assert_eq!(
        captured_url_event_ws.lock().unwrap().as_deref(),
        Some("wss://demo/evt/SES5/")
    );

    // Also verify the in-memory store was patched as a side-effect of
    // the same call (handle_credentials_refreshed = patch + hook in
    // one production code path).
    let store = manager.venue_credentials.lock().await;
    let VenueCredentialsPayload::Tachibana(c) = &store[0];
    let s = c.session.as_ref().expect("session populated by handler");
    assert_eq!(s.url_event_ws.as_str(), "wss://demo/evt/SES5/");
}

#[tokio::test]
async fn medium7_full_variant_overwrites_credentials_triple() {
    // MEDIUM-7 (ラウンド 7): the `Full` variant must replace user_id /
    // password / is_demo in the in-memory store (so the next restart
    // re-injects the post-login creds). The `SessionOnly` variant
    // tested above only patches the session field.
    use ::data::config::tachibana::TachibanaUserId;
    use flowsurface_engine_client::process::VenueCredentialsRefresh;

    let manager = Arc::new(ProcessManager::new("python"));
    manager.set_venue_credentials(cold_creds_no_session()).await;

    let refresh = VenueCredentialsRefresh::Full {
        session: dummy_session(),
        user_id: TachibanaUserId::new("new-user"),
        password: zeroize::Zeroizing::new("new-pw".to_string()),
        is_demo: false,
    };
    ProcessManager::handle_credentials_refreshed(
        &manager.venue_credentials,
        &manager.on_venue_credentials_refreshed,
        &refresh,
    )
    .await;

    let store = manager.venue_credentials.lock().await;
    let VenueCredentialsPayload::Tachibana(c) = &store[0];
    assert_eq!(c.user_id, "new-user", "Full variant must replace user_id");
    assert_eq!(
        c.password.as_str(),
        "new-pw",
        "Full variant must replace password"
    );
    assert!(!c.is_demo, "Full variant must replace is_demo");
    assert_eq!(
        c.session
            .as_ref()
            .expect("session populated")
            .url_event_ws
            .as_str(),
        "wss://demo/evt/SES5/"
    );
}

#[tokio::test]
async fn medium7_from_wire_partial_mixture_falls_back_to_session_only() {
    // MEDIUM-7 (ラウンド 7): a partial wire payload (e.g. user_id present
    // but password absent) must NOT silently flow into the keyring as
    // half-credentials. `from_wire` warns and falls back to `SessionOnly`.
    use ::data::config::tachibana::TachibanaUserId;
    use flowsurface_engine_client::process::VenueCredentialsRefresh;

    let r = VenueCredentialsRefresh::from_wire(
        dummy_session(),
        Some(TachibanaUserId::new("u")),
        None, // password missing → partial mixture
        Some(true),
    );
    assert!(
        matches!(r, VenueCredentialsRefresh::SessionOnly { .. }),
        "partial wire mixture must fall back to SessionOnly"
    );

    // Full triple → Full variant.
    let r2 = VenueCredentialsRefresh::from_wire(
        dummy_session(),
        Some(TachibanaUserId::new("u")),
        Some(zeroize::Zeroizing::new("p".to_string())),
        Some(true),
    );
    assert!(matches!(r2, VenueCredentialsRefresh::Full { .. }));

    // All-None → SessionOnly variant.
    let r3 = VenueCredentialsRefresh::from_wire(dummy_session(), None, None, None);
    assert!(matches!(r3, VenueCredentialsRefresh::SessionOnly { .. }));
}
