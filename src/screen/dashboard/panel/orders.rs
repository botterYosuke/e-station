//! Phase U1 — Orders panel.
//!
//! Displays today's order list. Cancel buttons send `CancelOrder` IPC commands.
//! Modify buttons open a stub modal (Tu1.2 — IPC wiring deferred until
//! `OrderModified` EngineEvent is added to dto.rs).

use engine_client::dto::OrderRecordWire;
use iced::{
    Element,
    widget::{button, center, column, container, row, scrollable, text},
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
    /// User clicked the Refresh button.
    RefreshClicked,
    /// User clicked Modify on an order.
    ModifyClicked { client_order_id: String },
    /// User clicked Cancel on an order.
    CancelClicked { client_order_id: String },
}

// ── Actions ───────────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub enum Action {
    /// Request a fresh order list via `GetOrderList` IPC.
    RequestOrderList,
    /// Cancel the specified order via `CancelOrder` IPC.
    CancelOrder {
        client_order_id: String,
        venue_order_id: String,
    },
}

// ── Update ────────────────────────────────────────────────────────────────────

pub fn update(panel: &mut OrdersPanel, msg: Message) -> Option<Action> {
    match msg {
        Message::RefreshClicked => Some(Action::RequestOrderList),
        Message::CancelClicked { client_order_id } => {
            let venue_order_id = panel
                .orders
                .iter()
                .find(|o| o.client_order_id.as_deref() == Some(&client_order_id))
                .map(|o| o.venue_order_id.clone());
            // Order not found in local list — do not send a CancelOrder IPC
            // with an empty venue_order_id, which would cause the WAL to
            // skip idempotency recovery (architecture.md §4.1).
            venue_order_id.map(|vid| Action::CancelOrder {
                client_order_id,
                venue_order_id: vid,
            })
        }
        // Tu1.2: ModifyOrder IPC deferred — OrderModified EngineEvent not yet in dto.rs
        Message::ModifyClicked { .. } => None,
    }
}

// ── View ──────────────────────────────────────────────────────────────────────

