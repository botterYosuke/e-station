use super::Message;
use crate::style;
use data::panel::ladder::{ChaseTracker, Config, GroupedDepth, Side, TradeStore};
use exchange::Trade;
use exchange::unit::qty::Qty;
use exchange::unit::{MinTicksize, Price, PriceStep};
use exchange::{TickerInfo, depth::Depth};

use iced::widget::canvas::{self, Path, Stroke, Text};
use iced::{Alignment, Event, Point, Rectangle, Renderer, Size, Theme, mouse};

use std::collections::BTreeMap;
use std::time::{Duration, Instant};

const TEXT_SIZE: f32 = 11.0;
const ROW_HEIGHT: f32 = 16.0;
// Currently equal to ROW_HEIGHT; kept as a separate constant so a future change
// to row height doesn't silently alter the header geometry.
const HEADER_HEIGHT: f32 = 16.0;

// Total width ratios must sum to 1.0
/// Uses half of the width for each side of the order quantity columns
const ORDER_QTY_COLS_WIDTH: f32 = 0.60;
/// Uses half of the width for each side of the trade quantity columns
const TRADE_QTY_COLS_WIDTH: f32 = 0.20;

const COL_PADDING: f32 = 4.0;
/// Used for calculating layout with texts inside the price column
const MONO_CHAR_ADVANCE: f32 = 0.62;
/// Minimum padding on each side of the price text inside the price column
const PRICE_TEXT_SIDE_PAD_MIN: f32 = 12.0;

const CHASE_CIRCLE_RADIUS: f32 = 4.0;
/// Maximum interval between chase updates to consider them part of the same chase
const CHASE_MIN_INTERVAL: Duration = Duration::from_millis(200);

/// Returns the top Y offset (relative to anchor_mid_y) for a logical row index.
/// idx=0: spread/divider row; idx<0: ask rows; idx>0: bid rows.
fn row_top_y(idx: i32) -> f32 {
    (idx as f32) * ROW_HEIGHT - ROW_HEIGHT * 0.5
}

/// Returns the screen center Y for a price using the VisibleBook.
/// Checks the visible price_centers cache first; falls back to off-screen extrapolation
/// via price_offsets so that chase trails remain drawable outside the viewport.
fn price_to_screen_y(price: Price, visible: &VisibleBook) -> Option<f32> {
    if let Some(&y) = visible.price_centers.get(&price) {
        return Some(y);
    }
    visible
        .price_offsets
        .get(&price)
        .map(|&offset| visible.anchor_mid_y + (offset as f32) * ROW_HEIGHT)
}

/// Pure function for the spread label string (testable without canvas access).
pub fn format_spread_label(spread: Price, min_ticksize: MinTicksize) -> String {
    let spread = spread.round_to_min_tick(min_ticksize);
    format!("Spread: {}", spread.to_string(min_ticksize))
}

impl super::Panel for Ladder {
    fn scroll(&mut self, delta: f32) {
        self.scroll_px += delta;
        Ladder::invalidate(self, Some(Instant::now()));
    }

    fn reset_scroll(&mut self) {
        self.scroll_px = 0.0;
        Ladder::invalidate(self, Some(Instant::now()));
    }

    fn invalidate(&mut self, now: Option<Instant>) -> Option<super::Action> {
        Ladder::invalidate(self, now)
    }

    fn is_empty(&self) -> bool {
        if self.pending_tick_size.is_some() {
            return true;
        }
        self.grouped_asks().is_empty() && self.grouped_bids().is_empty() && self.trades.is_empty()
    }
}

pub struct Ladder {
    ticker_info: TickerInfo,
    pub config: Config,
    cache: canvas::Cache,
    header_cache: canvas::Cache,
    last_tick: Instant,
    pub step: PriceStep,
    scroll_px: f32,
    last_exchange_ts_ms: Option<u64>,
    orderbook: [GroupedDepth; 2],
    trades: TradeStore,
    pending_tick_size: Option<PriceStep>,
    raw_price_spread: Option<Price>,
}

impl Ladder {
    pub fn new(config: Option<Config>, ticker_info: TickerInfo, step: PriceStep) -> Self {
        Self {
            trades: TradeStore::new(),
            config: config.unwrap_or_default(),
            ticker_info,
            cache: canvas::Cache::default(),
            header_cache: canvas::Cache::default(),
            last_tick: Instant::now(),
            step,
            scroll_px: 0.0,
            last_exchange_ts_ms: None,
            orderbook: [GroupedDepth::new(), GroupedDepth::new()],
            raw_price_spread: None,
            pending_tick_size: None,
        }
    }

    pub fn insert_trades(&mut self, buffer: &[Trade]) {
        self.trades.insert_trades(buffer, self.step);
    }

    pub fn insert_depth(&mut self, depth: &Depth, update_t: u64) {
        if let Some(next) = self.pending_tick_size.take() {
            self.step = next;
            self.trades.rebuild_grouped(self.step);
        }

        let raw_best_bid = depth.bids.last_key_value().map(|(p, _)| *p);
        let raw_best_ask = depth.asks.first_key_value().map(|(p, _)| *p);
        self.raw_price_spread = match (raw_best_bid, raw_best_ask) {
            (Some(bid), Some(ask)) => Some(ask - bid),
            _ => None,
        };

        if self.config.show_chase_tracker {
            let max_int = CHASE_MIN_INTERVAL;
            self.chase_tracker_mut(Side::Bid)
                .update(raw_best_bid, true, update_t, max_int);
            self.chase_tracker_mut(Side::Ask)
                .update(raw_best_ask, false, update_t, max_int);
        } else {
            self.chase_tracker_mut(Side::Bid).reset();
            self.chase_tracker_mut(Side::Ask).reset();
        }

        if self
            .trades
            .maybe_cleanup(update_t, self.config.trade_retention, self.step)
        {
            self.invalidate(Some(Instant::now()));
        }

        self.regroup_from_depth(depth);
        self.last_exchange_ts_ms = Some(update_t);
    }

