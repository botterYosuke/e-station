//! Phase 0.5: VenueBackend trait abstraction tests.

use flowsurface_exchange::adapter::venue_backend::{
    TickerMetadataMap, TickerStatsMap, VenueBackend,
};
use flowsurface_exchange::adapter::{
    AdapterError, AdapterHandles, Event, Exchange, MarketKind, StreamConfig, Venue,
};
use flowsurface_exchange::depth::DepthPayload;
use flowsurface_exchange::{
    Kline, OpenInterest, PushFrequency, TickMultiplier, Ticker, TickerInfo, Timeframe, Trade,
};

use futures::StreamExt;
use futures::future::BoxFuture;
use futures::stream::{BoxStream, empty};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{
    Arc,
    atomic::{AtomicU32, Ordering},
};

// ── helpers ──────────────────────────────────────────────────────────────────

fn binance_ticker() -> Ticker {
    Ticker::new("BTCUSDT", Exchange::BinanceLinear)
}

fn binance_ticker_info() -> TickerInfo {
    TickerInfo::new(binance_ticker(), 0.1, 0.001, None)
}

// ── StubBackend ───────────────────────────────────────────────────────────────

/// No-op backend; used to verify trait-object insertion and basic forwarding.
struct StubBackend;

impl VenueBackend for StubBackend {
    fn kline_stream(
        &self,
        _streams: Vec<(TickerInfo, Timeframe)>,
        _market_kind: MarketKind,
    ) -> BoxStream<'static, Event> {
        empty().boxed()
    }

    fn trade_stream(
        &self,
        _tickers: Vec<TickerInfo>,
        _market_kind: MarketKind,
    ) -> BoxStream<'static, Event> {
        empty().boxed()
    }

    fn depth_stream(
        &self,
        _ticker_info: TickerInfo,
        _tick_multiplier: Option<TickMultiplier>,
        _push_freq: PushFrequency,
    ) -> BoxStream<'static, Event> {
        empty().boxed()
    }

    fn fetch_ticker_metadata(
        &self,
        _markets: &[MarketKind],
    ) -> BoxFuture<'_, Result<TickerMetadataMap, AdapterError>> {
        Box::pin(async { Ok(HashMap::default()) })
    }

    fn fetch_ticker_stats(
        &self,
        _markets: &[MarketKind],
        _contract_sizes: Option<HashMap<Ticker, f32>>,
    ) -> BoxFuture<'_, Result<TickerStatsMap, AdapterError>> {
        Box::pin(async { Ok(HashMap::default()) })
    }

    fn fetch_klines(
        &self,
        _ticker_info: TickerInfo,
        _timeframe: Timeframe,
        _range: Option<(u64, u64)>,
    ) -> BoxFuture<'_, Result<Vec<Kline>, AdapterError>> {
        Box::pin(async { Ok(vec![]) })
    }

    fn fetch_open_interest(
        &self,
        _ticker_info: TickerInfo,
        _timeframe: Timeframe,
        _range: Option<(u64, u64)>,
    ) -> BoxFuture<'_, Result<Vec<OpenInterest>, AdapterError>> {
        Box::pin(async {
            Err(AdapterError::InvalidRequest(
                "not supported by stub".to_string(),
            ))
        })
    }

    fn fetch_trades(
        &self,
        _ticker_info: TickerInfo,
        _from_time: u64,
        _to_time: u64,
        _data_path: Option<PathBuf>,
    ) -> BoxFuture<'_, Result<Vec<Trade>, AdapterError>> {
        Box::pin(async {
            Err(AdapterError::InvalidRequest(
                "not supported by stub".to_string(),
            ))
        })
    }

    fn request_depth_snapshot(
        &self,
        _ticker: Ticker,
    ) -> BoxFuture<'_, Result<DepthPayload, AdapterError>> {
        Box::pin(async {
            Err(AdapterError::InvalidRequest(
                "not supported by stub".to_string(),
            ))
        })
    }

    fn health(&self) -> BoxFuture<'_, bool> {
        Box::pin(async { true })
    }
}

