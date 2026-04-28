use iced::{
    Alignment, Element, Length,
    widget::{button, column, container, pick_list, row, text, text_input},
};

#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub enum OrderSide {
    #[default]
    Buy,
    Sell,
}

#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub enum PriceKind {
    #[default]
    Market,
    Limit,
    /// 逆指値指値
    StopLimit,
}

/// 現物 / 信用区分（nautilus 互換名、立花用語は UI に出さない）
#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub enum CashMarginKind {
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

impl CashMarginKind {
    pub const ALL: &'static [Self] = &[
        Self::Cash,
        Self::MarginCreditNew,
        Self::MarginCreditRepay,
        Self::MarginGeneralNew,
        Self::MarginGeneralRepay,
    ];
}

impl std::fmt::Display for CashMarginKind {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Cash => write!(f, "現物"),
            Self::MarginCreditNew => write!(f, "制度信用 新規"),
            Self::MarginCreditRepay => write!(f, "制度信用 返済"),
            Self::MarginGeneralNew => write!(f, "一般信用 新規"),
            Self::MarginGeneralRepay => write!(f, "一般信用 返済"),
        }
    }
}

#[derive(Debug, Default)]
pub struct OrderEntryPanel {
    pub instrument_id: Option<String>,
    pub display_label: Option<String>,
    /// Venue identifier (e.g. "tachibana"). Set by `set_instrument`.
    pub venue: Option<String>,
    pub side: OrderSide,
    pub quantity: String,
    pub price_kind: PriceKind,
    pub price: String,
    /// StopLimit 時のトリガー価格
    pub trigger_price: String,
    /// 現物 / 信用区分
    pub cash_margin: CashMarginKind,
    pub submitting: bool,
    pub last_error: Option<String>,
    pub pending_request_id: Option<String>,
}

#[derive(Debug, Clone)]
pub enum Message {
    SideChanged(OrderSide),
    QuantityChanged(String),
    PriceKindChanged(PriceKind),
    PriceChanged(String),
    TriggerPriceChanged(String),
    CashMarginChanged(CashMarginKind),
    SubmitClicked,
    ConfirmSubmit,
}

#[derive(Debug, Clone)]
pub enum Action {
    /// Show an order confirmation dialog before submitting.
    RequestConfirm {
        instrument_id: String,
        order_side: engine_client::dto::OrderSide,
        order_type: engine_client::dto::OrderType,
        quantity: String,
        price: Option<String>,
    },
    SubmitOrder {
        request_id: String,
        venue: String,
        instrument_id: String,
        order_side: engine_client::dto::OrderSide,
        order_type: engine_client::dto::OrderType,
        quantity: String,
        price: Option<String>,
        trigger_price: Option<String>,
        cash_margin: CashMarginKind,
    },
}

impl OrderEntryPanel {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn update(&mut self, message: Message) -> Option<Action> {
        match message {
            Message::SideChanged(side) => self.side = side,
            Message::QuantityChanged(qty) => self.quantity = qty,
            Message::PriceKindChanged(kind) => self.price_kind = kind,
            Message::PriceChanged(price) => self.price = price,
            Message::TriggerPriceChanged(v) => self.trigger_price = v,
            Message::CashMarginChanged(k) => self.cash_margin = k,
            Message::SubmitClicked => {
                if self.quantity_valid() && self.instrument_id.is_some() {
                    return self.build_request_confirm_action();
                }
            }
            Message::ConfirmSubmit => {
                if self.quantity_valid() && self.instrument_id.is_some() {
                    return self.build_submit_action();
                }
            }
        }
        None
    }

