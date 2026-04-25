//! 立花証券 e支店 credentials & session — internal types kept in the `data`
//! crate. Secret material is wrapped in [`secrecy::SecretString`] so that
//! accidental `Debug` / `format!` calls cannot leak the raw value. The wire
//! DTOs that cross the IPC boundary live in `engine-client::dto` and convert
//! via [`From<&TachibanaCredentials>`] (T3 will wire the actual conversion).
//!
//! This module is the authoritative store: keyring read/write, in-memory
//! holding, and the source-of-truth for `ProcessManager` re-injection on
//! Python restart. T0.2 ships only the types and a stub keyring API; the
//! full restore/persist logic is implemented in T3.

use engine_client::dto::{TachibanaCredentialsWire, TachibanaSessionWire};
use secrecy::{ExposeSecret, SecretString};
use serde::{Deserialize, Serialize};

const KEYCHAIN_SERVICE: &str = "flowsurface.tachibana";
const KEYCHAIN_KEY_USER: &str = "user_id";

/// Authoritative credentials. Phase 1 collects neither the second password
/// nor a `session` (those arrive via the Python login flow in T3); the type
/// is Phase-2-ready so the DTO doesn't need a breaking change later.
#[derive(Clone)]
pub struct TachibanaCredentials {
    pub user_id: String,
    pub password: SecretString,
    /// Phase 1: always `None`. Phase 2 (orders) collects + persists this.
    pub second_password: Option<SecretString>,
    pub is_demo: bool,
    /// Last validated session, if `keyring` had one. `None` means we must
    /// re-login on the next Python handshake.
    pub session: Option<TachibanaSession>,
}

impl std::fmt::Debug for TachibanaCredentials {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("TachibanaCredentials")
            .field("user_id", &self.user_id)
            .field("password", &"***")
            .field("second_password", &self.second_password.as_ref().map(|_| "***"))
            .field("is_demo", &self.is_demo)
            .field("session", &self.session)
            .finish()
    }
}

/// 5 virtual URLs returned by `CLMAuthLoginRequest`, plus the "valid until"
/// hint and the equity-tax bucket. All URL fields are wrapped in
/// `SecretString`: the URLs themselves are session-bearer tokens — leaking
/// them in logs is equivalent to leaking the password.
#[derive(Clone)]
pub struct TachibanaSession {
    pub url_request: SecretString,
    pub url_master: SecretString,
    pub url_price: SecretString,
    pub url_event: SecretString,
    pub url_event_ws: SecretString,
    /// `None` is allowed: 立花 API does not return an explicit expiry. The
    /// Python startup path treats `None` as "must call validate_session".
    pub expires_at_ms: Option<i64>,
    /// 譲渡益課税区分 (`sZyoutoekiKazeiC`). Reused for order placement in
    /// Phase 2; not secret on its own but kept on the session struct so we
    /// don't have to carry a separate handle.
    pub zyoutoeki_kazei_c: String,
}

impl std::fmt::Debug for TachibanaSession {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("TachibanaSession")
            .field("url_request", &"***")
            .field("url_master", &"***")
            .field("url_price", &"***")
            .field("url_event", &"***")
            .field("url_event_ws", &"***")
            .field("expires_at_ms", &self.expires_at_ms)
            .field("zyoutoeki_kazei_c", &self.zyoutoeki_kazei_c)
            .finish()
    }
}

/// Plain-string mirror used only for keyring round-tripping. We keep it
/// `Deserialize` only — there's no reason for runtime code to *create* a
/// stored-form value other than from on-disk material.
#[derive(Deserialize, Serialize)]
pub(crate) struct StoredCredentials {
    pub user_id: String,
    pub password: String,
    pub second_password: Option<String>,
    pub is_demo: bool,
    pub session: Option<StoredSession>,
}

#[derive(Deserialize, Serialize)]
pub(crate) struct StoredSession {
    pub url_request: String,
    pub url_master: String,
    pub url_price: String,
    pub url_event: String,
    pub url_event_ws: String,
    pub expires_at_ms: Option<i64>,
    pub zyoutoeki_kazei_c: String,
}

impl From<StoredCredentials> for TachibanaCredentials {
    fn from(s: StoredCredentials) -> Self {
        Self {
            user_id: s.user_id,
            password: SecretString::new(s.password),
            second_password: s.second_password.map(SecretString::new),
            is_demo: s.is_demo,
            session: s.session.map(Into::into),
        }
    }
}

impl From<StoredSession> for TachibanaSession {
    fn from(s: StoredSession) -> Self {
        Self {
            url_request: SecretString::new(s.url_request),
            url_master: SecretString::new(s.url_master),
            url_price: SecretString::new(s.url_price),
            url_event: SecretString::new(s.url_event),
            url_event_ws: SecretString::new(s.url_event_ws),
            expires_at_ms: s.expires_at_ms,
            zyoutoeki_kazei_c: s.zyoutoeki_kazei_c,
        }
    }
}

/// Read previously-saved Tachibana credentials from the OS keyring. Returns
/// `None` if no entry exists or the stored payload is unparseable. Full
/// keyring write/refresh is wired up in T3.
pub fn load_tachibana_credentials() -> Option<TachibanaCredentials> {
    let entry = keyring::Entry::new(KEYCHAIN_SERVICE, KEYCHAIN_KEY_USER).ok()?;
    let secret = entry.get_password().ok()?;
    let stored: StoredCredentials = serde_json::from_str(&secret).ok()?;
    Some(stored.into())
}

// ── IPC wire conversions ──────────────────────────────────────────────────────
//
// These impls are the **single point** where `expose_secret()` is called for
// Tachibana credentials (F-B2). Everywhere else, the secret stays inside
// `SecretString`. The resulting `*Wire` value is intended to be serialized
// and dropped immediately — do not store it anywhere long-lived.

impl From<&TachibanaCredentials> for TachibanaCredentialsWire {
    fn from(c: &TachibanaCredentials) -> Self {
        Self {
            user_id: c.user_id.clone(),
            password: c.password.expose_secret().clone(),
            second_password: c
                .second_password
                .as_ref()
                .map(|s| s.expose_secret().clone()),
            is_demo: c.is_demo,
            session: c.session.as_ref().map(Into::into),
        }
    }
}

impl From<&TachibanaSession> for TachibanaSessionWire {
    fn from(s: &TachibanaSession) -> Self {
        Self {
            url_request: s.url_request.expose_secret().clone(),
            url_master: s.url_master.expose_secret().clone(),
            url_price: s.url_price.expose_secret().clone(),
            url_event: s.url_event.expose_secret().clone(),
            url_event_ws: s.url_event_ws.expose_secret().clone(),
            expires_at_ms: s.expires_at_ms,
            zyoutoeki_kazei_c: s.zyoutoeki_kazei_c.clone(),
        }
    }
}
