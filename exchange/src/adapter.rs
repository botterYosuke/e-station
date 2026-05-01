mod client;
pub mod proxy;
pub mod venue_backend;

pub use super::error::AdapterError;
use super::{QuoteCurrency, Timeframe};
use crate::{
    Kline, Price, PushFrequency, TickMultiplier, TickerInfo, Trade, depth::Depth, unit::Qty,
};

use enum_map::{Enum, EnumMap};
use rustc_hash::{FxHashMap, FxHashSet};
use serde::{Deserialize, Serialize};
use std::{str::FromStr, sync::Arc};

pub use client::{AdapterHandles, MAX_KLINE_STREAMS_PER_STREAM, MAX_TRADE_TICKERS_PER_STREAM};
pub use proxy::Proxy;
pub use venue_backend::VenueBackend;

// Hyperliquid-specific tick multiplier lookup table (moved from hub/hyperliquid).
const HL_MULTS_OVERFLOW: &[u16] = &[1, 10, 20, 50, 100, 1000, 10000];
const HL_MULTS_FRACTIONAL: &[u16] = &[1, 2, 5, 10, 100, 1000];
const HL_MULTS_SAFE: &[u16] = &[1, 10, 100, 1000];

/// Returns valid tick multipliers for Hyperliquid depth streams given the minimum tick size.
pub fn allowed_multipliers_for_min_tick(min_ticksize: crate::unit::MinTicksize) -> &'static [u16] {
    if min_ticksize.power < 0 {
        HL_MULTS_FRACTIONAL
    } else if min_ticksize.power > 0 {
        HL_MULTS_OVERFLOW
    } else {
        HL_MULTS_SAFE
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Deserialize, Serialize)]
pub enum MarketKind {
    Spot,
    LinearPerps,
    InversePerps,
    /// Equity (cash & margin) markets — Phase 1 covers Tachibana 立花証券 only.
    Stock,
}

impl MarketKind {
    pub const ALL: [MarketKind; 4] = [
        MarketKind::Spot,
        MarketKind::LinearPerps,
        MarketKind::InversePerps,
        MarketKind::Stock,
    ];

    pub fn qty_in_quote_value(&self, qty: Qty, price: Price, size_in_quote_ccy: bool) -> f32 {
        let qty_f = qty.to_f32_lossy();

        match self {
            // Stocks: quote value is always price * qty (JPY). The
            // `size_in_quote_ccy` flag is ignored on purpose — the crypto-only
            // call sites pass it and we must not silently produce a wrong value.
            MarketKind::Stock => price.to_f32() * qty_f,
            MarketKind::InversePerps => qty_f,
            MarketKind::Spot | MarketKind::LinearPerps => {
                if size_in_quote_ccy {
                    qty_f
                } else {
                    price.to_f32() * qty_f
                }
            }
        }
    }
}

impl std::fmt::Display for MarketKind {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "{}",
            match self {
                MarketKind::Spot => "Spot",
                MarketKind::LinearPerps => "Linear",
                MarketKind::InversePerps => "Inverse",
                MarketKind::Stock => "Stock",
            }
        )
    }
}

impl FromStr for MarketKind {
    type Err = String;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        if s.eq_ignore_ascii_case("spot") {
            Ok(Self::Spot)
        } else if s.eq_ignore_ascii_case("linear") {
            Ok(Self::LinearPerps)
        } else if s.eq_ignore_ascii_case("inverse") {
            Ok(Self::InversePerps)
        } else if s.eq_ignore_ascii_case("stock") {
            Ok(Self::Stock)
        } else {
            Err(format!("Invalid market kind: {}", s))
        }
    }
}

#[derive(Debug, Copy, Clone, PartialEq, Eq, Hash, Deserialize, Serialize)]
pub enum StreamKind {
    Kline {
        ticker_info: TickerInfo,
        timeframe: Timeframe,
    },
    Depth {
        ticker_info: TickerInfo,
        #[serde(default = "default_depth_aggr")]
        depth_aggr: StreamTicksize,
        push_freq: PushFrequency,
    },
    Trades {
        ticker_info: TickerInfo,
    },
}

