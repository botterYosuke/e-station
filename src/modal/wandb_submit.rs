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
#![allow(dead_code)]

// ---------------------------------------------------------------------------
// Inline masking helper (mirrors src/mask_secrets.rs)
// ---------------------------------------------------------------------------

/// Apply secret masking to a raw log line before storing it in the UI.
fn mask_secrets(line: &str) -> String {
    let mut out = line.to_string();

    if let Some(pos) = out.find("WANDB_API_KEY=") {
        let start = pos + "WANDB_API_KEY=".len();
        let end = out[start..]
            .find(|c: char| c.is_whitespace() || c == '&' || c == '"' || c == '\'')
            .map(|p| start + p)
            .unwrap_or(out.len());
        if start < end {
            out.replace_range(start..end, "***");
        }
    }

    if let Some(pos) = out.find("api_key=") {
        let start = pos + "api_key=".len();
        let end = out[start..]
            .find(|c: char| c.is_whitespace() || c == '&' || c == '"' || c == '\'')
            .map(|p| start + p)
            .unwrap_or(out.len());
        if start < end {
            out.replace_range(start..end, "***");
        }
    }

    for prefix in &["Bearer ", "bearer "] {
        if let Some(pos) = out.find(prefix) {
            let start = pos + prefix.len();
            let end = out[start..]
                .find(|c: char| c.is_whitespace() || c == '"' || c == '\'')
                .map(|p| start + p)
                .unwrap_or(out.len());
            if start < end {
                out.replace_range(start..end, "***");
            }
        }
    }

    out
}

// ---------------------------------------------------------------------------

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
}

/// Actions that the parent should react to.
#[derive(Debug, Clone)]
pub enum Action {
    /// Start the submit subprocess with these parameters.
    Submit {
        project: String,
        run_name: String,
        tags: String,
    },
    Cancel,
    /// User clicked the URL link — open in a browser.
    OpenUrl(String),
}

/// Maximum number of log lines stored in the modal.
const MAX_LOG_LINES: usize = 100;

/// W&B run submission modal state.
pub struct WandbSubmitModal {
    /// W&B project name.
    pub project: String,
    /// Human-readable run name.
    pub run_name: String,
    /// Comma-separated tags.
    pub tags: String,
    /// Optional free-form notes.
    pub notes: String,
    /// Current W&B authentication status (no key value).
    pub auth_status: AuthDisplayState,
    /// Stdout tail from the running subprocess (mask_secrets applied).
    pub log_lines: Vec<String>,
    /// URL returned by a successful submission.
    pub result_url: Option<String>,
    /// Error message from a failed submission.
    pub error: Option<String>,
    /// `true` while the subprocess is running (submit_in_flight guard).
    pub submitting: bool,
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
            log_lines: Vec::new(),
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
                });
            }
            Message::Cancel => {
                return Some(Action::Cancel);
            }
            Message::LogLine(raw_line) => {
                // Apply masking before storing.
                let masked = mask_secrets(&raw_line);
                if self.log_lines.len() >= MAX_LOG_LINES {
                    self.log_lines.remove(0);
                }
                self.log_lines.push(masked);
            }
            Message::Done(url) => {
                self.submitting = false;
                self.result_url = url;
            }
            Message::Failed(stderr, _exit_code) => {
                self.submitting = false;
                self.error = Some(stderr);
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
            let open_btn = button(text("ブラウザで開く").size(12))
                .on_press(Message::Done(self.result_url.clone()));
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
        assert!(!modal.log_lines[0].contains("supersecret"));
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

    #[test]
    fn auth_display_state_not_set_label() {
        assert_eq!(AuthDisplayState::NotSet.label(), "未設定");
    }

    #[test]
    fn auth_not_set_blocks_submit_capability() {
        let modal = WandbSubmitModal::new("run", "strat", AuthDisplayState::NotSet);
        assert!(!modal.auth_status.is_authenticated());
    }
}
