//! W&B 送信履歴モーダル — `Tools > 送信履歴を開く` で表示。
//!
//! `run-buffer/` 配下の各 `<run-id>/meta.json` を読んだ
//! [`RunBufferEntry`](crate::wandb_auth::RunBufferEntry) のリストを表示する。
//! status は completed / aborted / running の 3 値が想定される。
//!
//! 履歴 UI は read-only。削除は別経路（`Tools > バッファを削除…`）で行う。

use iced::{
    Element, Length,
    widget::{button, column, container, row, scrollable, text},
};

use crate::wandb_auth::RunBufferEntry;

#[derive(Debug, Clone)]
pub enum Message {
    Close,
}

#[derive(Debug, Clone)]
pub enum Action {
    Close,
}

pub struct WandbSubmissionLogModal {
    pub entries: Vec<RunBufferEntry>,
}

impl WandbSubmissionLogModal {
    pub fn new(entries: Vec<RunBufferEntry>) -> Self {
        Self { entries }
    }

    pub fn update(&mut self, message: Message) -> Option<Action> {
        match message {
            Message::Close => Some(Action::Close),
        }
    }

    pub fn view(&self) -> Element<'_, Message> {
        let header = text("W&B 送信履歴 (run-buffer/)").size(14);

        let body: Element<'_, Message> = if self.entries.is_empty() {
            text("送信履歴がまだありません").size(12).into()
        } else {
            let header_row = row![
                text("status").size(11).width(80),
                text("started_at").size(11).width(180),
                text("run_id").size(11).width(Length::Fill),
            ]
            .spacing(8);

            let list = self
                .entries
                .iter()
                .fold(column![header_row].spacing(2), |col, entry| {
                    // F9 R1-M1: `status="unknown"` の row は視覚的に注記する
                    // （履歴に出るが Submit できない silent failure を可視化）。
                    let status_label = if entry.status == "unknown" {
                        format!("⚠ {} (parse-degraded)", entry.status)
                    } else {
                        entry.status.clone()
                    };
                    col.push(
                        row![
                            text(status_label).size(11).width(80),
                            text(entry.started_at.as_str()).size(11).width(180),
                            text(entry.run_id.as_str()).size(11).width(Length::Fill),
                        ]
                        .spacing(8),
                    )
                });

            scrollable::Scrollable::with_direction(
                container(list).padding(4).width(Length::Fill),
                scrollable::Direction::Vertical(
                    scrollable::Scrollbar::new().width(6).scroller_width(4),
                ),
            )
            .height(280)
            .into()
        };

        let close_btn = button(text("閉じる").size(13)).on_press(Message::Close);

        let body = column![header, body, close_btn].spacing(10);

        container(body)
            .max_width(560)
            .padding(24)
            .style(crate::style::dashboard_modal)
            .into()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn entry(run_id: &str, status: &str, started_at: &str) -> RunBufferEntry {
        RunBufferEntry {
            run_id: run_id.to_string(),
            status: status.to_string(),
            started_at: started_at.to_string(),
            strategy_file: None,
        }
    }

    #[test]
    fn close_returns_close_action() {
        let mut modal = WandbSubmissionLogModal::new(vec![]);
        assert!(matches!(modal.update(Message::Close), Some(Action::Close)));
    }

    #[test]
    fn entries_are_stored() {
        let modal = WandbSubmissionLogModal::new(vec![
            entry("r1", "completed", "2026-05-04T00:00:00Z"),
            entry("r2", "aborted", "2026-05-04T01:00:00Z"),
        ]);
        assert_eq!(modal.entries.len(), 2);
    }
}