// ── CountingBackend ───────────────────────────────────────────────────────────

/// Backend that increments per-method call counters so delegation can be
/// verified through `AdapterHandles` rather than calling the backend directly.
struct CountingBackend {
    kline_calls: Arc<AtomicU32>,
    trade_calls: Arc<AtomicU32>,
    depth_calls: Arc<AtomicU32>,
    metadata_calls: Arc<AtomicU32>,
    stats_calls: Arc<AtomicU32>,
    klines_calls: Arc<AtomicU32>,
    oi_calls: Arc<AtomicU32>,
    trades_calls: Arc<AtomicU32>,
    snapshot_calls: Arc<AtomicU32>,
    health_calls: Arc<AtomicU32>,
}

impl CountingBackend {
    fn new() -> (Self, Counters) {
        let kline_calls = Arc::new(AtomicU32::new(0));
        let trade_calls = Arc::new(AtomicU32::new(0));
        let depth_calls = Arc::new(AtomicU32::new(0));
        let metadata_calls = Arc::new(AtomicU32::new(0));
        let stats_calls = Arc::new(AtomicU32::new(0));
        let klines_calls = Arc::new(AtomicU32::new(0));
        let oi_calls = Arc::new(AtomicU32::new(0));
        let trades_calls = Arc::new(AtomicU32::new(0));
        let snapshot_calls = Arc::new(AtomicU32::new(0));
        let health_calls = Arc::new(AtomicU32::new(0));

        let counters = Counters {
            kline: kline_calls.clone(),
            trade: trade_calls.clone(),
            depth: depth_calls.clone(),
            metadata: metadata_calls.clone(),
            stats: stats_calls.clone(),
            klines: klines_calls.clone(),
            oi: oi_calls.clone(),
            trades: trades_calls.clone(),
            snapshot: snapshot_calls.clone(),
            health: health_calls.clone(),
        };

        let backend = Self {
            kline_calls,
            trade_calls,
            depth_calls,
            metadata_calls,
            stats_calls,
            klines_calls,
            oi_calls,
            trades_calls,
            snapshot_calls,
            health_calls,
        };

        (backend, counters)
    }
}

struct Counters {
    kline: Arc<AtomicU32>,
    trade: Arc<AtomicU32>,
    depth: Arc<AtomicU32>,
    metadata: Arc<AtomicU32>,
    stats: Arc<AtomicU32>,
    klines: Arc<AtomicU32>,
    oi: Arc<AtomicU32>,
    trades: Arc<AtomicU32>,
    snapshot: Arc<AtomicU32>,
    health: Arc<AtomicU32>,
}

impl Counters {
    fn get(&self, c: &Arc<AtomicU32>) -> u32 {
        c.load(Ordering::SeqCst)
    }
}