impl StreamKind {
    pub fn ticker_info(&self) -> TickerInfo {
        match self {
            StreamKind::Kline { ticker_info, .. }
            | StreamKind::Depth { ticker_info, .. }
            | StreamKind::Trades { ticker_info, .. } => *ticker_info,
        }
    }

    pub fn as_depth_stream(&self) -> Option<(TickerInfo, StreamTicksize, PushFrequency)> {
        match self {
            StreamKind::Depth {
                ticker_info,
                depth_aggr,
                push_freq,
            } => Some((*ticker_info, *depth_aggr, *push_freq)),
            _ => None,
        }
    }

    pub fn as_trade_stream(&self) -> Option<TickerInfo> {
        match self {
            StreamKind::Trades { ticker_info } => Some(*ticker_info),
            _ => None,
        }
    }

    pub fn as_kline_stream(&self) -> Option<(TickerInfo, Timeframe)> {
        match self {
            StreamKind::Kline {
                ticker_info,
                timeframe,
            } => Some((*ticker_info, *timeframe)),
            _ => None,
        }
    }
}

#[derive(Debug, Default)]
pub struct UniqueStreams {
    streams: EnumMap<Exchange, Option<FxHashMap<TickerInfo, FxHashSet<StreamKind>>>>,
    specs: EnumMap<Exchange, Option<StreamSpecs>>,
}

impl UniqueStreams {
    pub fn from<'a>(streams: impl Iterator<Item = &'a StreamKind>) -> Self {
        let mut unique_streams = UniqueStreams::default();
        for stream in streams {
            unique_streams.add(*stream);
        }
        unique_streams
    }

    pub fn add(&mut self, stream: StreamKind) {
        let (exchange, ticker_info) = match stream {
            StreamKind::Kline { ticker_info, .. }
            | StreamKind::Depth { ticker_info, .. }
            | StreamKind::Trades { ticker_info, .. } => (ticker_info.exchange(), ticker_info),
        };

        self.streams[exchange]
            .get_or_insert_with(FxHashMap::default)
            .entry(ticker_info)
            .or_default()
            .insert(stream);

        self.update_specs_for_exchange(exchange);
    }

    pub fn extend<'a>(&mut self, streams: impl IntoIterator<Item = &'a StreamKind>) {
        for stream in streams {
            self.add(*stream);
        }
    }

    fn update_specs_for_exchange(&mut self, exchange: Exchange) {
        let depth_streams = self.depth_streams(Some(exchange));
        let trade_streams = self.trade_streams(Some(exchange));
        let kline_streams = self.kline_streams(Some(exchange));

        self.specs[exchange] = Some(StreamSpecs {
            depth: depth_streams,
            trade: trade_streams,
            kline: kline_streams,
        });
    }

    fn streams<T, F>(&self, exchange_filter: Option<Exchange>, stream_extractor: F) -> Vec<T>
    where
        F: Fn(Exchange, &StreamKind) -> Option<T>,
    {
        let f = &stream_extractor;

        let per_exchange = |exchange| {
            self.streams[exchange]
                .as_ref()
                .into_iter()
                .flat_map(|ticker_map| ticker_map.values().flatten())
                .filter_map(move |stream| f(exchange, stream))
        };

        match exchange_filter {
            Some(exchange) => per_exchange(exchange).collect(),
            None => Exchange::ALL.into_iter().flat_map(per_exchange).collect(),
        }
    }

    pub fn depth_streams(
        &self,
        exchange_filter: Option<Exchange>,
    ) -> Vec<(TickerInfo, StreamTicksize, PushFrequency)> {
        self.streams(exchange_filter, |_, stream| stream.as_depth_stream())
    }

    pub fn kline_streams(&self, exchange_filter: Option<Exchange>) -> Vec<(TickerInfo, Timeframe)> {
        self.streams(exchange_filter, |_, stream| stream.as_kline_stream())
    }

    pub fn trade_streams(&self, exchange_filter: Option<Exchange>) -> Vec<TickerInfo> {
        self.streams(exchange_filter, |_, stream| stream.as_trade_stream())
    }

    pub fn combined_used(&self) -> impl Iterator<Item = (Exchange, &StreamSpecs)> {
        self.specs
            .iter()
            .filter_map(|(exchange, specs)| specs.as_ref().map(|stream| (exchange, stream)))
    }

    pub fn combined(&self) -> &EnumMap<Exchange, Option<StreamSpecs>> {
        &self.specs
    }
}

