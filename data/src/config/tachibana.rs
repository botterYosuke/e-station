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

use crate::wire::tachibana::{TachibanaCredentialsWire, TachibanaSessionWire};
use secrecy::{ExposeSecret, SecretString};
use serde::{Deserialize, Serialize};
use std::sync::{Mutex, OnceLock};

const KEYCHAIN_SERVICE: &str = "flowsurface.tachibana";
const KEYCHAIN_KEY_USER: &str = "user_id";

/// MEDIUM-6 (ラウンド 6 強制修正 / Group F): newtype wrapper around the
/// 立花証券 e支店 user identifier (`uxNNNNNN` form). Prevents accidental
/// argument swap between `user_id` and other free-form `String`s
/// (notably `password.expose_secret()`) at function boundaries. The
/// inner `String` is **not** secret — it is logged in operator output —
/// but mixing it up with a secret string is exactly the kind of bug
/// the newtype pattern eliminates.
#[derive(Clone, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(transparent)]
pub struct TachibanaUserId(String);

impl TachibanaUserId {
    pub fn new(s: impl Into<String>) -> Self {
        Self(s.into())
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }

    /// Consume the newtype and return the inner `String`. Used at the
    /// IPC wire boundary where the `TachibanaCredentialsWire.user_id`
    /// field is plain `String` (the wire format is shared with Python).
    pub fn into_string(self) -> String {
        self.0
    }
}

impl std::fmt::Display for TachibanaUserId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

// MEDIUM-10 (ラウンド 7) / M-R8-1 (ラウンド 8): the previous
// `From<String>` / `From<&str>` blanket impls let any plain string
// flow into a `TachibanaUserId` implicitly via `.into()`, defeating
// the newtype's purpose at any call site that took
// `impl Into<TachibanaUserId>`. Use the explicit constructor
// `TachibanaUserId::new(s)` instead — the call site is then a
// deliberate, grep-able wrapping rather than an invisible coercion.
// The reverse `From<TachibanaUserId> for String` is also dropped
// (M-R8-1): callers use `into_string()` / `as_str().to_string()`. A
// previously stale `impl From<TachibanaUserId> for String` was kept
// alongside the comment by mistake; ラウンド 8 removes it so the
// newtype's outbound surface is as deliberate as its inbound one.

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
///
/// MEDIUM-1 (ラウンド 6 強制修正 / Group F): `password` and
/// `second_password` are private and accessed via [`Self::password`] /
/// [`Self::second_password`]. Construction is via [`Self::new`] (or via
/// `From<StoredCredentials>` / the existing keyring-load path). A
/// regression that makes either field `pub` again would let outside
/// code build a `TachibanaCredentials` literal without going through
/// the constructor — i.e. without honouring the Phase 1 invariant
/// `second_password == None`.
#[derive(Clone)]
pub struct TachibanaCredentials {
    user_id: TachibanaUserId,
    password: SecretString,
    /// Phase 1: always `None`. Phase 2 (orders) collects + persists this.
    second_password: Option<SecretString>,
    is_demo: bool,
    /// Last validated session, if `keyring` had one. `None` means we must
    /// re-login on the next Python handshake.
    session: Option<TachibanaSession>,
}

impl TachibanaCredentials {
    /// Public constructor enforcing the Phase 1 invariant
    /// `second_password == None` (F-H5). The IPC wire-conversion impl
    /// already carries a `debug_assert!`; this constructor is the
    /// preferred entry point for runtime code in `data` / `flowsurface`.
    pub fn new(
        user_id: TachibanaUserId,
        password: SecretString,
        is_demo: bool,
        session: Option<TachibanaSession>,
    ) -> Self {
        Self {
            user_id,
            password,
            second_password: None,
            is_demo,
            session,
        }
    }

    pub fn password(&self) -> &SecretString {
        &self.password
    }

    pub fn second_password(&self) -> Option<&SecretString> {
        self.second_password.as_ref()
    }

    /// HIGH-5 (ラウンド 7): accessor for the user identifier. Field is
    /// private so external callers cannot mutate it; replacement goes
    /// through the keyring helpers (`save_refreshed_credentials` etc.).
    pub fn user_id(&self) -> &TachibanaUserId {
        &self.user_id
    }

