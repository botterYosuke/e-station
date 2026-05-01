//! Phase O3 UX — Positions panel (PP3).
//!
//! Displays current cash + margin positions held at the venue.
//! Refresh button fires `GetPositions` IPC; `PositionsUpdated` populates the table.

use engine_client::dto::PositionRecordWire;
use iced::{
    Element,
    widget::{button, center, column, container, row, scrollable, text},
};

// ── State ─────────────────────────────────────────────────────────────────────

/// Positions panel state.
#[derive(Debug, Default)]
pub struct PositionsPanel {
    positions: Vec<PositionRecordWire>,
    /// True when this panel is shown in REPLAY mode (banner + no live IPC).
    is_replay: bool,
    /// Loading badge ("⟳ 更新中…") flag.
    loading: bool,
    /// Error message from latest fetch.
    last_error: Option<String>,
    /// 最終更新時刻（Unix ミリ秒）
    last_updated_ms: Option<i64>,
}

impl PositionsPanel {
    pub fn new() -> Self {
        Self::default()
    }

    /// REPLAY モード専用パネルを生成する。
    pub fn new_replay() -> Self {
        Self {
            is_replay: true,
            ..Self::default()
        }
    }

    pub fn set_positions(&mut self, positions: Vec<PositionRecordWire>, ts_ms: i64) {
        self.positions = positions;
        self.last_updated_ms = Some(ts_ms);
        self.last_error = None;
        self.loading = false;
    }

    /// IPC リクエスト送信中フラグを設定する。
    /// `true` にすると stale error をクリアしてペイン内ローディングバッジを表示する。
    pub fn set_loading(&mut self, loading: bool) {
        if loading {
            self.last_error = None; // stale-error 対策
        }
        self.loading = loading;
    }

    pub fn set_error(&mut self, message: String) {
        self.last_error = Some(message);
        self.loading = false;
    }

    pub fn is_replay(&self) -> bool {
        self.is_replay
    }

    pub fn position_count(&self) -> usize {
        self.positions.len()
    }

    pub fn is_empty(&self) -> bool {
        self.positions.is_empty()
    }
}

// ── Messages / Actions ────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub enum Message {
    RefreshClicked,
}

#[derive(Debug, Clone)]
pub enum Action {
    /// Request a fresh positions list via `GetPositions` IPC.
    RequestPositions,
}

pub fn update(panel: &mut PositionsPanel, msg: Message) -> Option<Action> {
    match msg {
        // REPLAY pane では IPC を発行しない（OrdersPanel と整合）
        Message::RefreshClicked if panel.is_replay() => None,
        Message::RefreshClicked => Some(Action::RequestPositions),
    }
}

// ── View ──────────────────────────────────────────────────────────────────────

pub fn view(panel: &PositionsPanel) -> Element<'_, Message> {
    let refresh_btn = button(text("更新").size(12))
        .on_press(Message::RefreshClicked)
        .padding([2, 8]);

    let header = if panel.loading {
        row![refresh_btn, text("↻ 更新中…").size(11)]
            .spacing(4)
            .padding([4, 8])
    } else {
        row![refresh_btn].spacing(4).padding([4, 8])
    };

    if panel.is_replay() {
        return column![header, center(text("⏪ REPLAY — 保有銘柄なし").size(13)),]
            .height(iced::Length::Fill)
            .into();
    }

    if let Some(ref err) = panel.last_error {
        return column![
            header,
            center(
                column![text("⚠ 取得失敗").size(13), text(err.as_str()).size(11),]
                    .spacing(4)
                    .align_x(iced::Alignment::Center),
            ),
        ]
        .height(iced::Length::Fill)
        .into();
    }

    if panel.positions.is_empty() {
        return column![header, center(text("保有なし").size(13)),]
            .height(iced::Length::Fill)
            .into();
    }

    let rows: Vec<Element<'_, Message>> = panel.positions.iter().map(position_row).collect();

    column![header, scrollable(column(rows).spacing(2).padding([4, 8])),]
        .height(iced::Length::Fill)
        .into()
}

