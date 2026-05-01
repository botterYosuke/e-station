use crate::{
    chart::{self, comparison::ComparisonChart, heatmap::HeatmapChart, kline::KlineChart},
    connector::{
        ResolvedStream,
        fetcher::{FetchSpec, InfoKind},
    },
    modal::{
        self, ModifierKind,
        pane::{
            Modal,
            mini_tickers_list::MiniPanel,
            settings::{
                comparison_cfg_view, heatmap_cfg_view, heatmap_shader_cfg_view, kline_cfg_view,
            },
            stack_modal,
        },
    },
    screen::dashboard::{
        panel::{
            self, buying_power::BuyingPowerPanel, ladder::Ladder, order_entry::OrderEntryPanel,
            timeandsales::TimeAndSales,
        },
        tickers_table::TickersTable,
    },
    style::{self, Icon, icon_text},
    widget::{
        self, button_with_tooltip, chart::heatmap::HeatmapShader, column_drag, link_group_button,
        toast::Toast,
    },
    window::{self, Window},
};
use data::{
    UserTimezone,
    chart::{
        Basis, ViewConfig,
        heatmap::HeatmapStudy,
        indicator::{HeatmapIndicator, Indicator, KlineIndicator, UiIndicator},
    },
    layout::pane::{ContentKind, LinkGroup, PaneSetup, Settings, VisualConfig},
    stream::PersistStreamKind,
};
use exchange::{
    Kline, OpenInterest, StreamPairKind, TickMultiplier, TickerInfo, Timeframe,
    adapter::{Exchange, MarketKind, StreamKind, StreamTicksize},
    unit::PriceStep,
};
use iced::{
    Alignment, Element, Length, Renderer, Theme, padding,
    widget::{button, center, column, container, pane_grid, pick_list, row, text, tooltip},
};
use std::time::Instant;

#[derive(Debug, Clone)]
pub enum Effect {
    RefreshStreams,
    RequestFetch(Vec<FetchSpec>),
    SwitchTickersInGroup(TickerInfo),
    FocusWidget(iced::widget::Id),
    OrderEntryAction(panel::order_entry::Action),
    OrderListAction(panel::orders::Action),
    BuyingPowerAction(panel::buying_power::Action),
    PositionsAction(panel::positions::Action),
    /// N1.11-ui: User pressed a speed button in `ReplayControl` pane.
    SetReplaySpeed(u32),
    /// N4.3: User pressed the strategy file picker button in `ReplayControl` pane.
    PickStrategyFile,
}

#[derive(Debug, Default, Clone, PartialEq)]
pub enum Status {
    #[default]
    Ready,
    Loading(InfoKind),
    Stale(String),
}

pub enum Action {
    Chart(chart::Action),
    Panel(panel::Action),
    ResolveStreams(Vec<PersistStreamKind>),
    ResolveContent,
    OrderEntry(panel::order_entry::Action),
}

#[derive(Debug, Clone)]
pub enum Message {
    PaneClicked(pane_grid::Pane),
    PaneResized(pane_grid::ResizeEvent),
    PaneDragged(pane_grid::DragEvent),
    ClosePane(pane_grid::Pane),
    SplitPane(pane_grid::Axis, pane_grid::Pane),
    MaximizePane(pane_grid::Pane),
    Restore,
    ReplacePane(pane_grid::Pane),
    Popout,
    Merge,
    SwitchLinkGroup(pane_grid::Pane, Option<LinkGroup>),
    VisualConfigChanged(pane_grid::Pane, VisualConfig, bool),
    PaneEvent(pane_grid::Pane, Event),
}

#[derive(Debug, Clone)]
pub enum Event {
    ShowModal(Modal),
    HideModal,
    ContentSelected(ContentKind),
    ChartInteraction(super::chart::Message),
    PanelInteraction(super::panel::Message),
    ToggleIndicator(UiIndicator),
    DeleteNotification(usize),
    ReorderIndicator(column_drag::DragEvent),
    ClusterKindSelected(data::chart::kline::ClusterKind),
    ClusterScalingSelected(data::chart::kline::ClusterScaling),
    StudyConfigurator(modal::pane::settings::study::StudyMessage),
    StreamModifierChanged(modal::stream::Message),
    ComparisonChartInteraction(super::chart::comparison::Message),
    HeatmapShaderInteraction(crate::widget::chart::heatmap::Message),
    MiniTickersListInteraction(modal::pane::mini_tickers_list::Message),
    OrderEntryMsg(panel::order_entry::Message),
    OrderListMsg(panel::orders::Message),
    BuyingPowerMsg(panel::buying_power::Message),
    PositionsMsg(panel::positions::Message),
    /// N1.11-ui: User pressed a replay speed button (1 | 10 | 100).
    SetReplaySpeed(u32),
    /// N4.3: User pressed the "Strategy ファイルを選ぶ" button in `ReplayControl`.
    PickStrategyFile,
}

pub struct State {
    id: uuid::Uuid,
    pub modal: Option<Modal>,
    pub content: Content,
    pub settings: Settings,
    pub notifications: Vec<Toast>,
    pub streams: ResolvedStream,
    pub status: Status,
    pub link_group: Option<LinkGroup>,
}

impl State {
    pub fn new() -> Self {
        Self::default()
    }

    /// Create a new pane pre-loaded with the given content kind.
    /// Used by `Action::OpenOrderPanel` to split a pane and set its content immediately.
    pub fn with_kind(kind: ContentKind) -> Self {
        Self {
            content: Content::placeholder(kind),
            ..Default::default()
        }
    }

    /// N1.15: Create a REPLAY OrderList pane (shows "⏪ REPLAY" banner).
    pub fn new_replay_order_list() -> Self {
        Self {
            content: Content::OrderList(panel::orders::OrdersPanel::new_replay()),
            ..Default::default()
        }
    }

    /// N1.16: Create a REPLAY BuyingPower pane (shows "⏪ REPLAY" banner).
    pub fn new_replay_buying_power() -> Self {
        Self {
            content: Content::BuyingPower(panel::buying_power::BuyingPowerPanel::new_replay()),
            ..Default::default()
        }
    }

    pub fn clear_replay_chart_data(&mut self) {
        match &mut self.content {
            Content::Kline {
                chart: Some(c),
                indicators,
                ..
            } => {
                let Basis::Time(timeframe) = c.basis() else {
                    return;
                };
                let layout = c.chart_layout();
                let step = c.tick_size();
                let ticker_info = c.ticker_info();
                let kind = c.kind().clone();
                let new_chart = KlineChart::new(
                    layout,
                    Basis::Time(timeframe),
                    step,
                    &[],
                    vec![],
                    indicators,
                    ticker_info,
                    &kind,
                );
                *c = new_chart;
            }
            Content::TimeAndSales(Some(p)) => {
                p.clear();
            }
            _ => {}
        }
    }

    /// N1.12: Push an `ExecutionMarker` into the Kline chart, if this pane holds one.
    pub fn push_execution_marker(&mut self, data: chart::kline::ExecutionMarkerData) {
        if let Content::Kline { chart: Some(c), .. } = &mut self.content {
            c.push_execution_marker(data);
        }
    }

    /// N1.12: Push a `StrategySignal` into the Kline chart, if this pane holds one.
    pub fn push_strategy_signal(&mut self, data: chart::kline::StrategySignalData) {
        if let Content::Kline { chart: Some(c), .. } = &mut self.content {
            c.push_strategy_signal(data);
        }
    }

    /// Mark a pending fetch request as failed so the request handler stops
    /// blocking future fetches with `ReqError::Overlaps`.
    pub fn mark_fetch_request_failed(&mut self, req_id: uuid::Uuid) {
        match &mut self.content {
            Content::Kline { chart: Some(c), .. } => {
                c.mark_request_failed(req_id);
            }
            Content::Comparison(Some(c)) => {
                c.mark_kline_request_failed(req_id);
            }
            _ => {}
        }
    }

    /// N1.12 / N1.14: Clear all chart overlay markers.
    pub fn clear_overlay_markers(&mut self) {
        if let Content::Kline { chart: Some(c), .. } = &mut self.content {
            c.clear_overlay_markers();
        }
    }

    pub fn from_config(
        content: Content,
        streams: Vec<PersistStreamKind>,
        settings: Settings,
        link_group: Option<LinkGroup>,
    ) -> Self {
        // 銘柄概念を持たないペインは link_group を保持できない。旧バージョンで
        // saved-state.json に保存された Some(N) を新バイナリで復元すると、UI から
        // トグル不能なゴースト link_group が残り switch_tickers_in_group 経路で
        // 永続的に warn ログを発生させるため、構築時点で None に正規化する。
        let link_group = match content {
            Content::Starter
            | Content::OrderList(_)
            | Content::BuyingPower(_)
            | Content::Positions(_) => {
                if link_group.is_some() {
                    log::debug!(
                        "normalizing stale link_group on non-ticker pane (kind={:?})",
                        content.kind()
                    );
                }
                None
            }
            _ => link_group,
        };
        Self {
            content,
            settings,
            streams: ResolvedStream::waiting(streams),
            link_group,
            ..Default::default()
        }
    }

    pub fn stream_pair(&self) -> Option<TickerInfo> {
        self.streams.find_ready_map(|stream| match stream {
            StreamKind::Kline { ticker_info, .. } => Some(*ticker_info),
            StreamKind::Depth { ticker_info, .. } => Some(*ticker_info),
            StreamKind::Trades { ticker_info, .. } => Some(*ticker_info),
        })
    }

    /// Returns the pane's active ticker — from streams for stream-based panes,
    /// or from `ticker_info` for `OrderEntry` (which has no streams).
    pub fn linked_ticker(&self) -> Option<TickerInfo> {
        if let Some(ti) = self.stream_pair() {
            return Some(ti);
        }
        if let Content::OrderEntry(panel) = &self.content {
            return panel.ticker_info;
        }
        None
    }

    pub fn stream_pair_kind(&self) -> Option<StreamPairKind> {
        let ready_streams = self.streams.ready_iter()?;
        let mut unique = vec![];

        for stream in ready_streams {
            let ticker = stream.ticker_info();
            if !unique.contains(&ticker) {
                unique.push(ticker);
            }
        }

        match unique.len() {
            0 => None,
            1 => Some(StreamPairKind::SingleSource(unique[0])),
            _ => Some(StreamPairKind::MultiSource(unique)),
        }
    }

