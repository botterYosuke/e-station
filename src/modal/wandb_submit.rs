//! W&B run submission modal.
//!
//! Presents a form where the user can specify a W&B project name, run name,
//! tags, and notes before submitting a buffered replay run to Weights & Biases.
//!
//! # Reentrancy guard (submit_in_flight)
//!
//! The `submitting` flag acts as the `submit_in_flight` sentinel (統一決定 46).
//! While it is `true` any further `Message::Submit` is silently dropped, so
//! only one subprocess can run at a time.
//
// F9 R1-H8: module-wide `#![allow(dead_code)]` removed. Per-item allows only
// where strictly needed.

// マスキングは crate::mask_secrets に集約（C3, R1 Phase 1）。raw String を直接
// log_lines に格納する経路は型レベルで禁止する。
use crate::mask_secrets::{MaskedLine, mask_secrets};

use iced::{
    Element, Length, Theme,
    widget::{button, column, container, row, scrollable, text, text_input},
};

/// Display-safe authentication status forwarded from [`WandbAuthState`].
///
/// Mirrors `WandbAuthState` so that modal code does not need to import the
/// auth module directly.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AuthDisplayState {
    /// No API key found — "未設定".
    NotSet,
    /// Key found via `WANDB_API_KEY` env — "env 経由".
    Env,
    /// Key found via the wandb credential store — "netrc 経由".
    Netrc,
}

impl AuthDisplayState {
    /// User-facing label.  Never contains the actual key.
    pub fn label(&self) -> &'static str {
        match self {
            Self::NotSet => "未設定",
            Self::Env => "env 経由",
            Self::Netrc => "netrc 経由",
        }
    }

    pub fn is_authenticated(&self) -> bool {
        !matches!(self, Self::NotSet)
    }
}

/// M5 (R1 Phase 1): `WandbAuthState` から `AuthDisplayState` への一元化変換。
/// 二重管理を解消し、modal は `WandbAuthState` を受け取って `From` で変換する。
///
/// 変換規則：
/// - `authenticated == false` → `NotSet`（method 値に関わらず）
/// - それ以外は `method` に従う（Env / Netrc / None→NotSet）
impl From<&crate::wandb_auth::WandbAuthState> for AuthDisplayState {
    fn from(state: &crate::wandb_auth::WandbAuthState) -> Self {
        use crate::wandb_auth::AuthMethod;
        if !state.authenticated {
            return Self::NotSet;
        }
        match state.method {
            AuthMethod::Env => Self::Env,
            AuthMethod::Netrc => Self::Netrc,
            AuthMethod::NotSet => Self::NotSet,
        }
    }
}

/// Messages handled by [`WandbSubmitModal::update`].
#[derive(Debug, Clone)]
pub enum Message {
    ProjectChanged(String),
    RunNameChanged(String),
    TagsChanged(String),
    NotesChanged(String),
    Submit,
    Cancel,
    /// One stdout line received from the subprocess (already masked).
    LogLine(String),
    /// Subprocess exited successfully; `url` may be `None` if not present.
    Done(Option<String>),
    /// Subprocess exited with non-zero code.
    Failed(String, i32),
    /// User clicked the result URL — request the parent to open it (H6).
    OpenUrl(String),
}

/// Actions that the parent should react to.
#[derive(Debug, Clone)]
pub enum Action {
    /// Start the submit subprocess with these parameters.
    Submit {
        project: String,
        run_name: String,
        tags: String,
        /// M6: free-form user notes forwarded to `wandb.init(notes=...)`.
        /// May be empty.
        notes: String,
    },
    Cancel,
    /// User clicked the URL link — open in a browser.
    OpenUrl(String),
}

/// Maximum number of log lines stored in the modal.
const MAX_LOG_LINES: usize = 100;