#[derive(Debug, Default, Clone, Copy, PartialEq, Eq, Hash, Deserialize, Serialize)]
pub enum StreamTicksize {
    ServerSide(TickMultiplier),
    #[default]
    Client,
}

fn default_depth_aggr() -> StreamTicksize {
    StreamTicksize::Client
}

#[derive(Debug, Clone, Default)]
pub struct StreamSpecs {
    pub depth: Vec<(TickerInfo, StreamTicksize, PushFrequency)>,
    pub trade: Vec<TickerInfo>,
    pub kline: Vec<(TickerInfo, Timeframe)>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Deserialize, Serialize)]
pub enum Venue {
    Bybit,
    Binance,
    Hyperliquid,
    Okex,
    Mexc,
    /// 立花証券 e支店 (Japanese equities). Phase 1 is read-only; demo only.
    Tachibana,
    /// Backtesting replay engine — market data emitted by Python NautilusTrader.
    Replay,
}

impl Venue {
    pub const ALL: [Venue; 7] = [
        Venue::Bybit,
        Venue::Binance,
        Venue::Hyperliquid,
        Venue::Okex,
        Venue::Mexc,
        Venue::Tachibana,
        Venue::Replay,
    ];
}

impl std::fmt::Display for Venue {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "{}",
            match self {
                Venue::Bybit => "Bybit",
                Venue::Binance => "Binance",
                Venue::Hyperliquid => "Hyperliquid",
                Venue::Okex => "OKX",
                Venue::Mexc => "MEXC",
                Venue::Tachibana => "Tachibana",
                Venue::Replay => "Replay",
            }
        )
    }
}

impl FromStr for Venue {
    type Err = String;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        if s.eq_ignore_ascii_case("bybit") {
            Ok(Self::Bybit)
        } else if s.eq_ignore_ascii_case("binance") {
            Ok(Self::Binance)
        } else if s.eq_ignore_ascii_case("hyperliquid") {
            Ok(Self::Hyperliquid)
        } else if s.eq_ignore_ascii_case("okx") || s.eq_ignore_ascii_case("okex") {
            Ok(Self::Okex)
        } else if s.eq_ignore_ascii_case("mexc") {
            Ok(Self::Mexc)
        } else if s.eq_ignore_ascii_case("tachibana") {
            Ok(Self::Tachibana)
        } else if s.eq_ignore_ascii_case("replay") {
            Ok(Self::Replay)
        } else {
            Err(format!("Invalid venue: {}", s))
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Deserialize, Serialize, Enum)]
pub enum Exchange {
    BinanceLinear,
    BinanceInverse,
    BinanceSpot,
    BybitLinear,
    BybitInverse,
    BybitSpot,
    HyperliquidLinear,
    HyperliquidSpot,
    OkexLinear,
    OkexInverse,
    OkexSpot,
    MexcLinear,
    MexcInverse,
    MexcSpot,
    /// 立花証券 e支店 — Tokyo Stock Exchange equities (cash & margin merged).
    TachibanaStock,
    /// Replay engine — backtesting data from NautilusTrader Python engine.
    ReplayStock,
}

impl std::fmt::Display for Exchange {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{} {}", self.venue(), self.market_type())
    }
}

impl FromStr for Exchange {
    type Err = String;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        let mut parts = s.split_whitespace();
        let Some(venue_part) = parts.next() else {
            return Err(format!("Invalid exchange: {}", s));
        };
        let Some(market_part) = parts.next() else {
            return Err(format!("Invalid exchange: {}", s));
        };