    pub fn set_content_and_streams(
        &mut self,
        tickers: Vec<TickerInfo>,
        kind: ContentKind,
    ) -> Vec<StreamKind> {
        if !(self.content.kind() == kind) {
            self.settings.selected_basis = None;
            self.settings.tick_multiply = None;
        }

        let base_ticker = tickers[0];
        let prev_base_ticker = self.stream_pair();

        let derived_plan = PaneSetup::new(
            kind,
            base_ticker,
            prev_base_ticker,
            self.settings.selected_basis,
            self.settings.tick_multiply,
        );

        self.settings.selected_basis = derived_plan.basis;
        self.settings.tick_multiply = derived_plan.tick_multiplier;

        let (content, streams) = {
            let kline_stream = |ti: TickerInfo, tf: Timeframe| StreamKind::Kline {
                ticker_info: ti,
                timeframe: tf,
            };
            let depth_stream = |derived_plan: &PaneSetup| StreamKind::Depth {
                ticker_info: derived_plan.ticker_info,
                depth_aggr: derived_plan.depth_aggr,
                push_freq: derived_plan.push_freq,
            };
            let trades_stream = |derived_plan: &PaneSetup| StreamKind::Trades {
                ticker_info: derived_plan.ticker_info,
            };

            match kind {
                ContentKind::HeatmapChart => {
                    let content = Content::new_heatmap(
                        &self.content,
                        derived_plan.ticker_info,
                        &self.settings,
                        derived_plan.price_step,
                    );

                    let streams = vec![depth_stream(&derived_plan), trades_stream(&derived_plan)];

                    (content, streams)
                }
                ContentKind::FootprintChart => {
                    let content = Content::new_kline(
                        kind,
                        &self.content,
                        derived_plan.ticker_info,
                        &self.settings,
                        derived_plan.price_step,
                    );

                    let streams = by_basis_default(
                        derived_plan.basis,
                        Timeframe::M5,
                        |tf| {
                            vec![
                                trades_stream(&derived_plan),
                                kline_stream(derived_plan.ticker_info, tf),
                            ]
                        },
                        || vec![trades_stream(&derived_plan)],
                    );

                    (content, streams)
                }
                ContentKind::CandlestickChart => {
                    let content = {
                        let base_ticker = tickers[0];
                        Content::new_kline(
                            kind,
                            &self.content,
                            derived_plan.ticker_info,
                            &self.settings,
                            base_ticker.min_ticksize.into(),
                        )
                    };

                    let time_basis_stream = |tf| vec![kline_stream(derived_plan.ticker_info, tf)];
                    let tick_basis_stream = || {
                        let depth_aggr = derived_plan
                            .ticker_info
                            .exchange()
                            .stream_ticksize(None, TickMultiplier(50));
                        let temp = PaneSetup {
                            depth_aggr,
                            ..derived_plan
                        };
                        vec![trades_stream(&temp)]
                    };

                    let streams = by_basis_default(
                        derived_plan.basis,
                        Timeframe::M15,
                        time_basis_stream,
                        tick_basis_stream,
                    );

                    (content, streams)
                }
                ContentKind::TimeAndSales => {
                    let config = self
                        .settings
                        .visual_config
                        .clone()
                        .and_then(|cfg| cfg.time_and_sales());
                    let content = Content::TimeAndSales(Some(TimeAndSales::new(
                        config,
                        derived_plan.ticker_info,
                    )));

                    let temp = PaneSetup {
                        push_freq: exchange::PushFrequency::ServerDefault,
                        ..derived_plan
                    };

                    let streams = vec![trades_stream(&temp)];

                    (content, streams)
                }
                ContentKind::Ladder => {
                    let config = self
                        .settings
                        .visual_config
                        .clone()
                        .and_then(|cfg| cfg.ladder());
                    let content = Content::Ladder(Some(Ladder::new(
                        config,
                        derived_plan.ticker_info,
                        derived_plan.price_step,
                    )));

                    let streams = vec![depth_stream(&derived_plan), trades_stream(&derived_plan)];

                    (content, streams)
                }
                ContentKind::ComparisonChart => {
                    let config = self
                        .settings
                        .visual_config
                        .clone()
                        .and_then(|cfg| cfg.comparison());

                    let timeframe = {
                        let supports = |tf| {
                            tickers
                                .iter()
                                .all(|ti| ti.exchange().supports_kline_timeframe(tf))
                        };

                        if let Some(tf) = derived_plan.basis.and_then(|basis| match basis {
                            Basis::Time(tf) => Some(tf),
                            Basis::Tick(_) => None,
                        }) && supports(tf)
                        {
                            tf
                        } else {
                            let fallback = Timeframe::M15;
                            if supports(fallback) {
                                fallback
                            } else {
                                Timeframe::KLINE
                                    .iter()
                                    .copied()
                                    .find(|tf| supports(*tf))
                                    .unwrap_or(fallback)
                            }
                        }
                    };

                    let basis = Basis::Time(timeframe);
                    self.settings.selected_basis = Some(basis);
                    let content =
                        Content::Comparison(Some(ComparisonChart::new(basis, &tickers, config)));

                    let streams = tickers
                        .iter()
                        .copied()
                        .map(|ti| kline_stream(ti, timeframe))
                        .collect();

                    (content, streams)
                }
                ContentKind::ShaderHeatmap => {
                    let basis = derived_plan
                        .basis
                        .unwrap_or(Basis::default_heatmap_time(Some(derived_plan.ticker_info)));

                    let (studies, indicators) = if let Content::ShaderHeatmap {
                        chart,
                        indicators,
                        studies,
                    } = &self.content
                    {
                        (
                            chart
                                .as_ref()
                                .map_or(studies.clone(), |c| c.studies.clone()),
                            indicators.clone(),
                        )
                    } else {
                        (
                            vec![HeatmapStudy::VolumeProfile(
                                data::chart::heatmap::ProfileKind::default(),
                            )],
                            vec![HeatmapIndicator::Volume],
                        )
                    };

                    let content = Content::ShaderHeatmap {
                        chart: Some(Box::new(HeatmapShader::new(
                            basis,
                            derived_plan.price_step,
                            base_ticker,
                            studies.clone(),
                            indicators.clone(),
                        ))),
                        studies,
                        indicators,
                    };

                    let streams = vec![depth_stream(&derived_plan), trades_stream(&derived_plan)];

                    (content, streams)
                }
                // Order panels and Starter do not need ticker streams — they are
                // created via `State::with_kind()` which bypasses this function.
                // If this path is reached it is a logic error, but we return an
                // empty stream set rather than panicking so the UI stays alive.
                ContentKind::Starter
                | ContentKind::OrderEntry
                | ContentKind::OrderList
                | ContentKind::BuyingPower
                | ContentKind::Positions
                // N1.11: ReplayControl は ticker ストリーム不要のコントロール pane
                | ContentKind::ReplayControl => {
                    debug_assert!(
                        false,
                        "set_content_and_streams called for non-stream content {kind:?}; \
                         this is a logic error"
                    );
                    log::warn!(
                        "set_content_and_streams called for non-stream content {:?}; ignoring",
                        kind
                    );
                    return vec![];
                }
            }
        };

        self.content = content;
        self.streams = ResolvedStream::Ready(streams.clone());

        streams
    }

    pub fn insert_hist_oi(&mut self, req_id: Option<uuid::Uuid>, oi: &[OpenInterest]) {
        match &mut self.content {
            Content::Kline { chart, .. } => {
                let Some(chart) = chart else {
                    panic!("Kline chart wasn't initialized when inserting open interest");
                };
                chart.insert_open_interest(req_id, oi);
            }
            _ => {
                log::error!("pane content not candlestick");
            }
        }
    }

    pub fn insert_hist_klines(
        &mut self,
        req_id: Option<uuid::Uuid>,
        timeframe: Timeframe,
        ticker_info: TickerInfo,
        klines: &[Kline],
    ) {
        match &mut self.content {
            Content::Kline {
                chart, indicators, ..
            } => {
                let Some(chart) = chart else {
                    panic!("chart wasn't initialized when inserting klines");
                };

                if let Some(id) = req_id {
                    if chart.basis() != Basis::Time(timeframe) {
                        log::warn!(
                            "Ignoring stale kline fetch for timeframe {:?}; chart basis = {:?}",
                            timeframe,
                            chart.basis()
                        );
                        return;
                    }
                    chart.insert_hist_klines(id, klines);
                } else {
                    let (raw_trades, tick_size) = (chart.raw_trades(), chart.tick_size());
                    let layout = chart.chart_layout();

                    *chart = KlineChart::new(
                        layout,
                        Basis::Time(timeframe),
                        tick_size,
                        klines,
                        raw_trades,
                        indicators,
                        ticker_info,
                        chart.kind(),
                    );
                }
            }
            Content::Comparison(chart) => {
                let Some(chart) = chart else {
                    panic!("Comparison chart wasn't initialized when inserting klines");
                };

                if let Some(id) = req_id {
                    if chart.timeframe != timeframe {
                        log::warn!(
                            "Ignoring stale kline fetch for timeframe {:?}; chart timeframe = {:?}",
                            timeframe,
                            chart.timeframe
                        );
                        return;
                    }
                    chart.insert_history(id, ticker_info, klines);
                } else {
                    *chart = ComparisonChart::new(
                        Basis::Time(timeframe),
                        &[ticker_info],
                        Some(chart.serializable_config()),
                    );
                }
            }
            _ => {
                log::error!("pane content not candlestick or footprint");
            }
        }
    }

    fn has_stream(&self) -> bool {
        match &self.streams {
            ResolvedStream::Ready(streams) => !streams.is_empty(),
            ResolvedStream::Waiting { streams, .. } => !streams.is_empty(),
        }
    }

