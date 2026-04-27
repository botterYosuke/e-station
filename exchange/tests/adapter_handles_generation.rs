//! Tests for `AdapterHandles::bump_generation` and its effect on the Hash impl.
//!
//! `generation` is the sole field factored into the hash.  Iced derives
//! subscription IDs from this hash, so a generation bump forces iced to
//! cancel old stream tasks and start fresh ones — the mechanism that
//! restores subscriptions after an engine reconnect.

use flowsurface_exchange::adapter::venue_backend::{
    TickerMetadataMap, TickerStatsMap, VenueBackend,
};
use flowsurface_exchange::adapter::{AdapterError, Event, Exchange, MarketKind, Venue};
use flowsurface_exchange::adapter::{AdapterHandles, StreamConfig};
use flowsurface_exchange::depth::DepthPayload;
use flowsurface_exchange::{
    Kline, OpenInterest, PushFrequency, TickMultiplier, Ticker, TickerInfo, Timeframe, Trade,
};

use futures::future::BoxFuture;
use futures::stream::{BoxStream, StreamExt, empty};
use std::collections::HashMap;
use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};
use std::path::PathBuf;
use std::sync::Arc;

// ── Helpers ───────────────────────────────────────────────────────────────────

fn compute_hash<T: Hash>(val: &T) -> u64 {
    let mut hasher = DefaultHasher::new();
    val.hash(&mut hasher);
    hasher.finish()
}

// ── Minimal stub backend ──────────────────────────────────────────────────────

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
        Box::pin(async { Err(AdapterError::InvalidRequest("stub".to_string())) })
    }

    fn health(&self) -> BoxFuture<'_, bool> {
        Box::pin(async { true })
    }
}

// ── Hash stability ────────────────────────────────────────────────────────────

#[test]
fn default_handles_produce_stable_hash() {
    let h1 = AdapterHandles::default();
    let h2 = AdapterHandles::default();
    assert_eq!(
        compute_hash(&h1),
        compute_hash(&h2),
        "two freshly constructed default handles must hash identically"
    );
}

// ── bump_generation changes the hash ─────────────────────────────────────────

#[test]
fn bump_generation_changes_hash() {
    let mut handles = AdapterHandles::default();
    let before = compute_hash(&handles);
    handles.bump_generation();
    let after = compute_hash(&handles);
    assert_ne!(
        before, after,
        "bump_generation must produce a different hash so iced restarts subscriptions"
    );
}

#[test]
fn multiple_bumps_each_change_hash() {
    let mut handles = AdapterHandles::default();
    let h0 = compute_hash(&handles);
    handles.bump_generation();
    let h1 = compute_hash(&handles);
    handles.bump_generation();
    let h2 = compute_hash(&handles);
    assert_ne!(h0, h1, "first bump must change the hash");
    assert_ne!(h1, h2, "second bump must change the hash again");
    assert_ne!(
        h0, h2,
        "hash after two bumps must differ from the initial hash"
    );
}

// ── set_backend alone must NOT change the hash ───────────────────────────────

#[test]
fn set_backend_without_bump_preserves_hash() {
    let mut handles = AdapterHandles::default();
    let before = compute_hash(&handles);
    handles.set_backend(Venue::Binance, Arc::new(StubBackend));
    let after = compute_hash(&handles);
    assert_eq!(
        before, after,
        "set_backend alone must not change the hash — only bump_generation affects subscription IDs"
    );
}

#[test]
fn set_all_backends_without_bump_preserves_hash() {
    let mut handles = AdapterHandles::default();
    let before = compute_hash(&handles);
    for venue in Venue::ALL {
        handles.set_backend(venue, Arc::new(StubBackend));
    }
    let after = compute_hash(&handles);
    assert_eq!(
        before, after,
        "populating all venue backends must not change the hash without a generation bump"
    );
}

// ── generation is the sole hash discriminant ─────────────────────────────────

#[test]
fn handles_with_same_generation_hash_equally_regardless_of_backends() {
    let mut with_backends = AdapterHandles::default();
    for venue in Venue::ALL {
        with_backends.set_backend(venue, Arc::new(StubBackend));
    }
    let empty = AdapterHandles::default();
    assert_eq!(
        compute_hash(&with_backends),
        compute_hash(&empty),
        "generation is the sole hash input — same generation means same hash even with different backends"
    );
}

#[test]
fn bumped_handles_hash_differs_from_unbumped_with_same_backends() {
    let mut bumped = AdapterHandles::default();
    bumped.set_backend(Venue::Bybit, Arc::new(StubBackend));
    bumped.bump_generation();

    let mut unbumped = AdapterHandles::default();
    unbumped.set_backend(Venue::Bybit, Arc::new(StubBackend));

    assert_ne!(
        compute_hash(&bumped),
        compute_hash(&unbumped),
        "bumped handles must hash differently from unbumped even with identical backends"
    );
}

// ── integration: reconnect scenario ──────────────────────────────────────────

#[test]
fn reconnect_scenario_produces_unique_hashes_each_cycle() {
    // Simulate: initial connect → disconnect → reconnect → disconnect → reconnect
    let mut handles = AdapterHandles::default();
    for venue in Venue::ALL {
        handles.set_backend(venue, Arc::new(StubBackend));
    }
    let h_initial = compute_hash(&handles);

    // First reconnect: swap backends and bump
    for venue in Venue::ALL {
        handles.set_backend(venue, Arc::new(StubBackend));
    }
    handles.bump_generation();
    let h_reconnect1 = compute_hash(&handles);

    // Second reconnect
    for venue in Venue::ALL {
        handles.set_backend(venue, Arc::new(StubBackend));
    }
    handles.bump_generation();
    let h_reconnect2 = compute_hash(&handles);

    assert_ne!(
        h_initial, h_reconnect1,
        "first reconnect must change the hash"
    );
    assert_ne!(
        h_reconnect1, h_reconnect2,
        "second reconnect must change the hash again"
    );
    assert_ne!(h_initial, h_reconnect2);
}

// ── kline_stream still delegates after set_backend ───────────────────────────

#[test]
fn kline_stream_works_after_set_backend_and_bump() {
    let mut handles = AdapterHandles::default();
    for venue in Venue::ALL {
        handles.set_backend(venue, Arc::new(StubBackend));
    }
    handles.bump_generation();

    let config = StreamConfig::new(
        vec![],
        Exchange::BinanceLinear,
        None,
        PushFrequency::ServerDefault,
    );
    // Must not panic — StubBackend returns an empty stream
    let _stream = handles.kline_stream(&config);
}
