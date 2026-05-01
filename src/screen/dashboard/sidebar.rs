use super::tickers_table::{self, TickersTable};
use crate::{
    TooltipPosition,
    layout::SavedState,
    style::{Icon, icon_text},
    widget::button_with_tooltip,
};
use data::sidebar;

use iced::{
    Alignment, Element, Subscription, Task,
    widget::responsive,
    widget::{column, row, space},
};
use rustc_hash::FxHashMap;

#[derive(Debug, Clone)]
pub enum Message {
    ToggleSidebarMenu(Option<sidebar::Menu>),
    SetSidebarPosition(sidebar::Position),
    TickersTable(super::tickers_table::Message),
    OrderPanelRequested(data::layout::pane::ContentKind),
}

pub struct Sidebar {
    pub state: data::Sidebar,
    pub tickers_table: TickersTable,
}

pub enum Action {
    TickerSelected(
        exchange::TickerInfo,
        Option<data::layout::pane::ContentKind>,
    ),
    ErrorOccurred(data::InternalError),
    /// Forwarded from `tickers_table::Action::RequestTachibanaLogin`.
    /// Flowsurface drives the actual `RequestVenueLogin` IPC frame so
    /// the duplicate-press suppression can consult `tachibana_state`
    /// (which lives at the top level). T35-U1 / T35-U3.
    RequestTachibanaLogin(crate::venue_state::Trigger),
    /// User selected an order panel from the sidebar Order menu.
    /// Flowsurface splits the focused pane and opens the selected content.
    OpenOrderPanel(data::layout::pane::ContentKind),
}

impl Sidebar {
    pub fn new(
        state: &SavedState,
        handles: exchange::adapter::AdapterHandles,
    ) -> (Self, Task<Message>) {
        let (tickers_table, initial_fetch) =
            if let Some(settings) = state.sidebar.tickers_table.as_ref() {
                TickersTable::new_with_settings(settings, handles.clone())
            } else {
                TickersTable::new(handles)
            };

        (
            Self {
                state: state.sidebar.clone(),
                tickers_table,
            },
            initial_fetch.map(Message::TickersTable),
        )
    }

    pub fn update(&mut self, message: Message) -> (Task<Message>, Option<Action>) {
        match message {
            Message::ToggleSidebarMenu(menu) => {
                self.set_menu(menu.filter(|&m| !self.is_menu_active(m)));
            }
            Message::SetSidebarPosition(position) => {
                self.state.position = position;
            }
            Message::TickersTable(msg) => {
                let action = self.tickers_table.update(msg);

                match action {
                    Some(tickers_table::Action::TickerSelected(ticker_info, content)) => {
                        return (
                            Task::none(),
                            Some(Action::TickerSelected(ticker_info, content)),
                        );
                    }
                    Some(tickers_table::Action::Fetch(task)) => {
                        return (task.map(Message::TickersTable), None);
                    }
                    Some(tickers_table::Action::ErrorOccurred(error)) => {
                        return (Task::none(), Some(Action::ErrorOccurred(error)));
                    }
                    Some(tickers_table::Action::FocusWidget(id)) => {
                        return (iced::widget::operation::focus(id), None);
                    }
                    Some(tickers_table::Action::RequestTachibanaLogin(trigger)) => {
                        return (Task::none(), Some(Action::RequestTachibanaLogin(trigger)));
                    }
                    None => {}
                }
            }
            Message::OrderPanelRequested(kind) => {
                // Close the order menu after selection
                self.set_menu(None);
                return (Task::none(), Some(Action::OpenOrderPanel(kind)));
            }
        }

        (Task::none(), None)
    }

    pub fn view(&self, audio_volume: Option<f32>) -> Element<'_, Message> {
        let state = &self.state;

        let tooltip_position = if state.position == sidebar::Position::Left {
            TooltipPosition::Right
        } else {
            TooltipPosition::Left
        };

        let is_table_open = self.tickers_table.is_shown;

        let nav_buttons = self.nav_buttons(is_table_open, audio_volume, tooltip_position);

        let tickers_table = if is_table_open {
            column![responsive(move |size| self
                .tickers_table
                .view(size)
                .map(Message::TickersTable))]
            .width(200)
        } else {
            column![]
        };

        let order_menu = if self.is_menu_active(sidebar::Menu::Order) {
            self.order_menu_view().width(140)
        } else {
            column![].width(0)
        };

        let is_order_menu_open = self.is_menu_active(sidebar::Menu::Order);

