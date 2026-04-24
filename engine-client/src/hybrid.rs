/// `HybridVenueBackend` — combines two `VenueBackend` implementations.
///
/// - **metadata backend**: handles `fetch_ticker_metadata` and `fetch_ticker_stats`.
///   Typically a `NativeBackend` so the ticker list / stats are always available.
/// - **streaming backend**: handles all stream methods and data-fetch methods.
///   Typically `EngineClientBackend` (Python IPC).
///
/// This split is needed because `EngineClientBackend::fetch_ticker_metadata` currently
/// returns an empty map (venue-specific field mapping not yet implemented), which would
/// break the sidebar ticker list.  The hybrid keeps the native REST path for metadata
/// while routing live streams through the Python engine.
use exchange::{
    Kline, OpenInterest, PushFrequency, Ticker, TickerInfo, TickMultiplier, Timeframe, Trade,
    adapter::{
        AdapterError, Event, MarketKind,
        venue_backend::{TickerMetadataMap, TickerStatsMap, VenueBackend},
    },
    depth::DepthPayload,
};
use futures::{future::BoxFuture, stream::BoxStream};
use std::{collections::HashMap, path::PathBuf, sync::Arc};

pub struct HybridVenueBackend {
    metadata: Arc<dyn VenueBackend>,
    streaming: Arc<dyn VenueBackend>,
}

impl HybridVenueBackend {
    /// Create a hybrid backend.
    ///
    /// `metadata` is called for `fetch_ticker_metadata` / `fetch_ticker_stats`.
    /// `streaming` is called for all other methods.
    pub fn new(metadata: Arc<dyn VenueBackend>, streaming: Arc<dyn VenueBackend>) -> Self {
        Self { metadata, streaming }
    }
}

impl VenueBackend for HybridVenueBackend {
    // ── Streams — routed to the engine backend ────────────────────────────────

    fn kline_stream(
        &self,
        streams: Vec<(TickerInfo, Timeframe)>,
        market_kind: MarketKind,
    ) -> BoxStream<'static, Event> {
        self.streaming.kline_stream(streams, market_kind)
    }

    fn trade_stream(
        &self,
        tickers: Vec<TickerInfo>,
        market_kind: MarketKind,
    ) -> BoxStream<'static, Event> {
        self.streaming.trade_stream(tickers, market_kind)
    }

    fn depth_stream(
        &self,
        ticker_info: TickerInfo,
        tick_multiplier: Option<TickMultiplier>,
        push_freq: PushFrequency,
    ) -> BoxStream<'static, Event> {
        self.streaming.depth_stream(ticker_info, tick_multiplier, push_freq)
    }

    // ── Metadata — routed to the native backend ───────────────────────────────

    fn fetch_ticker_metadata(
        &self,
        markets: &[MarketKind],
    ) -> BoxFuture<'_, Result<TickerMetadataMap, AdapterError>> {
        self.metadata.fetch_ticker_metadata(markets)
    }

    fn fetch_ticker_stats(
        &self,
        markets: &[MarketKind],
        contract_sizes: Option<HashMap<Ticker, f32>>,
    ) -> BoxFuture<'_, Result<TickerStatsMap, AdapterError>> {
        self.metadata.fetch_ticker_stats(markets, contract_sizes)
    }

    // ── Data fetches — routed to the engine backend ───────────────────────────

    fn fetch_klines(
        &self,
        ticker_info: TickerInfo,
        timeframe: Timeframe,
        range: Option<(u64, u64)>,
    ) -> BoxFuture<'_, Result<Vec<Kline>, AdapterError>> {
        self.streaming.fetch_klines(ticker_info, timeframe, range)
    }

    fn fetch_open_interest(
        &self,
        ticker_info: TickerInfo,
        timeframe: Timeframe,
        range: Option<(u64, u64)>,
    ) -> BoxFuture<'_, Result<Vec<OpenInterest>, AdapterError>> {
        self.streaming.fetch_open_interest(ticker_info, timeframe, range)
    }

    fn fetch_trades(
        &self,
        ticker_info: TickerInfo,
        from_time: u64,
        to_time: u64,
        data_path: Option<PathBuf>,
    ) -> BoxFuture<'_, Result<Vec<Trade>, AdapterError>> {
        self.streaming.fetch_trades(ticker_info, from_time, to_time, data_path)
    }

    fn request_depth_snapshot(
        &self,
        ticker: Ticker,
    ) -> BoxFuture<'_, Result<DepthPayload, AdapterError>> {
        self.streaming.request_depth_snapshot(ticker)
    }

    fn health(&self) -> BoxFuture<'_, bool> {
        self.streaming.health()
    }
}