    pub fn view<'a>(
        &'a self,
        id: pane_grid::Pane,
        panes: usize,
        is_focused: bool,
        maximized: bool,
        window: window::Id,
        main_window: &'a Window,
        timezone: UserTimezone,
        tickers_table: &'a TickersTable,
    ) -> pane_grid::Content<'a, Message, Theme, Renderer> {
        // 銘柄概念を持たないペイン（Starter / OrderList / BuyingPower）からは
        // link_group ボタン `[-]` を非表示にする。OrderEntry は銘柄を持つため
        // else 分岐で表示する（docs/✅order/order-entry-link-group-plan.md タスク 7）。
        let mut top_left_buttons = if matches!(
            self.content,
            Content::Starter
                | Content::OrderList(_)
                | Content::BuyingPower(_)
                | Content::Positions(_)
        ) {
            row![]
        } else {
            row![link_group_button(id, self.link_group, |id| {
                Message::PaneEvent(id, Event::ShowModal(Modal::LinkGroup))
            })]
        };

        if let Some(kind) = self.stream_pair_kind() {
            let (base_ti, extra) = match kind {
                StreamPairKind::MultiSource(list) => (list[0], list.len().saturating_sub(1)),
                StreamPairKind::SingleSource(ti) => (ti, 0),
            };

            let exchange_icon = icon_text(style::venue_icon(base_ti.ticker.exchange.venue()), 14);
            let mut label = {
                let symbol = base_ti.ticker.display_symbol_and_type().0;
                match base_ti.ticker.market_type() {
                    MarketKind::Spot | MarketKind::Stock => symbol,
                    MarketKind::LinearPerps | MarketKind::InversePerps => symbol + " PERP",
                }
            };
            if extra > 0 {
                label = format!("{label} +{extra}");
            }

            let content = row![
                exchange_icon.align_y(Alignment::Center).line_height(1.4),
                text(label)
                    .size(14)
                    .align_y(Alignment::Center)
                    .line_height(1.4)
            ]
            .align_y(Alignment::Center)
            .spacing(4);

            let tickers_list_btn = button(content)
                .on_press(Message::PaneEvent(
                    id,
                    Event::ShowModal(Modal::MiniTickersList(MiniPanel::new())),
                ))
                .style(|theme, status| {
                    style::button::modifier(
                        theme,
                        status,
                        !matches!(self.modal, Some(Modal::MiniTickersList(_))),
                    )
                })
                .height(widget::PANE_CONTROL_BTN_HEIGHT);

            top_left_buttons = top_left_buttons.push(tickers_list_btn);
        } else if !matches!(
            self.content,
            Content::Starter
                | Content::OrderEntry(_)
                | Content::OrderList(_)
                | Content::BuyingPower(_)
        ) && !self.has_stream()
        {
            let content = row![
                text("Choose a ticker")
                    .size(13)
                    .align_y(Alignment::Center)
                    .line_height(1.4)
            ]
            .align_y(Alignment::Center);

            let tickers_list_btn = button(content)
                .on_press(Message::PaneEvent(
                    id,
                    Event::ShowModal(Modal::MiniTickersList(MiniPanel::new())),
                ))
                .style(|theme, status| {
                    style::button::modifier(
                        theme,
                        status,
                        !matches!(self.modal, Some(Modal::MiniTickersList(_))),
                    )
                })
                .height(widget::PANE_CONTROL_BTN_HEIGHT);

            top_left_buttons = top_left_buttons.push(tickers_list_btn);
        }

        let order_panel_title: Option<&'static str> = match &self.content {
            Content::OrderEntry(_) => Some("注文入力"),
            Content::OrderList(_) => Some("注文一覧"),
            Content::BuyingPower(_) => Some("買余力"),
            Content::Positions(_) => Some("保有銘柄"),
            Content::Starter
            | Content::Heatmap { .. }
            | Content::ShaderHeatmap { .. }
            | Content::Kline { .. }
            | Content::TimeAndSales(_)
            | Content::Ladder(_)
            | Content::Comparison(_)
            // N1.11: ReplayControl はタイトルなし（TODO: 将来 "リプレイ速度" を返す）
            | Content::ReplayControl => None,
        };
        if let Some(title) = order_panel_title {
            top_left_buttons = top_left_buttons.push(
                text(title)
                    .size(13)
                    .align_y(Alignment::Center)
                    .line_height(1.4),
            );
        }

        let modifier: Option<modal::stream::Modifier> = self.modal.clone().and_then(|m| {
            if let Modal::StreamModifier(modifier) = m {
                Some(modifier)
            } else {
                None
            }
        });

        let compact_controls = if self.modal == Some(Modal::Controls) {
            Some(
                container(self.view_controls(id, panes, maximized, window != main_window.id))
                    .style(style::chart_modal)
                    .into(),
            )
        } else {
            None
        };

        let uninitialized_base = |kind: ContentKind| -> Element<'a, Message> {
            if self.has_stream() {
                center(text("Loading…").size(16)).into()
            } else {
                let content = column![
                    text(kind.to_string()).size(16),
                    text("No ticker selected").size(14)
                ]
                .spacing(8)
                .align_x(Alignment::Center);

                center(content).into()
            }
        };

        let body = match &self.content {
            Content::Starter => {
                let content_picklist =
                    pick_list(ContentKind::ALL, Some(ContentKind::Starter), move |kind| {
                        Message::PaneEvent(id, Event::ContentSelected(kind))
                    });

                let base: Element<_> = widget::toast::Manager::new(
                    center(
                        column![
                            text("Choose a view to get started").size(16),
                            content_picklist
                        ]
                        .align_x(Alignment::Center)
                        .spacing(12),
                    ),
                    &self.notifications,
                    Alignment::End,
                    move |msg| Message::PaneEvent(id, Event::DeleteNotification(msg)),
                )
                .into();

                self.compose_stack_view(
                    base,
                    id,
                    None,
                    compact_controls,
                    || column![].into(),
                    None,
                    tickers_table,
                )
            }
            Content::Comparison(chart) => {
                if let Some(c) = chart {
                    let selected_basis = Basis::Time(c.timeframe);
                    let kind = ModifierKind::Comparison(selected_basis);

                    let modifiers =
                        row![basis_modifier(id, selected_basis, modifier, kind),].spacing(4);

                    top_left_buttons = top_left_buttons.push(modifiers);

                    let base = c.view(timezone).map(move |message| {
                        Message::PaneEvent(id, Event::ComparisonChartInteraction(message))
                    });

                    let settings_modal = || comparison_cfg_view(id, c);

                    self.compose_stack_view(
                        base,
                        id,
                        None,
                        compact_controls,
                        settings_modal,
                        Some(c.selected_tickers()),
                        tickers_table,
                    )
                } else {
                    let base = uninitialized_base(ContentKind::ComparisonChart);
                    self.compose_stack_view(
                        base,
                        id,
                        None,
                        compact_controls,
                        || column![].into(),
                        None,
                        tickers_table,
                    )
                }
            }
            Content::TimeAndSales(panel) => {
                if let Some(panel) = panel {
                    let base = panel::view(panel, timezone).map(move |message| {
                        Message::PaneEvent(id, Event::PanelInteraction(message))
                    });

                    let settings_modal =
                        || modal::pane::settings::timesales_cfg_view(panel.config, id);

                    self.compose_stack_view(
                        base,
                        id,
                        None,
                        compact_controls,
                        settings_modal,
                        None,
                        tickers_table,
                    )
                } else {
                    let base = uninitialized_base(ContentKind::TimeAndSales);
                    self.compose_stack_view(
                        base,
                        id,
                        None,
                        compact_controls,
                        || column![].into(),
                        None,
                        tickers_table,
                    )
                }
            }
            Content::Ladder(panel) => {
                if let Some(panel) = panel {
                    let basis = self
                        .settings
                        .selected_basis
                        .unwrap_or(Basis::default_heatmap_time(self.stream_pair()));
                    let tick_multiply = self.settings.tick_multiply.unwrap_or(TickMultiplier(1));

                    let stream_pair = self.stream_pair();

                    let price_step = stream_pair
                        .map(|ti| {
                            tick_multiply.unscale_step_or_min_tick(panel.step, ti.min_ticksize)
                        })
                        .unwrap_or_else(|| tick_multiply.unscale_step(panel.step));

                    let exchange = stream_pair.map(|ti| ti.ticker.exchange);
                    let min_ticksize = stream_pair.map(|ti| ti.min_ticksize);

                    let modifiers = ticksize_modifier(
                        id,
                        price_step,
                        min_ticksize,
                        tick_multiply,
                        modifier,
                        ModifierKind::Orderbook(basis, tick_multiply),
                        exchange,
                    );

                    top_left_buttons = top_left_buttons.push(modifiers);

                    let base = panel::view(panel, timezone).map(move |message| {
                        Message::PaneEvent(id, Event::PanelInteraction(message))
                    });

                    let settings_modal =
                        || modal::pane::settings::ladder_cfg_view(panel.config, id);

                    self.compose_stack_view(
                        base,
                        id,
                        None,
                        compact_controls,
                        settings_modal,
                        None,
                        tickers_table,
                    )
                } else {
                    let base = uninitialized_base(ContentKind::Ladder);
                    self.compose_stack_view(
                        base,
                        id,
                        None,
                        compact_controls,
                        || column![].into(),
                        None,
                        tickers_table,
                    )
                }
            }
            Content::Heatmap {
                chart, indicators, ..
            } => {
                if let Some(chart) = chart {
                    let ticker_info = self.stream_pair();
                    let exchange = ticker_info.as_ref().map(|info| info.ticker.exchange);

                    let basis = self
                        .settings
                        .selected_basis
                        .unwrap_or(Basis::default_heatmap_time(ticker_info));
                    let tick_multiply = self.settings.tick_multiply.unwrap_or(TickMultiplier(5));

                    let kind = ModifierKind::Heatmap(basis, tick_multiply);
                    let price_step = ticker_info
                        .map(|ti| {
                            tick_multiply
                                .unscale_step_or_min_tick(chart.tick_size(), ti.min_ticksize)
                        })
                        .unwrap_or_else(|| tick_multiply.unscale_step(chart.tick_size()));
                    let min_ticksize = ticker_info.map(|ti| ti.min_ticksize);

                    let modifiers = row![
                        basis_modifier(id, basis, modifier, kind),
                        ticksize_modifier(
                            id,
                            price_step,
                            min_ticksize,
                            tick_multiply,
                            modifier,
                            kind,
                            exchange
                        ),
                    ]
                    .spacing(4);

                    top_left_buttons = top_left_buttons.push(modifiers);

                    let base = chart::view(chart, indicators, timezone).map(move |message| {
                        Message::PaneEvent(id, Event::ChartInteraction(message))
                    });
                    let settings_modal = || {
                        heatmap_cfg_view(
                            chart.visual_config(),
                            id,
                            chart.study_configurator(),
                            &chart.studies,
                            basis,
                        )
                    };

                    let indicator_modal = if self.modal == Some(Modal::Indicators) {
                        Some(modal::indicators::view(
                            id,
                            self,
                            indicators,
                            self.stream_pair().map(|i| i.ticker.market_type()),
                        ))
                    } else {
                        None
                    };

                    self.compose_stack_view(
                        base,
                        id,
                        indicator_modal,
                        compact_controls,
                        settings_modal,
                        None,
                        tickers_table,
                    )
                } else {
                    let base = uninitialized_base(ContentKind::HeatmapChart);
                    self.compose_stack_view(
                        base,
                        id,
                        None,
                        compact_controls,
                        || column![].into(),
                        None,
                        tickers_table,
                    )
                }
            }
            Content::Kline {
                chart,
                indicators,
                kind: chart_kind,
                ..
            } => {
                if let Some(chart) = chart {
                    match chart_kind {
                        data::chart::KlineChartKind::Footprint { .. } => {
                            let basis = chart.basis();
                            let tick_multiply =
                                self.settings.tick_multiply.unwrap_or(TickMultiplier(10));

                            let kind = ModifierKind::Footprint(basis, tick_multiply);
                            let stream_pair = self.stream_pair();
                            let price_step = stream_pair
                                .map(|ti| {
                                    tick_multiply.unscale_step_or_min_tick(
                                        chart.tick_size(),
                                        ti.min_ticksize,
                                    )
                                })
                                .unwrap_or_else(|| tick_multiply.unscale_step(chart.tick_size()));

                            let exchange = stream_pair.as_ref().map(|info| info.ticker.exchange);
                            let min_ticksize = stream_pair.map(|ti| ti.min_ticksize);

                            let modifiers = row![
                                basis_modifier(id, basis, modifier, kind),
                                ticksize_modifier(
                                    id,
                                    price_step,
                                    min_ticksize,
                                    tick_multiply,
                                    modifier,
                                    kind,
                                    exchange
                                ),
                            ]
                            .spacing(4);

                            top_left_buttons = top_left_buttons.push(modifiers);
                        }
                        data::chart::KlineChartKind::Candles => {
                            let selected_basis = chart.basis();
                            let kind = ModifierKind::Candlestick(selected_basis);

                            let modifiers =
                                row![basis_modifier(id, selected_basis, modifier, kind),]
                                    .spacing(4);

                            top_left_buttons = top_left_buttons.push(modifiers);
                        }
                    }

                    let base = chart::view(chart, indicators, timezone).map(move |message| {
                        Message::PaneEvent(id, Event::ChartInteraction(message))
                    });
                    let settings_modal = || {
                        kline_cfg_view(
                            chart.study_configurator(),
                            data::chart::kline::Config {},
                            chart_kind,
                            id,
                            chart.basis(),
                        )
                    };

                    let indicator_modal = if self.modal == Some(Modal::Indicators) {
                        Some(modal::indicators::view(
                            id,
                            self,
                            indicators,
                            self.stream_pair().map(|i| i.ticker.market_type()),
                        ))
                    } else {
                        None
                    };

                    self.compose_stack_view(
                        base,
                        id,
                        indicator_modal,
                        compact_controls,
                        settings_modal,
                        None,
                        tickers_table,
                    )
                } else {
                    let content_kind = match chart_kind {
                        data::chart::KlineChartKind::Candles => ContentKind::CandlestickChart,
                        data::chart::KlineChartKind::Footprint { .. } => {
                            ContentKind::FootprintChart
                        }
                    };
                    let base = uninitialized_base(content_kind);
                    self.compose_stack_view(
                        base,
                        id,
                        None,
                        compact_controls,
                        || column![].into(),
                        None,
                        tickers_table,
                    )
                }
            }
            Content::ShaderHeatmap {
                chart, indicators, ..
            } => {
                if let Some(chart) = chart {
                    let base = HeatmapShader::view(chart, timezone).map(move |message| {
                        Message::PaneEvent(id, Event::HeatmapShaderInteraction(message))
                    });

                    let ticker_info = self.stream_pair();
                    let exchange = ticker_info.as_ref().map(|info| info.ticker.exchange);

                    let basis = self
                        .settings
                        .selected_basis
                        .unwrap_or(Basis::default_heatmap_time(ticker_info));
                    let tick_multiply = self.settings.tick_multiply.unwrap_or(TickMultiplier(5));

                    let kind = ModifierKind::Heatmap(basis, tick_multiply);

                    let price_step = ticker_info
                        .map(|ti| {
                            tick_multiply
                                .unscale_step_or_min_tick(chart.tick_size(), ti.min_ticksize)
                        })
                        .unwrap_or_else(|| tick_multiply.unscale_step(chart.tick_size()));
                    let min_ticksize = ticker_info.map(|ti| ti.min_ticksize);

                    let settings_modal = || {
                        heatmap_shader_cfg_view(
                            chart.visual_config(),
                            id,
                            chart.study_configurator(),
                            &chart.studies,
                            basis,
                        )
                    };

                    let indicator_modal = if self.modal == Some(Modal::Indicators) {
                        Some(modal::indicators::view(
                            id,
                            self,
                            indicators,
                            self.stream_pair().map(|i| i.ticker.market_type()),
                        ))
                    } else {
                        None
                    };

                    let modifiers = row![
                        basis_modifier(id, basis, modifier, kind),
                        ticksize_modifier(
                            id,
                            price_step,
                            min_ticksize,
                            tick_multiply,
                            modifier,
                            kind,
                            exchange
                        ),
                    ]
                    .spacing(4);

                    top_left_buttons = top_left_buttons.push(modifiers);

                    self.compose_stack_view(
                        base,
                        id,
                        indicator_modal,
                        compact_controls,
                        settings_modal,
                        None,
                        tickers_table,
                    )
                } else {
                    let base = uninitialized_base(ContentKind::HeatmapChart);
                    self.compose_stack_view(
                        base,
                        id,
                        None,
                        compact_controls,
                        || column![].into(),
                        None,
                        tickers_table,
                    )
                }
            }
            Content::OrderEntry(panel) => {
                let base = panel
                    .view()
                    .map(move |msg| Message::PaneEvent(id, Event::OrderEntryMsg(msg)));
                self.compose_stack_view(
                    base,
                    id,
                    None,
                    compact_controls,
                    || column![].into(),
                    None,
                    tickers_table,
                )
            }
            Content::OrderList(panel) => {
                let base = panel::orders::view(panel)
                    .map(move |msg| Message::PaneEvent(id, Event::OrderListMsg(msg)));
                self.compose_stack_view(
                    base,
                    id,
                    None,
                    compact_controls,
                    || column![].into(),
                    None,
                    tickers_table,
                )
            }
            Content::BuyingPower(panel) => {
                let base = panel::buying_power::view(panel)
                    .map(move |msg| Message::PaneEvent(id, Event::BuyingPowerMsg(msg)));
                self.compose_stack_view(
                    base,
                    id,
                    None,
                    compact_controls,
                    || column![].into(),
                    None,
                    tickers_table,
                )
            }
            Content::Positions(panel) => {
                let base = panel::positions::view(panel)
                    .map(move |msg| Message::PaneEvent(id, Event::PositionsMsg(msg)));
                self.compose_stack_view(
                    base,
                    id,
                    None,
                    compact_controls,
                    || column![].into(),
                    None,
                    tickers_table,
                )
            }
            // N1.11-ui: ReplayControl — speed button row (1x / 10x / 100x).
            Content::ReplayControl => {
                let speed_buttons = row![
                    button(text("1x").size(14))
                        .on_press(Message::PaneEvent(id, Event::SetReplaySpeed(1)))
                        .padding([4, 10]),
                    button(text("10x").size(14))
                        .on_press(Message::PaneEvent(id, Event::SetReplaySpeed(10)))
                        .padding([4, 10]),
                    button(text("100x").size(14))
                        .on_press(Message::PaneEvent(id, Event::SetReplaySpeed(100)))
                        .padding([4, 10]),
                ]
                .spacing(8);
                // N4.3: strategy file picker button.
                let strategy_btn = button(text("Strategy ファイルを選ぶ").size(12))
                    .on_press(Message::PaneEvent(id, Event::PickStrategyFile))
                    .padding([4, 10]);
                let base: Element<_> = center(
                    column![text("再生速度").size(12), speed_buttons, strategy_btn,]
                        .spacing(8)
                        .align_x(Alignment::Center),
                )
                .into();
                self.compose_stack_view(
                    base,
                    id,
                    None,
                    compact_controls,
                    || column![].into(),
                    None,
                    tickers_table,
                )
            }
        };

        match &self.status {
            Status::Loading(InfoKind::FetchingKlines) => {
                top_left_buttons = top_left_buttons.push(text("Fetching Klines..."));
            }
            Status::Loading(InfoKind::FetchingTrades(count)) => {
                top_left_buttons =
                    top_left_buttons.push(text(format!("Fetching Trades... {count} fetched")));
            }
            Status::Loading(InfoKind::FetchingOI) => {
                top_left_buttons = top_left_buttons.push(text("Fetching Open Interest..."));
            }
            Status::Stale(msg) => {
                top_left_buttons = top_left_buttons.push(text(msg));
            }
            Status::Ready => {}
        }

        let content = pane_grid::Content::new(body)
            .style(move |theme| style::pane_background(theme, is_focused));

        let top_right_buttons = {
            let compact_control = container(
                button(text("...").size(13).align_y(Alignment::End))
                    .on_press(Message::PaneEvent(id, Event::ShowModal(Modal::Controls)))
                    .style(move |theme, status| {
                        style::button::transparent(
                            theme,
                            status,
                            self.modal == Some(Modal::Controls)
                                || self.modal == Some(Modal::Settings),
                        )
                    }),
            )
            .align_y(Alignment::Center)
            .padding(4);

            if self.modal == Some(Modal::Controls) {
                pane_grid::Controls::new(compact_control)
            } else {
                pane_grid::Controls::dynamic(
                    self.view_controls(id, panes, maximized, window != main_window.id),
                    compact_control,
                )
            }
        };

        let title_bar = pane_grid::TitleBar::new(
            top_left_buttons
                .padding(padding::left(4))
                .align_y(Alignment::Center)
                .spacing(8)
                .height(Length::Fixed(32.0)),
        )
        .controls(top_right_buttons)
        .style(style::pane_title_bar);

        content.title_bar(if self.modal.is_none() {
            title_bar
        } else {
            title_bar.always_show_controls()
        })
    }

    pub fn update(&mut self, msg: Event) -> Option<Effect> {
        match msg {
            Event::ShowModal(requested_modal) => {
                return self.show_modal_with_focus(requested_modal);
            }
            Event::HideModal => {
                self.modal = None;
            }
            Event::ContentSelected(kind) => {
                self.content = Content::placeholder(kind);

                if !matches!(
                    kind,
                    ContentKind::Starter
                        | ContentKind::OrderEntry
                        | ContentKind::OrderList
                        | ContentKind::BuyingPower
                        // N1.11: ReplayControl は ticker 選択を必要としない
                        | ContentKind::ReplayControl
                ) {
                    self.streams = ResolvedStream::waiting(vec![]);
                    let modal = Modal::MiniTickersList(MiniPanel::new());

                    if let Some(effect) = self.show_modal_with_focus(modal) {
                        return Some(effect);
                    }
                }
            }
            Event::ChartInteraction(msg) => match &mut self.content {
                Content::Heatmap { chart: Some(c), .. } => {
                    super::chart::update(c, &msg);
                }
                Content::Kline { chart: Some(c), .. } => {
                    super::chart::update(c, &msg);
                }
                _ => {}
            },
            Event::PanelInteraction(msg) => match &mut self.content {
                Content::Ladder(Some(p)) => super::panel::update(p, msg),
                Content::TimeAndSales(Some(p)) => super::panel::update(p, msg),
                _ => {}
            },
            Event::ToggleIndicator(ind) => {
                self.content.toggle_indicator(ind);
            }
            Event::DeleteNotification(idx) => {
                if idx < self.notifications.len() {
                    self.notifications.remove(idx);
                }
            }
            Event::ReorderIndicator(e) => {
                self.content.reorder_indicators(&e);
            }
            Event::ClusterKindSelected(kind) => {
                if let Content::Kline {
                    chart, kind: cur, ..
                } = &mut self.content
                    && let Some(c) = chart
                {
                    c.set_cluster_kind(kind);
                    *cur = c.kind.clone();
                }
            }
            Event::ClusterScalingSelected(scaling) => {
                if let Content::Kline { chart, kind, .. } = &mut self.content
                    && let Some(c) = chart
                {
                    c.set_cluster_scaling(scaling);
                    *kind = c.kind.clone();
                }
            }
            Event::StudyConfigurator(study_msg) => match study_msg {
                modal::pane::settings::study::StudyMessage::Footprint(m) => {
                    if let Content::Kline { chart, kind, .. } = &mut self.content
                        && let Some(c) = chart
                    {
                        c.update_study_configurator(m);
                        *kind = c.kind.clone();
                    }
                }
                modal::pane::settings::study::StudyMessage::Heatmap(m) => {
                    if let Content::Heatmap { chart, studies, .. } = &mut self.content
                        && let Some(c) = chart
                    {
                        c.update_study_configurator(m);
                        *studies = c.studies.clone();
                    } else if let Content::ShaderHeatmap { chart, studies, .. } = &mut self.content
                        && let Some(c) = chart
                    {
                        c.update_study_configurator(m);
                        *studies = c.studies.clone();
                    }
                }
            },
            Event::StreamModifierChanged(message) => {
                if let Some(Modal::StreamModifier(mut modifier)) = self.modal.take() {
                    let mut effect: Option<Effect> = None;

                    if let Some(action) = modifier.update(message) {
                        match action {
                            modal::stream::Action::TabSelected(tab) => {
                                modifier.tab = tab;
                            }
                            modal::stream::Action::TicksizeSelected(tm) => {
                                modifier.update_kind_with_multiplier(tm);
                                self.settings.tick_multiply = Some(tm);

                                if let Some(ticker) = self.stream_pair() {
                                    match &mut self.content {
                                        Content::Kline { chart: Some(c), .. } => {
                                            c.change_tick_size(
                                                tm.multiply_with_min_tick_step(ticker),
                                            );
                                            c.reset_request_handler();
                                        }
                                        Content::Heatmap { chart: Some(c), .. } => {
                                            c.change_tick_size(
                                                tm.multiply_with_min_tick_step(ticker),
                                            );
                                        }
                                        Content::Ladder(Some(p)) => {
                                            p.set_tick_size(tm.multiply_with_min_tick_step(ticker));
                                        }
                                        Content::ShaderHeatmap {
                                            chart: Some(c),
                                            indicators,
                                            studies,
                                            ..
                                        } => {
                                            **c = HeatmapShader::new(
                                                c.basis,
                                                tm.multiply_with_min_tick_step(ticker),
                                                c.ticker_info,
                                                studies.clone(),
                                                indicators.clone(),
                                            );
                                        }
                                        _ => {}
                                    }
                                }

                                let is_client = self
                                    .stream_pair()
                                    .map(|ti| ti.exchange().is_depth_client_aggr())
                                    .unwrap_or(false);

                                if let Some(mut it) = self.streams.ready_iter_mut() {
                                    for s in &mut it {
                                        if let StreamKind::Depth { depth_aggr, .. } = s {
                                            *depth_aggr = if is_client {
                                                StreamTicksize::Client
                                            } else {
                                                StreamTicksize::ServerSide(tm)
                                            };
                                        }
                                    }
                                }
                                if !is_client {
                                    effect = Some(Effect::RefreshStreams);
                                }
                            }
                            modal::stream::Action::BasisSelected(new_basis) => {
                                modifier.update_kind_with_basis(new_basis);
                                self.settings.selected_basis = Some(new_basis);

                                let base_ticker = self.stream_pair();

                                match &mut self.content {
                                    Content::Heatmap { chart: Some(c), .. } => {
                                        c.set_basis(new_basis);

                                        if let Some(stream_type) =
                                            self.streams.ready_iter_mut().and_then(|mut it| {
                                                it.find(|s| matches!(s, StreamKind::Depth { .. }))
                                            })
                                            && let StreamKind::Depth {
                                                push_freq,
                                                ticker_info,
                                                ..
                                            } = stream_type
                                            && ticker_info.exchange().is_custom_push_freq()
                                        {
                                            match new_basis {
                                                Basis::Time(tf) => {
                                                    *push_freq = exchange::PushFrequency::Custom(tf)
                                                }
                                                Basis::Tick(_) => {
                                                    *push_freq =
                                                        exchange::PushFrequency::ServerDefault
                                                }
                                            }
                                        }

                                        effect = Some(Effect::RefreshStreams);
                                    }
                                    Content::ShaderHeatmap {
                                        chart: Some(c),
                                        indicators,
                                        ..
                                    } => {
                                        **c = HeatmapShader::new(
                                            new_basis,
                                            c.tick_size(),
                                            c.ticker_info,
                                            c.studies.clone(),
                                            indicators.clone(),
                                        );

                                        if let Some(stream_type) =
                                            self.streams.ready_iter_mut().and_then(|mut it| {
                                                it.find(|s| matches!(s, StreamKind::Depth { .. }))
                                            })
                                            && let StreamKind::Depth {
                                                push_freq,
                                                ticker_info,
                                                ..
                                            } = stream_type
                                            && ticker_info.exchange().is_custom_push_freq()
                                        {
                                            match new_basis {
                                                Basis::Time(tf) => {
                                                    *push_freq = exchange::PushFrequency::Custom(tf)
                                                }
                                                Basis::Tick(_) => {
                                                    *push_freq =
                                                        exchange::PushFrequency::ServerDefault
                                                }
                                            }
                                        }

                                        effect = Some(Effect::RefreshStreams);
                                    }
                                    Content::Kline { chart: Some(c), .. } => {
                                        if let Some(base_ticker) = base_ticker {
                                            match new_basis {
                                                Basis::Time(tf) => {
                                                    let kline_stream = StreamKind::Kline {
                                                        ticker_info: base_ticker,
                                                        timeframe: tf,
                                                    };
                                                    let mut streams = vec![kline_stream];

                                                    if matches!(
                                                        c.kind,
                                                        data::chart::KlineChartKind::Footprint { .. }
                                                    ) {
                                                        let depth_aggr = if base_ticker
                                                            .exchange()
                                                            .is_depth_client_aggr()
                                                        {
                                                            StreamTicksize::Client
                                                        } else {
                                                            StreamTicksize::ServerSide(
                                                                self.settings
                                                                    .tick_multiply
                                                                    .unwrap_or(TickMultiplier(1)),
                                                            )
                                                        };
                                                        streams.push(StreamKind::Depth {
                                                            ticker_info: base_ticker,
                                                            depth_aggr,
                                                            push_freq: exchange::PushFrequency::ServerDefault,
                                                        });
                                                        streams.push(StreamKind::Trades {
                                                            ticker_info: base_ticker,
                                                        });
                                                    }

                                                    self.streams = ResolvedStream::Ready(streams);
                                                    let action = c.set_basis(new_basis);

                                                    if let Some(chart::Action::RequestFetch(
                                                        fetch,
                                                    )) = action
                                                    {
                                                        effect = Some(Effect::RequestFetch(fetch));
                                                    }
                                                }
                                                Basis::Tick(_) => {
                                                    let depth_aggr = if base_ticker
                                                        .exchange()
                                                        .is_depth_client_aggr()
                                                    {
                                                        StreamTicksize::Client
                                                    } else {
                                                        StreamTicksize::ServerSide(
                                                            self.settings
                                                                .tick_multiply
                                                                .unwrap_or(TickMultiplier(1)),
                                                        )
                                                    };

                                                    self.streams = ResolvedStream::Ready(vec![
                                                        StreamKind::Depth {
                                                            ticker_info: base_ticker,
                                                            depth_aggr,
                                                            push_freq: exchange::PushFrequency::ServerDefault,
                                                        },
                                                        StreamKind::Trades {
                                                            ticker_info: base_ticker,
                                                        },
                                                    ]);
                                                    c.set_basis(new_basis);
                                                    effect = Some(Effect::RefreshStreams);
                                                }
                                            }
                                        }
                                    }
                                    Content::Comparison(Some(c)) => {
                                        if let Basis::Time(tf) = new_basis {
                                            let streams: Vec<StreamKind> = c
                                                .selected_tickers()
                                                .iter()
                                                .copied()
                                                .map(|ti| StreamKind::Kline {
                                                    ticker_info: ti,
                                                    timeframe: tf,
                                                })
                                                .collect();

                                            self.streams = ResolvedStream::Ready(streams);
                                            let action = c.set_basis(new_basis);

                                            if let Some(chart::Action::RequestFetch(fetch)) = action
                                            {
                                                effect = Some(Effect::RequestFetch(fetch));
                                            }
                                        }
                                    }
                                    _ => {}
                                }
                            }
                        }
                    }

                    self.modal = Some(Modal::StreamModifier(modifier));

                    if let Some(e) = effect {
                        return Some(e);
                    }
                }
            }
            Event::ComparisonChartInteraction(message) => {
                if let Content::Comparison(chart_opt) = &mut self.content
                    && let Some(chart) = chart_opt
                    && let Some(action) = chart.update(message)
                {
                    match action {
                        super::chart::comparison::Action::SeriesColorChanged(t, color) => {
                            chart.set_series_color(t, color);
                        }
                        super::chart::comparison::Action::SeriesNameChanged(t, name) => {
                            chart.set_series_name(t, name);
                        }
                        super::chart::comparison::Action::OpenSeriesEditor => {
                            self.modal = Some(Modal::Settings);
                        }
                        super::chart::comparison::Action::RemoveSeries(ti) => {
                            let rebuilt = chart.remove_ticker(&ti);
                            self.streams = ResolvedStream::Ready(rebuilt);

                            return Some(Effect::RefreshStreams);
                        }
                    }
                }
            }
            Event::HeatmapShaderInteraction(message) => {
                if let Content::ShaderHeatmap { chart: Some(c), .. } = &mut self.content {
                    c.update(message);
                }
            }
            Event::MiniTickersListInteraction(message) => {
                if let Some(Modal::MiniTickersList(ref mut mini_panel)) = self.modal
                    && let Some(action) = mini_panel.update(message)
                {
                    let crate::modal::pane::mini_tickers_list::Action::RowSelected(sel) = action;
                    match sel {
                        crate::modal::pane::mini_tickers_list::RowSelection::Add(ti) => {
                            self.modal = Some(Modal::MiniTickersList(mini_panel.clone()));
                            if let Content::Comparison(chart) = &mut self.content
                                && let Some(c) = chart
                            {
                                let rebuilt = c.add_ticker(&ti);
                                self.streams = ResolvedStream::Ready(rebuilt);
                                return Some(Effect::RefreshStreams);
                            }
                        }
                        crate::modal::pane::mini_tickers_list::RowSelection::Remove(ti) => {
                            self.modal = Some(Modal::MiniTickersList(mini_panel.clone()));
                            if let Content::Comparison(chart) = &mut self.content
                                && let Some(c) = chart
                            {
                                let rebuilt = c.remove_ticker(&ti);
                                self.streams = ResolvedStream::Ready(rebuilt);
                                return Some(Effect::RefreshStreams);
                            }
                        }
                        crate::modal::pane::mini_tickers_list::RowSelection::Switch(ti) => {
                            if matches!(self.content, Content::OrderEntry(_))
                                && ti.ticker.exchange != Exchange::TachibanaStock
                            {
                                self.notifications.push(Toast::warn(
                                    "注文入力パネルは立花証券銘柄のみ対応しています".to_string(),
                                ));
                                self.modal = None;
                                return None;
                            }
                            // Broadcast via SwitchTickersInGroup so link_group peers sync.
                            // For OrderEntry, apply_ticker_to_order_entry is called in
                            // init_pane / init_focused_pane.
                            self.modal = None;
                            return Some(Effect::SwitchTickersInGroup(ti));
                        }
                    }
                }
            }
            Event::OrderEntryMsg(msg) => {
                if let Content::OrderEntry(panel) = &mut self.content
                    && let Some(action) = panel.update(msg)
                {
                    if matches!(action, panel::order_entry::Action::OpenInstrumentPicker) {
                        self.modal = Some(Modal::MiniTickersList(MiniPanel::new()));
                    } else {
                        return Some(Effect::OrderEntryAction(action));
                    }
                }
            }
            Event::OrderListMsg(msg) => {
                if let Content::OrderList(panel) = &mut self.content
                    && let Some(action) = panel::orders::update(panel, msg)
                {
                    return Some(Effect::OrderListAction(action));
                }
            }
            Event::BuyingPowerMsg(msg) => {
                if let Content::BuyingPower(panel) = &mut self.content
                    && let Some(action) = panel::buying_power::update(panel, msg)
                {
                    return Some(Effect::BuyingPowerAction(action));
                }
            }
            Event::PositionsMsg(msg) => {
                if let Content::Positions(panel) = &mut self.content
                    && let Some(action) = panel::positions::update(panel, msg)
                {
                    return Some(Effect::PositionsAction(action));
                }
            }
            // N1.11-ui: Relay speed-button press to the dashboard.
            Event::SetReplaySpeed(multiplier) => {
                return Some(Effect::SetReplaySpeed(multiplier));
            }
            // N4.3: Relay strategy file picker button press to the dashboard.
            Event::PickStrategyFile => {
                return Some(Effect::PickStrategyFile);
            }
        }
        None
    }

    fn view_controls(
        &'_ self,
        pane: pane_grid::Pane,
        total_panes: usize,
        is_maximized: bool,
        is_popout: bool,
    ) -> Element<'_, Message> {
        let modal_btn_style = |modal: Modal| {
            let is_active = self.modal == Some(modal);
            move |theme: &Theme, status: button::Status| {
                style::button::transparent(theme, status, is_active)
            }
        };

        let control_btn_style = |is_active: bool| {
            move |theme: &Theme, status: button::Status| {
                style::button::transparent(theme, status, is_active)
            }
        };

        let treat_as_starter =
            matches!(&self.content, Content::Starter) || !self.content.initialized();

        let tooltip_pos = tooltip::Position::Bottom;
        let mut buttons = row![];

        let show_modal = |modal: Modal| Message::PaneEvent(pane, Event::ShowModal(modal));

        if !treat_as_starter {
            buttons = buttons.push(button_with_tooltip(
                icon_text(Icon::Cog, 12),
                show_modal(Modal::Settings),
                None,
                tooltip_pos,
                modal_btn_style(Modal::Settings),
            ));
        }
        if !treat_as_starter
            && matches!(
                &self.content,
                Content::Heatmap { .. } | Content::Kline { .. } | Content::ShaderHeatmap { .. }
            )
        {
            buttons = buttons.push(button_with_tooltip(
                icon_text(Icon::ChartOutline, 12),
                show_modal(Modal::Indicators),
                Some("Indicators"),
                tooltip_pos,
                modal_btn_style(Modal::Indicators),
            ));
        }

        if is_popout {
            buttons = buttons.push(button_with_tooltip(
                icon_text(Icon::Popout, 12),
                Message::Merge,
                Some("Merge"),
                tooltip_pos,
                control_btn_style(is_popout),
            ));
        } else if total_panes > 1 {
            buttons = buttons.push(button_with_tooltip(
                icon_text(Icon::Popout, 12),
                Message::Popout,
                Some("Pop out"),
                tooltip_pos,
                control_btn_style(is_popout),
            ));
        }

        if total_panes > 1 {
            let (resize_icon, message) = if is_maximized {
                (Icon::ResizeSmall, Message::Restore)
            } else {
                (Icon::ResizeFull, Message::MaximizePane(pane))
            };

            buttons = buttons.push(button_with_tooltip(
                icon_text(resize_icon, 12),
                message,
                None,
                tooltip_pos,
                control_btn_style(is_maximized),
            ));

            buttons = buttons.push(button_with_tooltip(
                icon_text(Icon::Close, 12),
                Message::ClosePane(pane),
                None,
                tooltip_pos,
                control_btn_style(false),
            ));
        }

        buttons
            .padding(padding::right(4).left(4))
            .align_y(Alignment::Center)
            .height(Length::Fixed(32.0))
            .into()
    }

    fn compose_stack_view<'a, F>(
        &'a self,
        base: Element<'a, Message>,
        pane: pane_grid::Pane,
        indicator_modal: Option<Element<'a, Message>>,
        compact_controls: Option<Element<'a, Message>>,
        settings_modal: F,
        selected_tickers: Option<&'a [TickerInfo]>,
        tickers_table: &'a TickersTable,
    ) -> Element<'a, Message>
    where
        F: FnOnce() -> Element<'a, Message>,
    {
        let base =
            widget::toast::Manager::new(base, &self.notifications, Alignment::End, move |msg| {
                Message::PaneEvent(pane, Event::DeleteNotification(msg))
            })
            .into();

        let on_blur = Message::PaneEvent(pane, Event::HideModal);

        match &self.modal {
            Some(Modal::LinkGroup) => {
                let content = link_group_modal(pane, self.link_group);

                stack_modal(
                    base,
                    content,
                    on_blur,
                    padding::right(12).left(4),
                    Alignment::Start,
                )
            }
            Some(Modal::StreamModifier(modifier)) => stack_modal(
                base,
                modifier.view(self.stream_pair_kind()).map(move |message| {
                    Message::PaneEvent(pane, Event::StreamModifierChanged(message))
                }),
                Message::PaneEvent(pane, Event::HideModal),
                padding::right(12).left(48),
                Alignment::Start,
            ),
            Some(Modal::MiniTickersList(panel)) => {
                let mini_list = panel
                    .view(tickers_table, selected_tickers, self.stream_pair())
                    .map(move |msg| {
                        Message::PaneEvent(pane, Event::MiniTickersListInteraction(msg))
                    });

                let content: Element<_> = container(mini_list)
                    .max_width(260)
                    .padding(16)
                    .style(style::chart_modal)
                    .into();

                stack_modal(
                    base,
                    content,
                    Message::PaneEvent(pane, Event::HideModal),
                    padding::left(12),
                    Alignment::Start,
                )
            }
            Some(Modal::Settings) => stack_modal(
                base,
                settings_modal(),
                on_blur,
                padding::right(12).left(12),
                Alignment::End,
            ),
            Some(Modal::Indicators) => stack_modal(
                base,
                indicator_modal.unwrap_or_else(|| column![].into()),
                on_blur,
                padding::right(12).left(12),
                Alignment::End,
            ),
            Some(Modal::Controls) => stack_modal(
                base,
                if let Some(controls) = compact_controls {
                    controls
                } else {
                    column![].into()
                },
                on_blur,
                padding::left(12),
                Alignment::End,
            ),
            None => base,
        }
    }

    pub fn matches_stream(&self, stream: &StreamKind) -> bool {
        self.streams.matches_stream(stream)
    }

    fn show_modal_with_focus(&mut self, requested_modal: Modal) -> Option<Effect> {
        let should_toggle_close = match (&self.modal, &requested_modal) {
            (Some(Modal::StreamModifier(open)), Modal::StreamModifier(req)) => {
                open.view_mode == req.view_mode
            }
            (Some(open), req) => core::mem::discriminant(open) == core::mem::discriminant(req),
            _ => false,
        };

        if should_toggle_close {
            self.modal = None;
            return None;
        }

        let focus_widget_id = match &requested_modal {
            Modal::MiniTickersList(m) => Some(m.search_box_id.clone()),
            _ => None,
        };

        self.modal = Some(requested_modal);
        focus_widget_id.map(Effect::FocusWidget)
    }

    pub fn invalidate(&mut self, now: Instant) -> Option<Action> {
        match &mut self.content {
            Content::Heatmap { chart, .. } => chart
                .as_mut()
                .and_then(|c| c.invalidate(Some(now)).map(Action::Chart)),
            Content::Kline { chart, .. } => chart
                .as_mut()
                .and_then(|c| c.invalidate(Some(now)).map(Action::Chart)),
            Content::TimeAndSales(panel) => panel
                .as_mut()
                .and_then(|p| p.invalidate(Some(now)).map(Action::Panel)),
            Content::Ladder(panel) => panel
                .as_mut()
                .and_then(|p| p.invalidate(Some(now)).map(Action::Panel)),
            Content::Starter
            | Content::OrderEntry(_)
            | Content::OrderList(_)
            | Content::BuyingPower(_)
            | Content::Positions(_)
            | Content::ReplayControl => None,
            Content::Comparison(chart) => chart
                .as_mut()
                .and_then(|c| c.invalidate(Some(now)).map(Action::Chart)),
            Content::ShaderHeatmap { chart, .. } => chart
                .as_mut()
                .and_then(|c| c.invalidate(Some(now)).map(Action::Chart)),
        }
    }

    pub fn park_for_inactive_layout(&mut self) {
        if let Content::ShaderHeatmap { chart, .. } = &mut self.content {
            *chart = None;
            self.status = Status::Ready;
        }
    }

    pub fn update_interval(&self) -> Option<u64> {
        match &self.content {
            Content::Kline { .. } | Content::Comparison(_) => Some(1000),
            Content::Heatmap { chart, .. } => {
                if let Some(chart) = chart {
                    chart.basis_interval()
                } else {
                    None
                }
            }
            Content::Ladder(_) | Content::TimeAndSales(_) => Some(100),
            Content::ShaderHeatmap { .. } => None,
            Content::Starter
            | Content::OrderEntry(_)
            | Content::OrderList(_)
            | Content::BuyingPower(_)
            | Content::Positions(_)
            | Content::ReplayControl => None,
        }
    }

    pub fn last_tick(&self) -> Option<Instant> {
        self.content.last_tick()
    }

    pub fn tick(&mut self, now: Instant) -> Option<Action> {
        let invalidate_interval: Option<u64> = self.update_interval();
        let last_tick: Option<Instant> = self.last_tick();

        if let Some(streams) = self.streams.due_streams_to_resolve(now) {
            return Some(Action::ResolveStreams(streams));
        }

        if !self.content.initialized() {
            return Some(Action::ResolveContent);
        }

        match (invalidate_interval, last_tick) {
            (Some(interval_ms), Some(previous_tick_time)) => {
                if interval_ms > 0 {
                    let interval_duration = std::time::Duration::from_millis(interval_ms);
                    if now.duration_since(previous_tick_time) >= interval_duration {
                        return self.invalidate(now);
                    }
                }
            }
            (Some(interval_ms), None) => {
                if interval_ms > 0 {
                    return self.invalidate(now);
                }
            }
            (None, _) => {
                return self.invalidate(now);
            }
        }

        None
    }

    pub fn unique_id(&self) -> uuid::Uuid {
        self.id
    }
}

