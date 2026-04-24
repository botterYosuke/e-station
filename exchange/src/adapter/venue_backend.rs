use super::{AdapterError, Event, MarketKind};
use crate::{
    Kline, OpenInterest, PushFrequency, TickMultiplier, Ticker, TickerInfo, TickerStats, Timeframe,
    Trade, depth::DepthPayload,
};

use futures::future::BoxFuture;
use futures::stream::BoxStream;
use std::{collections::HashMap, path::PathBuf};

pub type TickerMetadataMap = HashMap<Ticker, Option<TickerInfo>>;
pub type TickerStatsMap = HashMap<Ticker, TickerStats>;

/// Per-venue data backend.
///
/// The primary implementation is `EngineClientBackend`, which routes all requests
/// to the Python data engine via IPC.
pub trait VenueBackend: Send + Sync {
    fn kline_stream(
        &self,
        streams: Vec<(TickerInfo, Timeframe)>,
        market_kind: MarketKind,
    ) -> BoxStream<'static, Event>;

    fn trade_stream(
        &self,
        tickers: Vec<TickerInfo>,
        market_kind: MarketKind,
    ) -> BoxStream<'static, Event>;

    fn depth_stream(
        &self,
        ticker_info: TickerInfo,
        tick_multiplier: Option<TickMultiplier>,
        push_freq: PushFrequency,
    ) -> BoxStream<'static, Event>;

    fn fetch_ticker_metadata(
        &self,
        markets: &[MarketKind],
    ) -> BoxFuture<'_, Result<TickerMetadataMap, AdapterError>>;

    fn fetch_ticker_stats(
        &self,
        markets: &[MarketKind],
        contract_sizes: Option<HashMap<Ticker, f32>>,
    ) -> BoxFuture<'_, Result<TickerStatsMap, AdapterError>>;

    fn fetch_klines(
        &self,
        ticker_info: TickerInfo,
        timeframe: Timeframe,
        range: Option<(u64, u64)>,
    ) -> BoxFuture<'_, Result<Vec<Kline>, AdapterError>>;

    fn fetch_open_interest(
        &self,
        ticker_info: TickerInfo,
        timeframe: Timeframe,
        range: Option<(u64, u64)>,
    ) -> BoxFuture<'_, Result<Vec<OpenInterest>, AdapterError>>;

    fn fetch_trades(
        &self,
        ticker_info: TickerInfo,
        from_time: u64,
        to_time: u64,
        data_path: Option<PathBuf>,
    ) -> BoxFuture<'_, Result<Vec<Trade>, AdapterError>>;

    fn request_depth_snapshot(
        &self,
        ticker: Ticker,
    ) -> BoxFuture<'_, Result<DepthPayload, AdapterError>>;

    fn health(&self) -> BoxFuture<'_, bool>;
}