    /// HIGH-5 (ラウンド 7): accessor for the demo flag.
    pub fn is_demo(&self) -> bool {
        self.is_demo
    }

    /// HIGH-5 (ラウンド 7): accessor for the optional session. Returned
    /// as `Option<&TachibanaSession>` so the caller cannot accidentally
    /// take ownership without going through `clone()`.
    pub fn session(&self) -> Option<&TachibanaSession> {
        self.session.as_ref()
    }

    /// HIGH-5 (ラウンド 7): builder-style chained setter for the
    /// optional session. Used by code that constructs credentials in
    /// stages (currently only the `From<StoredCredentials>` impl path,
    /// but exposed for future callers that don't have all fields at
    /// `new()` time).
    pub fn with_session(mut self, session: TachibanaSession) -> Self {
        self.session = Some(session);
        self
    }

    /// HIGH-5 (ラウンド 7): module-internal mutator for the session
    /// field. Used by [`update_session_in_keyring`] when splicing a
    /// fresh session into a previously-loaded credentials value.
    /// Visibility is `pub(super)` so only the parent module
    /// (`crate::config`) can call it; outside the crate the field is
    /// effectively immutable.
    pub(super) fn set_session(&mut self, session: Option<TachibanaSession>) {
        self.session = session;
    }

    /// Test-only escape hatch used by
    /// `tachibana_keyring_roundtrip.rs::test_phase1_second_password_guard_panics_in_debug`
    /// to materialize the invalid Phase 1 state (`second_password ==
    /// Some`) and exercise the debug_assert in
    /// `From<&TachibanaCredentials> for TachibanaCredentialsWire`.
    /// Production code MUST construct via [`Self::new`] so the F-H5
    /// invariant cannot be reached by mistake.
    ///
    /// HIGH-4 (ラウンド 7): gated on `cfg(test)` for in-crate unit
    /// tests *or* the `testing` cargo feature for integration tests
    /// in `data/tests/`. Production binaries do **not** enable the
    /// feature and therefore cannot link against this symbol — the
    /// previous `#[doc(hidden)] pub` form was reachable from any
    /// dependent crate at runtime.
    #[cfg(any(test, feature = "testing"))]
    pub fn set_second_password_for_test(&mut self, sp: Option<SecretString>) {
        self.second_password = sp;
    }
}

impl std::fmt::Debug for TachibanaCredentials {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("TachibanaCredentials")
            .field("user_id", &self.user_id)
            .field("password", &"***")
            .field(
                "second_password",
                &self.second_password.as_ref().map(|_| "***"),
            )
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
    // MEDIUM-8 (ラウンド 7): all fields private. Readers go through
    // accessors (`url_request()` etc.) so a future refactor can change
    // the in-memory layout (e.g. switch `SecretString` to
    // `secrecy::SecretBox`) without touching every callsite.
    url_request: SecretString,
    url_master: SecretString,
    url_price: SecretString,
    url_event: SecretString,
    url_event_ws: SecretString,
    /// `None` is allowed: 立花 API does not return an explicit expiry. The
    /// Python startup path treats `None` as "must call validate_session".
    expires_at_ms: Option<i64>,
    /// 譲渡益課税区分 (`sZyoutoekiKazeiC`). Reused for order placement in
    /// Phase 2; not secret on its own but kept on the session struct so we
    /// don't have to carry a separate handle.
    zyoutoeki_kazei_c: String,
}

impl TachibanaSession {
    /// MEDIUM-8 (ラウンド 7): explicit constructor. Test code that needs
    /// a `TachibanaSession` literal goes through this — the field-by-
    /// field literal form is not available outside the module. The
    /// helper [`sample_session_for_test`] (cfg-gated) provides a
    /// canonical fixture for integration tests.
    pub fn new(
        url_request: SecretString,
        url_master: SecretString,
        url_price: SecretString,
        url_event: SecretString,
        url_event_ws: SecretString,
        expires_at_ms: Option<i64>,
        zyoutoeki_kazei_c: impl Into<String>,
    ) -> Self {
        Self {
            url_request,
            url_master,
            url_price,
            url_event,
            url_event_ws,
            expires_at_ms,
            zyoutoeki_kazei_c: zyoutoeki_kazei_c.into(),
        }
    }