        if parts.next().is_some() {
            return Err(format!("Invalid exchange: {}", s));
        }

        let venue = Venue::from_str(venue_part).map_err(|_| format!("Invalid exchange: {}", s))?;
        let market =
            MarketKind::from_str(market_part).map_err(|_| format!("Invalid exchange: {}", s))?;

        Self::from_venue_and_market(venue, market).ok_or_else(|| format!("Invalid exchange: {}", s))
    }
}

impl Exchange {
    pub const ALL: [Exchange; 16] = [
        Exchange::BinanceLinear,
        Exchange::BinanceInverse,
        Exchange::BinanceSpot,
        Exchange::BybitLinear,
        Exchange::BybitInverse,
        Exchange::BybitSpot,
        Exchange::HyperliquidLinear,
        Exchange::HyperliquidSpot,
        Exchange::OkexLinear,
        Exchange::OkexInverse,
        Exchange::OkexSpot,
        Exchange::MexcLinear,
        Exchange::MexcInverse,
        Exchange::MexcSpot,
        Exchange::TachibanaStock,
        Exchange::ReplayStock,
    ];

    pub fn from_venue_and_market(venue: Venue, market: MarketKind) -> Option<Self> {
        Self::ALL
            .into_iter()
            .find(|exchange| exchange.venue() == venue && exchange.market_type() == market)
    }

    pub fn market_type(&self) -> MarketKind {
        match self {
            Exchange::BinanceLinear
            | Exchange::BybitLinear
            | Exchange::HyperliquidLinear
            | Exchange::OkexLinear
            | Exchange::MexcLinear => MarketKind::LinearPerps,
            Exchange::BinanceInverse
            | Exchange::BybitInverse
            | Exchange::OkexInverse
            | Exchange::MexcInverse => MarketKind::InversePerps,
            Exchange::BinanceSpot
            | Exchange::BybitSpot
            | Exchange::HyperliquidSpot
            | Exchange::OkexSpot
            | Exchange::MexcSpot => MarketKind::Spot,
            Exchange::TachibanaStock | Exchange::ReplayStock => MarketKind::Stock,
        }
    }

    pub fn venue(&self) -> Venue {
        match self {
            Exchange::BybitLinear | Exchange::BybitInverse | Exchange::BybitSpot => Venue::Bybit,
            Exchange::BinanceLinear | Exchange::BinanceInverse | Exchange::BinanceSpot => {
                Venue::Binance
            }
            Exchange::HyperliquidLinear | Exchange::HyperliquidSpot => Venue::Hyperliquid,
            Exchange::OkexLinear | Exchange::OkexInverse | Exchange::OkexSpot => Venue::Okex,
            Exchange::MexcLinear | Exchange::MexcInverse | Exchange::MexcSpot => Venue::Mexc,
            Exchange::TachibanaStock => Venue::Tachibana,
            Exchange::ReplayStock => Venue::Replay,
        }
    }

    /// Quote currency the venue's instruments are denominated in by default.
    /// Used by `TickerInfo` to seed `quote_currency` when the persisted state
    /// has no value.
    pub fn default_quote_currency(&self) -> QuoteCurrency {
        match self.venue() {
            Venue::Tachibana | Venue::Replay => QuoteCurrency::Jpy,
            // Crypto venues: USDT for derivatives + most spot pairs is a
            // reasonable default; the actual currency is conveyed by the
            // ticker symbol suffix (USDT/USDC/USD) which the formatter can
            // detect when present.
            Venue::Binance | Venue::Bybit | Venue::Okex | Venue::Mexc | Venue::Hyperliquid => {
                QuoteCurrency::Usdt
            }
        }
    }

    #[deprecated(note = "use VenueCapsStore::get(&ticker).map(|c| c.client_aggr_depth) instead")]
    pub fn is_depth_client_aggr(&self) -> bool {
        panic!(
            "is_depth_client_aggr() is removed in Phase D; \
             use VenueCapsStore::get(&ticker).map(|c| c.client_aggr_depth)"
        )
    }