fn position_row(p: &PositionRecordWire) -> Element<'_, Message> {
    let type_label = match p.position_type.as_str() {
        "cash" => "現物",
        "margin_credit" => "信用(信用)",
        "margin_general" => "信用(一般)",
        other => other,
    };

    let qty_display = p
        .qty
        .parse::<i64>()
        .map(|n| format!("{} 株", format_number(n)))
        .unwrap_or_else(|_| format!("{} 株", p.qty));

    let value_display = if p.market_value.is_empty() {
        "-".to_string()
    } else {
        p.market_value
            .parse::<i64>()
            .map(|v| format!("¥{}", format_number(v)))
            .unwrap_or_else(|_| "-".to_string())
    };

    let mut r = row![
        text(p.instrument_id.as_str()).size(12).width(90),
        text(type_label).size(11).width(70),
        text(qty_display).size(12).width(70),
        text(value_display).size(12).width(90),
    ]
    .spacing(8)
    .align_y(iced::Alignment::Center);

    if let Some(id) = &p.tategyoku_id {
        r = r.push(text(format!("[建{}]", id)).size(10));
    }

    container(r).padding([2, 4]).into()
}

fn format_number(n: i64) -> String {
    let s = n.abs().to_string();
    let chars: Vec<char> = s.chars().rev().collect();
    let grouped: String = chars
        .chunks(3)
        .map(|c| c.iter().collect::<String>())
        .collect::<Vec<_>>()
        .join(",");
    let result: String = grouped.chars().rev().collect();
    if n < 0 { format!("-{result}") } else { result }
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn make_wire(instrument_id: &str, qty: i64, market_value: i64) -> PositionRecordWire {
        PositionRecordWire {
            instrument_id: instrument_id.to_string(),
            qty: qty.to_string(),
            market_value: market_value.to_string(),
            position_type: "cash".to_string(),
            tategyoku_id: None,
            venue: "tachibana".to_string(),
        }
    }

    #[test]
    fn position_count_starts_at_zero() {
        let panel = PositionsPanel::new();
        assert_eq!(panel.position_count(), 0);
    }

    #[test]
    fn set_positions_updates_count() {
        let mut panel = PositionsPanel::new();
        let positions = vec![make_wire("7203.TSE", 100, 345600)];
        panel.set_positions(positions, 1746000000000);
        assert_eq!(panel.position_count(), 1);
    }

    #[test]
    fn set_positions_empty_count_is_zero() {
        let mut panel = PositionsPanel::new();
        panel.set_positions(vec![], 1746000000000);
        assert_eq!(panel.position_count(), 0);
    }

    #[test]
    fn set_positions_clears_loading_and_error() {
        let mut panel = PositionsPanel::new();
        panel.set_loading(true);
        panel.set_error("some error".to_string());
        panel.set_positions(vec![make_wire("7203.TSE", 100, 0)], 1746000000000);
        assert!(!panel.loading);
        assert!(panel.last_error.is_none());
    }

    #[test]
    fn set_loading_true_clears_stale_error() {
        let mut panel = PositionsPanel::new();
        panel.set_error("stale error".to_string());
        panel.set_loading(true);
        assert!(panel.last_error.is_none());
        assert!(panel.loading);
    }

    #[test]
    fn set_error_clears_loading() {
        let mut panel = PositionsPanel::new();
        panel.set_loading(true);
        panel.set_error("fetch failed".to_string());
        assert!(!panel.loading);
        assert!(panel.last_error.is_some());
    }

    #[test]
    fn refresh_clicked_live_returns_request_positions() {
        let mut panel = PositionsPanel::new();
        let action = update(&mut panel, Message::RefreshClicked);
        assert!(matches!(action, Some(Action::RequestPositions)));
    }

    #[test]
    fn refresh_clicked_replay_returns_none() {
        let mut panel = PositionsPanel::new_replay();
        let action = update(&mut panel, Message::RefreshClicked);
        assert!(action.is_none());
    }
}