/// W&B run submission modal state.
///
/// F9 R1-M8: fields are `pub(crate)` (not `pub`) because flowsurface is a
/// bin-only crate and these fields are only accessed from the binary.
pub struct WandbSubmitModal {
    /// W&B project name.
    pub(crate) project: String,
    /// Human-readable run name.
    pub(crate) run_name: String,
    /// Comma-separated tags.
    pub(crate) tags: String,
    /// Optional free-form notes.
    pub(crate) notes: String,
    /// Current W&B authentication status (no key value).
    pub(crate) auth_status: AuthDisplayState,
    /// Stdout tail from the running subprocess (mask_secrets applied).
    /// `MaskedLine` newtype によって raw String 格納が型レベルで禁止される（C3）。
    /// F9 R1-M5: `VecDeque` で先頭 pop を O(1) 化（旧 `Vec::remove(0)` を排除）。
    pub(crate) log_lines: std::collections::VecDeque<MaskedLine>,
    /// URL returned by a successful submission.
    pub(crate) result_url: Option<String>,
    /// Error message from a failed submission.
    pub(crate) error: Option<String>,
    /// `true` while the subprocess is running (submit_in_flight guard).
    pub(crate) submitting: bool,
}

impl WandbSubmitModal {
    /// Create a new modal with default values.
    ///
    /// `run_name_default` is typically derived from `meta.json` by the caller.
    pub fn new(
        run_name_default: impl Into<String>,
        strategy_stem: impl AsRef<str>,
        auth_status: AuthDisplayState,
    ) -> Self {
        let stem = strategy_stem.as_ref();
        Self {
            project: "flowsurface-strategies".to_string(),
            run_name: run_name_default.into(),
            tags: format!("replay,{stem}"),
            notes: String::new(),
            auth_status,
            log_lines: std::collections::VecDeque::new(),
            result_url: None,
            error: None,
            submitting: false,
        }
    }

    /// Process a [`Message`] and optionally return an [`Action`] for the parent.
    pub fn update(&mut self, message: Message) -> Option<Action> {
        match message {
            Message::ProjectChanged(v) => {
                self.project = v;
            }
            Message::RunNameChanged(v) => {
                self.run_name = v;
            }
            Message::TagsChanged(v) => {
                self.tags = v;
            }
            Message::NotesChanged(v) => {
                self.notes = v;
            }
            Message::Submit => {
                // Reentrancy guard: drop the message if already submitting.
                if self.submitting {
                    return None;
                }
                self.submitting = true;
                self.error = None;
                self.result_url = None;
                self.log_lines.clear();
                return Some(Action::Submit {
                    project: self.project.clone(),
                    run_name: self.run_name.clone(),
                    tags: self.tags.clone(),
                    notes: self.notes.clone(),
                });
            }
            Message::Cancel => {
                return Some(Action::Cancel);
            }
            Message::LogLine(raw_line) => {
                // Apply masking before storing.
                let masked = mask_secrets(&raw_line);
                // F9 R1-M5: VecDeque::pop_front (O(1)) replaces Vec::remove(0) (O(n)).
                if self.log_lines.len() >= MAX_LOG_LINES {
                    self.log_lines.pop_front();
                }
                self.log_lines.push_back(masked);
            }
            Message::Done(url) => {
                self.submitting = false;
                self.result_url = url;
            }
            Message::Failed(stderr, _exit_code) => {
                self.submitting = false;
                self.error = Some(stderr);
            }
            Message::OpenUrl(url) => {
                // H6: 送信完了 URL を外部ブラウザで開く要求を親に伝播する。
                // モーダル内部状態（submitting / result_url）は変更しない。
                return Some(Action::OpenUrl(url));
            }
        }
        None
    }

    /// Render the modal content.
    pub fn view(&self) -> Element<'_, Message> {
        // Auth status row
        let auth_label = text(format!("W&B 認証: {}", self.auth_status.label())).size(12);

        // Form fields
        let project_field = row![
            text("Project:").size(12).width(80),
            text_input("flowsurface-strategies", &self.project)
                .on_input(Message::ProjectChanged)
                .size(13)
        ]
        .spacing(8)
        .align_y(iced::Alignment::Center);

        let run_name_field = row![
            text("Run name:").size(12).width(80),
            text_input("", &self.run_name)
                .on_input(Message::RunNameChanged)
                .size(13)
        ]
        .spacing(8)
        .align_y(iced::Alignment::Center);

