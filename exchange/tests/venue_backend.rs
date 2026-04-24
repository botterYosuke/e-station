//! Phase 0.5: VenueBackend trait abstraction tests.
//!
//! RED phase: these tests reference types and APIs that do not yet exist,
//! so the crate will fail to compile until the implementation is in place.

use flowsurface_exchange::adapter::{AdapterError, AdapterHandles, Event, MarketKind, Venue};
use flowsurface_exchange::adapter::venue_backend::{TickerMetadataMap, TickerStatsMap, VenueBackend};
use flowsurface_exchange::depth::DepthPayload;
use flowsurface_exchange::{
    Kline, OpenInterest, PushFrequency, Ticker, TickMultiplier, TickerInfo, Timeframe, Trade,
};

use futures::future::BoxFuture;
use futures::stream::{BoxStream, empty};
use futures::StreamExt;
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;

/// A no-op backend used to verify that `AdapterHandles` accepts trait objects.
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

#[test]
fn set_backend_makes_venue_available() {
    let mut handles = AdapterHandles::default();
    assert!(!handles.has_venue(Venue::Binance));
    assert!(!handles.has_venue(Venue::Bybit));

    handles.set_backend(Venue::Binance, Arc::new(StubBackend));
    assert!(handles.has_venue(Venue::Binance));
    assert!(!handles.has_venue(Venue::Bybit), "only Binance should be set");
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