    fn trade_qty_at(&self, price: Price) -> (Qty, Qty) {
        self.trades.trade_qty_at(price)
    }

    pub fn last_update(&self) -> Instant {
        self.last_tick
    }

    fn grouped_asks(&self) -> &BTreeMap<Price, Qty> {
        &self.orderbook[Side::Ask.idx()].orders
    }

    fn grouped_bids(&self) -> &BTreeMap<Price, Qty> {
        &self.orderbook[Side::Bid.idx()].orders
    }

    fn chase_tracker(&self, side: Side) -> &ChaseTracker {
        &self.orderbook[side.idx()].chase
    }

    fn chase_tracker_mut(&mut self, side: Side) -> &mut ChaseTracker {
        &mut self.orderbook[side.idx()].chase
    }

    pub fn min_tick_size(&self) -> f32 {
        self.ticker_info.min_ticksize.into()
    }

    pub fn set_tick_size(&mut self, step: PriceStep) {
        self.pending_tick_size = Some(step);
        self.invalidate(Some(Instant::now()));
    }

    pub fn set_show_chase_tracker(&mut self, enabled: bool) {
        if self.config.show_chase_tracker != enabled {
            self.config.show_chase_tracker = enabled;
            if !enabled {
                self.chase_tracker_mut(Side::Bid).reset();
                self.chase_tracker_mut(Side::Ask).reset();
            }

            self.invalidate(Some(Instant::now()));
        }
    }

    /// Store raw depth prices directly — no step rounding.
    /// step is used only for TradeStore grouping.
    fn regroup_from_depth(&mut self, depth: &Depth) {
        self.orderbook[Side::Ask.idx()].copy_raw(&depth.asks);
        self.orderbook[Side::Bid.idx()].copy_raw(&depth.bids);
    }

    pub fn invalidate(&mut self, now: Option<Instant>) -> Option<super::Action> {
        self.cache.clear();
        self.header_cache.clear();
        if let Some(now) = now {
            self.last_tick = now;
        }
        None
    }

    fn format_price(&self, price: Price) -> String {
        let precision = self.ticker_info.min_ticksize;
        price.to_string(precision)
    }

    fn format_quantity(&self, qty: Qty) -> String {
        data::util::abbr_large_numbers(qty.to_f32_lossy())
    }
}

impl canvas::Program<Message> for Ladder {
    type State = ();

    fn update(
        &self,
        _state: &mut Self::State,
        event: &iced::Event,
        bounds: iced::Rectangle,
        cursor: iced_core::mouse::Cursor,
    ) -> Option<canvas::Action<Message>> {
        let _cursor_position = cursor.position_in(bounds)?;

        match event {
            Event::Mouse(mouse::Event::ButtonPressed(
                mouse::Button::Middle | mouse::Button::Left | mouse::Button::Right,
            )) => Some(canvas::Action::publish(Message::ResetScroll).and_capture()),
            Event::Mouse(mouse::Event::WheelScrolled { delta }) => {
                let scroll_amount = match delta {
                    mouse::ScrollDelta::Lines { y, .. } => -(*y) * ROW_HEIGHT,
                    mouse::ScrollDelta::Pixels { y, .. } => -*y,
                };

                Some(canvas::Action::publish(Message::Scrolled(scroll_amount)).and_capture())
            }
            _ => None,
        }
    }