        let tags_field = row![
            text("Tags:").size(12).width(80),
            text_input("replay,strategy", &self.tags)
                .on_input(Message::TagsChanged)
                .size(13)
        ]
        .spacing(8)
        .align_y(iced::Alignment::Center);

        let notes_field = row![
            text("Notes:").size(12).width(80),
            text_input("(optional)", &self.notes)
                .on_input(Message::NotesChanged)
                .size(13)
        ]
        .spacing(8)
        .align_y(iced::Alignment::Center);

        // Action buttons
        let submit_btn = {
            let can_submit = !self.submitting && self.auth_status.is_authenticated();
            let btn = button(
                text(if self.submitting {
                    "送信中..."
                } else {
                    "送信"
                })
                .size(13),
            );
            if can_submit {
                btn.on_press(Message::Submit)
            } else {
                btn
            }
        };
        let cancel_btn = button(text("キャンセル").size(13)).on_press(Message::Cancel);

        let buttons = row![submit_btn, cancel_btn].spacing(8);

        // Log area (scrollable, max MAX_LOG_LINES lines)
        let log_content = {
            let lines: Element<'_, Message> = if self.log_lines.is_empty() {
                text("").size(11).into()
            } else {
                let log_col = self
                    .log_lines
                    .iter()
                    .fold(column![].spacing(1), |col, line| {
                        col.push(text(line.as_str()).size(11))
                    });
                log_col.into()
            };