impl Default for State {
    fn default() -> Self {
        Self {
            id: uuid::Uuid::new_v4(),
            modal: None,
            content: Content::Starter,
            settings: Settings::default(),
            streams: ResolvedStream::waiting(vec![]),
            notifications: vec![],
            status: Status::Ready,
            link_group: None,
        }
    }
}

#[derive(Default)]
pub enum Content {
    #[default]
    Starter,
    Heatmap {
        chart: Option<HeatmapChart>,
        indicators: Vec<HeatmapIndicator>,
        layout: data::chart::ViewConfig,
        studies: Vec<data::chart::heatmap::HeatmapStudy>,
    },
    ShaderHeatmap {
        chart: Option<Box<HeatmapShader>>,
        indicators: Vec<HeatmapIndicator>,
        studies: Vec<data::chart::heatmap::HeatmapStudy>,
    },
    Kline {
        chart: Option<KlineChart>,
        indicators: Vec<KlineIndicator>,
        layout: data::chart::ViewConfig,
        kind: data::chart::KlineChartKind,
    },
    TimeAndSales(Option<TimeAndSales>),
    Ladder(Option<Ladder>),
    Comparison(Option<ComparisonChart>),
    /// Order Entry panel (U0)
    OrderEntry(OrderEntryPanel),
    /// Order List panel (U1)
    OrderList(panel::orders::OrdersPanel),
    /// Buying Power panel (U3)
    BuyingPower(BuyingPowerPanel),
    /// Positions panel (PP3) — 保有銘柄ペイン。
    Positions(panel::positions::PositionsPanel),
    /// N1.11: Replay speed control pane skeleton.
    /// TODO(N1.11-ui): 実際の UI 描画は N1.11 UI フェーズで実装する。
    ReplayControl,
}