    fn draw(
        &self,
        _state: &Self::State,
        renderer: &Renderer,
        theme: &Theme,
        bounds: Rectangle,
        _cursor: iced_core::mouse::Cursor,
    ) -> Vec<iced::widget::canvas::Geometry<Renderer>> {
        let palette = theme.extended_palette();

        let text_color = palette.background.base.text;
        let bid_color = palette.success.base.color;
        let ask_color = palette.danger.base.color;

        let divider_color = style::split_ruler(theme).color;

        // Build sparse visible book from raw depth prices.
        // Returns None when depth is empty (trade-only state → empty display).
        let visible_book_opt: Option<VisibleBook> = self.build_visible_book(bounds);
        let price_layout_opt: Option<(PriceLayout, ColumnRanges)> =
            visible_book_opt.as_ref().map(|vb| {
                let layout = self.price_layout_for_book(bounds.width, vb);
                let cols = self.column_ranges(bounds.width, layout.price_px);
                (layout, cols)
            });
        let cols_opt: Option<ColumnRanges> = price_layout_opt.map(|(_, c)| c);

        let label_color = palette.background.base.text.scale_alpha(0.55);

        let orderbook_visual = self.cache.draw(renderer, bounds.size(), |frame| {
            if let (Some(vb), Some((layout, cols))) = (visible_book_opt, price_layout_opt) {
                let mut spread_row: Option<(f32, f32)> = None;
                let mut best_bid_y: Option<f32> = None;
                let mut best_ask_y: Option<f32> = None;

                for visible_row in vb.rows.iter() {
                    // Skip rows whose text centre falls inside the header zone;
                    // fill_text renders above canvas fill_rectangle layers in iced,
                    // so the header's opaque background alone cannot suppress them.
                    if visible_row.y + ROW_HEIGHT / 2.0 < HEADER_HEIGHT {
                        continue;
                    }

                    match visible_row.row {
                        DomRow::Ask { price, .. } if Some(price) == vb.best_ask => {
                            best_ask_y = Some(visible_row.y);
                        }
                        DomRow::Bid { price, .. } if Some(price) == vb.best_bid => {
                            best_bid_y = Some(visible_row.y);
                        }
                        _ => {}
                    }

                    match visible_row.row {
                        DomRow::Ask { price, qty } => {
                            self.draw_row(
                                frame,
                                visible_row.y,
                                price,
                                qty,
                                false,
                                ask_color,
                                text_color,
                                vb.maxima.vis_max_order_qty,
                                visible_row.buy_t,
                                visible_row.sell_t,
                                vb.maxima.vis_max_trade_qty,
                                bid_color,
                                ask_color,
                                &cols,
                            );
                        }
                        DomRow::Bid { price, qty } => {
                            self.draw_row(
                                frame,
                                visible_row.y,
                                price,
                                qty,
                                true,
                                bid_color,
                                text_color,
                                vb.maxima.vis_max_order_qty,
                                visible_row.buy_t,
                                visible_row.sell_t,
                                vb.maxima.vis_max_trade_qty,
                                bid_color,
                                ask_color,
                                &cols,
                            );
                        }
                        DomRow::Spread => {
                            if let Some(spread) = self.raw_price_spread {
                                let min_ticksize = self.ticker_info.min_ticksize;
                                spread_row = Some((visible_row.y, visible_row.y + ROW_HEIGHT));

                                let content = format_spread_label(spread, min_ticksize);
                                frame.fill_text(Text {
                                    content,
                                    position: Point::new(
                                        bounds.width / 2.0,
                                        visible_row.y + ROW_HEIGHT / 2.0,
                                    ),
                                    color: palette.secondary.strong.color,
                                    size: (TEXT_SIZE - 1.0).into(),
                                    font: style::AZERET_MONO,
                                    align_x: Alignment::Center.into(),
                                    align_y: Alignment::Center.into(),
                                    ..Default::default()
                                });
                            }
                        }
                        DomRow::CenterDivider => {
                            let y_mid = visible_row.y + ROW_HEIGHT / 2.0 - 0.5;

                            frame.fill_rectangle(
                                Point::new(0.0, y_mid),
                                Size::new(bounds.width, 1.0),
                                divider_color,
                            );
                        }
                    }
                }

                if self.config.show_chase_tracker {
                    let left_gap_mid_x = cols.sell.1 + (layout.inside_pad_px + COL_PADDING) * 0.5;
                    let right_gap_mid_x = cols.buy.0 - (layout.inside_pad_px + COL_PADDING) * 0.5;

                    self.draw_chase_trail(
                        frame,
                        &vb,
                        self.chase_tracker(Side::Bid),
                        right_gap_mid_x,
                        best_ask_y.map(|y| y + ROW_HEIGHT / 2.0),
                        palette.success.weak.color,
                    );
                    self.draw_chase_trail(
                        frame,
                        &vb,
                        self.chase_tracker(Side::Ask),
                        left_gap_mid_x,
                        best_bid_y.map(|y| y + ROW_HEIGHT / 2.0),
                        palette.danger.weak.color,
                    );
                }

                // Price column vertical dividers (start below header, gap over spread row).
                // When the spread row scrolls above HEADER_HEIGHT, the top segment is
                // intentionally omitted — the header overlay covers that region.
                let mut draw_vsplit = |x: f32, gap: Option<(f32, f32)>| {
                    let x = x.floor() + 0.5;
                    match gap {
                        Some((top, bottom)) => {
                            if top > HEADER_HEIGHT {
                                frame.fill_rectangle(
                                    Point::new(x, HEADER_HEIGHT),
                                    Size::new(1.0, top - HEADER_HEIGHT),
                                    divider_color,
                                );
                            }
                            if bottom < bounds.height {
                                frame.fill_rectangle(
                                    Point::new(x, bottom),
                                    Size::new(1.0, (bounds.height - bottom).max(0.0)),
                                    divider_color,
                                );
                            }
                        }
                        None => {
                            frame.fill_rectangle(
                                Point::new(x, HEADER_HEIGHT),
                                Size::new(1.0, (bounds.height - HEADER_HEIGHT).max(0.0)),
                                divider_color,
                            );
                        }
                    }
                };
                draw_vsplit(cols.sell.1, spread_row);
                draw_vsplit(cols.buy.0, spread_row);

                if let Some((top, bottom)) = spread_row {
                    let y_top: f32 = top.floor() + 0.5;
                    let y_bot = bottom.floor() + 0.5;

                    frame.fill_rectangle(
                        Point::new(0.0, y_top),
                        Size::new(cols.sell.1, 1.0),
                        divider_color,
                    );
                    frame.fill_rectangle(
                        Point::new(0.0, y_bot),
                        Size::new(cols.sell.1, 1.0),
                        divider_color,
                    );

                    frame.fill_rectangle(
                        Point::new(cols.buy.0, y_top),
                        Size::new(bounds.width - cols.buy.0, 1.0),
                        divider_color,
                    );
                    frame.fill_rectangle(
                        Point::new(cols.buy.0, y_bot),
                        Size::new(bounds.width - cols.buy.0, 1.0),
                        divider_color,
                    );
                }
            }
        });

        let header_geo = self
            .header_cache
            .draw(renderer, bounds.size(), move |frame| {
                let bg = palette.background.base.color;
                frame.fill_rectangle(
                    Point::new(0.0, 0.0),
                    Size::new(bounds.width, HEADER_HEIGHT),
                    bg,
                );
                frame.fill_rectangle(
                    Point::new(0.0, HEADER_HEIGHT - 1.0),
                    Size::new(bounds.width, 1.0),
                    divider_color,
                );

                if let Some(cols) = cols_opt {
                    let labels: &[(&str, f32, Alignment)] = &[
                        ("買板", cols.bid_order.0 + 6.0, Alignment::Start),
                        ("売T", cols.sell.1 - 6.0, Alignment::End),
                        (
                            "価格",
                            (cols.price.0 + cols.price.1) * 0.5,
                            Alignment::Center,
                        ),
                        ("買T", cols.buy.0 + 6.0, Alignment::Start),
                        ("売板", cols.ask_order.1 - 6.0, Alignment::End),
                    ];
                    for &(label, x, align) in labels {
                        Self::draw_cell_text(frame, label, x, 0.0, label_color, align);
                    }
                }
            });

        vec![orderbook_visual, header_geo]
    }
}