/// Render the orders panel.
pub fn view(panel: &OrdersPanel) -> Element<'_, Message> {
    let refresh_btn = button(text("更新").size(12))
        .on_press(Message::RefreshClicked)
        .padding([2, 8]);

    let header = row![refresh_btn].spacing(4).padding([4, 8]);

    if panel.is_empty() {
        return column![header, center(text("注文なし").size(14))].into();
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
        row![
            text(label).size(12),
            button(text("訂正").size(11)).on_press(Message::ModifyClicked {
                client_order_id: cid_mod
            }),
            button(text("取消").size(11)).on_press(Message::CancelClicked {
                client_order_id: cid
            }),
        ]
        .spacing(8)
        .into()
    });

    let content = column(rows).spacing(4);

    column![header, container(scrollable(content)).padding(8)].into()
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

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn order_count_starts_at_zero() {
        let panel = OrdersPanel::new();
        assert_eq!(panel.order_count(), 0);
        assert!(panel.is_empty());
    }

    #[test]
    fn refresh_clicked_returns_request_order_list() {
        let mut panel = OrdersPanel::new();
        let action = update(&mut panel, Message::RefreshClicked);
        assert!(matches!(action, Some(Action::RequestOrderList)));
    }

    #[test]
    fn modify_clicked_returns_none() {
        let mut panel = OrdersPanel::new();
        let action = update(
            &mut panel,
            Message::ModifyClicked {
                client_order_id: "ord-001".to_string(),
            },
        );
        assert!(action.is_none());
    }

    #[test]
    fn cancel_clicked_unknown_order_returns_none() {
        // An order not present in the local list must not produce a CancelOrder action
        // — an empty venue_order_id would silently skip WAL idempotency recovery.
        let mut panel = OrdersPanel::new();
        let action = update(
            &mut panel,
            Message::CancelClicked {
                client_order_id: "ord-unknown".to_string(),
            },
        );
        assert!(
            action.is_none(),
            "expected None for unknown order, got {action:?}"
        );
    }

    #[test]
    fn set_orders_updates_order_count() {
        use engine_client::dto::{OrderRecordWire, OrderSide, OrderType, TimeInForce};
        let mut panel = OrdersPanel::new();
        assert_eq!(panel.order_count(), 0);

        let records = vec![
            OrderRecordWire {
                client_order_id: Some("c-1".to_string()),
                venue_order_id: "v-1".to_string(),
                instrument_id: "7203.TSE".to_string(),
                order_side: OrderSide::Buy,
                order_type: OrderType::Limit,
                quantity: "100".to_string(),
                filled_qty: "0".to_string(),
                leaves_qty: "100".to_string(),
                price: Some("1500".to_string()),
                trigger_price: None,
                time_in_force: TimeInForce::Day,
                expire_time_ns: None,
                status: "OPEN".to_string(),
                ts_event_ms: 0,
            },
            OrderRecordWire {
                client_order_id: Some("c-2".to_string()),
                venue_order_id: "v-2".to_string(),
                instrument_id: "6758.TSE".to_string(),
                order_side: OrderSide::Sell,
                order_type: OrderType::Market,
                quantity: "50".to_string(),
                filled_qty: "0".to_string(),
                leaves_qty: "50".to_string(),
                price: None,
                trigger_price: None,
                time_in_force: TimeInForce::Day,
                expire_time_ns: None,
                status: "OPEN".to_string(),
                ts_event_ms: 0,
            },
        ];

        panel.set_orders(records);
        assert_eq!(panel.order_count(), 2);
    }

    /// T2.4: After `OrderListUpdated` → `set_orders`, cancelling a known order
    /// resolves venue_order_id and produces a `CancelOrder` action.
    #[test]
    fn cancel_clicked_known_order_returns_cancel_action_with_venue_id() {
        use engine_client::dto::{OrderRecordWire, OrderSide, OrderType, TimeInForce};
        let mut panel = OrdersPanel::new();

        let record = OrderRecordWire {
            client_order_id: Some("c-42".to_string()),
            venue_order_id: "v-99".to_string(),
            instrument_id: "7203.TSE".to_string(),
            order_side: OrderSide::Buy,
            order_type: OrderType::Market,
            quantity: "100".to_string(),
            filled_qty: "0".to_string(),
            leaves_qty: "100".to_string(),
            price: None,
            trigger_price: None,
            time_in_force: TimeInForce::Day,
            expire_time_ns: None,
            status: "OPEN".to_string(),
            ts_event_ms: 0,
        };
        panel.set_orders(vec![record]);

        let action = update(
            &mut panel,
            Message::CancelClicked {
                client_order_id: "c-42".to_string(),
            },
        );
        assert!(
            matches!(
                &action,
                Some(Action::CancelOrder {
                    client_order_id,
                    venue_order_id,
                }) if client_order_id == "c-42" && venue_order_id == "v-99"
            ),
            "expected CancelOrder with correct ids, got {action:?}"
        );
    }

    #[test]
    fn set_orders_replaces_previous_list() {
        use engine_client::dto::{OrderRecordWire, OrderSide, OrderType, TimeInForce};
        let mut panel = OrdersPanel::new();

        let make_record = |cid: &str, vid: &str| OrderRecordWire {
            client_order_id: Some(cid.to_string()),
            venue_order_id: vid.to_string(),
            instrument_id: "7203.TSE".to_string(),
            order_side: OrderSide::Buy,
            order_type: OrderType::Market,
            quantity: "100".to_string(),
            filled_qty: "0".to_string(),
            leaves_qty: "100".to_string(),
            price: None,
            trigger_price: None,
            time_in_force: TimeInForce::Day,
            expire_time_ns: None,
            status: "OPEN".to_string(),
            ts_event_ms: 0,
        };

        panel.set_orders(vec![make_record("c-1", "v-1"), make_record("c-2", "v-2")]);
        assert_eq!(panel.order_count(), 2);

        panel.set_orders(vec![make_record("c-3", "v-3")]);
        assert_eq!(panel.order_count(), 1, "set_orders should replace previous list");

        let action = update(
            &mut panel,
            Message::CancelClicked {
                client_order_id: "c-1".to_string(),
            },
        );
        assert!(
            action.is_none(),
            "stale order c-1 should not be cancellable after replacement"
        );
    }
}