    pub fn url_request(&self) -> &SecretString {
        &self.url_request
    }
    pub fn url_master(&self) -> &SecretString {
        &self.url_master
    }
    pub fn url_price(&self) -> &SecretString {
        &self.url_price
    }
    pub fn url_event(&self) -> &SecretString {
        &self.url_event
    }
    pub fn url_event_ws(&self) -> &SecretString {
        &self.url_event_ws
    }
    pub fn expires_at_ms(&self) -> Option<i64> {
        self.expires_at_ms
    }
    pub fn zyoutoeki_kazei_c(&self) -> &str {
        &self.zyoutoeki_kazei_c
    }

    /// MEDIUM-8 (ラウンド 7): replace the websocket URL only. Used by
    /// the keyring round-trip integration test to simulate a server-
    /// rotated session URL. Test-gated — production code constructs
    /// a fresh `TachibanaSession` rather than mutating one in place.
    #[cfg(any(test, feature = "testing"))]
    pub fn set_url_event_ws_for_test(&mut self, url: SecretString) {
        self.url_event_ws = url;
    }
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
///
/// MEDIUM-16 / M-12 (ラウンド 6): `Debug` is **deliberately not
/// derived**. The plain `String` `password` field would otherwise leak
/// verbatim into any `format!("{:?}", ...)` call — a regression of the
/// `SecretString` masking that `TachibanaCredentials` enforces. If you
/// ever need to debug the on-disk form, log only the field names that
/// are not secret (`user_id` / `is_demo` / `expires_at_ms`).
///
/// MEDIUM-2 (ラウンド 6 強制修正 / Group F): fields are `pub(super)`
/// rather than `pub`. The struct itself stays `pub(crate)` — only the
/// `From<&TachibanaCredentials> for StoredCredentials` impl in this
/// module is the legitimate construction path, and only the `From`
/// pair into `TachibanaCredentials` is the legitimate consumption
/// path. Keeping the fields `pub(super)` rather than `pub(crate)`
/// confines structural access strictly to this module.
#[derive(Deserialize, Serialize)]
pub(crate) struct StoredCredentials {
    pub(super) user_id: TachibanaUserId,
    pub(super) password: String,
    pub(super) second_password: Option<String>,
    pub(super) is_demo: bool,
    pub(super) session: Option<StoredSession>,
}

#[derive(Deserialize, Serialize)]
pub(crate) struct StoredSession {
    pub(super) url_request: String,
    pub(super) url_master: String,
    pub(super) url_price: String,
    pub(super) url_event: String,
    pub(super) url_event_ws: String,
    pub(super) expires_at_ms: Option<i64>,
    pub(super) zyoutoeki_kazei_c: String,
}

impl From<StoredCredentials> for TachibanaCredentials {
    fn from(s: StoredCredentials) -> Self {
        // Note: `second_password` is intentionally dropped on the way
        // in (Phase 1 invariant F-H5: orders are not yet implemented,
        // so a value would be unused attack surface). When Phase 2
        // wires order placement we will add a separate
        // `with_second_password` builder rather than re-exposing the
        // field on `TachibanaCredentials`.
        let _ = s.second_password;
        Self {
            user_id: s.user_id,
            password: SecretString::new(s.password),
            second_password: None,
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
    user_id: TachibanaUserId,
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
    //
    // MEDIUM-1 (ラウンド 6 強制修正): the public constructor
    // `TachibanaCredentials::new` enforces `second_password == None`
    // (F-H5) without exposing the field. Phase 2 will add an analogous
    // `with_second_password` builder when the order surface is wired.
    let creds = TachibanaCredentials::new(
        user_id,
        zeroizing_to_secret(password),
        is_demo,
        Some(session),
    );
    save_tachibana_credentials(&creds);
}

/// HIGH-4 (ラウンド 6): move the inner `String` from a
/// [`zeroize::Zeroizing`] envelope into a [`SecretString`] without
/// going through a plain `String` clone. The previous pattern was
/// `SecretString::new((*password).clone())`, which materialised an
/// intermediate `String` heap allocation that lived past the
/// keyring write — exactly the leak `Zeroizing` exists to prevent.
///
/// `std::mem::take` swaps the inner buffer out of the `Zeroizing`
/// envelope. The emptied envelope is then dropped by the caller,
/// which still runs the `Zeroize` impl on its (now-empty) buffer
/// — harmless but kept for symmetry. The moved buffer travels into
/// `SecretString` directly, so the only copies that exist are the
/// `SecretString`'s internal `Box<str>` and (transiently) the
/// caller's `Zeroizing<String>` shell.
///
/// MEDIUM-5 (ラウンド 7): `SecretString::new` internally converts
/// the supplied `String` into a `Box<str>`. That conversion performs
/// **one additional heap copy** (`String → Box<str>`) inside the
/// `secrecy` crate which is not avoidable without changing crates
/// — `secrecy` 0.8 stores secrets as `Box<str>` for size reasons.
/// The original `Zeroizing<String>` argument is dropped at the end
/// of this scope, and its (now-empty) inner buffer is zeroed by the
/// `Zeroize` impl. The intermediate `String` produced by
/// `mem::take` lives only inside `SecretString::new` and is
/// immediately consumed; on the way through it is *not* zeroed (it
/// is a plain `String`), so the lifetime window for the unprotected
/// copy is bounded by this single function call. A future move to
/// `secrecy::SecretBox` would close that gap; the current crate API
/// is the trade-off documented here.
fn zeroizing_to_secret(mut password: zeroize::Zeroizing<String>) -> SecretString {
    SecretString::new(std::mem::take(&mut *password))
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
            existing.set_session(Some(session.clone()));
            existing
        }
        None => {
            // url_request / url_event_ws live inside `SecretString`; we
            // expose them ONLY for the substring check (no log, no
            // clone-out) and let the borrow drop at the end of this
            // scope.
            //
            // MEDIUM-15 (ラウンド 6): require **both** `url_request`
            // (HTTPS) and `url_event_ws` (WSS) to carry the
            // `demo-kabuka.e-shiten.jp` host before classifying as
            // demo. A single field could in theory be misrouted (e.g.
            // a future migration that introduces split hosts) and a
            // single-field check would silently flip the prod/demo
            // flag. The two valid hosts are pinned by
            // `tachibana_url.py::BASE_URL_PROD` / `BASE_URL_DEMO` and
            // by the `_validate_virtual_urls` https/wss checker — see
            // also `_restore_session_from_payload`'s scheme guard.
            const DEMO_HOST: &str = "demo-kabuka.e-shiten.jp";
            let is_demo = session.url_request.expose_secret().contains(DEMO_HOST)
                && session.url_event_ws.expose_secret().contains(DEMO_HOST);
            TachibanaCredentials::new(
                TachibanaUserId::new(String::new()),
                SecretString::new(String::new()),
                is_demo,
                Some(session.clone()),
            )
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
            second_password: c
                .second_password
                .as_ref()
                .map(|s| s.expose_secret().clone()),
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
            user_id: c.user_id.as_str().to_string(),
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
        // MEDIUM-9 (ラウンド 7): the wire DTO wraps URLs in
        // `Zeroizing<String>`, which deref-targets to `String` but does
        // **not** expose an `into_inner()`. To move the URL into a
        // `SecretString` we therefore call `.to_string()` on the deref
        // target — which performs **one** copy of the bytes into a
        // fresh `String`. The original `Zeroizing<String>` source `s`
        // is dropped at end of scope and its `Zeroize` impl wipes the
        // inner buffer. The new `String` produced by `to_string()`
        // immediately moves into `SecretString::new`, which performs
        // the additional `String → Box<str>` repack documented on
        // `zeroizing_to_secret`. This is the single legitimate exit
        // point for these strings out of the `Zeroizing` envelope.
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