#[derive(Default)]
struct Maxima {
    vis_max_order_qty: f32,
    vis_max_trade_qty: f32,
}

struct VisibleRow {
    row: DomRow,
    y: f32,
    buy_t: Qty,
    sell_t: Qty,
}

/// Intermediate model for sparse ladder display.
///
/// `rows` contains only prices present in raw depth (no step-interpolated ghost rows).
/// `price_centers` caches visible row center Y values for O(log n) lookup.
/// `price_offsets` covers the full raw depth (including off-screen rows) for chase
/// trail extrapolation — allows drawing trails that start outside the viewport.
struct VisibleBook {
    rows: Vec<VisibleRow>,
    price_centers: BTreeMap<Price, f32>,
    price_offsets: BTreeMap<Price, i32>,
    anchor_mid_y: f32,
    best_bid: Option<Price>,
    best_ask: Option<Price>,
    maxima: Maxima,
}

#[derive(Debug, Clone, Copy)]
struct ColumnRanges {
    bid_order: (f32, f32),
    sell: (f32, f32),
    price: (f32, f32),
    buy: (f32, f32),
    ask_order: (f32, f32),
}

#[derive(Debug, Clone, Copy)]
struct PriceLayout {
    price_px: f32,
    inside_pad_px: f32,
}

impl Ladder {
    // [BidOrderQty][SellQty][ Price ][BuyQty][AskOrderQty]
    const NUMBER_OF_COLUMN_GAPS: f32 = 4.0;

    fn price_sample_text_for_book(&self, vb: &VisibleBook) -> String {
        let a = vb
            .best_ask
            .map(|p| self.format_price(p))
            .unwrap_or_default();
        let b = vb
            .best_bid
            .map(|p| self.format_price(p))
            .unwrap_or_default();
        if a.len() >= b.len() { a } else { b }
    }

    fn mono_text_width_px(text_len: usize) -> f32 {
        (text_len as f32) * TEXT_SIZE * MONO_CHAR_ADVANCE
    }

    fn price_layout_for_book(&self, total_width: f32, vb: &VisibleBook) -> PriceLayout {
        let sample = self.price_sample_text_for_book(vb);
        let text_px = Self::mono_text_width_px(sample.len());

        let desired_total_gap = CHASE_CIRCLE_RADIUS * 2.0 + 4.0;
        let inside_pad_px = PRICE_TEXT_SIDE_PAD_MIN
            .max(desired_total_gap - COL_PADDING)
            .max(0.0);

        let price_px = (text_px + 2.0 * inside_pad_px).min(total_width.max(0.0));

        PriceLayout {
            price_px,
            inside_pad_px,
        }
    }

    fn column_ranges(&self, width: f32, price_px: f32) -> ColumnRanges {
        let total_gutter_width = COL_PADDING * Self::NUMBER_OF_COLUMN_GAPS;
        let usable_width = (width - total_gutter_width).max(0.0);

        let price_width = price_px.min(usable_width);

        let rest = (usable_width - price_width).max(0.0);
        let rest_ratio = ORDER_QTY_COLS_WIDTH + TRADE_QTY_COLS_WIDTH; // 0.80

        let order_share = if rest_ratio > 0.0 {
            (ORDER_QTY_COLS_WIDTH / rest_ratio) * rest
        } else {
            0.0
        };
        let trade_share = if rest_ratio > 0.0 {
            (TRADE_QTY_COLS_WIDTH / rest_ratio) * rest
        } else {
            0.0
        };

        let bid_order_width = order_share * 0.5;
        let sell_trades_width = trade_share * 0.5;
        let buy_trades_width = trade_share * 0.5;
        let ask_order_width = order_share * 0.5;

        let mut cursor_x = 0.0;

        let bid_order_end = cursor_x + bid_order_width;
        let bid_order_range = (cursor_x, bid_order_end);
        cursor_x = bid_order_end + COL_PADDING;

        let sell_trades_end = cursor_x + sell_trades_width;
        let sell_trades_range = (cursor_x, sell_trades_end);
        cursor_x = sell_trades_end + COL_PADDING;

        let price_end = cursor_x + price_width;
        let price_range = (cursor_x, price_end);
        cursor_x = price_end + COL_PADDING;

        let buy_trades_end = cursor_x + buy_trades_width;
        let buy_trades_range = (cursor_x, buy_trades_end);
        cursor_x = buy_trades_end + COL_PADDING;

        let ask_order_end = cursor_x + ask_order_width;
        let ask_order_range = (cursor_x, ask_order_end);

        ColumnRanges {
            bid_order: bid_order_range,
            sell: sell_trades_range,
            price: price_range,
            buy: buy_trades_range,
            ask_order: ask_order_range,
        }
    }

