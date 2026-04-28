//! Phase U3 — Buying Power panel (Tu3.1). IPC 配線完了。
//!
//! 余力パネル。現物買付余力・信用新規可能額を `GetBuyingPower` IPC で取得して表示する。
//! - IPC 送信: `src/main.rs` の `BuyingPowerAction` ハンドラ
//! - IPC 受信 (成功): `Event::BuyingPowerUpdated` → `distribute_buying_power()`
//! - IPC 受信 (失敗): `EngineEvent::Error` → `Message::IpcError` → `distribute_buying_power_error()`

use iced::{
    Element,
    widget::{center, column, container, row, text},
};

// ── State ─────────────────────────────────────────────────────────────────────

/// Buying power panel state.
#[derive(Debug, Default)]
pub struct BuyingPowerPanel {
    /// 現物買付余力（円）
    cash_available: Option<i64>,
    /// 現物余力不足額（円）
    cash_shortfall: Option<i64>,
    /// 信用新規可能額（円）
    credit_available: Option<i64>,
    /// 最終更新時刻（Unix ミリ秒）
    last_updated_ms: Option<i64>,
    /// エラーメッセージ（API 呼び出し失敗時）
    error: Option<String>,
}

impl BuyingPowerPanel {
    pub fn new() -> Self {
        Self::default()
    }

    /// 現物余力データを更新する。
    pub fn set_cash_buying_power(&mut self, available: i64, shortfall: i64, ts_ms: i64) {
        self.cash_available = Some(available);
        self.cash_shortfall = Some(shortfall);
        self.last_updated_ms = Some(ts_ms);
        self.error = None;
    }

    /// 信用余力データを更新する。
    pub fn set_credit_buying_power(&mut self, available: i64, ts_ms: i64) {
        self.credit_available = Some(available);
        self.last_updated_ms = Some(ts_ms);
        self.error = None;
    }

    /// エラー状態にする。
    pub fn set_error(&mut self, message: String) {
        self.error = Some(message);
    }

    /// 余力不足かどうか。
    pub fn has_shortfall(&self) -> bool {
        self.cash_shortfall.map(|s| s > 0).unwrap_or(false)
    }
}

// ── Actions ───────────────────────────────────────────────────────────────────

/// Actions produced by the panel and consumed by the dashboard / main.rs.
#[derive(Debug, Clone)]
pub enum Action {
    /// 将来 IPC が追加されたときに使用。
    RequestBuyingPower,
}

// ── Messages ──────────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub enum Message {
    /// ユーザーが余力更新ボタンを押した。
    RefreshRequested,
}

// ── Update ────────────────────────────────────────────────────────────────────

pub fn update(_panel: &mut BuyingPowerPanel, msg: Message) -> Option<Action> {
    match msg {
        Message::RefreshRequested => Some(Action::RequestBuyingPower),
    }
}

// ── View ──────────────────────────────────────────────────────────────────────

/// 余力表示パネルをレンダリングする。
pub fn view(panel: &BuyingPowerPanel) -> Element<'_, Message> {
    if let Some(ref err) = panel.error {
        return center(
            column![text("余力取得エラー").size(13), text(err.as_str()).size(11),].spacing(4),
        )
        .into();
    }

    let cash_row = match panel.cash_available {
        Some(avail) => {
            let shortfall = panel.cash_shortfall.unwrap_or(0);
            let shortfall_text = if shortfall > 0 {
                format!("（不足: ¥{shortfall}）")
            } else {
                String::new()
            };
            row![
                text("現物余力:").size(12),
                text(format!("¥{avail}{shortfall_text}")).size(12),
            ]
            .spacing(8)
        }
        None => row![text("現物余力: ---").size(12)],
    };

    let credit_row = match panel.credit_available {
        Some(avail) => row![
            text("信用余力:").size(12),
            text(format!("¥{avail}")).size(12),
        ]
        .spacing(8),
        None => row![text("信用余力: ---").size(12)],
    };

    let refresh_btn =
        iced::widget::button(text("更新").size(11)).on_press(Message::RefreshRequested);

    let content = column![cash_row, credit_row, refresh_btn].spacing(6);

    container(content).padding(8).into()
}

// ── Order Form Extensions (Phase O3 T3.4 stubs) ────────────────────────────

