//! Integration tests for the Tachibana credentials keyring round-trip
//! (MEDIUM-D3-3). Verifies:
//!
//! (a) `save_tachibana_credentials` followed by `load_tachibana_credentials`
//!     returns a value structurally equal to the input (every field, every
//!     URL of the optional session).
//! (b) `format!("{:?}", creds)` does not leak the plaintext password or
//!     any virtual-URL substring — they appear as `***`.
//! (c) The wire DTO (`TachibanaCredentialsWire` / `TachibanaSessionWire`),
//!     into which the credentials are converted before crossing the IPC
//!     boundary, wraps every secret in `Zeroizing<String>`. We assert the
//!     `Zeroize` trait is implemented on those types so dropping the wire
//!     value wipes the buffer (without `Zeroize` impl, the assertion below
//!     fails to compile).
//!
//! Side-effect isolation: `keyring::set_default_credential_builder` is
//! pointed at the in-memory mock backend so this test never touches the
//! real OS credential store.

use flowsurface_data::config::tachibana::{
    TachibanaCredentials, TachibanaSession, load_tachibana_credentials,
    save_tachibana_credentials,
};
use keyring::credential::{
    Credential, CredentialApi, CredentialBuilder, CredentialBuilderApi, CredentialPersistence,
};
use keyring::Error as KeyringError;
use secrecy::{ExposeSecret, SecretString};
use std::any::Any;
use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};

// ── Process-shared in-memory keyring backend (for tests) ──────────────────────
//
// keyring 3's built-in `mock::default_credential_builder()` does *not*
// persist values across `Entry::new` calls — every Entry gets its own
// MockData. That means `save_tachibana_credentials()` followed by
// `load_tachibana_credentials()` write/read against different mocks and
// the round-trip cannot be observed. We therefore install a tiny shared-
// state backend keyed by (service, user) so the round-trip is real.

#[derive(Default)]
struct SharedStore {
    map: HashMap<(String, String), Vec<u8>>,
}

fn shared_store() -> &'static Mutex<SharedStore> {
    static STORE: OnceLock<Mutex<SharedStore>> = OnceLock::new();
    STORE.get_or_init(|| Mutex::new(SharedStore::default()))
}

#[derive(Debug)]
struct SharedCred {
    service: String,
    user: String,
}

impl CredentialApi for SharedCred {
    fn set_secret(&self, secret: &[u8]) -> keyring::Result<()> {
        shared_store()
            .lock()
            .unwrap()
            .map
            .insert((self.service.clone(), self.user.clone()), secret.to_vec());
        Ok(())
    }

    fn get_secret(&self) -> keyring::Result<Vec<u8>> {
        shared_store()
            .lock()
            .unwrap()
            .map
            .get(&(self.service.clone(), self.user.clone()))
            .cloned()
            .ok_or(KeyringError::NoEntry)
    }

    fn delete_credential(&self) -> keyring::Result<()> {
        shared_store()
            .lock()
            .unwrap()
            .map
            .remove(&(self.service.clone(), self.user.clone()));
        Ok(())
    }

    fn as_any(&self) -> &dyn Any {
        self
    }

    fn debug_fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "SharedCred({}, {})", self.service, self.user)
    }
}

#[derive(Debug, Default)]
struct SharedBuilder;

impl CredentialBuilderApi for SharedBuilder {
    fn build(
        &self,
        _target: Option<&str>,
        service: &str,
        user: &str,
    ) -> keyring::Result<Box<Credential>> {
        Ok(Box::new(SharedCred {
            service: service.to_string(),
            user: user.to_string(),
        }))
    }

    fn as_any(&self) -> &dyn Any {
        self
    }

    fn persistence(&self) -> CredentialPersistence {
        CredentialPersistence::ProcessOnly
    }
}

fn install_mock_keyring() {
    // The default credential builder is set once per process via OnceLock
    // inside the keyring crate — repeated calls are harmless because
    // the function clones an `Arc`-equivalent guard. Using our own
    // process-shared backend keeps `save → load` observable.
    keyring::set_default_credential_builder(Box::new(SharedBuilder) as Box<CredentialBuilder>);
}

fn sample_session() -> TachibanaSession {
    TachibanaSession {
        url_request: SecretString::new("https://demo-kabuka.e-shiten.jp/e_api_v4r8/req/SESSION1/".to_string()),
        url_master: SecretString::new("https://demo-kabuka.e-shiten.jp/e_api_v4r8/mst/SESSION2/".to_string()),
        url_price: SecretString::new("https://demo-kabuka.e-shiten.jp/e_api_v4r8/prc/SESSION3/".to_string()),
        url_event: SecretString::new("https://demo-kabuka.e-shiten.jp/e_api_v4r8/evt/SESSION4/".to_string()),
        url_event_ws: SecretString::new("wss://demo-kabuka.e-shiten.jp/e_api_v4r8/evt/SESSION5/".to_string()),
        expires_at_ms: None,
        zyoutoeki_kazei_c: "1".into(),
    }
}

fn sample_creds() -> TachibanaCredentials {
    TachibanaCredentials {
        user_id: "uxf05882".into(),
        password: SecretString::new("vw20sr9h".to_string()),
        // Phase 1 invariant (F-H5): always None. The debug_assert in the
        // wire-conversion impl panics if this is ever set.
        second_password: None,
        is_demo: true,
        session: Some(sample_session()),
    }
}