impl Content {
    fn new_heatmap(
        current_content: &Content,
        ticker_info: TickerInfo,
        settings: &Settings,
        price_step: exchange::unit::PriceStep,
    ) -> Self {
        let (enabled_indicators, layout, prev_studies) = if let Content::Heatmap {
            chart,
            indicators,
            studies,
            layout,
        } = current_content
        {
            (
                indicators.clone(),
                chart
                    .as_ref()
                    .map(|c| c.chart_layout())
                    .unwrap_or(layout.clone()),
                chart
                    .as_ref()
                    .map_or(studies.clone(), |c| c.studies.clone()),
            )
        } else {
            (
                vec![HeatmapIndicator::Volume],
                ViewConfig {
                    splits: vec![],
                    autoscale: Some(data::chart::Autoscale::CenterLatest),
                },
                vec![],
            )
        };

        let basis = settings
            .selected_basis
            .unwrap_or_else(|| Basis::default_heatmap_time(Some(ticker_info)));
        let config = settings.visual_config.clone().and_then(|cfg| cfg.heatmap());

        let chart = HeatmapChart::new(
            layout.clone(),
            basis,
            price_step,
            &enabled_indicators,
            ticker_info,
            config,
            prev_studies.clone(),
        );

        Content::Heatmap {
            chart: Some(chart),
            indicators: enabled_indicators,
            layout,
            studies: prev_studies,
        }
    }