impl VenueBackend for CountingBackend {
    fn kline_stream(
        &self,
        _streams: Vec<(TickerInfo, Timeframe)>,
        _market_kind: MarketKind,
    ) -> BoxStream<'static, Event> {
        self.kline_calls.fetch_add(1, Ordering::SeqCst);
        empty().boxed()
    }

    fn trade_stream(
        &self,
        _tickers: Vec<TickerInfo>,
        _market_kind: MarketKind,
    ) -> BoxStream<'static, Event> {
        self.trade_calls.fetch_add(1, Ordering::SeqCst);
        empty().boxed()
    }

    fn depth_stream(
        &self,
        _ticker_info: TickerInfo,
        _tick_multiplier: Option<TickMultiplier>,
        _push_freq: PushFrequency,
    ) -> BoxStream<'static, Event> {
        self.depth_calls.fetch_add(1, Ordering::SeqCst);
        empty().boxed()
    }

    fn fetch_ticker_metadata(
        &self,
        _markets: &[MarketKind],
    ) -> BoxFuture<'_, Result<TickerMetadataMap, AdapterError>> {
        self.metadata_calls.fetch_add(1, Ordering::SeqCst);
        Box::pin(async { Ok(HashMap::default()) })
    }

    fn fetch_ticker_stats(
        &self,
        _markets: &[MarketKind],
        _contract_sizes: Option<HashMap<Ticker, f32>>,
    ) -> BoxFuture<'_, Result<TickerStatsMap, AdapterError>> {
        self.stats_calls.fetch_add(1, Ordering::SeqCst);
        Box::pin(async { Ok(HashMap::default()) })
    }

    fn fetch_klines(
        &self,
        _ticker_info: TickerInfo,
        _timeframe: Timeframe,
        _range: Option<(u64, u64)>,
    ) -> BoxFuture<'_, Result<Vec<Kline>, AdapterError>> {
        self.klines_calls.fetch_add(1, Ordering::SeqCst);
        Box::pin(async { Ok(vec![]) })
    }

    fn fetch_open_interest(
        &self,
        _ticker_info: TickerInfo,
        _timeframe: Timeframe,
        _range: Option<(u64, u64)>,
    ) -> BoxFuture<'_, Result<Vec<OpenInterest>, AdapterError>> {
        self.oi_calls.fetch_add(1, Ordering::SeqCst);
        Box::pin(async { Err(AdapterError::InvalidRequest("unsupported".to_string())) })
    }

    fn fetch_trades(
        &self,
        _ticker_info: TickerInfo,
        _from_time: u64,
        _to_time: u64,
        _data_path: Option<PathBuf>,
    ) -> BoxFuture<'_, Result<Vec<Trade>, AdapterError>> {
        self.trades_calls.fetch_add(1, Ordering::SeqCst);
        Box::pin(async { Err(AdapterError::InvalidRequest("unsupported".to_string())) })
    }

    fn request_depth_snapshot(
        &self,
        _ticker: Ticker,
    ) -> BoxFuture<'_, Result<DepthPayload, AdapterError>> {
        self.snapshot_calls.fetch_add(1, Ordering::SeqCst);
        Box::pin(async { Err(AdapterError::InvalidRequest("unsupported".to_string())) })
    }

    fn health(&self) -> BoxFuture<'_, bool> {
        self.health_calls.fetch_add(1, Ordering::SeqCst);
        Box::pin(async { true })
    }
}

// ── insertion tests ───────────────────────────────────────────────────────────

#[test]
fn set_backend_makes_venue_available() {
    let mut handles = AdapterHandles::default();
    assert!(!handles.has_venue(Venue::Binance));
    assert!(!handles.has_venue(Venue::Bybit));

    handles.set_backend(Venue::Binance, Arc::new(StubBackend));
    assert!(handles.has_venue(Venue::Binance));
    assert!(
        !handles.has_venue(Venue::Bybit),
        "only Binance should be set"
    );
}

#[test]
fn set_backend_all_venues() {
    let mut handles = AdapterHandles::default();

    for venue in Venue::ALL {
        handles.set_backend(venue, Arc::new(StubBackend));
    }

    for venue in Venue::ALL {
        assert!(handles.has_venue(venue), "venue {venue:?} should be set");
    }
}

#[test]
fn configured_venues_reflects_set_backends() {
    let mut handles = AdapterHandles::default();
    handles.set_backend(Venue::Binance, Arc::new(StubBackend));
    handles.set_backend(Venue::Bybit, Arc::new(StubBackend));

    let venues: Vec<_> = handles.configured_venues().collect();
    assert_eq!(venues.len(), 2);
    assert!(venues.contains(&Venue::Binance));
    assert!(venues.contains(&Venue::Bybit));
}

#[tokio::test]
async fn health_check_returns_true_for_stub() {
    let backend: Arc<dyn VenueBackend> = Arc::new(StubBackend);
    assert!(backend.health().await);
}

// ── delegation tests: verify every route goes through the registered backend ──

fn counting_handles() -> (AdapterHandles, Counters) {
    let (backend, counters) = CountingBackend::new();
    let mut handles = AdapterHandles::default();
    handles.set_backend(Venue::Binance, Arc::new(backend));
    (handles, counters)
}

