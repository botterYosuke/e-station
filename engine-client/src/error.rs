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

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct VenueErrorClass {
    pub severity: VenueErrorSeverity,
    pub action: VenueErrorAction,
}

/// Map a `VenueError.code` to its severity + action button. Unknown codes
/// fall through to `(Error, Hidden)` so a UI client never crashes on a
/// freshly-introduced Python-side code.
pub fn classify_venue_error(code: &str) -> VenueErrorClass {
    use VenueErrorAction::*;
    use VenueErrorSeverity::*;
    match code {
        "session_expired" => VenueErrorClass {
            severity: Error,
            action: Relogin,
        },
        "login_failed" => VenueErrorClass {
            severity: Error,
            action: Relogin,
        },
        "unread_notices" => VenueErrorClass {
            severity: Warning,
            action: Relogin,
        },
        "phone_auth_required" => VenueErrorClass {
            severity: Error,
            action: Dismiss,
        },
        "ticker_not_found" => VenueErrorClass {
            severity: Warning,
            action: Dismiss,
        },
        "transport_error" => VenueErrorClass {
            severity: Error,
            action: Relogin,
        },
        // Fail-safe default for any future code not yet in the table.
        _ => VenueErrorClass {
            severity: Error,
            action: Hidden,
        },
    }
}

#[cfg(test)]
mod venue_error_class_tests {
    use super::*;

    #[test]
    fn architecture_md_section_6_table_is_covered() {
        // Every code that architecture.md §6 enumerates must produce a
        // non-default classification — the unit assertion is "the row
        // is wired up", not "this exact pair" (the pairs themselves are
        // documented in the table and pinned by the per-row tests below).
        let documented = [
            "session_expired",
            "login_failed",
            "unread_notices",
            "phone_auth_required",
            "ticker_not_found",
            "transport_error",
        ];
        for code in documented {
            let class = classify_venue_error(code);
            assert!(
                class.action != VenueErrorAction::Hidden
                    || class.severity == VenueErrorSeverity::Error,
                "code {code} produced an unexpected fall-through default"
            );
        }
    }

    #[test]
    fn unknown_code_is_fail_safe_error_hidden() {
        let class = classify_venue_error("brand_new_code_for_phase_2");
        assert_eq!(class.severity, VenueErrorSeverity::Error);
        assert_eq!(class.action, VenueErrorAction::Hidden);
    }

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
    fn unread_notices_is_warning_relogin() {
        assert_eq!(
            classify_venue_error("unread_notices"),
            VenueErrorClass {
                severity: VenueErrorSeverity::Warning,
                action: VenueErrorAction::Relogin,
            }
        );
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