    pub fn is_custom_push_freq(&self) -> bool {
        matches!(
            self,
            Exchange::BybitLinear | Exchange::BybitInverse | Exchange::BybitSpot
        )
    }

    pub fn supports_heatmap_timeframe(&self, tf: Timeframe) -> bool {
        match self {
            Exchange::BybitSpot
            | Exchange::MexcSpot
            | Exchange::MexcInverse
            | Exchange::MexcLinear => {
                tf != Timeframe::MS100 && tf != Timeframe::MS300 && tf != Timeframe::MS500
            }
            Exchange::BybitLinear | Exchange::BybitInverse => tf != Timeframe::MS200,
            Exchange::HyperliquidLinear | Exchange::HyperliquidSpot => {
                tf != Timeframe::MS100 && tf != Timeframe::MS200 && tf != Timeframe::MS300
            }
            _ => true,
        }
    }

    pub fn supports_kline_timeframe(&self, tf: Timeframe) -> bool {
        match self.venue() {
            Venue::Binance | Venue::Bybit | Venue::Hyperliquid | Venue::Okex => {
                Timeframe::KLINE.contains(&tf)
            }
            Venue::Mexc => {
                Timeframe::KLINE.contains(&tf)
                    && !matches!(tf, Timeframe::M3 | Timeframe::H2 | Timeframe::H12)
            }
            // Tachibana (立花) returns daily klines only via
            // CLMMfdsGetMarketPriceHistory; sub-day timeframes are aggregated
            // client-side from FD frames in a future phase.
            Venue::Tachibana => tf == Timeframe::D1,
            // Replay engine supports Daily (D1) and Minute (M1) bars from
            // NautilusTrader backtest data; sub-minute granularities are not
            // emitted as bars.
            Venue::Replay => matches!(tf, Timeframe::D1 | Timeframe::M1),
        }
    }

    pub fn is_perps(&self) -> bool {
        matches!(
            self,
            Exchange::BinanceLinear
                | Exchange::BinanceInverse
                | Exchange::BybitLinear
                | Exchange::BybitInverse
                | Exchange::HyperliquidLinear
                | Exchange::OkexLinear
                | Exchange::OkexInverse
                | Exchange::MexcLinear
                | Exchange::MexcInverse
        )
    }

    pub fn stream_ticksize(
        &self,
        is_client_aggr: bool,
        multiplier: Option<TickMultiplier>,
        server_fallback: TickMultiplier,
    ) -> StreamTicksize {
        if is_client_aggr {
            StreamTicksize::Client
        } else {
            StreamTicksize::ServerSide(multiplier.unwrap_or(server_fallback))
        }
    }

    pub fn is_symbol_supported(&self, symbol: &str, log: bool) -> bool {
        let valid_symbol = symbol
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-');

        if valid_symbol {
            return true;
        } else if log {
            log::warn!("Unsupported ticker: '{}': {:?}", self, symbol,);
        }
        false
    }
}

#[derive(Debug, Clone)]
pub enum Event {
    Connected(Exchange),
    Disconnected(Exchange, String),
    DepthReceived(StreamKind, u64, Arc<Depth>),
    TradesReceived(StreamKind, u64, Box<[Trade]>),
    KlineReceived(StreamKind, Kline),
}

#[derive(Debug, Clone, Hash)]
pub struct StreamConfig<I> {
    pub id: I,
    pub exchange: Exchange,
    pub tick_mltp: Option<TickMultiplier>,
    pub push_freq: PushFrequency,
}

impl<I> StreamConfig<I> {
    pub fn new(
        id: I,
        exchange: Exchange,
        tick_mltp: Option<TickMultiplier>,
        push_freq: PushFrequency,
    ) -> Self {
        Self {
            id,
            exchange,
            tick_mltp,
            push_freq,
        }
    }
}