#[test]
fn kline_stream_delegates_to_backend() {
    let (handles, counters) = counting_handles();
    let config = StreamConfig::new(
        vec![],
        Exchange::BinanceLinear,
        None,
        PushFrequency::ServerDefault,
    );
    let _stream = handles.kline_stream(&config);
    assert_eq!(counters.get(&counters.kline), 1);
}

#[test]
fn trade_stream_delegates_to_backend() {
    let (handles, counters) = counting_handles();
    let config = StreamConfig::new(
        vec![],
        Exchange::BinanceLinear,
        None,
        PushFrequency::ServerDefault,
    );
    let _stream = handles.trade_stream(&config);
    assert_eq!(counters.get(&counters.trade), 1);
}

#[test]
fn depth_stream_delegates_to_backend() {
    let (handles, counters) = counting_handles();
    let config = StreamConfig::new(
        binance_ticker_info(),
        Exchange::BinanceLinear,
        None,
        PushFrequency::ServerDefault,
    );
    let _stream = handles.depth_stream(&config);
    assert_eq!(counters.get(&counters.depth), 1);
}

#[tokio::test]
async fn fetch_ticker_metadata_delegates_to_backend() {
    let (handles, counters) = counting_handles();
    let result = handles
        .fetch_ticker_metadata(Venue::Binance, &[MarketKind::Spot])
        .await;
    assert!(result.is_ok());
    assert_eq!(counters.get(&counters.metadata), 1);
}

#[tokio::test]
async fn fetch_ticker_stats_delegates_to_backend() {
    let (handles, counters) = counting_handles();
    let result = handles
        .fetch_ticker_stats(Venue::Binance, &[MarketKind::Spot], None)
        .await;
    assert!(result.is_ok());
    assert_eq!(counters.get(&counters.stats), 1);
}

#[tokio::test]
async fn fetch_klines_delegates_to_backend() {
    let (handles, counters) = counting_handles();
    let result = handles
        .fetch_klines(binance_ticker_info(), Timeframe::M1, None)
        .await;
    assert!(result.is_ok());
    assert_eq!(counters.get(&counters.klines), 1);
}

#[tokio::test]
async fn fetch_open_interest_delegates_to_backend() {
    let (handles, counters) = counting_handles();
    let _ = handles
        .fetch_open_interest(binance_ticker_info(), Timeframe::M1, None)
        .await;
    assert_eq!(counters.get(&counters.oi), 1);
}

#[tokio::test]
async fn fetch_trades_delegates_to_backend() {
    let (handles, counters) = counting_handles();
    let _ = handles
        .fetch_trades(binance_ticker_info(), 0, 0, None)
        .await;
    assert_eq!(counters.get(&counters.trades), 1);
}

#[tokio::test]
async fn request_depth_snapshot_delegates_to_backend() {
    let (handles, counters) = counting_handles();
    let _ = handles.request_depth_snapshot(binance_ticker()).await;
    assert_eq!(counters.get(&counters.snapshot), 1);
}

#[tokio::test]
async fn venue_health_delegates_to_backend() {
    let (handles, counters) = counting_handles();
    let result = handles.venue_health(Venue::Binance).await;
    assert_eq!(result, Some(true));
    assert_eq!(counters.get(&counters.health), 1);
}

#[tokio::test]
async fn venue_health_returns_none_when_not_configured() {
    let handles = AdapterHandles::default();
    assert_eq!(handles.venue_health(Venue::Binance).await, None);
}

#[tokio::test]
async fn request_depth_snapshot_errors_on_missing_venue() {
    let handles = AdapterHandles::default();
    let result = handles.request_depth_snapshot(binance_ticker()).await;
    assert!(result.is_err());
}

#[tokio::test]
async fn stream_methods_return_disconnected_event_on_missing_venue() {
    let handles = AdapterHandles::default();
    let config = StreamConfig::new(
        vec![],
        Exchange::BinanceLinear,
        None,
        PushFrequency::ServerDefault,
    );
    let mut stream = handles.kline_stream(&config);
    let first = stream.next().await;
    assert!(
        matches!(first, Some(Event::Disconnected(Exchange::BinanceLinear, _))),
        "expected Disconnected event, got {first:?}"
    );
}
