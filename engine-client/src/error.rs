// ── VenueError severity / action classification (MEDIUM-5, F-L9) ──────────────
//
// Centralizes the `VenueError.code` → (severity, action) mapping so the
// Banner renderer never branches on raw code strings. Adding a new code
// requires editing one place; the unknown-code branch is fail-safe
// (`Severity::Error` + `ActionButton::Hidden`) so a future Python-side
// emission never breaks the UI.
//
// The tabulated codes match architecture.md §6 verbatim. The Python side
// authors `VenueError.message`; the UI shows that string verbatim and
// uses the result of `classify_venue_error` only to pick severity colors
// and which (if any) action button to render.

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VenueErrorSeverity {
    /// Recoverable — the user can keep using the app, but the venue
    /// is in a degraded state.
    Warning,
    /// Non-recoverable until the user takes the suggested action.
    Error,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VenueErrorAction {
    /// No action button is shown — the banner is informational only.
    Hidden,
    /// Show a "再ログイン" button that re-fires `RequestVenueLogin`.
    Relogin,
    /// Show a "閉じる" button that simply dismisses the banner.
    Dismiss,
}

/// Resolved (severity, action) pair for a `VenueError.code`.
///
/// MEDIUM-3 (ラウンド 6 強制修正 / Group F): the inner fields are
/// `pub(crate)` and accessed via the public [`Self::severity`] /
/// [`Self::action`] methods. Construction outside this crate goes
/// through [`classify_venue_error`] / [`VenueErrorCode::classify`], so
/// the (Severity, Action) invariants table cannot be bypassed by an
/// external caller fabricating a `VenueErrorClass { severity, action }`
/// literal.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct VenueErrorClass {
    pub(crate) severity: VenueErrorSeverity,
    pub(crate) action: VenueErrorAction,
}

impl VenueErrorClass {
    pub fn severity(&self) -> VenueErrorSeverity {
        self.severity
    }

    pub fn action(&self) -> VenueErrorAction {
        self.action
    }
}

/// Strongly-typed `VenueError.code` (MEDIUM-4 ラウンド 6 強制修正 /
/// Group F). The previous `&str`-keyed table allowed silent typos at
/// emitter sites; with this enum, adding a new variant forces a match
/// arm in [`Self::classify`] and a corresponding conversion in
/// [`Self::from_str`]. `Unknown(String)` is the only fail-safe fall-
/// through and resolves to `(Error, Hidden)`.
///
/// `#[non_exhaustive]` is set so external crates cannot pattern-match
/// exhaustively against the enum and break when a new code is added
/// (architecture.md §6 promises forward-compat at the banner layer).
#[non_exhaustive]
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum VenueErrorCode {
    SessionExpired,
    LoginFailed,
    UnreadNotices,
    PhoneAuthRequired,
    TickerNotFound,
    TransportError,
    SessionRestoreFailed,
    UnsupportedVenue,
    /// Any code that isn't yet known in this table. The classifier
    /// returns `(Error, Hidden)` for these, matching the
    /// `classify_venue_error` fail-safe default.
    Unknown(String),
}

impl VenueErrorCode {
    /// Parse a `&str` into the typed code. Unknown strings become
    /// `Unknown(s.to_string())` rather than panicking — a wire-protocol
    /// drift between Rust and Python must not crash the UI. Named
    /// `from_code` (rather than `from_str`) so it does not collide
    /// with the inferred `std::str::FromStr` trait method.
    pub fn from_code(s: &str) -> Self {
        match s {
            "session_expired" => VenueErrorCode::SessionExpired,
            "login_failed" => VenueErrorCode::LoginFailed,
            "unread_notices" => VenueErrorCode::UnreadNotices,
            "phone_auth_required" => VenueErrorCode::PhoneAuthRequired,
            "ticker_not_found" => VenueErrorCode::TickerNotFound,
            "transport_error" => VenueErrorCode::TransportError,
            "session_restore_failed" => VenueErrorCode::SessionRestoreFailed,
            "unsupported_venue" => VenueErrorCode::UnsupportedVenue,
            other => VenueErrorCode::Unknown(other.to_string()),
        }
    }