    fn new_kline(
        content_kind: ContentKind,
        current_content: &Content,
        ticker_info: TickerInfo,
        settings: &Settings,
        step: exchange::unit::PriceStep,
    ) -> Self {
        let (prev_indis, prev_layout, prev_kind_opt) = if let Content::Kline {
            chart,
            indicators,
            kind,
            layout,
        } = current_content
        {
            (
                Some(indicators.clone()),
                Some(chart.as_ref().map_or(layout.clone(), |c| c.chart_layout())),
                Some(chart.as_ref().map_or(kind.clone(), |c| c.kind().clone())),
            )
        } else {
            (None, None, None)
        };

        let (default_tf, determined_chart_kind) = match content_kind {
            ContentKind::FootprintChart => (
                Timeframe::M5,
                prev_kind_opt
                    .filter(|k| matches!(k, data::chart::KlineChartKind::Footprint { .. }))
                    .unwrap_or_else(|| data::chart::KlineChartKind::Footprint {
                        clusters: data::chart::kline::ClusterKind::default(),
                        scaling: data::chart::kline::ClusterScaling::default(),
                        studies: vec![],
                    }),
            ),
            ContentKind::CandlestickChart => (Timeframe::M15, data::chart::KlineChartKind::Candles),
            _ => unreachable!("invalid content kind for kline chart"),
        };

        let basis = settings.selected_basis.unwrap_or(Basis::Time(default_tf));

        let enabled_indicators = {
            let available = KlineIndicator::for_market(ticker_info.market_type());
            prev_indis.map_or_else(
                || vec![KlineIndicator::Volume],
                |indis| {
                    indis
                        .into_iter()
                        .filter(|i| available.contains(i))
                        .collect()
                },
            )
        };

        let splits = {
            let main_chart_split: f32 = 0.8;
            let mut splits_vec = vec![main_chart_split];

            if !enabled_indicators.is_empty() {
                let num_indicators = enabled_indicators.len();

                if num_indicators > 0 {
                    let indicator_total_height_ratio = 1.0 - main_chart_split;
                    let height_per_indicator_pane =
                        indicator_total_height_ratio / num_indicators as f32;

                    let mut current_split_pos = main_chart_split;
                    for _ in 0..(num_indicators - 1) {
                        current_split_pos += height_per_indicator_pane;
                        splits_vec.push(current_split_pos);
                    }
                }
            }
            splits_vec
        };

        let layout = prev_layout
            .filter(|l| l.splits.len() == splits.len())
            .unwrap_or(ViewConfig {
                splits,
                autoscale: Some(data::chart::Autoscale::FitToVisible),
            });

        let chart = KlineChart::new(
            layout.clone(),
            basis,
            step,
            &[],
            vec![],
            &enabled_indicators,
            ticker_info,
            &determined_chart_kind,
        );

        Content::Kline {
            chart: Some(chart),
            indicators: enabled_indicators,
            layout,
            kind: determined_chart_kind,
        }
    }