    /// Build the sparse visible book from raw depth prices.
    ///
    /// Returns None when both sides of the depth are empty (including the trade-only
    /// state), which triggers the empty-state display in the caller.
    ///
    /// Row layout uses logical index offsets:
    ///   idx=0  → spread/divider row at the vertical center
    ///   idx=-1 → best ask, idx=-2 → next ask (higher price), …
    ///   idx=+1 → best bid, idx=+2 → next bid (lower price), …
    fn build_visible_book(&self, bounds: Rectangle) -> Option<VisibleBook> {
        let asks = self.grouped_asks();
        let bids = self.grouped_bids();

        if asks.is_empty() && bids.is_empty() {
            return None;
        }

        let best_ask = asks.first_key_value().map(|(p, _)| *p);
        let best_bid = bids.last_key_value().map(|(p, _)| *p);

        let mid_screen_y = HEADER_HEIGHT + (bounds.height - HEADER_HEIGHT).max(0.0) * 0.5;
        let anchor_mid_y = mid_screen_y - self.scroll_px;

        // Build price_offsets for ALL depth rows (including off-screen) for chase trail extrapolation.
        // asks: sorted ascending; best_ask (lowest ask) → offset -1
        // bids: sorted ascending; best_bid (highest bid, last in BTreeMap) → offset +1
        let mut price_offsets: BTreeMap<Price, i32> = BTreeMap::new();
        for (i, (price, _)) in asks.iter().enumerate() {
            price_offsets.insert(*price, -(i as i32 + 1));
        }
        for (i, (price, _)) in bids.iter().rev().enumerate() {
            price_offsets.insert(*price, i as i32 + 1);
        }

        let mut rows: Vec<VisibleRow> = Vec::new();
        let mut price_centers: BTreeMap<Price, f32> = BTreeMap::new();
        let mut maxima = Maxima::default();

        // Spread/divider row at logical index 0
        {
            let top_y = anchor_mid_y + row_top_y(0);
            if top_y < bounds.height && top_y + ROW_HEIGHT > 0.0 {
                let row = if self.config.show_spread
                    && self.ticker_info.exchange().is_depth_client_aggr()
                {
                    DomRow::Spread
                } else {
                    DomRow::CenterDivider
                };
                rows.push(VisibleRow {
                    row,
                    y: top_y,
                    buy_t: Qty::default(),
                    sell_t: Qty::default(),
                });
            }
        }

        // Ask and bid rows from price_offsets
        for (&price, &offset) in &price_offsets {
            let top_y = anchor_mid_y + row_top_y(offset);
            if top_y >= bounds.height || top_y + ROW_HEIGHT <= 0.0 {
                continue;
            }

            let (order_qty, is_bid) = if offset < 0 {
                (asks.get(&price).copied().unwrap_or_default(), false)
            } else {
                (bids.get(&price).copied().unwrap_or_default(), true)
            };

            let (buy_t, sell_t) = self.trade_qty_at(price);
            maxima.vis_max_order_qty = maxima.vis_max_order_qty.max(f32::from(order_qty));
            maxima.vis_max_trade_qty = maxima
                .vis_max_trade_qty
                .max(f32::from(buy_t).max(f32::from(sell_t)));

            let center_y = top_y + ROW_HEIGHT / 2.0;
            price_centers.insert(price, center_y);

            let row = if is_bid {
                DomRow::Bid {
                    price,
                    qty: order_qty,
                }
            } else {
                DomRow::Ask {
                    price,
                    qty: order_qty,
                }
            };

            rows.push(VisibleRow {
                row,
                y: top_y,
                buy_t,
                sell_t,
            });
        }

        rows.sort_by(|a, b| a.y.total_cmp(&b.y));

        Some(VisibleBook {
            rows,
            price_centers,
            price_offsets,
            anchor_mid_y,
            best_bid,
            best_ask,
            maxima,
        })
    }