    /// Build a `RequestConfirm` action — called by `SubmitClicked` to show a
    /// confirmation dialog before actual IPC submission.
    fn build_request_confirm_action(&self) -> Option<Action> {
        let instrument_id = self.instrument_id.clone()?;
        let order_side = match self.side {
            OrderSide::Buy => engine_client::dto::OrderSide::Buy,
            OrderSide::Sell => engine_client::dto::OrderSide::Sell,
        };
        let order_type = match self.price_kind {
            PriceKind::Market => engine_client::dto::OrderType::Market,
            PriceKind::Limit => engine_client::dto::OrderType::Limit,
            PriceKind::StopLimit => engine_client::dto::OrderType::StopLimit,
        };
        let price = match self.price_kind {
            PriceKind::Market => None,
            PriceKind::Limit | PriceKind::StopLimit => Some(self.price.clone()),
        };
        Some(Action::RequestConfirm {
            instrument_id,
            order_side,
            order_type,
            quantity: self.quantity.clone(),
            price,
        })
    }

    fn build_submit_action(&mut self) -> Option<Action> {
        let instrument_id = self.instrument_id.clone()?;
        let venue = self.venue.clone()?;

        let request_id = uuid::Uuid::new_v4().to_string();
        self.submitting = true;
        self.pending_request_id = Some(request_id.clone());
        let order_type = match self.price_kind {
            PriceKind::Market => engine_client::dto::OrderType::Market,
            PriceKind::Limit => engine_client::dto::OrderType::Limit,
            PriceKind::StopLimit => engine_client::dto::OrderType::StopLimit,
        };
        let price = match self.price_kind {
            PriceKind::Market => None,
            PriceKind::Limit | PriceKind::StopLimit => Some(self.price.clone()),
        };
        let trigger_price = match self.price_kind {
            PriceKind::StopLimit => Some(self.trigger_price.clone()),
            _ => None,
        };

        let order_side = match self.side {
            OrderSide::Buy => engine_client::dto::OrderSide::Buy,
            OrderSide::Sell => engine_client::dto::OrderSide::Sell,
        };

        Some(Action::SubmitOrder {
            request_id,
            venue,
            instrument_id,
            order_side,
            order_type,
            quantity: self.quantity.clone(),
            price,
            trigger_price,
            cash_margin: self.cash_margin,
        })
    }

    pub fn view(&self) -> Element<'_, Message> {
        let instrument_label = self.display_label.as_deref().unwrap_or("銘柄未選択");

        let side_row = {
            let is_buy = self.side == OrderSide::Buy;
            let buy_btn = button(text("買い").size(13))
                .on_press(Message::SideChanged(OrderSide::Buy))
                .style(move |theme, status| crate::style::button::confirm(theme, status, is_buy));

            // Phase O0: SELL は disabled
            let sell_btn = button(text("売り").size(13))
                .style(|theme, status| crate::style::button::cancel(theme, status, false));

            row![buy_btn, sell_btn].spacing(4)
        };

        let qty_input = text_input("数量（株）", &self.quantity).on_input(Message::QuantityChanged);

        let price_kind_row = {
            let is_market = self.price_kind == PriceKind::Market;
            let is_limit = self.price_kind == PriceKind::Limit;
            let is_stop_limit = self.price_kind == PriceKind::StopLimit;

            let market_btn = button(text("成行").size(13))
                .on_press(Message::PriceKindChanged(PriceKind::Market))
                .style(move |theme, status| {
                    crate::style::button::confirm(theme, status, is_market)
                });

            let limit_btn = button(text("指値").size(13))
                .on_press(Message::PriceKindChanged(PriceKind::Limit))
                .style(move |theme, status| crate::style::button::confirm(theme, status, is_limit));

            let stop_limit_btn = button(text("逆指値指値").size(13))
                .on_press(Message::PriceKindChanged(PriceKind::StopLimit))
                .style(move |theme, status| {
                    crate::style::button::confirm(theme, status, is_stop_limit)
                });

            row![market_btn, limit_btn, stop_limit_btn].spacing(4)
        };

        let cash_margin_list = pick_list(
            CashMarginKind::ALL,
            Some(self.cash_margin),
            Message::CashMarginChanged,
        );

