//! Tachibana venue lifecycle banner.
//!
//! Renders the `VenueState::Error` (and only that variant) as a
//! palette-coloured banner at the top of the main view. The banner
//! contents are **entirely Python-authored** — Rust supplies the
//! severity → palette role mapping and nothing else (F-Banner1 / plan
//! §3 Step E).
//!
//! The Python `VenueError.message` is split on `'\n'` into:
//!
//! 1. header line  — bold-weighted heading
//! 2. body line(s) — additional context
//! 3. button label — used iff `class.action()` is `Relogin` or
//!    `Dismiss`; the third line acts as the button text
//!
//! Empty or shorter messages degrade gracefully (no header / no
//! button label) so a Python emitter that has not yet adopted the
//! 3-line convention still produces a sensible banner.
//!
//! `VenueState::Idle` / `LoginInFlight` / `Ready` produce no banner
//! (`view` returns `None`) — the dialog itself is the in-flight
//! affordance, and `Ready` / `Idle` are quiescent states.

use engine_client::error::{VenueErrorAction, VenueErrorClass, VenueErrorSeverity};
use iced::widget::{button, column, container, text};
use iced::{Element, Length, Theme};

use crate::venue_state::VenueState;

#[derive(Debug, Clone)]
pub(crate) enum BannerMessage {
    /// User pressed the "再ログイン" button supplied via the third
    /// line of `VenueError.message`. Bubbles up as
    /// `Flowsurface::Message::RequestTachibanaLogin(Trigger::Manual)`.
    Relogin,
    /// User pressed the "閉じる" button. Bubbles up as a banner
    /// dismiss request that transitions `tachibana_state` back to
    /// `Idle` so the banner is hidden until the next venue event.
    Dismiss,
}

#[derive(Debug, PartialEq, Eq)]
struct ParsedMessage<'a> {
    header: Option<&'a str>,
    body: Option<&'a str>,
    button_label: Option<&'a str>,
}

/// Decompose `VenueError.message` according to the 3-line Phase 1
/// convention. Trailing empty fields are dropped so a single-line
/// message places its content in `body` (not `header`).
fn parse_message(message: &str) -> ParsedMessage<'_> {
    let mut iter = message.splitn(3, '\n');
    let first = iter.next();
    let second = iter.next();
    let third = iter.next();

    match (first, second, third) {
        (Some(only), None, _) => ParsedMessage {
            header: None,
            body: Some(only).filter(|s| !s.is_empty()),
            button_label: None,
        },
        (Some(h), Some(b), None) => ParsedMessage {
            header: Some(h).filter(|s| !s.is_empty()),
            body: Some(b).filter(|s| !s.is_empty()),
            button_label: None,
        },
        (Some(h), Some(b), Some(label)) => ParsedMessage {
            header: Some(h).filter(|s| !s.is_empty()),
            body: Some(b).filter(|s| !s.is_empty()),
            button_label: Some(label).filter(|s| !s.is_empty()),
        },
        _ => ParsedMessage {
            header: None,
            body: None,
            button_label: None,
        },
    }
}

/// Render the banner element for a given Tachibana venue state.
/// Returns `None` for non-banner states (`Idle` / `LoginInFlight` /
/// `Ready`).
pub fn view(state: &VenueState) -> Option<Element<'_, BannerMessage>> {
    match state {
        VenueState::Error { class, message } => Some(error_banner(class, message)),
        VenueState::Idle | VenueState::LoginInFlight | VenueState::Ready => None,
    }
}

/// Phase 1 暫定 fallback button labels — Rust 側に文字列リテラルを
/// 持たせない F-Banner1 の制約は **Python が単一行 `VenueError.message`
/// しか出していない現実と非互換** だった (reviewer 2026-04-26 R5
/// MEDIUM-2)。Python が 3 行構造を採用するまでの暫定として、
/// `class.action()` がボタンを要求し、かつ `parsed.button_label` が
/// 取れないときに限ってここで定義する label を使う。Python が
/// 3 行 message を emit するようになり次第、これらの定数を削除して
/// F-Banner1 を完全な状態に戻すこと。spec.md §3.3 / open-questions.md
/// に追跡項目を残す。
const RELOGIN_LABEL_FALLBACK: &str = "再ログイン";
const DISMISS_LABEL_FALLBACK: &str = "閉じる";