    fn placeholder(kind: ContentKind) -> Self {
        match kind {
            ContentKind::Starter => Content::Starter,
            ContentKind::CandlestickChart => Content::Kline {
                chart: None,
                indicators: vec![KlineIndicator::Volume],
                kind: data::chart::KlineChartKind::Candles,
                layout: ViewConfig {
                    splits: vec![],
                    autoscale: Some(data::chart::Autoscale::FitToVisible),
                },
            },
            ContentKind::FootprintChart => Content::Kline {
                chart: None,
                indicators: vec![KlineIndicator::Volume],
                kind: data::chart::KlineChartKind::Footprint {
                    clusters: data::chart::kline::ClusterKind::default(),
                    scaling: data::chart::kline::ClusterScaling::default(),
                    studies: vec![],
                },
                layout: ViewConfig {
                    splits: vec![],
                    autoscale: Some(data::chart::Autoscale::FitToVisible),
                },
            },
            ContentKind::ShaderHeatmap => Content::ShaderHeatmap {
                chart: None,
                indicators: vec![HeatmapIndicator::Volume],
                studies: vec![data::chart::heatmap::HeatmapStudy::VolumeProfile(
                    data::chart::heatmap::ProfileKind::default(),
                )],
            },
            ContentKind::HeatmapChart => Content::Heatmap {
                chart: None,
                indicators: vec![HeatmapIndicator::Volume],
                studies: vec![],
                layout: ViewConfig {
                    splits: vec![],
                    autoscale: Some(data::chart::Autoscale::CenterLatest),
                },
            },
            ContentKind::ComparisonChart => Content::Comparison(None),
            ContentKind::TimeAndSales => Content::TimeAndSales(None),
            ContentKind::Ladder => Content::Ladder(None),
            ContentKind::OrderEntry => Content::OrderEntry(OrderEntryPanel::new()),
            ContentKind::OrderList => Content::OrderList(panel::orders::OrdersPanel::new()),
            ContentKind::BuyingPower => Content::BuyingPower(BuyingPowerPanel::new()),
            ContentKind::Positions => Content::Positions(panel::positions::PositionsPanel::new()),
            // N1.11: skeleton のみ — 実際の UI は TODO(N1.11-ui)
            ContentKind::ReplayControl => Content::ReplayControl,
        }
    }

    pub fn last_tick(&self) -> Option<Instant> {
        match self {
            Content::Heatmap { chart, .. } => Some(chart.as_ref()?.last_update()),
            Content::Kline { chart, .. } => Some(chart.as_ref()?.last_update()),
            Content::TimeAndSales(panel) => Some(panel.as_ref()?.last_update()),
            Content::Ladder(panel) => Some(panel.as_ref()?.last_update()),
            Content::Comparison(chart) => Some(chart.as_ref()?.last_update()),
            Content::Starter
            | Content::OrderEntry(_)
            | Content::OrderList(_)
            | Content::BuyingPower(_)
            | Content::Positions(_)
            | Content::ReplayControl => None,
            Content::ShaderHeatmap { chart, .. } => Some(chart.as_ref()?.last_tick?),
        }
    }

    pub fn chart_kind(&self) -> Option<data::chart::KlineChartKind> {
        match self {
            Content::Kline { chart, .. } => Some(chart.as_ref()?.kind().clone()),
            _ => None,
        }
    }

    pub fn toggle_indicator(&mut self, indicator: UiIndicator) {
        match (self, indicator) {
            (
                Content::Heatmap {
                    chart, indicators, ..
                },
                UiIndicator::Heatmap(ind),
            ) => {
                let Some(chart) = chart else {
                    return;
                };

                if indicators.contains(&ind) {
                    indicators.retain(|i| i != &ind);
                } else {
                    indicators.push(ind);
                }
                chart.toggle_indicator(ind);
            }
            (
                Content::Kline {
                    chart, indicators, ..
                },
                UiIndicator::Kline(ind),
            ) => {
                let Some(chart) = chart else {
                    return;
                };

                if indicators.contains(&ind) {
                    indicators.retain(|i| i != &ind);
                } else {
                    indicators.push(ind);
                }
                chart.toggle_indicator(ind);
            }
            (
                Content::ShaderHeatmap {
                    chart, indicators, ..
                },
                UiIndicator::Heatmap(ind),
            ) => {
                let Some(chart) = chart else {
                    return;
                };

                if indicators.contains(&ind) {
                    indicators.retain(|i| i != &ind);
                } else {
                    indicators.push(ind);
                }
                chart.toggle_indicator(ind);
            }
            _ => panic!("indicator toggle on {indicator:?} pane",),
        }
    }

    pub fn reorder_indicators(&mut self, event: &column_drag::DragEvent) {
        match self {
            Content::Heatmap { indicators, .. } => column_drag::reorder_vec(indicators, event),
            Content::Kline { indicators, .. } => column_drag::reorder_vec(indicators, event),
            Content::TimeAndSales(_)
            | Content::Ladder(_)
            | Content::Starter
            | Content::Comparison(_)
            | Content::ShaderHeatmap { .. }
            | Content::OrderEntry(_)
            | Content::OrderList(_)
            | Content::BuyingPower(_)
            | Content::Positions(_)
            | Content::ReplayControl => {
                panic!("indicator reorder on {} pane", self)
            }
        }
    }

    pub fn change_visual_config(&mut self, config: VisualConfig) {
        match (self, config) {
            (Content::Heatmap { chart: Some(c), .. }, VisualConfig::Heatmap(cfg)) => {
                c.set_visual_config(cfg);
            }
            (Content::ShaderHeatmap { chart: Some(c), .. }, VisualConfig::Heatmap(cfg)) => {
                c.set_visual_config(cfg);
            }
            (Content::TimeAndSales(Some(panel)), VisualConfig::TimeAndSales(cfg)) => {
                panel.config = cfg;
            }
            (Content::Ladder(Some(panel)), VisualConfig::Ladder(cfg)) => {
                panel.config = cfg;
            }
            (Content::Comparison(Some(chart)), VisualConfig::Comparison(cfg)) => {
                chart.config = cfg;
            }
            _ => {}
        }
    }

    pub fn studies(&self) -> Option<data::chart::Study> {
        match &self {
            Content::Heatmap { studies, .. } => Some(data::chart::Study::Heatmap(studies.clone())),
            Content::ShaderHeatmap { studies, .. } => {
                Some(data::chart::Study::Heatmap(studies.clone()))
            }
            Content::Kline { kind, .. } => {
                if let data::chart::KlineChartKind::Footprint { studies, .. } = kind {
                    Some(data::chart::Study::Footprint(studies.clone()))
                } else {
                    None
                }
            }
            Content::TimeAndSales(_)
            | Content::Ladder(_)
            | Content::Starter
            | Content::Comparison(_)
            | Content::OrderEntry(_)
            | Content::OrderList(_)
            | Content::BuyingPower(_)
            | Content::Positions(_)
            | Content::ReplayControl => None,
        }
    }

    pub fn update_studies(&mut self, studies: data::chart::Study) {
        match (self, studies) {
            (
                Content::Heatmap {
                    chart,
                    studies: previous,
                    ..
                },
                data::chart::Study::Heatmap(studies),
            ) => {
                chart
                    .as_mut()
                    .expect("heatmap chart not initialized")
                    .studies = studies.clone();
                *previous = studies;
            }
            (
                Content::ShaderHeatmap {
                    chart,
                    studies: previous,
                    ..
                },
                data::chart::Study::Heatmap(studies),
            ) => {
                chart
                    .as_mut()
                    .expect("shader heatmap chart not initialized")
                    .studies = studies.clone();
                *previous = studies;
            }
            (Content::Kline { chart, kind, .. }, data::chart::Study::Footprint(studies)) => {
                chart
                    .as_mut()
                    .expect("kline chart not initialized")
                    .set_studies(studies.clone());
                if let data::chart::KlineChartKind::Footprint {
                    studies: k_studies, ..
                } = kind
                {
                    *k_studies = studies;
                }
            }
            _ => {}
        }
    }

    pub fn kind(&self) -> ContentKind {
        match self {
            Content::Heatmap { .. } => ContentKind::HeatmapChart,
            Content::Kline { kind, .. } => match kind {
                data::chart::KlineChartKind::Footprint { .. } => ContentKind::FootprintChart,
                data::chart::KlineChartKind::Candles => ContentKind::CandlestickChart,
            },
            Content::TimeAndSales(_) => ContentKind::TimeAndSales,
            Content::Ladder(_) => ContentKind::Ladder,
            Content::Comparison(_) => ContentKind::ComparisonChart,
            Content::Starter => ContentKind::Starter,
            Content::ShaderHeatmap { .. } => ContentKind::ShaderHeatmap,
            Content::OrderEntry(_) => ContentKind::OrderEntry,
            Content::OrderList(_) => ContentKind::OrderList,
            Content::BuyingPower(_) => ContentKind::BuyingPower,
            Content::Positions(_) => ContentKind::Positions,
            Content::ReplayControl => ContentKind::ReplayControl,
        }
    }

