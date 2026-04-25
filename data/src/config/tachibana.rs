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
use std::sync::{Mutex, OnceLock};

const KEYCHAIN_SERVICE: &str = "flowsurface.tachibana";
const KEYCHAIN_KEY_USER: &str = "user_id";

/// Process-local lock that serialises the load→modify→save sequence in
/// [`update_session_in_keyring`]. Without it, two refreshes racing
/// (e.g. the in-`start()` wait wins one event and the post-start
/// continuation listener wins another that arrives micro-seconds later)
/// can interleave their reads and writes and produce a torn
/// `StoredCredentials` where the persisted `is_demo` / `user_id` no
/// longer match the freshest `session`. Cross-process protection is out
/// of scope here — multi-instance flowsurface is not a supported config
/// — but a single-instance lock removes the within-process ABA window.
fn keyring_write_lock() -> &'static Mutex<()> {
    static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
    LOCK.get_or_init(|| Mutex::new(()))
}

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
/// `None` if no entry exists or the stored payload is unparseable.
pub fn load_tachibana_credentials() -> Option<TachibanaCredentials> {
    let entry = keyring::Entry::new(KEYCHAIN_SERVICE, KEYCHAIN_KEY_USER).ok()?;
    let secret = entry.get_password().ok()?;
    // M-5: a corrupt JSON blob in the keyring used to be dropped silently
    // with `serde_json::from_str(&secret).ok()?`, leaving operators
    // unable to tell "no entry" apart from "entry is unreadable". Surface
    // the parse error via warn so a keyring rotation / version skew is
    // immediately diagnosable.
    let stored: StoredCredentials = match serde_json::from_str(&secret) {
        Ok(v) => v,
        Err(e) => {
            log::warn!("tachibana keyring entry is corrupt: {e}");
            return None;
        }
    };
    Some(stored.into())
}

/// Persist the supplied credentials (including any newly issued session)
/// into the OS keyring. The on-disk form is JSON via `StoredCredentials`
/// — the same shape `load_tachibana_credentials` reads. Errors are
/// logged but not propagated: the running session continues even if the
/// keyring write fails (the user simply has to log in again next time).
pub fn save_tachibana_credentials(creds: &TachibanaCredentials) {
    let entry = match keyring::Entry::new(KEYCHAIN_SERVICE, KEYCHAIN_KEY_USER) {
        Ok(e) => e,
        Err(err) => {
            log::warn!(
                "tachibana keyring entry init failed (service={KEYCHAIN_SERVICE} key={KEYCHAIN_KEY_USER}): {err}"
            );
            return;
        }
    };

    let stored = StoredCredentials::from(creds);
    let payload = match serde_json::to_string(&stored) {
        Ok(s) => s,
        Err(err) => {
            log::warn!("tachibana keyring serialize failed: {err}");
            return;
        }
    };

    if let Err(err) = entry.set_password(&payload) {
        log::warn!(
            "tachibana keyring write failed (service={KEYCHAIN_SERVICE} key={KEYCHAIN_KEY_USER}): {err}"
        );
    } else {
        log::info!("tachibana credentials stored in keyring");
    }
}

/// Persist a refreshed credential set (full triple) into the OS keyring.
///
/// Used when Python's `VenueCredentialsRefreshed` event includes the
/// `user_id` / `password` / `is_demo` the user actually authenticated
/// with — i.e. the full reverse-trip of `SetVenueCredentials`. Without
/// this path, an account switch / demo↔prod toggle / password change
/// performed in the login dialog never reaches the keyring (only the
/// session URLs do), and the next cold-start fast path replays stale
/// credentials.
///
/// Equivalent to building a [`TachibanaCredentials`] and calling
/// [`save_tachibana_credentials`], but accepts plaintext password so
/// callers outside the `data` crate (notably `flowsurface` main.rs)
/// don't need a direct dependency on `secrecy`.
pub fn save_refreshed_credentials(
    user_id: String,
    password: zeroize::Zeroizing<String>,
    is_demo: bool,
    session: TachibanaSession,
) {
    let _guard = keyring_write_lock()
        .lock()
        .unwrap_or_else(|e| e.into_inner());
    // H4: take the password as `Zeroizing<String>` so the calling site
    // (main.rs) cannot accidentally hold a plain `String` copy past the
    // keyring write. We then convert into `SecretString` for the
    // in-memory `TachibanaCredentials`; the original `Zeroizing` buffer
    // is dropped + zeroed at the end of this scope.
    let creds = TachibanaCredentials {
        user_id,
        password: SecretString::new((*password).clone()),
        // Phase 1 invariant (F-H5): second password is collected only in
        // Phase 2 (orders). Refresh therefore never carries it.
        second_password: None,
        is_demo,
        session: Some(session),
    };
    save_tachibana_credentials(&creds);
}

