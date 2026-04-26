//! Phase O1 — Orders panel (minimal stub).
//!
//! Displays today's order list. Modify / cancel buttons send HTTP requests
//! to the local order API endpoints (`POST /api/order/modify`,
//! `POST /api/order/cancel`).
//!
//! Full implementation: T1.4 exit condition — limit order → modify → cancel
//! completes end-to-end in the demo environment.
//!
//! Current state: scaffold only — renders an empty placeholder until
//! `OrderListUpdated` events populate the order list.
#![allow(dead_code)] // scaffold — not yet wired into the dashboard pane router

use engine_client::dto::OrderRecordWire;
use iced::{
    Element,
    widget::{center, column, container, scrollable, text},
};

// ── State ─────────────────────────────────────────────────────────────────────

/// Orders panel state — holds the most recent order list from the engine.
#[derive(Debug, Default)]
pub struct OrdersPanel {
    orders: Vec<OrderRecordWire>,
}

impl OrdersPanel {
    pub fn new() -> Self {
        Self::default()
    }

    /// Update the order list from an `OrderListUpdated` IPC event.
    pub fn set_orders(&mut self, orders: Vec<OrderRecordWire>) {
        self.orders = orders;
    }

    /// Returns the number of orders currently held.
    pub fn order_count(&self) -> usize {
        self.orders.len()
    }

    /// Returns `true` when no orders are available to display.
    pub fn is_empty(&self) -> bool {
        self.orders.is_empty()
    }
}

// ── Messages ──────────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub enum Message {
    /// User clicked Modify on an order.
    ModifyClicked { client_order_id: String },
    /// User clicked Cancel on an order.
    CancelClicked { client_order_id: String },
}

// ── View ──────────────────────────────────────────────────────────────────────

/// Render the orders panel.
pub fn view(panel: &OrdersPanel) -> Element<'_, Message> {
    if panel.is_empty() {
        return center(text("注文なし").size(14)).into();
    }

    let rows = panel.orders.iter().map(|order| {
        let cid = order
            .client_order_id
            .clone()
            .unwrap_or_else(|| order.venue_order_id.clone());
        let label = format!(
            "{} {} {} {} @ {} [{}]",
            order.venue_order_id,
            order.instrument_id,
            order.order_side.as_str(),
            order.quantity,
            order.price.as_deref().unwrap_or("MKT"),
            order.status,
        );
        let cid_mod = cid.clone();
        let cid_can = cid.clone();
        iced::widget::row![
            text(label).size(12),
            iced::widget::button(text("訂正").size(11))
                .on_press(Message::ModifyClicked { client_order_id: cid_mod }),
            iced::widget::button(text("取消").size(11))
                .on_press(Message::CancelClicked { client_order_id: cid_can }),
        ]
        .spacing(8)
        .into()
    });

    let content = column(rows).spacing(4);

    container(scrollable(content))
        .padding(8)
        .into()
}

// ── OrderSide display helper ──────────────────────────────────────────────────

trait OrderSideStr {
    fn as_str(&self) -> &'static str;
}

impl OrderSideStr for engine_client::dto::OrderSide {
    fn as_str(&self) -> &'static str {
        match self {
            engine_client::dto::OrderSide::Buy => "BUY",
            engine_client::dto::OrderSide::Sell => "SELL",
        }
    }
}