/// 発注フォームの信用/現物セレクタの状態。
#[derive(Debug, Clone, PartialEq, Eq, Default)]
// Phase O3 フォーム拡張 UI 未実装（Order Entry フォームへの統合は別タスク）
#[allow(dead_code)]
pub enum CashMarginSelection {
    /// 現物（cash）— デフォルト
    #[default]
    Cash,
    /// 制度信用 新規
    MarginCreditNew,
    /// 制度信用 返済
    MarginCreditRepay,
    /// 一般信用 新規
    MarginGeneralNew,
    /// 一般信用 返済
    MarginGeneralRepay,
}

// Phase O3 フォーム拡張 UI 未実装（Order Entry フォームへの統合は別タスク）
#[allow(dead_code)]
impl CashMarginSelection {
    /// IPC tags に変換する。
    pub fn to_tag(&self) -> &'static str {
        match self {
            Self::Cash => "cash_margin=cash",
            Self::MarginCreditNew => "cash_margin=margin_credit_new",
            Self::MarginCreditRepay => "cash_margin=margin_credit_repay",
            Self::MarginGeneralNew => "cash_margin=margin_general_new",
            Self::MarginGeneralRepay => "cash_margin=margin_general_repay",
        }
    }

    /// 表示ラベル。
    pub fn label(&self) -> &'static str {
        match self {
            Self::Cash => "現物",
            Self::MarginCreditNew => "制度信用 新規",
            Self::MarginCreditRepay => "制度信用 返済",
            Self::MarginGeneralNew => "一般信用 新規",
            Self::MarginGeneralRepay => "一般信用 返済",
        }
    }
}

/// 逆指値フォームの状態。
#[derive(Debug, Clone, Default)]
// Phase O3 フォーム拡張 UI 未実装（Order Entry フォームへの統合は別タスク）
#[allow(dead_code)]
pub struct StopOrderForm {
    /// 逆指値注文を有効にするかどうか。
    pub enabled: bool,
    /// 逆指値トリガー価格（文字列）。
    pub trigger_price: String,
    /// 逆指値の注文種別（STOP_MARKET / STOP_LIMIT）。
    pub stop_order_type: StopOrderType,
}

/// 逆指値注文の種別。
#[derive(Debug, Clone, PartialEq, Eq, Default)]
// Phase O3 フォーム拡張 UI 未実装（Order Entry フォームへの統合は別タスク）
#[allow(dead_code)]
pub enum StopOrderType {
    /// 逆指値成行
    #[default]
    StopMarket,
    /// 逆指値指値
    StopLimit,
}

/// 期日指定フォームの状態。
#[derive(Debug, Clone, Default)]
// Phase O3 フォーム拡張 UI 未実装（Order Entry フォームへの統合は別タスク）
#[allow(dead_code)]
pub struct GtdForm {
    /// GTD（期日指定）を有効にするかどうか。
    pub enabled: bool,
    /// 期日（YYYYMMDD 形式の文字列）。
    pub expire_date: String,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn refresh_requested_returns_action() {
        let mut panel = BuyingPowerPanel::new();
        let action = update(&mut panel, Message::RefreshRequested);
        assert!(matches!(action, Some(Action::RequestBuyingPower)));
    }

    #[test]
    fn cash_margin_selection_to_tag() {
        assert_eq!(CashMarginSelection::Cash.to_tag(), "cash_margin=cash");
        assert_eq!(
            CashMarginSelection::MarginCreditNew.to_tag(),
            "cash_margin=margin_credit_new"
        );
        assert_eq!(
            CashMarginSelection::MarginCreditRepay.to_tag(),
            "cash_margin=margin_credit_repay"
        );
        assert_eq!(
            CashMarginSelection::MarginGeneralNew.to_tag(),
            "cash_margin=margin_general_new"
        );
        assert_eq!(
            CashMarginSelection::MarginGeneralRepay.to_tag(),
            "cash_margin=margin_general_repay"
        );
    }

    #[test]
    fn buying_power_panel_shortfall_detection() {
        let mut panel = BuyingPowerPanel::new();
        assert!(!panel.has_shortfall());
        panel.set_cash_buying_power(0, 50_000, 0);
        assert!(panel.has_shortfall());
        panel.set_cash_buying_power(500_000, 0, 0);
        assert!(!panel.has_shortfall());
    }

    #[test]
    fn buying_power_panel_error_state() {
        let mut panel = BuyingPowerPanel::new();
        panel.set_error("API connection failed".to_string());
        assert!(panel.error.is_some());
    }
}