    fn draw_row(
        &self,
        frame: &mut iced::widget::canvas::Frame,
        y: f32,
        price: Price,
        order_qty: Qty,
        is_bid: bool,
        side_color: iced::Color,
        text_color: iced::Color,
        max_order_qty: f32,
        trade_buy_qty: Qty,
        trade_sell_qty: Qty,
        max_trade_qty: f32,
        trade_buy_color: iced::Color,
        trade_sell_color: iced::Color,
        cols: &ColumnRanges,
    ) {
        let order_qty_f32 = f32::from(order_qty);
        let trade_buy_qty_f32 = f32::from(trade_buy_qty);
        let trade_sell_qty_f32 = f32::from(trade_sell_qty);

        if is_bid {
            Self::fill_bar(
                frame,
                cols.bid_order,
                y,
                ROW_HEIGHT,
                order_qty_f32,
                max_order_qty,
                side_color,
                true,
                0.20,
            );
            let qty_txt = self.format_quantity(order_qty);
            let x_text = cols.bid_order.0 + 6.0;
            Self::draw_cell_text(frame, qty_txt, x_text, y, text_color, Alignment::Start);
        } else {
            Self::fill_bar(
                frame,
                cols.ask_order,
                y,
                ROW_HEIGHT,
                order_qty_f32,
                max_order_qty,
                side_color,
                false,
                0.20,
            );
            let qty_txt = self.format_quantity(order_qty);
            let x_text = cols.ask_order.1 - 6.0;
            Self::draw_cell_text(frame, qty_txt, x_text, y, text_color, Alignment::End);
        }

        // Sell trades (right-to-left)
        Self::fill_bar(
            frame,
            cols.sell,
            y,
            ROW_HEIGHT,
            trade_sell_qty_f32,
            max_trade_qty,
            trade_sell_color,
            false,
            0.30,
        );
        let sell_txt = if trade_sell_qty_f32 > 0.0 {
            self.format_quantity(trade_sell_qty)
        } else {
            "".into()
        };
        Self::draw_cell_text(
            frame,
            sell_txt,
            cols.sell.1 - 6.0,
            y,
            text_color,
            Alignment::End,
        );

        // Buy trades (left-to-right)
        Self::fill_bar(
            frame,
            cols.buy,
            y,
            ROW_HEIGHT,
            trade_buy_qty_f32,
            max_trade_qty,
            trade_buy_color,
            true,
            0.30,
        );
        let buy_txt = if trade_buy_qty_f32 > 0.0 {
            self.format_quantity(trade_buy_qty)
        } else {
            "".into()
        };
        Self::draw_cell_text(
            frame,
            buy_txt,
            cols.buy.0 + 6.0,
            y,
            text_color,
            Alignment::Start,
        );

        // Price
        let price_text = self.format_price(price);
        let price_x_center = (cols.price.0 + cols.price.1) * 0.5;
        Self::draw_cell_text(
            frame,
            price_text,
            price_x_center,
            y,
            side_color,
            Alignment::Center,
        );
    }

    fn fill_bar(
        frame: &mut iced::widget::canvas::Frame,
        (x_start, x_end): (f32, f32),
        y: f32,
        height: f32,
        value: f32,
        scale_value_max: f32,
        color: iced::Color,
        from_left: bool,
        alpha: f32,
    ) {
        if scale_value_max <= 0.0 || value <= 0.0 {
            return;
        }
        let col_width = x_end - x_start;

        let mut bar_width = (value / scale_value_max) * col_width.max(1.0);
        bar_width = bar_width.min(col_width);
        let bar_x = if from_left {
            x_start
        } else {
            x_end - bar_width
        };

        frame.fill_rectangle(
            Point::new(bar_x, y),
            Size::new(bar_width, height),
            iced::Color { a: alpha, ..color },
        );
    }

    fn draw_cell_text(
        frame: &mut iced::widget::canvas::Frame,
        text: impl Into<String>,
        x_anchor: f32,
        y: f32,
        color: iced::Color,
        align: Alignment,
    ) {
        frame.fill_text(Text {
            content: text.into(),
            position: Point::new(x_anchor, y + ROW_HEIGHT / 2.0),
            color,
            size: TEXT_SIZE.into(),
            font: style::AZERET_MONO,
            align_x: align.into(),
            align_y: Alignment::Center.into(),
            ..Default::default()
        });
    }

    fn draw_chase_trail(
        &self,
        frame: &mut iced::widget::canvas::Frame,
        visible: &VisibleBook,
        tracker: &ChaseTracker,
        pos_x: f32,
        best_offer_y: Option<f32>,
        color: iced::Color,
    ) {
        let radius = CHASE_CIRCLE_RADIUS;
        if let Some((start_p_raw, end_p_raw, alpha)) = tracker.segment() {
            let color = color.scale_alpha(alpha);
            let stroke_w = 2.0;
            let pad_to_circle = radius + stroke_w * 0.5;

            let start_y = price_to_screen_y(start_p_raw, visible);
            let end_y = price_to_screen_y(end_p_raw, visible).or(best_offer_y);

            if let Some(end_y) = end_y {
                if let Some(start_y) = start_y {
                    let dy = end_y - start_y;
                    if dy.abs() > pad_to_circle {
                        let line_end_y = end_y - dy.signum() * pad_to_circle;
                        let line_path =
                            Path::line(Point::new(pos_x, start_y), Point::new(pos_x, line_end_y));
                        frame.stroke(
                            &line_path,
                            Stroke::default().with_color(color).with_width(stroke_w),
                        );
                    }
                }

                let circle = &Path::circle(Point::new(pos_x, end_y), radius);
                frame.fill(circle, color);
            }
        }
    }
}

enum DomRow {
    Ask { price: Price, qty: Qty },
    Spread,
    CenterDivider,
    Bid { price: Price, qty: Qty },
}

#[cfg(test)]
mod tests {
    use super::*;
    use exchange::Trade;
    use exchange::adapter::Exchange;
    use exchange::{Ticker, TickerInfo};

    fn test_ticker_info() -> TickerInfo {
        let ticker = Ticker::new("7203", Exchange::TachibanaStock);
        TickerInfo::new_stock(ticker, 1.0, 100.0, 100)
    }

    fn default_step() -> PriceStep {
        PriceStep::from_f32_lossy(1.0)
    }