        let mut form = column![
            text(instrument_label).size(14),
            side_row,
            qty_input,
            price_kind_row,
            cash_margin_list,
        ]
        .spacing(8);

        if matches!(self.price_kind, PriceKind::Limit | PriceKind::StopLimit) {
            let price_input = text_input("価格（円）", &self.price).on_input(Message::PriceChanged);
            form = form.push(price_input);
        }

        if matches!(self.price_kind, PriceKind::StopLimit) {
            let trigger_input = text_input("トリガー価格（円）", &self.trigger_price)
                .on_input(Message::TriggerPriceChanged);
            form = form.push(trigger_input);
        }

        let submit_enabled =
            !self.submitting && self.quantity_valid() && self.instrument_id.is_some();
        let submit_btn = if submit_enabled {
            button(text("注文").size(13))
                .on_press(Message::SubmitClicked)
                .width(Length::Fill)
        } else {
            button(text("注文").size(13)).width(Length::Fill)
        };

        form = form.push(submit_btn);

        if let Some(err) = &self.last_error {
            form = form.push(text(err.as_str()).size(12).style(|theme: &iced::Theme| {
                iced::widget::text::Style {
                    color: Some(theme.palette().danger),
                }
            }));
        }

        container(form.align_x(Alignment::Start))
            .padding(8)
            .width(Length::Fill)
            .into()
    }

    pub fn quantity_valid(&self) -> bool {
        self.quantity.parse::<u64>().map(|v| v > 0).unwrap_or(false)
    }

    pub fn set_instrument(&mut self, id: String, display: String) {
        self.instrument_id = Some(id);
        self.display_label = Some(display);
        // Phase O0: Tachibana 専用。将来の多取引所対応時は呼び出し元から venue を渡す。
        self.venue = Some("tachibana".to_string());
    }

    /// Called when the engine connection drops (e.g. restart).
    /// Resets the in-flight submission state so the submit button becomes
    /// re-enabled once the connection is restored.
    pub fn on_engine_disconnected(&mut self) {
        self.submitting = false;
        self.pending_request_id = None;
        self.last_error = Some("接続が切断されました".to_string());
    }

    /// Called when the engine connection is restored.
    /// Clears the disconnection error so the panel returns to normal state.
    pub fn on_engine_reconnected(&mut self) {
        self.last_error = None;
    }

    /// Called when an OrderAccepted event arrives for our pending_request_id.
    pub fn on_accepted(&mut self) {
        self.submitting = false;
        self.pending_request_id = None;
        self.last_error = None;
    }

    /// Called when an OrderRejected event arrives for our pending_request_id.
    pub fn on_rejected(&mut self, reason: String) {
        self.submitting = false;
        self.pending_request_id = None;
        self.last_error = Some(reason);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stop_limit_includes_trigger_price() {
        let mut panel = OrderEntryPanel {
            quantity: "100".into(),
            price_kind: PriceKind::StopLimit,
            price: "1500".into(),
            trigger_price: "1480".into(),
            instrument_id: Some("1234".into()),
            venue: Some("tachibana".into()),
            ..Default::default()
        };
        let action = panel.update(Message::ConfirmSubmit);
        assert!(matches!(
            action,
            Some(Action::SubmitOrder {
                order_type: engine_client::dto::OrderType::StopLimit,
                ..
            })
        ));
    }

    #[test]
    fn quantity_zero_is_invalid() {
        let panel = OrderEntryPanel {
            quantity: "0".into(),
            ..Default::default()
        };
        assert!(!panel.quantity_valid());
    }

    #[test]
    fn quantity_positive_is_valid() {
        let panel = OrderEntryPanel {
            quantity: "100".into(),
            ..Default::default()
        };
        assert!(panel.quantity_valid());
    }

    #[test]
    fn quantity_empty_is_invalid() {
        let panel = OrderEntryPanel {
            quantity: "".into(),
            ..Default::default()
        };
        assert!(!panel.quantity_valid());
    }

    #[test]
    fn side_changed_message_updates_side() {
        let mut panel = OrderEntryPanel::default();
        panel.update(Message::SideChanged(OrderSide::Sell));
        assert_eq!(panel.side, OrderSide::Sell);
    }

    #[test]
    fn confirm_submit_with_valid_quantity_returns_action() {
        let mut panel = OrderEntryPanel {
            quantity: "10".into(),
            instrument_id: Some("1234".into()),
            venue: Some("tachibana".into()),
            ..Default::default()
        };
        let action = panel.update(Message::ConfirmSubmit);
        assert!(action.is_some());
        assert!(panel.submitting);
    }

    #[test]
    fn confirm_submit_without_instrument_id_returns_none() {
        let mut panel = OrderEntryPanel {
            quantity: "10".into(),
            instrument_id: None,
            ..Default::default()
        };
        let action = panel.update(Message::ConfirmSubmit);
        assert!(action.is_none());
        assert!(!panel.submitting);
    }

    #[test]
    fn confirm_submit_with_invalid_quantity_returns_none() {
        let mut panel = OrderEntryPanel {
            quantity: "0".into(),
            ..Default::default()
        };
        let action = panel.update(Message::ConfirmSubmit);
        assert!(action.is_none());
        assert!(!panel.submitting);
    }

    #[test]
    fn on_rejected_sets_error_and_clears_submitting() {
        let mut panel = OrderEntryPanel {
            submitting: true,
            pending_request_id: Some("req-1".into()),
            ..Default::default()
        };
        panel.on_rejected("insufficient funds".into());
        assert!(!panel.submitting);
        assert_eq!(panel.last_error, Some("insufficient funds".into()));
    }

    #[test]
    fn on_accepted_clears_submitting_and_error() {
        let mut panel = OrderEntryPanel {
            submitting: true,
            pending_request_id: Some("req-1".into()),
            last_error: Some("old error".into()),
            ..Default::default()
        };
        panel.on_accepted();
        assert!(!panel.submitting);
        assert!(panel.last_error.is_none());
    }

    #[test]
    fn cash_margin_changed_updates_field() {
        let mut panel = OrderEntryPanel::default();
        assert_eq!(panel.cash_margin, CashMarginKind::Cash);

        panel.update(Message::CashMarginChanged(CashMarginKind::MarginCreditNew));
        assert_eq!(panel.cash_margin, CashMarginKind::MarginCreditNew);

        panel.update(Message::CashMarginChanged(
            CashMarginKind::MarginGeneralRepay,
        ));
        assert_eq!(panel.cash_margin, CashMarginKind::MarginGeneralRepay);
    }

    #[test]
    fn submit_clicked_returns_request_confirm_not_submit_order() {
        // SubmitClicked must open a confirmation dialog (RequestConfirm), NOT
        // directly submit the order (SubmitOrder). Only ConfirmSubmit should
        // trigger SubmitOrder.
        let mut panel = OrderEntryPanel {
            quantity: "10".into(),
            instrument_id: Some("7203.TSE".into()),
            ..Default::default()
        };
        let action = panel.update(Message::SubmitClicked);
        assert!(
            matches!(action, Some(Action::RequestConfirm { .. })),
            "SubmitClicked should return RequestConfirm, got {action:?}"
        );
        // submitting must NOT be set — the order hasn't been confirmed yet.
        assert!(!panel.submitting);
    }

    #[test]
    fn order_side_sell_produces_sell_in_action() {
        let mut panel = OrderEntryPanel {
            quantity: "10".into(),
            side: OrderSide::Sell,
            instrument_id: Some("7203.TSE".into()),
            venue: Some("tachibana".into()),
            ..Default::default()
        };
        let action = panel.update(Message::ConfirmSubmit);
        assert!(
            matches!(
                action,
                Some(Action::SubmitOrder {
                    order_side: engine_client::dto::OrderSide::Sell,
                    ..
                })
            ),
            "Sell side should produce OrderSide::Sell, got {action:?}"
        );
    }

    // ── C-1: 非 TachibanaStock 銘柄では set_instrument が呼ばれないことを確認 ──
    // pane.rs の exchange チェックにより非 Tachibana 銘柄では set_instrument が
    // 呼ばれない。呼ばれなかった場合、instrument_id が None のままで
    // submit ボタンが無効化されることをここで検証する。
    #[test]
    fn without_set_instrument_submit_button_is_disabled() {
        let panel = OrderEntryPanel {
            quantity: "100".into(),
            // instrument_id は未設定 — 非 Tachibana 銘柄が選択されたとき
            // pane.rs の早期リターンにより set_instrument は呼ばれない
            ..Default::default()
        };
        // submit_enabled = !submitting && quantity_valid && instrument_id.is_some()
        let submit_enabled =
            !panel.submitting && panel.quantity_valid() && panel.instrument_id.is_some();
        assert!(
            !submit_enabled,
            "instrument_id が None のときは submit ボタンを有効にしてはならない"
        );
    }

    // ── M-1: set_instrument が venue を自動セットすることを確認 ──
    #[test]
    fn set_instrument_sets_venue_to_tachibana() {
        let mut panel = OrderEntryPanel::default();
        panel.set_instrument("7203.TSE".into(), "トヨタ".into());
        assert_eq!(panel.venue, Some("tachibana".to_string()));
    }

    // ── M-1: build_submit_action が venue を正しく使うことを確認 ──
    #[test]
    fn submit_order_uses_venue_from_field() {
        let mut panel = OrderEntryPanel {
            quantity: "10".into(),
            instrument_id: Some("7203.TSE".into()),
            venue: Some("tachibana".into()),
            ..Default::default()
        };
        let action = panel.update(Message::ConfirmSubmit);
        assert!(
            matches!(
                action,
                Some(Action::SubmitOrder {
                    ref venue,
                    ..
                }) if venue == "tachibana"
            ),
            "venue フィールドが SubmitOrder に伝播されなかった: {action:?}"
        );
    }

    // ── H-B: instrument_id = Some, venue = None のとき ConfirmSubmit は None を返す ──
    #[test]
    fn confirm_submit_with_instrument_id_but_no_venue_returns_none() {
        let mut panel = OrderEntryPanel {
            quantity: "10".into(),
            instrument_id: Some("1234".into()),
            venue: None,
            ..Default::default()
        };
        let action = panel.update(Message::ConfirmSubmit);
        assert!(
            action.is_none(),
            "venue が None のときは SubmitOrder を返してはならない"
        );
        assert!(
            !panel.submitting,
            "venue が None のときは submitting を true にしてはならない"
        );
    }

    // ── M-2: on_engine_disconnected が submitting をリセットすることを確認 ──
    #[test]
    fn on_engine_disconnected_resets_submitting_state() {
        let mut panel = OrderEntryPanel {
            submitting: true,
            pending_request_id: Some("req-abc".into()),
            last_error: None,
            ..Default::default()
        };
        panel.on_engine_disconnected();
        assert!(
            !panel.submitting,
            "submitting は false にリセットされるべき"
        );
        assert!(
            panel.pending_request_id.is_none(),
            "pending_request_id は None にクリアされるべき"
        );
        assert!(
            panel.last_error.is_some(),
            "last_error に切断メッセージがセットされるべき"
        );
    }

    // ── M-1: on_engine_reconnected が last_error をクリアすることを確認 ──
    #[test]
    fn on_engine_reconnected_clears_last_error() {
        let mut panel = OrderEntryPanel {
            last_error: Some("接続が切断されました".to_string()),
            ..Default::default()
        };
        panel.on_engine_reconnected();
        assert!(
            panel.last_error.is_none(),
            "再接続後は last_error が None にクリアされるべき"
        );
    }
}