/// Resolve the action button label. Returns `None` when the venue
/// class action is `Hidden` (no button at all). Otherwise prefers
/// the Python-supplied label (3rd line of `VenueError.message`)
/// and falls back to the Phase 1 暫定 Rust constant.
fn action_button_label<'a>(
    parsed: &ParsedMessage<'a>,
    action: VenueErrorAction,
) -> Option<&'a str> {
    match action {
        VenueErrorAction::Hidden => None,
        VenueErrorAction::Relogin => parsed.button_label.or(Some(RELOGIN_LABEL_FALLBACK)),
        VenueErrorAction::Dismiss => parsed.button_label.or(Some(DISMISS_LABEL_FALLBACK)),
    }
}

fn error_banner<'a>(class: &VenueErrorClass, message: &'a str) -> Element<'a, BannerMessage> {
    let parsed = parse_message(message);
    let severity = class.severity();
    let action = class.action();

    let mut col = column![].spacing(4);
    if let Some(header) = parsed.header {
        col = col.push(text(header).size(13));
    }
    if let Some(body) = parsed.body {
        col = col.push(text(body).size(11));
    }

    if let Some(label) = action_button_label(&parsed, action) {
        match action {
            VenueErrorAction::Relogin => {
                col = col.push(button(text(label).size(11)).on_press(BannerMessage::Relogin));
            }
            VenueErrorAction::Dismiss => {
                col = col.push(button(text(label).size(11)).on_press(BannerMessage::Dismiss));
            }
            VenueErrorAction::Hidden => {
                // Unreachable — `action_button_label` returns `None`
                // for Hidden, so we never enter this match arm.
            }
        }
    }

    container(col)
        .width(Length::Fill)
        .padding(8)
        .style(move |theme| banner_container_style(theme, severity))
        .into()
}

