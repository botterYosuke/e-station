use super::{
    AdapterError, Event, Exchange, MarketKind, StreamConfig, Venue,
    hub::{binance, bybit, hyperliquid, mexc, okex},
    venue_backend::{NativeBackend, VenueBackend},
};
use crate::{Kline, OpenInterest, Ticker, TickerInfo, TickerStats, Timeframe, Trade, depth::DepthPayload};

use futures::{StreamExt, stream, stream::BoxStream};
use std::{collections::HashMap, collections::HashSet, path::PathBuf, sync::Arc};

// Keep topics per websocket conservative across venues
// allow up to 100 tickers per websocket stream
pub const MAX_TRADE_TICKERS_PER_STREAM: usize = 100;
pub const MAX_KLINE_STREAMS_PER_STREAM: usize = 100;

#[derive(Debug, Clone, Default)]
pub struct AdapterNetworkConfig {
    pub proxy_cfg: Option<super::proxy::Proxy>,
}

#[derive(Clone, Default)]
pub struct AdapterHandles {
    binance: Option<Arc<dyn VenueBackend>>,
    bybit: Option<Arc<dyn VenueBackend>>,
    hyperliquid: Option<Arc<dyn VenueBackend>>,
    okex: Option<Arc<dyn VenueBackend>>,
    mexc: Option<Arc<dyn VenueBackend>>,
}

impl AdapterHandles {
    pub fn spawn_all(config: AdapterNetworkConfig) -> Result<Self, AdapterError> {
        Self::spawn_selected(config, Venue::ALL)
    }

    pub fn spawn_selected(
        config: AdapterNetworkConfig,
        venues: impl IntoIterator<Item = Venue>,
    ) -> Result<Self, AdapterError> {
        let mut out = Self::default();
        let mut seen = HashSet::new();

        for venue in venues {
            if !seen.insert(venue) {
                continue;
            }

            out.spawn_venue(venue, config.clone())?;
        }

        Ok(out)
    }

    /// Inserts a custom backend for the given venue.
    ///
    /// Replaces any previously registered backend for that venue.
    /// Primarily used for testing and for the future `EngineClientBackend`.
    pub fn set_backend(&mut self, venue: Venue, backend: Arc<dyn VenueBackend>) {
        match venue {
            Venue::Binance => self.binance = Some(backend),
            Venue::Bybit => self.bybit = Some(backend),
            Venue::Hyperliquid => self.hyperliquid = Some(backend),
            Venue::Okex => self.okex = Some(backend),
            Venue::Mexc => self.mexc = Some(backend),
        }
    }

    fn get_backend(&self, venue: Venue) -> Option<Arc<dyn VenueBackend>> {
        self.get_backend_arc(venue)
    }

    /// Returns a clone of the `Arc<dyn VenueBackend>` registered for `venue`, if any.
    pub fn get_backend_arc(&self, venue: Venue) -> Option<Arc<dyn VenueBackend>> {
        match venue {
            Venue::Binance => self.binance.clone(),
            Venue::Bybit => self.bybit.clone(),
            Venue::Hyperliquid => self.hyperliquid.clone(),
            Venue::Okex => self.okex.clone(),
            Venue::Mexc => self.mexc.clone(),
        }
    }