    fn test_bounds() -> iced::Rectangle {
        iced::Rectangle {
            x: 0.0,
            y: 0.0,
            width: 300.0,
            height: 400.0,
        }
    }

    #[test]
    fn mid_screen_y_is_offset_by_header_height() {
        let mut ladder = Ladder::new(None, test_ticker_info(), default_step());
        let step = default_step();
        let best_bid = Price::from_f32_lossy(1000.0);
        let best_ask = best_bid.add_steps(1, step);
        ladder.orderbook[Side::Bid.idx()]
            .orders
            .insert(best_bid, Qty::default());
        ladder.orderbook[Side::Ask.idx()]
            .orders
            .insert(best_ask, Qty::default());

        let bounds = iced::Rectangle {
            x: 0.0,
            y: 0.0,
            width: 300.0,
            height: 200.0,
        };
        let vb = ladder
            .build_visible_book(bounds)
            .expect("CenterDivider row must be present");
        let divider = vb
            .rows
            .iter()
            .find(|r| matches!(r.row, DomRow::CenterDivider))
            .expect("CenterDivider row must be present");
        let height = 200.0_f32;
        let mid = HEADER_HEIGHT + (height - HEADER_HEIGHT).max(0.0) * 0.5;
        let expected_y = mid + row_top_y(0);
        assert!(
            (divider.y - expected_y).abs() < 0.01,
            "expected divider y≈{expected_y}, got {}",
            divider.y
        );
    }

    #[test]
    fn build_price_grid_returns_none_when_empty() {
        let ladder = Ladder::new(None, test_ticker_info(), default_step());
        assert!(ladder.build_visible_book(test_bounds()).is_none());
    }

    #[test]
    fn narrow_pane_column_ranges_does_not_panic() {
        let ladder = Ladder::new(None, test_ticker_info(), default_step());
        for width in [0.0_f32, 1.0, 30.0, 60.0] {
            let _ = ladder.column_ranges(width, 20.0);
        }
    }

    // ── T3: Sparse display tests ─────────────────────────────────────────────

    #[test]
    fn test_format_spread_label() {
        // MinTicksize::new(-1) = 10^-1 = 0.1
        let min_ticksize = MinTicksize::new(-1);

        let spread = Price::from_f32_lossy(1.0);
        let label = format_spread_label(spread, min_ticksize);
        assert_eq!(label, "Spread: 1.0");

        let spread2 = Price::from_f32_lossy(2.5);
        let label2 = format_spread_label(spread2, min_ticksize);
        assert_eq!(label2, "Spread: 2.5");
    }

    #[test]
    fn test_ladder_sparse_no_empty_rows() {
        let mut ladder = Ladder::new(None, test_ticker_info(), default_step());

        let expected: std::collections::BTreeSet<Price> = [
            5375.0_f32, 5376.0, 5377.0, 5378.0, 5379.0, 5380.0, 5381.0, 5382.0, 5383.0, 5384.0,
        ]
        .iter()
        .map(|&f| Price::from_f32_lossy(f))
        .collect();

        for i in 0..5u32 {
            ladder.orderbook[Side::Bid.idx()].orders.insert(
                Price::from_f32_lossy(5379.0 - i as f32),
                Qty::from_f32_lossy(100.0),
            );
            ladder.orderbook[Side::Ask.idx()].orders.insert(
                Price::from_f32_lossy(5380.0 + i as f32),
                Qty::from_f32_lossy(100.0),
            );
        }

        let bounds = iced::Rectangle {
            x: 0.0,
            y: 0.0,
            width: 300.0,
            height: 400.0,
        };
        let vb = ladder.build_visible_book(bounds).expect("visible book");

        for row in &vb.rows {
            if let DomRow::Ask { price, .. } | DomRow::Bid { price, .. } = row.row {
                assert!(
                    expected.contains(&price),
                    "unexpected price {price:?} in rows — ghost row detected"
                );
            }
        }
    }

    #[test]
    fn test_ladder_sparse_bid_ask_order() {
        let mut ladder = Ladder::new(None, test_ticker_info(), default_step());

        for i in 0..3u32 {
            ladder.orderbook[Side::Bid.idx()].orders.insert(
                Price::from_f32_lossy(100.0 - i as f32),
                Qty::from_f32_lossy(100.0),
            );
            ladder.orderbook[Side::Ask.idx()].orders.insert(
                Price::from_f32_lossy(101.0 + i as f32),
                Qty::from_f32_lossy(100.0),
            );
        }

        let bounds = iced::Rectangle {
            x: 0.0,
            y: 0.0,
            width: 300.0,
            height: 600.0,
        };
        let vb = ladder.build_visible_book(bounds).expect("visible book");

        // All rows sorted by ascending Y
        let ys: Vec<f32> = vb.rows.iter().map(|r| r.y).collect();
        for w in ys.windows(2) {
            assert!(w[0] <= w[1], "rows not sorted by Y: {} > {}", w[0], w[1]);
        }

        // Ask rows (higher price, lower Y = higher on screen) appear before bid rows
        let ask_max_y = vb
            .rows
            .iter()
            .filter(|r| matches!(r.row, DomRow::Ask { .. }))
            .map(|r| r.y)
            .fold(f32::NEG_INFINITY, f32::max);
        let bid_min_y = vb
            .rows
            .iter()
            .filter(|r| matches!(r.row, DomRow::Bid { .. }))
            .map(|r| r.y)
            .fold(f32::INFINITY, f32::min);
        assert!(ask_max_y < bid_min_y, "asks should be above bids on screen");
    }