            scrollable::Scrollable::with_direction(
                container(lines).padding(4).width(Length::Fill),
                scrollable::Direction::Vertical(
                    scrollable::Scrollbar::new().width(6).scroller_width(4),
                ),
            )
            .height(120)
        };

        // Result URL
        let result_area: Option<Element<'_, Message>> = if let Some(url) = &self.result_url {
            let url_text = text(url.as_str()).size(12);
            // H6: 「ブラウザで開く」は専用 Message::OpenUrl を経由して
            // Action::OpenUrl にマップする。Message::Done の再利用は禁止。
            let open_btn =
                button(text("ブラウザで開く").size(12)).on_press(Message::OpenUrl(url.clone()));
            Some(
                column![text("送信完了").size(13), url_text, open_btn,]
                    .spacing(4)
                    .into(),
            )
        } else {
            None
        };

        // Error display (red text)
        let error_area: Option<Element<'_, Message>> = self.error.as_ref().map(|err| {
            text(err.as_str())
                .size(12)
                .style(|theme: &Theme| {
                    let palette = theme.palette();
                    iced::widget::text::Style {
                        color: Some(palette.danger),
                    }
                })
                .into()
        });

        // Assemble
        let mut body = column![
            auth_label,
            project_field,
            run_name_field,
            tags_field,
            notes_field,
            buttons,
            log_content,
        ]
        .spacing(10);

        if let Some(result) = result_area {
            body = body.push(result);
        }
        if let Some(err) = error_area {
            body = body.push(err);
        }

        container(body)
            .max_width(420)
            .padding(24)
            .style(crate::style::dashboard_modal)
            .into()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn default_modal() -> WandbSubmitModal {
        WandbSubmitModal::new("test-run", "buy_and_hold", AuthDisplayState::Env)
    }

    #[test]
    fn initial_state_not_submitting() {
        let modal = default_modal();
        assert!(!modal.submitting);
    }

    #[test]
    fn submit_sets_submitting_flag() {
        let mut modal = default_modal();
        let action = modal.update(Message::Submit);
        assert!(modal.submitting);
        assert!(matches!(action, Some(Action::Submit { .. })));
    }

    #[test]
    fn submit_ignored_when_submitting() {
        let mut modal = default_modal();
        // First submit starts the process
        modal.update(Message::Submit);
        assert!(modal.submitting);
        // Second submit while in-flight must be ignored
        let action = modal.update(Message::Submit);
        assert!(action.is_none(), "second Submit must be dropped");
    }

    #[test]
    fn done_clears_submitting() {
        let mut modal = default_modal();
        modal.update(Message::Submit);
        modal.update(Message::Done(Some("https://wandb.ai/run".to_string())));
        assert!(!modal.submitting);
        assert_eq!(modal.result_url.as_deref(), Some("https://wandb.ai/run"));
    }

    #[test]
    fn failed_clears_submitting() {
        let mut modal = default_modal();
        modal.update(Message::Submit);
        modal.update(Message::Failed("error output".to_string(), 1));
        assert!(!modal.submitting);
        assert!(modal.error.is_some());
    }

    #[test]
    fn log_line_applies_masking() {
        let mut modal = default_modal();
        modal.update(Message::LogLine(
            "WANDB_API_KEY=supersecret token".to_string(),
        ));
        assert!(!modal.log_lines.is_empty());
        assert!(!modal.log_lines[0].as_str().contains("supersecret"));
    }

    #[test]
    fn log_lines_capped_at_max() {
        let mut modal = default_modal();
        for i in 0..=MAX_LOG_LINES + 5 {
            modal.update(Message::LogLine(format!("line {i}")));
        }
        assert!(modal.log_lines.len() <= MAX_LOG_LINES);
    }

    #[test]
    fn cancel_returns_action() {
        let mut modal = default_modal();
        let action = modal.update(Message::Cancel);
        assert!(matches!(action, Some(Action::Cancel)));
    }

    /// H6: Message::OpenUrl は Action::OpenUrl にマップされ、内部状態
    /// （submitting / result_url）を変更しない。Message::Done を再利用
    /// していた旧実装の不変条件違反を防ぐ回帰ガード。
    #[test]
    fn open_url_returns_action_without_mutating_state() {
        let mut modal = default_modal();
        // 完了状態を再現
        modal.submitting = false;
        modal.result_url = Some("https://wandb.ai/run/abc".to_string());
        let action = modal.update(Message::OpenUrl("https://wandb.ai/run/abc".to_string()));
        match action {
            Some(Action::OpenUrl(url)) => assert_eq!(url, "https://wandb.ai/run/abc"),
            other => panic!("expected Action::OpenUrl, got {other:?}"),
        }
        // 内部状態は変更されない
        assert!(!modal.submitting);
        assert_eq!(
            modal.result_url.as_deref(),
            Some("https://wandb.ai/run/abc")
        );
    }

    #[test]
    fn auth_display_state_not_set_label() {
        assert_eq!(AuthDisplayState::NotSet.label(), "未設定");
    }

    #[test]
    fn auth_not_set_blocks_submit_capability() {
        let modal = WandbSubmitModal::new("run", "strat", AuthDisplayState::NotSet);
        assert!(!modal.auth_status.is_authenticated());
    }

    // ── M5: From<&WandbAuthState> for AuthDisplayState ──────────────────────

    use crate::wandb_auth::{AuthMethod, WandbAuthState};

    fn auth(authenticated: bool, method: AuthMethod) -> WandbAuthState {
        WandbAuthState {
            authenticated,
            method,
            username: None,
            error: None,
        }
    }

    #[test]
    fn from_wandb_auth_state_maps_env() {
        let s = auth(true, AuthMethod::Env);
        assert_eq!(AuthDisplayState::from(&s), AuthDisplayState::Env);
    }

    #[test]
    fn from_wandb_auth_state_with_credstore_variant() {
        let s = auth(true, AuthMethod::Netrc);
        assert_eq!(AuthDisplayState::from(&s), AuthDisplayState::Netrc);
    }

    #[test]
    fn from_wandb_auth_state_maps_not_set_to_not_set() {
        let s = auth(true, AuthMethod::NotSet);
        assert_eq!(AuthDisplayState::from(&s), AuthDisplayState::NotSet);
    }

    #[test]
    fn from_wandb_auth_state_unauthenticated_maps_to_not_set() {
        // M5 不変条件: authenticated=false なら method に関わらず NotSet
        let s = auth(false, AuthMethod::Env);
        assert_eq!(AuthDisplayState::from(&s), AuthDisplayState::NotSet);
    }
}