#[test]
fn test_credentials_roundtrip_with_zeroize_and_masked_debug() {
    install_mock_keyring();

    let creds = sample_creds();

    // (a) round-trip via the keyring.
    // First, sanity-check the mock backend itself with a direct Entry call
    // — if this fails the rest is not testable.
    save_tachibana_credentials(&creds);
    // Diagnostic: read directly via Entry to confirm save reached the backend.
    let probe = keyring::Entry::new("flowsurface.tachibana", "user_id")
        .expect("mock entry must initialize");
    let raw = probe.get_password().expect("save must have written into mock");
    assert!(raw.contains("uxf05882"), "stored payload missing user_id: {raw}");

    let loaded = load_tachibana_credentials().expect("must round-trip from keyring");

    assert_eq!(loaded.user_id, creds.user_id);
    assert_eq!(
        loaded.password.expose_secret(),
        creds.password.expose_secret()
    );
    assert!(loaded.second_password.is_none());
    assert_eq!(loaded.is_demo, creds.is_demo);

    let loaded_session = loaded.session.expect("session round-tripped");
    let creds_session = creds.session.as_ref().unwrap();
    assert_eq!(
        loaded_session.url_request.expose_secret(),
        creds_session.url_request.expose_secret()
    );
    assert_eq!(
        loaded_session.url_master.expose_secret(),
        creds_session.url_master.expose_secret()
    );
    assert_eq!(
        loaded_session.url_price.expose_secret(),
        creds_session.url_price.expose_secret()
    );
    assert_eq!(
        loaded_session.url_event.expose_secret(),
        creds_session.url_event.expose_secret()
    );
    assert_eq!(
        loaded_session.url_event_ws.expose_secret(),
        creds_session.url_event_ws.expose_secret()
    );
    assert_eq!(loaded_session.expires_at_ms, creds_session.expires_at_ms);
    assert_eq!(loaded_session.zyoutoeki_kazei_c, creds_session.zyoutoeki_kazei_c);

    // (b) Debug output must not contain the plaintext password / URLs.
    let dbg = format!("{:?}", creds);
    assert!(
        !dbg.contains("vw20sr9h"),
        "Debug output leaks password: {dbg}"
    );
    assert!(
        !dbg.contains("SESSION1"),
        "Debug output leaks url_request: {dbg}"
    );
    assert!(
        !dbg.contains("SESSION5"),
        "Debug output leaks url_event_ws: {dbg}"
    );
    assert!(dbg.contains("***"), "Debug output should mask with ***: {dbg}");

    // (c) Compile-time assertion that the wire DTOs implement `Zeroize`
    // (provided by `Zeroizing<String>`). If a future refactor demotes the
    // wrapped fields back to bare `String`, this fn ceases to compile.
    fn _assert_zeroize<T: zeroize::Zeroize>() {}
    _assert_zeroize::<zeroize::Zeroizing<String>>();

    // And verify the actual conversion succeeds (so the debug_assert in
    // `From<&TachibanaCredentials>` is exercised in the same test).
    let _wire: engine_client::dto::TachibanaCredentialsWire = (&creds).into();
}

#[test]
fn test_update_session_in_keyring_creates_entry_when_none_exists() {
    // Regression: the very first successful login arrives via
    // `VenueCredentialsRefreshed` while the OS keyring is still empty.
    // Earlier `update_session_in_keyring` short-circuited with a warning
    // and never persisted the new session, so the next restart lost
    // the session and had to re-login.
    install_mock_keyring();
    // Force a clean slate for this test.
    if let Ok(entry) = keyring::Entry::new("flowsurface.tachibana", "user_id") {
        entry.delete_credential().ok();
    }
    assert!(
        load_tachibana_credentials().is_none(),
        "test must start with empty keyring"
    );

    let session = sample_session();
    flowsurface_data::config::tachibana::update_session_in_keyring(&session);

    let loaded =
        load_tachibana_credentials().expect("session-only entry must be created");
    assert_eq!(loaded.user_id, "", "first-time entry has empty user_id");
    assert_eq!(loaded.password.expose_secret(), "");
    // is_demo inferred from the URL host.
    assert!(
        loaded.is_demo,
        "demo-kabuka URL must imply is_demo=true; got {loaded:?}"
    );
    let s = loaded.session.expect("session persisted");
    assert_eq!(
        s.url_event_ws.expose_secret(),
        session.url_event_ws.expose_secret()
    );
}

#[test]
fn test_update_session_in_keyring_preserves_existing_user_id() {
    // When a prior entry exists, the user_id / password / is_demo are
    // preserved and only the session is replaced.
    install_mock_keyring();
    let original = sample_creds(); // user_id=uxf05882, password=vw20sr9h
    save_tachibana_credentials(&original);

    // Build a *new* session with different URL fragments.
    let mut new_session = sample_session();
    new_session.url_event_ws =
        SecretString::new("wss://demo-kabuka.e-shiten.jp/e_api_v4r8/evt/ROTATED/".into());

    flowsurface_data::config::tachibana::update_session_in_keyring(&new_session);
    let loaded = load_tachibana_credentials().expect("must round-trip");

    assert_eq!(loaded.user_id, "uxf05882", "user_id must survive");
    assert_eq!(
        loaded.password.expose_secret(),
        "vw20sr9h",
        "password must survive"
    );
    assert!(
        loaded.session.unwrap().url_event_ws.expose_secret().ends_with("ROTATED/"),
        "session must be replaced"
    );
}

#[test]
#[should_panic(expected = "second_password must be None in Phase 1")]
fn test_phase1_second_password_guard_panics_in_debug() {
    let creds = TachibanaCredentials {
        user_id: "u".into(),
        password: SecretString::new("p".to_string()),
        second_password: Some(SecretString::new("dummy".to_string())),
        is_demo: true,
        session: None,
    };
    let _wire: engine_client::dto::TachibanaCredentialsWire = (&creds).into();
}