    /// Resolve the severity + action for this code. The match is
    /// exhaustive over the typed variants (compiler enforced via the
    /// non-`Unknown` arms) so adding a new code in this crate forces
    /// editing this site. `Unknown` is the fail-safe default.
    pub fn classify(&self) -> VenueErrorClass {
        use VenueErrorAction::*;
        use VenueErrorSeverity::*;
        match self {
            // HIGH-1 (ラウンド 6): per architecture.md §6 failure-mode
            // table. `session_restore_failed` surfaces when Python's
            // `_restore_session_from_payload` rejects malformed wire URLs
            // (scheme / host validation failure) — re-login is the
            // intended remediation. `unsupported_venue` is emitted by
            // `_do_set_venue_credentials` when a payload arrives for a
            // venue Python doesn't know about — the banner is hidden
            // because no user action helps; this is a programmer / wire
            // schema bug.
            VenueErrorCode::SessionExpired => VenueErrorClass {
                severity: Error,
                action: Relogin,
            },
            VenueErrorCode::LoginFailed => VenueErrorClass {
                severity: Error,
                action: Relogin,
            },
            VenueErrorCode::UnreadNotices => VenueErrorClass {
                severity: Warning,
                action: Relogin,
            },
            VenueErrorCode::PhoneAuthRequired => VenueErrorClass {
                severity: Error,
                action: Dismiss,
            },
            VenueErrorCode::TickerNotFound => VenueErrorClass {
                severity: Warning,
                action: Dismiss,
            },
            VenueErrorCode::TransportError => VenueErrorClass {
                severity: Error,
                action: Relogin,
            },
            VenueErrorCode::SessionRestoreFailed => VenueErrorClass {
                severity: Error,
                action: Relogin,
            },
            VenueErrorCode::UnsupportedVenue => VenueErrorClass {
                severity: Error,
                action: Hidden,
            },
            VenueErrorCode::Unknown(_) => VenueErrorClass {
                severity: Error,
                action: Hidden,
            },
        }
    }
}

/// Map a `VenueError.code` to its severity + action button. Unknown codes
/// fall through to `(Error, Hidden)` so a UI client never crashes on a
/// freshly-introduced Python-side code. Thin wrapper over
/// [`VenueErrorCode::from_code`] + [`VenueErrorCode::classify`] to
/// preserve backwards compatibility for callers that still hold a
/// `&str` code.
pub fn classify_venue_error(code: &str) -> VenueErrorClass {
    VenueErrorCode::from_code(code).classify()
}

#[cfg(test)]
mod venue_error_class_tests {
    use super::*;

    // M9: replace the loose "is wired up" loop with one explicit
    // assertion per architecture.md §6 row. The previous form
    // (`action != Hidden || severity == Error`) accidentally accepts
    // (Error, Hidden) as well — i.e. the fail-safe default — for any
    // row, so a regression that quietly demotes a documented row to
    // (Error, Hidden) would not fail the table test. Pin the exact
    // (severity, action) tuple per code instead.

    #[test]
    fn session_expired_is_error_relogin() {
        assert_eq!(
            classify_venue_error("session_expired"),
            VenueErrorClass {
                severity: VenueErrorSeverity::Error,
                action: VenueErrorAction::Relogin,
            }
        );
    }

    #[test]
    fn login_failed_is_error_relogin() {
        assert_eq!(
            classify_venue_error("login_failed"),
            VenueErrorClass {
                severity: VenueErrorSeverity::Error,
                action: VenueErrorAction::Relogin,
            }
        );
    }

    #[test]
    fn unread_notices_is_warning_relogin() {
        assert_eq!(
            classify_venue_error("unread_notices"),
            VenueErrorClass {
                severity: VenueErrorSeverity::Warning,
                action: VenueErrorAction::Relogin,
            }
        );
    }

