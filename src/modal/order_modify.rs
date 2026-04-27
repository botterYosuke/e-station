//! Tu1.2 — Order Modify modal stub.
//!
//! IPC wiring is deferred until `OrderModified` EngineEvent is added to
//! `engine-client/src/dto.rs`.  Until then this module only provides the
//! data structures and a minimal view so the panel can compile without a
//! hard dependency on the missing event.
#![allow(dead_code)]

use iced::{
    Element,
    widget::{button, column, row, text, text_input},
};

// ── State ─────────────────────────────────────────────────────────────────────

#[allow(dead_code)]
pub struct OrderModifyModal {
    pub client_order_id: String,
    pub venue_order_id: String,
    pub new_price: String,
    pub submitting: bool,
}

impl OrderModifyModal {
    pub fn new(client_order_id: impl Into<String>, venue_order_id: impl Into<String>) -> Self {
        Self {
            client_order_id: client_order_id.into(),
            venue_order_id: venue_order_id.into(),
            new_price: String::new(),
            submitting: false,
        }
    }
}

// ── Messages ──────────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub enum Message {
    PriceChanged(String),
    Submit,
    Cancel,
}

// ── Actions ───────────────────────────────────────────────────────────────────

#[allow(dead_code)]
pub enum Action {
    Submit { new_price: String },
    Cancel,
}

// ── Update ────────────────────────────────────────────────────────────────────

pub fn update(modal: &mut OrderModifyModal, msg: Message) -> Option<Action> {
    match msg {
        Message::PriceChanged(v) => {
            modal.new_price = v;
            None
        }
        Message::Submit => Some(Action::Submit {
            new_price: modal.new_price.clone(),
        }),
        Message::Cancel => Some(Action::Cancel),
    }
}

// ── View ──────────────────────────────────────────────────────────────────────

pub fn view(modal: &OrderModifyModal) -> Element<'_, Message> {
    let price_input = text_input("新しい価格", &modal.new_price)
        .on_input(Message::PriceChanged)
        .padding(6);

    let submit_btn = button(text("訂正送信").size(13))
        .on_press_maybe(if modal.submitting {
            None
        } else {
            Some(Message::Submit)
        })
        .padding([4, 12]);

    let cancel_btn = button(text("キャンセル").size(13))
        .on_press(Message::Cancel)
        .padding([4, 12]);

    column![
        text(format!("注文訂正: {}", modal.client_order_id)).size(14),
        price_input,
        row![submit_btn, cancel_btn].spacing(8),
    ]
    .spacing(8)
    .padding(16)
    .into()
}