/// Persist a refreshed session into the OS keyring.
///
/// * If a credentials entry already exists, splice the new session into
///   it (preserving any prior `user_id` / `is_demo` so a re-login can
///   prefill the dialog).
/// * If no entry exists yet — i.e. **first-time login** — create a
///   session-only entry. Empty `user_id` / `password` are coherent in
///   Phase 1 because the runtime startup re-login path goes through the
///   env fast path or the tkinter dialog, not the stored credentials.
///   Without this branch, the very first successful login was lost on
///   the next restart (no keyring write happened) and the user had to
///   log in again every cold start.
///
/// `is_demo` is inferred from the session URL host. The two valid hosts
/// (`demo-kabuka.e-shiten.jp` and `kabuka.e-shiten.jp`) are pinned by
/// `tachibana_url.py::BASE_URL_PROD` / `BASE_URL_DEMO` and by the
/// `_validate_virtual_urls` https/wss checker, so the substring match
/// is stable.
pub fn update_session_in_keyring(session: &TachibanaSession) {
    // Serialise the entire load→modify→save against any other concurrent
    // refresh on the same process. The write lock is purely an in-process
    // mutex; the actual keyring API does not expose file locking.
    let _guard = keyring_write_lock()
        .lock()
        .unwrap_or_else(|e| e.into_inner());
    let creds = match load_tachibana_credentials() {
        Some(mut existing) => {
            existing.session = Some(session.clone());
            existing
        }
        None => {
            // url_request lives inside `SecretString`; we expose it ONLY
            // for the substring check (no log, no clone-out) and let it
            // drop at the end of the borrow.
            let is_demo = session
                .url_request
                .expose_secret()
                .contains("demo-kabuka.e-shiten.jp");
            TachibanaCredentials {
                user_id: String::new(),
                password: SecretString::new(String::new()),
                second_password: None,
                is_demo,
                session: Some(session.clone()),
            }
        }
    };
    save_tachibana_credentials(&creds);
}

// ── Stored ↔ runtime conversion ───────────────────────────────────────────────

impl From<&TachibanaCredentials> for StoredCredentials {
    fn from(c: &TachibanaCredentials) -> Self {
        Self {
            user_id: c.user_id.clone(),
            password: c.password.expose_secret().clone(),
            second_password: c.second_password.as_ref().map(|s| s.expose_secret().clone()),
            is_demo: c.is_demo,
            session: c.session.as_ref().map(StoredSession::from),
        }
    }
}

impl From<&TachibanaSession> for StoredSession {
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

// ── IPC wire conversions ──────────────────────────────────────────────────────
//
// These impls are the **single point** where `expose_secret()` is called for
// Tachibana credentials (F-B2). Everywhere else, the secret stays inside
// `SecretString`. The resulting `*Wire` value is intended to be serialized
// and dropped immediately — do not store it anywhere long-lived.

impl From<&TachibanaCredentials> for TachibanaCredentialsWire {
    fn from(c: &TachibanaCredentials) -> Self {
        // Phase 1 hard guard (H2 修正 / F-H5): the second password is part
        // of the order-placement surface (Phase 2). Sending it across the
        // IPC boundary in Phase 1 would create attack surface for code we
        // don't yet validate. The debug_assert is a noop in release but
        // catches mistakes in CI / debug builds before the wire payload
        // ever leaves the process.
        debug_assert!(
            c.second_password.is_none(),
            "second_password must be None in Phase 1 (F-H5)"
        );
        Self {
            user_id: c.user_id.clone(),
            password: c.password.expose_secret().clone().into(),
            second_password: c
                .second_password
                .as_ref()
                .map(|s| s.expose_secret().clone().into()),
            is_demo: c.is_demo,
            session: c.session.as_ref().map(Into::into),
        }
    }
}

impl From<TachibanaSessionWire> for TachibanaSession {
    fn from(s: TachibanaSessionWire) -> Self {
        // The wire DTO wraps URLs in `Zeroizing<String>`. We `clone()` the
        // inner `String` once into `SecretString`, then drop the wire
        // value (its `Zeroizing` wipes the original buffer). This is the
        // single legitimate exit point for these strings out of the
        // `Zeroizing` envelope.
        Self {
            url_request: SecretString::new(s.url_request.to_string()),
            url_master: SecretString::new(s.url_master.to_string()),
            url_price: SecretString::new(s.url_price.to_string()),
            url_event: SecretString::new(s.url_event.to_string()),
            url_event_ws: SecretString::new(s.url_event_ws.to_string()),
            expires_at_ms: s.expires_at_ms,
            zyoutoeki_kazei_c: s.zyoutoeki_kazei_c,
        }
    }
}

impl From<&TachibanaSession> for TachibanaSessionWire {
    fn from(s: &TachibanaSession) -> Self {
        Self {
            url_request: s.url_request.expose_secret().clone().into(),
            url_master: s.url_master.expose_secret().clone().into(),
            url_price: s.url_price.expose_secret().clone().into(),
            url_event: s.url_event.expose_secret().clone().into(),
            url_event_ws: s.url_event_ws.expose_secret().clone().into(),
            expires_at_ms: s.expires_at_ms,
            zyoutoeki_kazei_c: s.zyoutoeki_kazei_c.clone(),
        }
    }
}