    #[test]
    fn test_ladder_sparse_spread_label() {
        let spread = Price::from_f32_lossy(1.0);
        let min_ticksize = MinTicksize::new(0); // 10^0 = 1.0
        let label = format_spread_label(spread, min_ticksize);
        assert!(label.starts_with("Spread: "), "label: {label}");
    }

    #[test]
    fn test_ladder_sparse_empty_book() {
        let ladder = Ladder::new(None, test_ticker_info(), default_step());
        assert!(ladder.build_visible_book(test_bounds()).is_none());
    }

    #[test]
    fn test_ladder_sparse_one_side_only() {
        let bounds = iced::Rectangle {
            x: 0.0,
            y: 0.0,
            width: 300.0,
            height: 400.0,
        };

        // Asks only
        let mut ladder = Ladder::new(None, test_ticker_info(), default_step());
        for i in 0..3u32 {
            ladder.orderbook[Side::Ask.idx()].orders.insert(
                Price::from_f32_lossy(101.0 + i as f32),
                Qty::from_f32_lossy(100.0),
            );
        }
        let vb = ladder
            .build_visible_book(bounds)
            .expect("visible book with asks only");
        assert!(
            !vb.rows.iter().any(|r| matches!(r.row, DomRow::Bid { .. })),
            "no bid rows expected when only asks present"
        );
        assert!(
            vb.rows.iter().any(|r| matches!(r.row, DomRow::Ask { .. })),
            "ask rows expected"
        );
        assert!(
            vb.rows
                .iter()
                .any(|r| matches!(r.row, DomRow::CenterDivider | DomRow::Spread)),
            "divider expected"
        );

        // Bids only
        let mut ladder2 = Ladder::new(None, test_ticker_info(), default_step());
        for i in 0..3u32 {
            ladder2.orderbook[Side::Bid.idx()].orders.insert(
                Price::from_f32_lossy(100.0 - i as f32),
                Qty::from_f32_lossy(100.0),
            );
        }
        let vb2 = ladder2
            .build_visible_book(bounds)
            .expect("visible book with bids only");
        assert!(
            !vb2.rows.iter().any(|r| matches!(r.row, DomRow::Ask { .. })),
            "no ask rows expected when only bids present"
        );
        assert!(
            vb2.rows.iter().any(|r| matches!(r.row, DomRow::Bid { .. })),
            "bid rows expected"
        );
    }

    #[test]
    fn test_ladder_sparse_trade_only_rows() {
        let mut ladder = Ladder::new(None, test_ticker_info(), default_step());

        // Insert a trade at a .5 price (the kind that caused ghost rows before)
        let trade = Trade {
            time: 1000,
            is_sell: false,
            price: Price::from_f32_lossy(5379.5),
            qty: Qty::from_f32_lossy(100.0),
        };
        ladder.insert_trades(&[trade]);

        // Depth is empty → returns None; no ghost rows from trade prices
        assert!(
            ladder.build_visible_book(test_bounds()).is_none(),
            "empty depth + trades should give None (no ghost rows from trade prices)"
        );
    }

    #[test]
    fn test_ladder_sparse_trade_at_non_book_price() {
        let mut ladder = Ladder::new(None, test_ticker_info(), default_step());

        let bid_price = Price::from_f32_lossy(5379.0);
        let ask_price = Price::from_f32_lossy(5380.0);
        ladder.orderbook[Side::Bid.idx()]
            .orders
            .insert(bid_price, Qty::from_f32_lossy(6200.0));
        ladder.orderbook[Side::Ask.idx()]
            .orders
            .insert(ask_price, Qty::from_f32_lossy(2900.0));

        // Trade at a non-book price (.5 — would have been a ghost row before)
        let non_book_price = Price::from_f32_lossy(5379.5);
        let trade = Trade {
            time: 1000,
            is_sell: false,
            price: non_book_price,
            qty: Qty::from_f32_lossy(100.0),
        };
        ladder.insert_trades(&[trade]);

        let vb = ladder
            .build_visible_book(test_bounds())
            .expect("visible book");

        for row in &vb.rows {
            if let DomRow::Ask { price, .. } | DomRow::Bid { price, .. } = row.row {
                assert_ne!(
                    price, non_book_price,
                    "non-book price {non_book_price:?} must not appear as a row"
                );
            }
        }

        // Book prices exist
        assert!(
            vb.rows
                .iter()
                .any(|r| matches!(r.row, DomRow::Bid { price, .. } if price == bid_price)),
            "book bid price 5379.0 must have a row"
        );
        assert!(
            vb.rows
                .iter()
                .any(|r| matches!(r.row, DomRow::Ask { price, .. } if price == ask_price)),
            "book ask price 5380.0 must have a row"
        );
    }

    #[test]
    fn test_ladder_sparse_price_format() {
        // min_ticksize=0.1 → SoftBank-like price 5379.0 displays as "5379.0"
        let ticker = Ticker::new("9984", Exchange::TachibanaStock);
        let info = TickerInfo::new_stock(ticker, 0.1, 100.0, 100);
        let ladder = Ladder::new(None, info, PriceStep::from_f32_lossy(0.5));
        let price = Price::from_f32_lossy(5379.0);
        let formatted = ladder.format_price(price);
        assert_eq!(
            formatted, "5379.0",
            "SoftBank price with min_ticksize=0.1 should show 1 decimal place"
        );
    }
}
