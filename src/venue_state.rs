//! Tachibana venue lifecycle state machine.
//!
//! Replaces the earlier `tachibana_ready: bool + tachibana_login_in_flight: bool`
//! double-flag with a single enum so illegal combinations
//! (e.g. `ready=true && login_in_flight=true`) are unrepresentable.
//!
//! The transition table is the single source of truth for which UI
//! actions are allowed in each state — see `is_ready()` /
//! `is_login_in_flight()`. Plan §3.2 / T3.5.
//!
//! Owned by [`crate::Flowsurface`]; `tickers_table` mirrors only the
//! `is_ready()` projection it needs to gate metadata fetches
//! (T35-U4-VenueReadyGate).

use engine_client::error::VenueErrorClass;

/// Source of a `RequestTachibanaLogin` emission. The `Auto` variant is
/// reserved for the U3 first-open path triggered by the user selecting
/// the Tachibana venue tile (still classified as LOW-3 "ユーザー明示"
/// per spec.md §3.2). `Manual` covers the explicit sidebar / banner
/// "再ログイン" button presses.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Trigger {
    /// First-time auto-fire on `Venue::Tachibana` selection while in
    /// [`VenueState::Idle`].
    Auto,
    /// User pressed the sidebar login icon or banner re-login button.
    Manual,
}

/// Tachibana venue lifecycle, driven by `EngineEvent::Venue*`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum VenueState {
    /// Initial state, and the state we return to after
    /// `VenueLoginCancelled`.
    Idle,
    /// Between `VenueLoginStarted` (or our own `RequestVenueLogin`) and
    /// the next terminal event (`VenueReady` / `VenueLoginCancelled` /
    /// `VenueError`).
    LoginInFlight,
    /// `VenueReady` received — metadata fetch / subscribe paths are
    /// unlocked.
    Ready,
    /// `VenueError` received. The banner renders the verbatim
    /// `message` and uses `class.action()` to decide which button (if
    /// any) to show.
    Error {
        class: VenueErrorClass,
        message: String,
    },
}

impl VenueState {
    pub fn is_ready(&self) -> bool {
        matches!(self, VenueState::Ready)
    }

    pub fn is_login_in_flight(&self) -> bool {
        matches!(self, VenueState::LoginInFlight)
    }
}

/// Inputs that drive the FSM. The `Hello` variant covers Python
/// subprocess restarts: Python re-emits `Hello` and our recovery loop
/// surfaces it here so we drop back to `Idle` and wait for a fresh
/// `VenueReady` (spec.md §3.2 idempotence requirement).
#[derive(Debug, Clone)]
pub enum VenueEvent {
    LoginStarted,
    LoginCancelled,
    LoginError {
        class: VenueErrorClass,
        message: String,
    },
    Ready,
    /// Engine subprocess restart detected — reset to `Idle`.
    EngineRehello,
    /// User pressed the banner's "閉じる" button. Transitions an
    /// `Error` state back to `Idle` (acknowledged); other states are
    /// idempotent so a stray dismiss has no effect.
    Dismissed,
}

impl VenueState {
    /// Apply a single venue event to produce the next state. Pure; no
    /// IO or logging here so the table is exercised exhaustively in
    /// unit tests below.
    #[must_use]
    pub fn next(self, event: VenueEvent) -> Self {
        match (self, event) {
            // Engine restart always returns us to Idle regardless of
            // current state.
            (_, VenueEvent::EngineRehello) => VenueState::Idle,

            // From any state, a fresh login attempt starts.
            (_, VenueEvent::LoginStarted) => VenueState::LoginInFlight,

            // Ready / Cancel / Error transitions only conclude an
            // in-flight login (or a re-emitted Ready after a Python
            // restart that has already re-pumped Hello → Idle).
            (_, VenueEvent::Ready) => VenueState::Ready,
            (_, VenueEvent::LoginCancelled) => VenueState::Idle,
            (_, VenueEvent::LoginError { class, message }) => VenueState::Error { class, message },

            // User-driven dismiss: only `Error` actually has anything
            // to clear; other states ignore so a stray dismiss is a
            // no-op.
            (VenueState::Error { .. }, VenueEvent::Dismissed) => VenueState::Idle,
            (other, VenueEvent::Dismissed) => other,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use engine_client::error::classify_venue_error;

    #[test]
    fn fresh_state_is_idle_and_not_ready() {
        let s = VenueState::Idle;
        assert!(!s.is_ready());
        assert!(!s.is_login_in_flight());
    }

    #[test]
    fn login_started_transitions_idle_to_in_flight() {
        let s = VenueState::Idle.next(VenueEvent::LoginStarted);
        assert!(s.is_login_in_flight());
        assert!(!s.is_ready());
    }

    #[test]
    fn ready_event_transitions_in_flight_to_ready() {
        let s = VenueState::LoginInFlight.next(VenueEvent::Ready);
        assert!(s.is_ready());
        assert!(!s.is_login_in_flight());
    }

    #[test]
    fn cancel_returns_to_idle_so_user_can_retry() {
        let s = VenueState::LoginInFlight.next(VenueEvent::LoginCancelled);
        assert_eq!(s, VenueState::Idle);
    }

    #[test]
    fn error_carries_class_and_verbatim_message() {
        let class = classify_venue_error("session_expired");
        let s = VenueState::LoginInFlight.next(VenueEvent::LoginError {
            class,
            message: "セッションの有効期限が切れました".to_string(),
        });
        match s {
            VenueState::Error { class: c, message } => {
                assert_eq!(c, class);
                assert_eq!(message, "セッションの有効期限が切れました");
            }
            other => panic!("expected Error variant, got {other:?}"),
        }
    }

    #[test]
    fn login_started_can_recover_from_error() {
        let class = classify_venue_error("login_failed");
        let s = VenueState::Error {
            class,
            message: "認証失敗".to_string(),
        }
        .next(VenueEvent::LoginStarted);
        assert!(s.is_login_in_flight());
    }

    #[test]
    fn engine_rehello_always_resets_to_idle() {
        let class = classify_venue_error("session_expired");
        let states = [
            VenueState::Idle,
            VenueState::LoginInFlight,
            VenueState::Ready,
            VenueState::Error {
                class,
                message: "x".to_string(),
            },
        ];
        for state in states {
            assert_eq!(state.next(VenueEvent::EngineRehello), VenueState::Idle);
        }
    }

    #[test]
    fn ready_is_idempotent_under_repeated_ready_events() {
        // VenueReady is documented as idempotent in dto.rs — re-emitting
        // it from Ready should keep us in Ready.
        let s = VenueState::Ready.next(VenueEvent::Ready);
        assert!(s.is_ready());
    }

    #[test]
    fn dismissed_clears_error_to_idle() {
        let class = classify_venue_error("phone_auth_required");
        let s = VenueState::Error {
            class,
            message: "x".to_string(),
        }
        .next(VenueEvent::Dismissed);
        assert_eq!(s, VenueState::Idle);
    }

    #[test]
    fn dismissed_is_noop_for_non_error_states() {
        // A stray Dismiss while Idle / Ready / LoginInFlight must not
        // perturb the FSM.
        assert_eq!(
            VenueState::Idle.next(VenueEvent::Dismissed),
            VenueState::Idle
        );
        assert_eq!(
            VenueState::Ready.next(VenueEvent::Dismissed),
            VenueState::Ready
        );
        assert!(
            VenueState::LoginInFlight
                .next(VenueEvent::Dismissed)
                .is_login_in_flight()
        );
    }
}