    pub fn configured_venues(&self) -> impl Iterator<Item = Venue> + '_ {
        Venue::ALL
            .into_iter()
            .filter(|venue| self.has_venue(*venue))
    }

    pub fn has_venue(&self, venue: Venue) -> bool {
        self.get_backend(venue).is_some()
    }

    fn spawn_venue(
        &mut self,
        venue: Venue,
        config: AdapterNetworkConfig,
    ) -> Result<(), AdapterError> {
        let backend: Arc<dyn VenueBackend> = match venue {
            Venue::Binance => Arc::new(NativeBackend::Binance(
                binance::spawn_binance_with_network(config)?,
            )),
            Venue::Bybit => Arc::new(NativeBackend::Bybit(
                bybit::spawn_bybit_with_network(config)?,
            )),
            Venue::Hyperliquid => Arc::new(NativeBackend::Hyperliquid(
                hyperliquid::spawn_hyperliquid_with_network(config)?,
            )),
            Venue::Okex => Arc::new(NativeBackend::Okex(
                okex::spawn_okex_with_network(config)?,
            )),
            Venue::Mexc => Arc::new(NativeBackend::Mexc(
                mexc::spawn_mexc_with_network(config)?,
            )),
        };
        self.set_backend(venue, backend);
        Ok(())
    }

    fn missing_venue_stream(exchange: Exchange) -> BoxStream<'static, Event> {
        let reason = format!(
            "No adapter handle configured for venue {}",
            exchange.venue()
        );
        stream::once(async move { Event::Disconnected(exchange, reason) }).boxed()
    }

    fn missing_venue_error(venue: Venue) -> AdapterError {
        AdapterError::InvalidRequest(format!("No adapter handle configured for venue {venue}"))
    }

    pub fn kline_stream(
        &self,
        config: &StreamConfig<Vec<(TickerInfo, Timeframe)>>,
    ) -> BoxStream<'static, Event> {
        let streams = config.id.clone();
        let market_kind = config.exchange.market_type();
        let venue = config.exchange.venue();

        self.get_backend(venue).map_or_else(
            || Self::missing_venue_stream(config.exchange),
            |backend| backend.kline_stream(streams, market_kind),
        )
    }

    pub fn trade_stream(
        &self,
        config: &StreamConfig<Vec<TickerInfo>>,
    ) -> BoxStream<'static, Event> {
        let tickers = config.id.clone();
        let market_kind = config.exchange.market_type();
        let venue = config.exchange.venue();

        self.get_backend(venue).map_or_else(
            || Self::missing_venue_stream(config.exchange),
            |backend| backend.trade_stream(tickers, market_kind),
        )
    }

    pub fn depth_stream(&self, config: &StreamConfig<TickerInfo>) -> BoxStream<'static, Event> {
        let ticker_info = config.id;
        let tick_mltp = config.tick_mltp;
        let push_freq = config.push_freq;
        let venue = config.exchange.venue();

        self.get_backend(venue).map_or_else(
            || Self::missing_venue_stream(config.exchange),
            |backend| backend.depth_stream(ticker_info, tick_mltp, push_freq),
        )
    }

    /// Returns a map of tickers to their [`TickerInfo`].
    /// If metadata for a ticker can't be fetched/parsed expectedly, it will still be included in the map as `None`.
    ///
    /// `Binance`, `Bybit`, and `Hyperliquid` are fetched per market, while
    /// `Okex` and `Mexc` handle market branching internally due to combined perps endpoints.
    pub async fn fetch_ticker_metadata(
        &self,
        venue: Venue,
        markets: &[MarketKind],
    ) -> Result<HashMap<Ticker, Option<TickerInfo>>, AdapterError> {
        let Some(backend) = self.get_backend(venue) else {
            return Err(Self::missing_venue_error(venue));
        };
        backend.fetch_ticker_metadata(markets).await
    }

    /// Returns a map of tickers to their [`TickerStats`].
    ///
    /// `Binance`, `Bybit`, and `Hyperliquid` are fetched per market, while
    /// `Okex` and `Mexc` handle market branching internally due to combined perps endpoints.
    pub async fn fetch_ticker_stats(
        &self,
        venue: Venue,
        markets: &[MarketKind],
        contract_sizes: Option<HashMap<Ticker, f32>>,
    ) -> Result<HashMap<Ticker, TickerStats>, AdapterError> {
        let Some(backend) = self.get_backend(venue) else {
            return Err(Self::missing_venue_error(venue));
        };
        backend.fetch_ticker_stats(markets, contract_sizes).await
    }

    pub async fn fetch_klines(
        &self,
        ticker_info: TickerInfo,
        timeframe: Timeframe,
        range: Option<(u64, u64)>,
    ) -> Result<Vec<Kline>, AdapterError> {
        let venue = ticker_info.ticker.exchange.venue();
        let Some(backend) = self.get_backend(venue) else {
            return Err(Self::missing_venue_error(venue));
        };
        backend.fetch_klines(ticker_info, timeframe, range).await
    }

    pub async fn fetch_open_interest(
        &self,
        ticker_info: TickerInfo,
        timeframe: Timeframe,
        range: Option<(u64, u64)>,
    ) -> Result<Vec<OpenInterest>, AdapterError> {
        let venue = ticker_info.ticker.exchange.venue();
        let Some(backend) = self.get_backend(venue) else {
            return Err(Self::missing_venue_error(venue));
        };
        backend.fetch_open_interest(ticker_info, timeframe, range).await
    }

    pub async fn fetch_trades(
        &self,
        ticker_info: TickerInfo,
        from_time: u64,
        data_path: Option<PathBuf>,
    ) -> Result<Vec<Trade>, AdapterError> {
        let venue = ticker_info.ticker.exchange.venue();
        let Some(backend) = self.get_backend(venue) else {
            return Err(Self::missing_venue_error(venue));
        };
        backend.fetch_trades(ticker_info, from_time, data_path).await
    }
    /// Requests a fresh depth snapshot for the given ticker.
    ///
    /// The venue is derived from `ticker.exchange`; returns an error if no backend is configured
    /// for that venue or if the venue does not support depth snapshots.
    pub async fn request_depth_snapshot(
        &self,
        ticker: Ticker,
    ) -> Result<DepthPayload, AdapterError> {
        let venue = ticker.exchange.venue();
        let Some(backend) = self.get_backend(venue) else {
            return Err(Self::missing_venue_error(venue));
        };
        backend.request_depth_snapshot(ticker).await
    }

    /// Returns `Some(true/false)` when the backend for `venue` is configured, `None` otherwise.
    pub async fn venue_health(&self, venue: Venue) -> Option<bool> {
        let backend = self.get_backend(venue)?;
        Some(backend.health().await)
    }
}

impl std::hash::Hash for AdapterHandles {
    fn hash<H: std::hash::Hasher>(&self, state: &mut H) {
        std::any::TypeId::of::<Self>().hash(state);
    }
}