fn banner_container_style(theme: &Theme, severity: VenueErrorSeverity) -> container::Style {
    let palette = theme.extended_palette();
    let (bg, fg) = match severity {
        VenueErrorSeverity::Error => (palette.danger.weak.color, palette.danger.weak.text),
        VenueErrorSeverity::Warning => (palette.warning.weak.color, palette.warning.weak.text),
    };
    container::Style {
        background: Some(bg.into()),
        text_color: Some(fg),
        border: iced::Border {
            radius: 4.0.into(),
            ..Default::default()
        },
        ..Default::default()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use engine_client::error::classify_venue_error;

    #[test]
    fn idle_state_yields_no_banner() {
        assert!(view(&VenueState::Idle).is_none());
    }

    #[test]
    fn ready_state_yields_no_banner() {
        assert!(view(&VenueState::Ready).is_none());
    }

    #[test]
    fn login_in_flight_yields_no_banner() {
        // Plan §3 Step E acceptance: dialog itself is the affordance
        // during LoginInFlight. The banner is reserved for terminal
        // error states.
        assert!(view(&VenueState::LoginInFlight).is_none());
    }

    #[test]
    fn error_state_yields_banner() {
        let class = classify_venue_error("session_expired");
        let state = VenueState::Error {
            class,
            message: "セッション切れ\n再ログインしてください\n再ログイン".to_string(),
        };
        assert!(view(&state).is_some());
    }

    #[test]
    fn parse_message_three_lines_extracts_header_body_label() {
        let parsed = parse_message("セッション切れ\n再ログインしてください\n再ログイン");
        assert_eq!(parsed.header, Some("セッション切れ"));
        assert_eq!(parsed.body, Some("再ログインしてください"));
        assert_eq!(parsed.button_label, Some("再ログイン"));
    }

    #[test]
    fn parse_message_two_lines_has_no_button_label() {
        let parsed = parse_message("ヘッダ\n本文");
        assert_eq!(parsed.header, Some("ヘッダ"));
        assert_eq!(parsed.body, Some("本文"));
        assert_eq!(parsed.button_label, None);
    }

    #[test]
    fn parse_message_single_line_goes_into_body() {
        // Python emitters that have not adopted the 3-line convention
        // still produce a usable banner — the message is rendered as
        // a single body line and no button shows.
        let parsed = parse_message("ログインに失敗しました");
        assert_eq!(parsed.header, None);
        assert_eq!(parsed.body, Some("ログインに失敗しました"));
        assert_eq!(parsed.button_label, None);
    }

    #[test]
    fn parse_message_empty_lines_are_dropped() {
        let parsed = parse_message("\n本文\n");
        assert_eq!(parsed.header, None);
        assert_eq!(parsed.body, Some("本文"));
        assert_eq!(parsed.button_label, None);
    }

    /// Exhaustive transition table from plan §3 Step E. Pure FSM
    /// behaviour is already covered in `venue_state::tests`; this
    /// test pins the *banner-visible projection* of those
    /// transitions so a future change to either layer breaks the
    /// table here.
    #[test]
    fn banner_transitions() {
        use crate::venue_state::VenueEvent;

        let class = classify_venue_error("session_expired");

        let cases: &[(VenueState, VenueEvent, VenueState, bool)] = &[
            // (start, event, end, banner_visible_after)
            (
                VenueState::Idle,
                VenueEvent::LoginStarted,
                VenueState::LoginInFlight,
                false,
            ),
            (
                VenueState::LoginInFlight,
                VenueEvent::LoginCancelled,
                VenueState::Idle,
                false,
            ),
            (
                VenueState::LoginInFlight,
                VenueEvent::Ready,
                VenueState::Ready,
                false,
            ),
            (
                VenueState::LoginInFlight,
                VenueEvent::LoginError {
                    class,
                    message: "x".to_string(),
                },
                VenueState::Error {
                    class,
                    message: "x".to_string(),
                },
                true,
            ),
            (
                VenueState::Error {
                    class,
                    message: "y".to_string(),
                },
                VenueEvent::LoginStarted,
                VenueState::LoginInFlight,
                false,
            ),
        ];

        for (start, event, expected_end, banner_after) in cases {
            let actual_end = start.clone().next(event.clone());
            assert_eq!(actual_end, *expected_end, "transition mismatch");
            assert_eq!(
                view(&actual_end).is_some(),
                *banner_after,
                "banner visibility after {event:?} from {start:?} mismatches"
            );
        }
    }

    #[test]
    fn action_button_label_uses_python_supplied_label_when_present() {
        let class = classify_venue_error("session_expired"); // Relogin
        let parsed = parse_message("ヘッダ\n本文\n再ログイン Python");
        let label = action_button_label(&parsed, class.action());
        assert_eq!(label, Some("再ログイン Python"));
    }

    #[test]
    fn action_button_label_falls_back_for_relogin_when_message_is_single_line() {
        // Reviewer 2026-04-26 R5 (MEDIUM-2): Python emits single-line
        // messages today (`_MSG_LOGIN_FAILED` etc.). Without a fallback
        // the Relogin button silently disappears.
        let class = classify_venue_error("session_expired"); // Relogin
        let parsed = parse_message("セッション切れです");
        let label = action_button_label(&parsed, class.action());
        assert_eq!(label, Some(RELOGIN_LABEL_FALLBACK));
        assert_eq!(label, Some("再ログイン"));
    }

    #[test]
    fn action_button_label_falls_back_for_dismiss_when_message_is_single_line() {
        let class = classify_venue_error("phone_auth_required"); // Dismiss
        let parsed = parse_message("電話認証が必要です");
        let label = action_button_label(&parsed, class.action());
        assert_eq!(label, Some(DISMISS_LABEL_FALLBACK));
        assert_eq!(label, Some("閉じる"));
    }

    #[test]
    fn action_button_label_returns_none_for_hidden_even_with_third_line() {
        // Hidden suppresses the button regardless of Python supplying
        // a label — architecture.md §6 codes like `unsupported_venue`
        // have no UI recovery path.
        let class = classify_venue_error("unsupported_venue"); // Hidden
        let parsed = parse_message("h\nb\n（無視されるラベル）");
        let label = action_button_label(&parsed, class.action());
        assert_eq!(label, None);
    }

    #[test]
    fn dismiss_action_uses_dismiss_button() {
        // architecture.md §6: phone_auth_required → action=Dismiss.
        // Verify the banner still renders (no panic on style lookup
        // and the parser handled the label).
        let class = classify_venue_error("phone_auth_required");
        let state = VenueState::Error {
            class,
            message: "電話認証\n所定の手順で認証してください\n閉じる".to_string(),
        };
        assert!(view(&state).is_some());
    }

    #[test]
    fn hidden_action_renders_message_without_button() {
        // architecture.md §6: unsupported_venue → action=Hidden.
        // Even if message includes a third line, Hidden suppresses
        // the button. We can't directly inspect the rendered Element
        // tree from a unit test, but we pin that the banner is
        // returned (i.e. rendering does not panic on the Hidden
        // branch).
        let class = classify_venue_error("unsupported_venue");
        let state = VenueState::Error {
            class,
            message: "未対応 venue\n本文\n（無視されるラベル）".to_string(),
        };
        assert!(view(&state).is_some());
    }
}