        match state.position {
            sidebar::Position::Left => row![nav_buttons, tickers_table, order_menu],
            sidebar::Position::Right => row![order_menu, tickers_table, nav_buttons],
        }
        .spacing(if is_table_open || is_order_menu_open {
            8
        } else {
            4
        })
        .into()
    }

    pub fn subscription(&self) -> Subscription<Message> {
        self.tickers_table.subscription().map(Message::TickersTable)
    }

    /// Propagate new handles after an engine reconnect; returns a re-fetch task.
    pub fn update_handles(&mut self, handles: exchange::adapter::AdapterHandles) -> Task<Message> {
        self.tickers_table
            .update_handles(handles)
            .map(Message::TickersTable)
    }

    /// B5: Wire the Tachibana display-metadata handle so `filtered_rows` can
    /// do Japanese-name prefix search. Delegates to `TickersTable`.
    pub fn set_tachibana_meta_handle(
        &mut self,
        handle: Option<std::sync::Arc<tokio::sync::Mutex<engine_client::TickerMetaMap>>>,
    ) {
        self.tickers_table.set_tachibana_meta_handle(handle);
    }

    fn nav_buttons(
        &self,
        is_table_open: bool,
        audio_volume: Option<f32>,
        tooltip_position: TooltipPosition,
    ) -> iced::widget::Column<'_, Message> {
        let settings_modal_button = {
            let is_active = self.is_menu_active(sidebar::Menu::Settings)
                || self.is_menu_active(sidebar::Menu::ThemeEditor)
                || self.is_menu_active(sidebar::Menu::Network);

            button_with_tooltip(
                icon_text(Icon::Cog, 14)
                    .width(24)
                    .align_x(Alignment::Center),
                Message::ToggleSidebarMenu(Some(sidebar::Menu::Settings)),
                None,
                tooltip_position,
                move |theme, status| crate::style::button::transparent(theme, status, is_active),
            )
        };

        let layout_modal_button = {
            let is_active = self.is_menu_active(sidebar::Menu::Layout);

            button_with_tooltip(
                icon_text(Icon::Layout, 14)
                    .width(24)
                    .align_x(Alignment::Center),
                Message::ToggleSidebarMenu(Some(sidebar::Menu::Layout)),
                None,
                tooltip_position,
                move |theme, status| crate::style::button::transparent(theme, status, is_active),
            )
        };

        let ticker_search_button = {
            button_with_tooltip(
                icon_text(Icon::Search, 14)
                    .width(24)
                    .align_x(Alignment::Center),
                Message::TickersTable(super::tickers_table::Message::ToggleTable),
                None,
                tooltip_position,
                move |theme, status| {
                    crate::style::button::transparent(theme, status, is_table_open)
                },
            )
        };

        let audio_btn = {
            let is_active = self.is_menu_active(sidebar::Menu::Audio);

            let icon = match audio_volume.unwrap_or(0.0) {
                v if v >= 40.0 => Icon::SpeakerHigh,
                v if v > 0.0 => Icon::SpeakerLow,
                _ => Icon::SpeakerOff,
            };

            button_with_tooltip(
                icon_text(icon, 14).width(24).align_x(Alignment::Center),
                Message::ToggleSidebarMenu(Some(sidebar::Menu::Audio)),
                None,
                tooltip_position,
                move |theme, status| crate::style::button::transparent(theme, status, is_active),
            )
        };

        let order_btn = {
            let is_active = self.is_menu_active(sidebar::Menu::Order);

            button_with_tooltip(
                icon_text(Icon::Edit, 14)
                    .width(24)
                    .align_x(Alignment::Center),
                Message::ToggleSidebarMenu(Some(sidebar::Menu::Order)),
                Some("Order"),
                tooltip_position,
                move |theme, status| crate::style::button::transparent(theme, status, is_active),
            )
        };

        column![
            ticker_search_button,
            layout_modal_button,
            audio_btn,
            order_btn,
            space::vertical(),
            settings_modal_button,
        ]
        .width(32)
        .spacing(8)
    }

    fn order_menu_view(&self) -> iced::widget::Column<'_, Message> {
        use data::layout::pane::ContentKind;

        let entry_btn = iced::widget::button(iced::widget::text("注文入力").size(13))
            .on_press(Message::OrderPanelRequested(ContentKind::OrderEntry))
            .style(|theme, status| crate::style::button::transparent(theme, status, false));

        let list_btn = iced::widget::button(iced::widget::text("注文一覧").size(13))
            .on_press(Message::OrderPanelRequested(ContentKind::OrderList))
            .style(|theme, status| crate::style::button::transparent(theme, status, false));

        let power_btn = iced::widget::button(iced::widget::text("買余力").size(13))
            .on_press(Message::OrderPanelRequested(ContentKind::BuyingPower))
            .style(|theme, status| crate::style::button::transparent(theme, status, false));

        let positions_btn = iced::widget::button(iced::widget::text("保有銘柄").size(13))
            .on_press(Message::OrderPanelRequested(ContentKind::Positions))
            .style(|theme, status| crate::style::button::transparent(theme, status, false));

        column![entry_btn, list_btn, power_btn, positions_btn].spacing(4)
    }

    pub fn hide_tickers_table(&mut self) -> bool {
        let table = &mut self.tickers_table;

        if table.expand_ticker_card.is_some() {
            table.expand_ticker_card = None;
            return true;
        } else if table.is_shown {
            table.is_shown = false;
            return true;
        }

        false
    }

    pub fn is_menu_active(&self, menu: sidebar::Menu) -> bool {
        self.state.active_menu == Some(menu)
    }

    pub fn active_menu(&self) -> Option<sidebar::Menu> {
        self.state.active_menu
    }

    pub fn position(&self) -> sidebar::Position {
        self.state.position
    }

    pub fn set_menu(&mut self, menu: Option<sidebar::Menu>) {
        self.state.active_menu = menu;
    }

    pub fn sync_tickers_table_settings(&mut self) {
        let settings = &self.tickers_table.settings();
        self.state.tickers_table = Some(settings.clone());
    }

    pub fn tickers_info(&self) -> &FxHashMap<exchange::Ticker, Option<exchange::TickerInfo>> {
        &self.tickers_table.tickers_info
    }
}
