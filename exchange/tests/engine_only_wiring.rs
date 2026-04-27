//! Phase 5: Engine-only wiring tests.
//!
//! Verifies that `AdapterHandles` can be fully populated by individually
//! injecting backends (engine-client path) without any native `spawn_*` calls.

use flowsurface_exchange::adapter::venue_backend::VenueBackend;
use flowsurface_exchange::adapter::{AdapterError, StreamConfig};
use flowsurface_exchange::adapter::{AdapterHandles, Event, Exchange, MarketKind, Venue};
use flowsurface_exchange::depth::DepthPayload;
use flowsurface_exchange::{Kline, OpenInterest, TickMultiplier, Trade};
use flowsurface_exchange::{PushFrequency, Ticker, TickerInfo, Timeframe};

use futures::future::BoxFuture;
use futures::stream::{BoxStream, StreamExt, empty};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;

use flowsurface_exchange::adapter::venue_backend::{TickerMetadataMap, TickerStatsMap};

// ── StubEngineBackend: mimics what EngineClientBackend would be ───────────────

struct StubEngineBackend;

impl VenueBackend for StubEngineBackend {
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
        Box::pin(async { Ok(vec![]) })
    }

    fn fetch_trades(
        &self,
        _ticker_info: TickerInfo,
        _from_time: u64,
        _to_time: u64,
        _data_path: Option<PathBuf>,
    ) -> BoxFuture<'_, Result<Vec<Trade>, AdapterError>> {
        Box::pin(async { Ok(vec![]) })
    }

    fn request_depth_snapshot(
        &self,
        _ticker: Ticker,
    ) -> BoxFuture<'_, Result<DepthPayload, AdapterError>> {
        Box::pin(async {
            Err(AdapterError::InvalidRequest(
                "depth snapshot not supported by stub".to_string(),
            ))
        })
    }

    fn health(&self) -> BoxFuture<'_, bool> {
        Box::pin(async { true })
    }
}

// ── helper ────────────────────────────────────────────────────────────────────

fn all_venues_engine_handles() -> AdapterHandles {
    let mut handles = AdapterHandles::default();
    for venue in Venue::ALL {
        handles.set_backend(venue, Arc::new(StubEngineBackend));
    }
    handles
}

// ── tests ─────────────────────────────────────────────────────────────────────

#[test]
fn all_venues_wired_without_spawn() {
    let handles = all_venues_engine_handles();
    for venue in Venue::ALL {
        assert!(
            handles.has_venue(venue),
            "venue {venue:?} should be wired via engine backend"
        );
    }
    assert_eq!(handles.configured_venues().count(), Venue::ALL.len());
}

#[test]
fn empty_handles_default_has_no_venues() {
    let handles = AdapterHandles::default();
    assert_eq!(handles.configured_venues().count(), 0);
    for venue in Venue::ALL {
        assert!(!handles.has_venue(venue));
    }
}

#[tokio::test]
async fn engine_backend_health_returns_true() {
    let handles = all_venues_engine_handles();
    for venue in Venue::ALL {
        let result = handles.venue_health(venue).await;
        assert_eq!(result, Some(true), "venue {venue:?} health should be true");
    }
}

#[tokio::test]
async fn engine_backend_fetch_klines_returns_empty() {
    let handles = all_venues_engine_handles();
    let ticker_info = TickerInfo::new(
        Ticker::new("BTCUSDT", Exchange::BinanceLinear),
        0.1,
        0.001,
        None,
    );
    let result = handles.fetch_klines(ticker_info, Timeframe::M1, None).await;
    assert!(result.is_ok());
    assert!(result.unwrap().is_empty());
}

#[test]
fn kline_stream_delegates_to_engine_backend() {
    let handles = all_venues_engine_handles();
    let config = StreamConfig::new(
        vec![],
        Exchange::BinanceLinear,
        None,
        PushFrequency::ServerDefault,
    );
    // Should not panic - returns an empty stream from StubEngineBackend
    let _stream = handles.kline_stream(&config);
}

#[test]
fn trade_stream_delegates_to_engine_backend() {
    let handles = all_venues_engine_handles();
    let config = StreamConfig::new(
        vec![],
        Exchange::BybitLinear,
        None,
        PushFrequency::ServerDefault,
    );
    let _stream = handles.trade_stream(&config);
}