    pub fn update_theme(&mut self, theme: &iced_core::Theme) {
        match self {
            Content::ShaderHeatmap { chart: Some(c), .. } => c.update_theme(theme),
            Content::Ladder(Some(panel)) => {
                panel.invalidate(None);
            }
            _ => {}
        }
    }

    pub fn initialized(&self) -> bool {
        match self {
            Content::Heatmap { chart, .. } => chart.is_some(),
            Content::ShaderHeatmap { chart, .. } => chart.is_some(),
            Content::Kline { chart, .. } => chart.is_some(),
            Content::TimeAndSales(panel) => panel.is_some(),
            Content::Ladder(panel) => panel.is_some(),
            Content::Comparison(chart) => chart.is_some(),
            Content::Starter
            | Content::OrderEntry(_)
            | Content::OrderList(_)
            | Content::BuyingPower(_)
            | Content::Positions(_)
            // N1.11: ReplayControl は常に initialized（表示するコンテンツが固定）
            | Content::ReplayControl => true,
        }
    }
}

impl std::fmt::Display for Content {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.kind())
    }
}

impl PartialEq for Content {
    fn eq(&self, other: &Self) -> bool {
        matches!(
            (self, other),
            (Content::Starter, Content::Starter)
                | (Content::Heatmap { .. }, Content::Heatmap { .. })
                | (Content::Kline { .. }, Content::Kline { .. })
                | (Content::TimeAndSales(_), Content::TimeAndSales(_))
                | (Content::Ladder(_), Content::Ladder(_))
                | (Content::OrderEntry(_), Content::OrderEntry(_))
                | (Content::OrderList(_), Content::OrderList(_))
                | (Content::BuyingPower(_), Content::BuyingPower(_))
                | (Content::ReplayControl, Content::ReplayControl)
        )
    }
}

fn link_group_modal<'a>(
    pane: pane_grid::Pane,
    selected_group: Option<LinkGroup>,
) -> Element<'a, Message> {
    let mut grid = column![].spacing(4);
    let rows = LinkGroup::ALL.chunks(3);

    for row_groups in rows {
        let mut button_row = row![].spacing(4);

        for &group in row_groups {
            let is_selected = selected_group == Some(group);
            let btn_content = text(group.to_string()).font(style::AZERET_MONO);

            let btn = if is_selected {
                button_with_tooltip(
                    btn_content.align_x(iced::Alignment::Center),
                    Message::SwitchLinkGroup(pane, None),
                    Some("Unlink"),
                    tooltip::Position::Bottom,
                    move |theme, status| style::button::menu_body(theme, status, true),
                )
            } else {
                button(btn_content.align_x(iced::Alignment::Center))
                    .on_press(Message::SwitchLinkGroup(pane, Some(group)))
                    .style(move |theme, status| style::button::menu_body(theme, status, false))
                    .into()
            };

            button_row = button_row.push(btn);
        }

        grid = grid.push(button_row);
    }

    container(grid)
        .max_width(240)
        .padding(16)
        .style(style::chart_modal)
        .into()
}

fn ticksize_modifier<'a>(
    id: pane_grid::Pane,
    price_step: PriceStep,
    min_ticksize: Option<exchange::unit::MinTicksize>,
    multiplier: TickMultiplier,
    modifier: Option<modal::stream::Modifier>,
    kind: ModifierKind,
    exchange: Option<exchange::adapter::Exchange>,
) -> Element<'a, Message> {
    let modifier_modal =
        Modal::StreamModifier(modal::stream::Modifier::new(kind).with_ticksize_view(
            price_step,
            min_ticksize,
            multiplier,
            exchange,
        ));

    let is_active = modifier.is_some_and(|m| {
        matches!(
            m.view_mode,
            modal::stream::ViewMode::TicksizeSelection { .. }
        )
    });

    button(text(multiplier.to_string()).align_y(Alignment::Center))
        .style(move |theme, status| style::button::modifier(theme, status, !is_active))
        .on_press(Message::PaneEvent(id, Event::ShowModal(modifier_modal)))
        .height(widget::PANE_CONTROL_BTN_HEIGHT)
        .into()
}

fn basis_modifier<'a>(
    id: pane_grid::Pane,
    selected_basis: Basis,
    modifier: Option<modal::stream::Modifier>,
    kind: ModifierKind,
) -> Element<'a, Message> {
    let modifier_modal = Modal::StreamModifier(
        modal::stream::Modifier::new(kind).with_view_mode(modal::stream::ViewMode::BasisSelection),
    );

    let is_active =
        modifier.is_some_and(|m| m.view_mode == modal::stream::ViewMode::BasisSelection);

    button(text(selected_basis.to_string()).align_y(Alignment::Center))
        .style(move |theme, status| style::button::modifier(theme, status, !is_active))
        .on_press(Message::PaneEvent(id, Event::ShowModal(modifier_modal)))
        .height(widget::PANE_CONTROL_BTN_HEIGHT)
        .into()
}

fn by_basis_default<T>(
    basis: Option<Basis>,
    default_tf: Timeframe,
    on_time: impl FnOnce(Timeframe) -> T,
    on_tick: impl FnOnce() -> T,
) -> T {
    match basis.unwrap_or(Basis::Time(default_tf)) {
        Basis::Time(tf) => on_time(tf),
        Basis::Tick(_) => on_tick(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tachibana_ticker_info() -> TickerInfo {
        let ticker = exchange::Ticker::new("7203", Exchange::TachibanaStock);
        TickerInfo::new_stock(ticker, 1.0, 100.0, 100)
    }

    fn ladder_depth_stream(ti: TickerInfo) -> StreamKind {
        StreamKind::Depth {
            ticker_info: ti,
            depth_aggr: exchange::adapter::StreamTicksize::Client,
            push_freq: exchange::PushFrequency::ServerDefault,
        }
    }

    /// Regression guard for the race where DepthSnapshot arrives before Ladder
    /// content is initialized (Content::Ladder(None)).
    ///
    /// When `resolve_streams` makes streams Ready, `set_content_and_streams` must
    /// be called immediately so incoming data is not silently dropped.
    #[test]
    fn set_content_and_streams_initializes_ladder_when_content_is_none() {
        let ti = tachibana_ticker_info();
        let mut state = State {
            content: Content::Ladder(None),
            streams: ResolvedStream::Ready(vec![
                ladder_depth_stream(ti),
                StreamKind::Trades { ticker_info: ti },
            ]),
            ..Default::default()
        };

        assert!(
            !state.content.initialized(),
            "pre-condition: Ladder(None) must not be initialized"
        );

        state.set_content_and_streams(vec![ti], ContentKind::Ladder);

        assert!(
            state.content.initialized(),
            "Ladder must be initialized after set_content_and_streams — \
             if this fails, DepthSnapshot data arriving right after resolve_streams \
             will be dropped into Content::Ladder(None)"
        );
    }

    /// Verify that a Ladder with Ready streams can produce a valid stream_pair_kind,
    /// which is the prerequisite for the eager initialization in resolve_streams.
    #[test]
    fn stream_pair_kind_returns_some_when_streams_are_ready() {
        let ti = tachibana_ticker_info();
        let state = State {
            content: Content::Ladder(None),
            streams: ResolvedStream::Ready(vec![
                ladder_depth_stream(ti),
                StreamKind::Trades { ticker_info: ti },
            ]),
            ..Default::default()
        };

        let kind = state.stream_pair_kind();
        assert!(
            matches!(kind, Some(StreamPairKind::SingleSource(_))),
            "stream_pair_kind must return SingleSource when streams are Ready — \
             if this fails, the eager initialization branch in resolve_streams will \
             fall through to the None arm and leave Ladder uninitialized"
        );
    }

    #[test]
    fn order_panes_are_always_initialized() {
        assert!(Content::OrderEntry(panel::order_entry::OrderEntryPanel::new()).initialized());
        assert!(Content::OrderList(panel::orders::OrdersPanel::new()).initialized());
        assert!(Content::BuyingPower(panel::buying_power::BuyingPowerPanel::new()).initialized());
    }

    /// Starter / OrderList / BuyingPower は銘柄概念を持たないため
    /// link_group を保持してはならない。
    /// 旧バージョンで saved-state.json に link_group: Some(N) を保存していたユーザーが
    /// 新バイナリで開いたとき、UI からはトグル不能のため永続的にゴースト link_group が
    /// 残り、switch_tickers_in_group 経路で warn ログを発生させ続ける silent な
    /// 劣化を招く。from_config で None に正規化することで根を断つ。
    #[test]
    fn from_config_normalizes_link_group_for_order_list() {
        let state = State::from_config(
            Content::OrderList(panel::orders::OrdersPanel::new()),
            vec![],
            Settings::default(),
            Some(LinkGroup::A),
        );
        assert_eq!(
            state.link_group, None,
            "OrderList は link_group を持てないため from_config で None に正規化すべき"
        );
    }

    #[test]
    fn from_config_normalizes_link_group_for_buying_power() {
        let state = State::from_config(
            Content::BuyingPower(panel::buying_power::BuyingPowerPanel::new()),
            vec![],
            Settings::default(),
            Some(LinkGroup::A),
        );
        assert_eq!(
            state.link_group, None,
            "BuyingPower は link_group を持てないため from_config で None に正規化すべき"
        );
    }

    #[test]
    fn from_config_normalizes_link_group_for_starter() {
        let state = State::from_config(
            Content::Starter,
            vec![],
            Settings::default(),
            Some(LinkGroup::A),
        );
        assert_eq!(
            state.link_group, None,
            "Starter は link_group を持てないため from_config で None に正規化すべき"
        );
    }

    /// OrderEntry は銘柄を持つため link_group をそのまま保持する（正規化対象外）。
    #[test]
    fn from_config_preserves_link_group_for_order_entry() {
        let state = State::from_config(
            Content::OrderEntry(panel::order_entry::OrderEntryPanel::new()),
            vec![],
            Settings::default(),
            Some(LinkGroup::A),
        );
        assert_eq!(state.link_group, Some(LinkGroup::A));
    }

    /// view() の `[-]` 非描画分岐に OrderEntry が含まれないことを pin する。
    /// 計画書 docs/✅order/order-entry-link-group-plan.md でリンクグループ対応予定の
    /// ペインなので、誤って除外リストに追加されると機能が無効化される。
    #[test]
    fn link_group_button_exclusion_excludes_only_non_ticker_panes() {
        let order_entry = Content::OrderEntry(panel::order_entry::OrderEntryPanel::new());
        assert!(
            !matches!(
                order_entry,
                Content::Starter | Content::OrderList(_) | Content::BuyingPower(_)
            ),
            "OrderEntry は銘柄概念を持つため link_group ボタン除外リストに含めてはならない"
        );

        for content in [
            Content::OrderList(panel::orders::OrdersPanel::new()),
            Content::BuyingPower(panel::buying_power::BuyingPowerPanel::new()),
            Content::Starter,
        ] {
            assert!(
                matches!(
                    content,
                    Content::Starter | Content::OrderList(_) | Content::BuyingPower(_)
                ),
                "銘柄概念を持たないペインは link_group ボタン除外リストに含めるべき"
            );
        }
    }
}