    #[test]
    fn phone_auth_required_is_error_dismiss() {
        assert_eq!(
            classify_venue_error("phone_auth_required"),
            VenueErrorClass {
                severity: VenueErrorSeverity::Error,
                action: VenueErrorAction::Dismiss,
            }
        );
    }

    #[test]
    fn ticker_not_found_is_warning_dismiss() {
        assert_eq!(
            classify_venue_error("ticker_not_found"),
            VenueErrorClass {
                severity: VenueErrorSeverity::Warning,
                action: VenueErrorAction::Dismiss,
            }
        );
    }

    #[test]
    fn transport_error_is_error_relogin() {
        assert_eq!(
            classify_venue_error("transport_error"),
            VenueErrorClass {
                severity: VenueErrorSeverity::Error,
                action: VenueErrorAction::Relogin,
            }
        );
    }

    #[test]
    fn session_restore_failed_is_error_relogin() {
        assert_eq!(
            classify_venue_error("session_restore_failed"),
            VenueErrorClass {
                severity: VenueErrorSeverity::Error,
                action: VenueErrorAction::Relogin,
            }
        );
    }

    #[test]
    fn unsupported_venue_is_error_hidden() {
        assert_eq!(
            classify_venue_error("unsupported_venue"),
            VenueErrorClass {
                severity: VenueErrorSeverity::Error,
                action: VenueErrorAction::Hidden,
            }
        );
    }

    #[test]
    fn unknown_code_is_fail_safe_error_hidden() {
        let class = classify_venue_error("brand_new_code_for_phase_2");
        assert_eq!(class.severity(), VenueErrorSeverity::Error);
        assert_eq!(class.action(), VenueErrorAction::Hidden);
    }

    // MEDIUM-3 (ラウンド 6): VenueErrorClass fields are pub(crate); the
    // public surface is the accessor methods. A regression that re-
    // exposes the fields publicly would be caught by external-crate
    // callers, but we pin the accessor contract here too.
    #[test]
    fn venue_error_class_exposes_severity_and_action_via_accessors() {
        let class = classify_venue_error("session_expired");
        assert_eq!(class.severity(), VenueErrorSeverity::Error);
        assert_eq!(class.action(), VenueErrorAction::Relogin);
    }

    // MEDIUM-4 (ラウンド 6): typed VenueErrorCode classify match. The
    // table-shape is the same as the &str path, but the enum makes
    // adding a new code force a `match` edit.
    #[test]
    fn venue_error_code_typed_classify_matches_string_path() {
        for code in [
            "session_expired",
            "login_failed",
            "unread_notices",
            "phone_auth_required",
            "ticker_not_found",
            "transport_error",
            "session_restore_failed",
            "unsupported_venue",
        ] {
            let typed = VenueErrorCode::from_code(code).classify();
            let stringly = classify_venue_error(code);
            assert_eq!(typed, stringly, "diverged for code={code}");
        }
    }

    #[test]
    fn venue_error_code_unknown_round_trips_to_fail_safe() {
        let unknown = VenueErrorCode::from_code("brand_new_code");
        assert!(matches!(unknown, VenueErrorCode::Unknown(ref s) if s == "brand_new_code"));
        let class = unknown.classify();
        assert_eq!(class.severity(), VenueErrorSeverity::Error);
        assert_eq!(class.action(), VenueErrorAction::Hidden);
    }
}

/// Errors produced by the engine-client crate.
#[derive(Debug, thiserror::Error)]
pub enum EngineClientError {
    #[error("WebSocket error: {0}")]
    WebSocket(String),

    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),

    #[error("Engine restarting")]
    EngineRestarting,

    #[error(
        "Schema version mismatch: local={local_major}.{local_minor}, remote={remote_major}.{remote_minor}"
    )]
    SchemaMismatch {
        local_major: u16,
        local_minor: u16,
        remote_major: u16,
        remote_minor: u16,
    },

    #[error("Handshake timeout")]
    HandshakeTimeout,

    #[error("Connection refused")]
    ConnectionRefused,

    #[error("Engine error: {code}: {message}")]
    EngineError { code: String, message: String },

    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),
}
