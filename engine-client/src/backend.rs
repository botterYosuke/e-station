/// `EngineClientBackend` — a `VenueBackend` that routes all requests through
/// the Python data engine via IPC WebSocket.
///
/// Stream methods subscribe to the engine and translate `EngineEvent`s into
/// `exchange::Event`s. Fetch methods send a command with a unique `request_id`
/// and wait for the matching reply event.
use exchange::{
    Kline, OpenInterest, PushFrequency, TickMultiplier, Ticker, TickerInfo, TickerStats, Timeframe,
    Trade,
    adapter::{
        AdapterError, Event, Exchange, MarketKind, StreamKind, StreamTicksize,
        venue_backend::{TickerMetadataMap, TickerStatsMap, VenueBackend},
    },
    depth::DepthPayload,
};
use futures::{StreamExt, future::BoxFuture, stream::BoxStream};
use std::{collections::HashMap, path::PathBuf, sync::Arc};
use tokio::sync::Mutex;
use uuid::Uuid;

use crate::{
    connection::EngineConnection,
    convert::{depth_levels_to_arc_depth, depth_levels_to_payload},
    depth_tracker::DepthTracker,
    dto::{Command, EngineEvent},
};

/// Timeout for one-shot fetch requests to the Python engine.
const FETCH_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(60);

/// Timeout for a depth snapshot request.
const SNAPSHOT_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(10);

/// Side-channel `Ticker -> TickerDisplayMeta` map populated by
/// `fetch_ticker_metadata` for Tachibana stocks. Wrapped in `Arc<Mutex<_>>`
/// so the UI (which only borrows the backend) can clone the handle and read
/// asynchronously without deadlocking the fetch path.
///
/// Exposed as `pub` so the UI crate (`src`) can store and name the handle type
/// in `TickersTable`. UI callers **must** use `try_lock()` on the rendering
/// path (T35-H8 purity): `lock().await` is forbidden in `view()` /
/// `filtered_rows`. External callers go through `ticker_meta_handle()`.
pub type TickerMetaMap = HashMap<exchange::Ticker, crate::tachibana_meta::TickerDisplayMeta>;

/// Venue-scoped backend for the Python data engine.
///
/// **Invariant:** 1 backend instance corresponds to exactly one venue. Reuse
/// across venues is not supported — the `ticker_meta` side-channel is keyed
/// by `Ticker` (which embeds the `Exchange`) and the `fetch_ticker_metadata`
/// preamble clears Tachibana entries gated on `MarketKind::Stock`, so swapping
/// the `venue` field on a live backend would leave stale state. Spin up a
/// fresh `EngineClientBackend` per venue switch instead.
pub struct EngineClientBackend {
    connection: Arc<EngineConnection>,
    /// Venue string sent over IPC (e.g. `"binance"`).
    venue: String,
    depth_tracker: Arc<Mutex<DepthTracker>>,
    /// B4: Tachibana display metadata captured during `fetch_ticker_metadata`
    /// so the ticker selector can do incremental search by `display_name_ja`.
    /// Empty for crypto venues — `parse_tachibana_ticker_dict` is only called
    /// in the `MarketKind::Stock` branch.
    ticker_meta: Arc<Mutex<TickerMetaMap>>,
}

impl EngineClientBackend {
    pub fn new(connection: Arc<EngineConnection>, venue: impl Into<String>) -> Self {
        Self {
            connection,
            venue: venue.into(),
            depth_tracker: Arc::new(Mutex::new(DepthTracker::new())),
            ticker_meta: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    /// B4: Cheap-clone handle for the UI to read the Tachibana display
    /// metadata side-channel. The map is populated by `fetch_ticker_metadata`
    /// (B3 plumbing path); for crypto venues it stays empty and reads simply
    /// miss.
    ///
    /// **Locking contract (T35-H8 purity):** callers MUST use
    /// `try_lock()` from UI/rendering paths. `lock().await` is forbidden on
    /// the `Application::update` path because it can serialize against the
    /// fetch path that holds the same mutex while staging Tachibana metadata.
    /// Use `blocking_lock()` only from synchronous, non-UI contexts where
    /// blocking is acceptable.
    pub fn ticker_meta_handle(&self) -> Arc<Mutex<TickerMetaMap>> {
        Arc::clone(&self.ticker_meta)
    }

    /// B4 / H1-est: Reset the Tachibana display-metadata side-channel.
    ///
    /// Called on engine reconnect (or before a fresh `fetch_ticker_metadata`
    /// run) so stale entries from a previous session never leak through.
    ///
    /// **Must be called from an async context.** This awaits the side-channel
    /// mutex; the fetch staging section only holds the lock long enough to
    /// drain a `Vec`, so the wait is bounded. We deliberately avoid
    /// `blocking_lock()` here — it panics on a current-thread runtime and
    /// risks deadlock by parking a multi-thread worker.
    pub async fn reset_ticker_meta(&self) {
        self.ticker_meta.lock().await.clear();
    }

    fn market_kind_to_ipc(mk: MarketKind) -> String {
        match mk {
            MarketKind::LinearPerps => "linear_perp".to_string(),
            MarketKind::InversePerps => "inverse_perp".to_string(),
            MarketKind::Spot => "spot".to_string(),
            MarketKind::Stock => "stock".to_string(),
        }
    }

    /// Derive the `Exchange` variant that matches `venue + market_kind`.
    ///
    /// Falls back to `BinanceLinear` so the stream can still emit meaningful events.
    fn exchange_for(venue: &str, market_kind: MarketKind) -> Exchange {
        let venue_parsed = match venue.parse::<exchange::adapter::Venue>() {
            Ok(v) => v,
            Err(_) => {
                log::warn!("exchange_for: unknown venue {venue:?} — falling back to Binance");
                exchange::adapter::Venue::Binance
            }
        };
        match Exchange::from_venue_and_market(venue_parsed, market_kind) {
            Some(ex) => ex,
            None => {
                log::warn!(
                    "exchange_for: no Exchange variant for venue={venue:?} market={market_kind:?} — falling back to BinanceLinear"
                );
                Exchange::BinanceLinear
            }
        }
    }
}

impl VenueBackend for EngineClientBackend {
    // ── Streaming methods ─────────────────────────────────────────────────────

    fn kline_stream(
        &self,
        streams: Vec<(TickerInfo, Timeframe)>,
        market_kind: MarketKind,
    ) -> BoxStream<'static, Event> {
        let connection = Arc::clone(&self.connection);
        let venue = self.venue.clone();

        let stream = async_stream::stream! {
            let exchange = Self::exchange_for(&venue, market_kind);

            for (ticker_info, timeframe) in &streams {
                let ticker_sym = ticker_info.ticker.to_string();
                let tf_str = timeframe_to_str(*timeframe);
                let cmd = Command::Subscribe {
                    venue: venue.clone(),
                    ticker: ticker_sym,
                    stream: "kline".to_string(),
                    timeframe: Some(tf_str),
                    market: Self::market_kind_to_ipc(market_kind),
                };
                if let Err(e) = connection.send(cmd).await {
                    log::error!("kline_stream: subscribe failed: {e}");
                    yield Event::Disconnected(exchange, e.to_string());
                    return;
                }
            }

            yield Event::Connected(exchange);

            let mut rx = connection.subscribe_events();

            loop {
                match rx.recv().await {
                    Ok(EngineEvent::KlineUpdate { venue: ev_venue, ticker, market: ev_market, timeframe: tf_str, kline }) => {
                        if ev_venue != venue { continue; }
                        if !ev_market.is_empty() && ev_market != Self::market_kind_to_ipc(market_kind) { continue; }

                        let Some((ticker_info, timeframe)) = streams.iter().find(|(ti, tf)| {
                            ti.ticker.to_string() == ticker && timeframe_to_str(*tf) == tf_str
                        }) else {
                            continue;
                        };

                        let Some(k) = kline.to_kline() else {
                            log::warn!("kline_stream: failed to parse kline for {ticker}");
                            continue;
                        };

                        let stream_kind = StreamKind::Kline {
                            ticker_info: *ticker_info,
                            timeframe: *timeframe,
                        };
                        yield Event::KlineReceived(stream_kind, k);
                    }
                    Ok(EngineEvent::Disconnected { venue: ev_venue, market: ev_market, reason, .. }) => {
                        if ev_venue != venue { continue; }
                        if !ev_market.is_empty() && ev_market != Self::market_kind_to_ipc(market_kind) { continue; }
                        yield Event::Disconnected(exchange, reason.unwrap_or_default());
                        return;
                    }
                    Err(tokio::sync::broadcast::error::RecvError::Lagged(n)) => {
                        // Klines have no resync mechanism — dropped events mean a permanent gap.
                        // Terminate the stream so the consumer can re-subscribe and get fresh data.
                        log::warn!("kline_stream: lagged by {n} events — stream restarting to recover gap");
                        yield Event::Disconnected(exchange, format!("broadcast lagged by {n} events"));
                        return;
                    }
                    Ok(EngineEvent::ConnectionDropped)
                    | Err(tokio::sync::broadcast::error::RecvError::Closed) => {
                        yield Event::Disconnected(exchange, "engine connection closed".to_string());
                        return;
                    }
                    _ => {}
                }
            }
        };

        stream.boxed()
    }

    fn trade_stream(
        &self,
        tickers: Vec<TickerInfo>,
        market_kind: MarketKind,
    ) -> BoxStream<'static, Event> {
        let connection = Arc::clone(&self.connection);
        let venue = self.venue.clone();

        let stream = async_stream::stream! {
            let exchange = Self::exchange_for(&venue, market_kind);

            for ticker_info in &tickers {
                let ticker_sym = ticker_info.ticker.to_string();
                let cmd = Command::Subscribe {
                    venue: venue.clone(),
                    ticker: ticker_sym,
                    stream: "trade".to_string(),
                    timeframe: None,
                    market: Self::market_kind_to_ipc(market_kind),
                };
                if let Err(e) = connection.send(cmd).await {
                    log::error!("trade_stream: subscribe failed: {e}");
                    yield Event::Disconnected(exchange, e.to_string());
                    return;
                }
            }

            yield Event::Connected(exchange);

            let mut rx = connection.subscribe_events();

            loop {
                match rx.recv().await {
                    Ok(EngineEvent::Trades { venue: ev_venue, ticker, market: ev_market, trades, .. }) => {
                        if ev_venue != venue { continue; }
                        if !ev_market.is_empty() && ev_market != Self::market_kind_to_ipc(market_kind) { continue; }

                        let Some(ticker_info) = tickers
                            .iter()
                            .find(|ti| ti.ticker.to_string() == ticker)
                        else {
                            continue;
                        };

                        let parsed: Vec<Trade> =
                            trades.iter().filter_map(|t| t.to_trade()).collect();
                        if parsed.is_empty() { continue; }

                        let ts = parsed.iter().map(|t| t.time).max().unwrap_or(0);
                        let stream_kind = StreamKind::Trades { ticker_info: *ticker_info };
                        yield Event::TradesReceived(stream_kind, ts, parsed.into_boxed_slice());
                    }
                    Ok(EngineEvent::Disconnected { venue: ev_venue, market: ev_market, reason, .. }) => {
                        if ev_venue != venue { continue; }
                        if !ev_market.is_empty() && ev_market != Self::market_kind_to_ipc(market_kind) { continue; }
                        yield Event::Disconnected(exchange, reason.unwrap_or_default());
                        return;
                    }
                    Err(tokio::sync::broadcast::error::RecvError::Lagged(n)) => {
                        // Trades have no resync mechanism — terminate so the consumer can re-subscribe.
                        log::warn!("trade_stream: lagged by {n} events — stream restarting to recover gap");
                        yield Event::Disconnected(exchange, format!("broadcast lagged by {n} events"));
                        return;
                    }
                    Ok(EngineEvent::ConnectionDropped)
                    | Err(tokio::sync::broadcast::error::RecvError::Closed) => {
                        yield Event::Disconnected(exchange, "engine connection closed".to_string());
                        return;
                    }
                    _ => {}
                }
            }
        };

        stream.boxed()
    }

    fn depth_stream(
        &self,
        ticker_info: TickerInfo,
        _tick_multiplier: Option<TickMultiplier>,
        _push_freq: PushFrequency,
    ) -> BoxStream<'static, Event> {
        let connection = Arc::clone(&self.connection);
        let venue = self.venue.clone();
        let tracker = Arc::clone(&self.depth_tracker);
        let market_kind = ticker_info.market_type();
        let min_ticksize = ticker_info.min_ticksize;

        let stream = async_stream::stream! {
            let exchange = Self::exchange_for(&venue, market_kind);
            let ticker_sym = ticker_info.ticker.to_string();

            let cmd = Command::Subscribe {
                venue: venue.clone(),
                ticker: ticker_sym.clone(),
                stream: "depth".to_string(),
                timeframe: None,
                market: Self::market_kind_to_ipc(market_kind),
            };
            if let Err(e) = connection.send(cmd).await {
                yield Event::Disconnected(exchange, e.to_string());
                return;
            }

            yield Event::Connected(exchange);

            let mut rx = connection.subscribe_events();
            let mut depth = exchange::depth::Depth::default();

            loop {
                match rx.recv().await {
                    Ok(EngineEvent::DepthSnapshot {
                        venue: ev_venue,
                        ticker,
                        market: ev_market,
                        stream_session_id,
                        sequence_id,
                        bids,
                        asks,
                        ..
                    }) => {
                        if ev_venue != venue || ticker != ticker_sym { continue; }
                        if !ev_market.is_empty() && ev_market != Self::market_kind_to_ipc(market_kind) { continue; }

                        tracker.lock().await.on_snapshot(&ticker, &stream_session_id, sequence_id);

                        let arc_depth = depth_levels_to_arc_depth(&bids, &asks);
                        depth = (*arc_depth).clone();
                        let seq_u64 = sequence_id as u64;

                        let stream_kind = StreamKind::Depth {
                            ticker_info,
                            depth_aggr: StreamTicksize::Client,
                            push_freq: PushFrequency::ServerDefault,
                        };
                        yield Event::DepthReceived(stream_kind, seq_u64, arc_depth);
                    }

                    Ok(EngineEvent::DepthDiff {
                        venue: ev_venue,
                        ticker,
                        market: ev_market,
                        stream_session_id,
                        sequence_id,
                        prev_sequence_id,
                        bids,
                        asks,
                    }) => {
                        if ev_venue != venue || ticker != ticker_sym { continue; }
                        if !ev_market.is_empty() && ev_market != Self::market_kind_to_ipc(market_kind) { continue; }

                        let accepted = tracker.lock().await.on_diff(
                            &ticker, &stream_session_id, sequence_id, prev_sequence_id,
                        );

                        if !accepted {
                            if let Err(e) = connection
                                .send(Command::RequestDepthSnapshot {
                                    request_id: Uuid::new_v4().to_string(),
                                    venue: venue.clone(),
                                    ticker: ticker_sym.clone(),
                                    market: Self::market_kind_to_ipc(market_kind),
                                })
                                .await
                            {
                                log::error!("depth_stream: failed to send RequestDepthSnapshot for {ticker_sym}: {e}");
                            }
                            continue;
                        }

                        apply_diff_levels(&mut depth, &bids, &asks, min_ticksize);
                        let seq_u64 = sequence_id as u64;
                        let arc_depth = Arc::new(depth.clone());

                        let stream_kind = StreamKind::Depth {
                            ticker_info,
                            depth_aggr: StreamTicksize::Client,
                            push_freq: PushFrequency::ServerDefault,
                        };
                        yield Event::DepthReceived(stream_kind, seq_u64, arc_depth);
                    }

                    Ok(EngineEvent::DepthGap { venue: ev_venue, ticker, market: ev_market, .. }) => {
                        if ev_venue != venue || ticker != ticker_sym { continue; }
                        if !ev_market.is_empty() && ev_market != Self::market_kind_to_ipc(market_kind) { continue; }
                        // DepthGap is the engine's signal that sequence continuity was lost.
                        // Treat it like a broadcast lag: reset the tracker and request a fresh
                        // snapshot in-stream so the subscription keeps flowing without relying
                        // on the UI layer to rebuild the `Subscription::run_with` identity.
                        log::warn!("depth_stream: DepthGap for {ticker_sym} — forcing snapshot resync");
                        tracker.lock().await.reset_ticker(&ticker_sym);
                        if let Err(e) = connection
                            .send(Command::RequestDepthSnapshot {
                                request_id: Uuid::new_v4().to_string(),
                                venue: venue.clone(),
                                ticker: ticker_sym.clone(),
                                market: Self::market_kind_to_ipc(market_kind),
                            })
                            .await
                        {
                            log::error!("depth_stream: failed to send RequestDepthSnapshot after DepthGap for {ticker_sym}: {e}");
                        }
                    }

                    Ok(EngineEvent::Disconnected { venue: ev_venue, ticker, market: ev_market, reason, .. }) => {
                        if ev_venue != venue || ticker != ticker_sym { continue; }
                        if !ev_market.is_empty() && ev_market != Self::market_kind_to_ipc(market_kind) { continue; }
                        yield Event::Disconnected(exchange, reason.unwrap_or_else(|| "engine disconnected".to_string()));
                        return;
                    }

                    Err(tokio::sync::broadcast::error::RecvError::Lagged(n)) => {
                        // Depth diffs must not be silently dropped (spec §4.4).
                        // Force a snapshot resync so we never serve a corrupted book.
                        log::warn!("depth_stream: lagged by {n} events — forcing resync for {ticker_sym}");
                        tracker.lock().await.reset_ticker(&ticker_sym);
                        if let Err(e) = connection
                            .send(Command::RequestDepthSnapshot {
                                request_id: Uuid::new_v4().to_string(),
                                venue: venue.clone(),
                                ticker: ticker_sym.clone(),
                                market: Self::market_kind_to_ipc(market_kind),
                            })
                            .await
                        {
                            log::error!("depth_stream: failed to send RequestDepthSnapshot after lag for {ticker_sym}: {e}");
                        }
                    }
                    Ok(EngineEvent::ConnectionDropped)
                    | Err(tokio::sync::broadcast::error::RecvError::Closed) => {
                        yield Event::Disconnected(exchange, "engine connection closed".to_string());
                        return;
                    }
                    _ => {}
                }
            }
        };

        stream.boxed()
    }

    // ── Fetch methods ─────────────────────────────────────────────────────────

    fn fetch_ticker_metadata(
        &self,
        markets: &[MarketKind],
    ) -> BoxFuture<'_, Result<TickerMetadataMap, AdapterError>> {
        let connection = Arc::clone(&self.connection);
        let venue = self.venue.clone();
        let markets = markets.to_vec();
        // H1-est: Clone Arc before the async block so the inner future does not
        // borrow `self` across await points.
        let ticker_meta = Arc::clone(&self.ticker_meta);

        Box::pin(async move {
            let mut out: TickerMetadataMap = HashMap::new();

            // H1-est: Idempotent reset of the Tachibana display-meta side
            // channel. Callers may also invoke `reset_ticker_meta()` on
            // reconnect, but doing it here too keeps the post-condition clean:
            // after `fetch_ticker_metadata` returns, the map only reflects the
            // tickers from this run.
            if markets.iter().any(|m| matches!(m, MarketKind::Stock)) {
                ticker_meta.lock().await.clear();
            }

            for &market_kind in &markets {
                let exchange = Self::exchange_for(&venue, market_kind);
                let request_id = Uuid::new_v4().to_string();
                let market_str = Self::market_kind_to_ipc(market_kind);
                let cmd = Command::ListTickers {
                    request_id: request_id.clone(),
                    venue: venue.clone(),
                    market: market_str.clone(),
                };
                let mut rx = connection.subscribe_events();

                if let Err(e) = connection.send(cmd).await {
                    return Err(AdapterError::WebsocketError(e.to_string()));
                }

                // H2-rust: Clone the Arc *outside* the `async move` block so
                // the inner future captures only the cheap handle, not `&self`.
                let ticker_meta_for_capture = Arc::clone(&ticker_meta);
                let market_map = tokio::time::timeout(FETCH_TIMEOUT, async {
                    loop {
                        match rx.recv().await {
                            Ok(EngineEvent::TickerInfo {
                                request_id: rid,
                                tickers,
                                ..
                            }) if rid == request_id => {
                                // Stage Tachibana stock meta in a local buffer
                                // first so we can take the async lock once
                                // (rather than per-iteration) when the parse
                                // loop completes.
                                let mut staged_meta: Vec<(
                                    exchange::Ticker,
                                    crate::tachibana_meta::TickerDisplayMeta,
                                )> = Vec::new();
                                let map: TickerMetadataMap = tickers
                                    .iter()
                                    .filter_map(|t| {
                                        if market_kind == MarketKind::Stock {
                                            // B3 HIGH-U-9 + B4: route Tachibana stock dicts
                                            // through the typed parser and stash the display
                                            // meta in `self.ticker_meta` so the UI can do
                                            // Japanese-name incremental search.
                                            let (ticker, info, meta) =
                                                crate::tachibana_meta::parse_tachibana_ticker_dict(
                                                    t, exchange,
                                                )?;
                                            if meta.display_name_ja().is_none() {
                                                log::debug!(
                                                    "TickerInfo: display_name_ja absent for {ticker}"
                                                );
                                            }
                                            staged_meta.push((ticker, meta));
                                            return Some((ticker, Some(info)));
                                        }
                                        let symbol = t.get("symbol")?.as_str()?;
                                        if !symbol.is_ascii()
                                            || symbol.len() > Ticker::MAX_LEN as usize
                                            || symbol.contains('|')
                                        {
                                            return None;
                                        }
                                        let display_symbol =
                                            t.get("display_symbol").and_then(|v| v.as_str());
                                        let display_symbol = display_symbol.filter(|d| {
                                            d.is_ascii()
                                                && d.len() <= Ticker::MAX_LEN as usize
                                                && !d.contains('|')
                                        });
                                        let min_tick = t.get("min_ticksize")?.as_f64()? as f32;
                                        let min_qty = t.get("min_qty")?.as_f64()? as f32;
                                        let contract_size = t
                                            .get("contract_size")
                                            .and_then(|v| v.as_f64())
                                            .map(|v| v as f32);
                                        let ticker = Ticker::new_with_display(
                                            symbol,
                                            exchange,
                                            display_symbol,
                                        );
                                        let info = TickerInfo::new(
                                            ticker,
                                            min_tick,
                                            min_qty,
                                            contract_size,
                                        );
                                        Some((ticker, Some(info)))
                                    })
                                    .collect();
                                if !staged_meta.is_empty() {
                                    let mut guard = ticker_meta_for_capture.lock().await;
                                    for (t, m) in staged_meta {
                                        guard.insert(t, m);
                                    }
                                }
                                return Ok(map);
                            }
                            Ok(EngineEvent::Error {
                                request_id: Some(rid),
                                code,
                                message,
                            }) if rid == request_id => {
                                return Err(AdapterError::InvalidRequest(format!(
                                    "{code}: {message}"
                                )));
                            }
                            Ok(EngineEvent::ConnectionDropped)
                            | Err(tokio::sync::broadcast::error::RecvError::Closed) => {
                                return Err(AdapterError::EngineRestarting);
                            }
                            Err(tokio::sync::broadcast::error::RecvError::Lagged(n)) => {
                                // M3-silent: never silently swallow a broadcast lag —
                                // we may have skipped past the matching `TickerInfo`
                                // reply for our `request_id`, which would deadlock
                                // until the timeout. Surface as a recoverable error
                                // so the caller can retry.
                                log::warn!(
                                    "fetch_ticker_metadata: broadcast lagged by {n} \
                                     events — aborting to avoid missed reply"
                                );
                                return Err(AdapterError::WebsocketError(format!(
                                    "fetch_ticker_metadata lagged by {n}"
                                )));
                            }
                            Ok(_) => continue,
                        }
                    }
                })
                .await
                .map_err(|_| {
                    AdapterError::WebsocketError("fetch_ticker_metadata timeout".to_string())
                })??;

                out.extend(market_map);
            }

            Ok(out)
        })
    }

    fn fetch_ticker_stats(
        &self,
        markets: &[MarketKind],
        _contract_sizes: Option<HashMap<Ticker, f32>>,
    ) -> BoxFuture<'_, Result<TickerStatsMap, AdapterError>> {
        let connection = Arc::clone(&self.connection);
        let venue = self.venue.clone();
        let markets = markets.to_vec();

        Box::pin(async move {
            let mut out: TickerStatsMap = HashMap::new();

            for &market_kind in &markets {
                let exchange = Self::exchange_for(&venue, market_kind);
                let request_id = Uuid::new_v4().to_string();
                let cmd = Command::FetchTickerStats {
                    request_id: request_id.clone(),
                    venue: venue.clone(),
                    ticker: "__all__".to_string(),
                    market: Self::market_kind_to_ipc(market_kind),
                };

                let mut rx = connection.subscribe_events();

                if let Err(e) = connection.send(cmd).await {
                    return Err(AdapterError::WebsocketError(e.to_string()));
                }

                let market_stats: TickerStatsMap = tokio::time::timeout(FETCH_TIMEOUT, async {
                    loop {
                        match rx.recv().await {
                            Ok(EngineEvent::TickerStats {
                                request_id: rid,
                                ticker,
                                stats,
                                ..
                            }) if rid == request_id => {
                                // Python returns a bulk {symbol: stats} object when ticker=="__all__"
                                if ticker == "__all__" {
                                    let bulk: HashMap<String, serde_json::Value> =
                                        serde_json::from_value(stats).unwrap_or_else(|e| {
                                            log::error!("fetch_ticker_stats: failed to parse bulk stats: {e}");
                                            HashMap::new()
                                        });
                                    return Ok(bulk
                                        .into_iter()
                                        .filter_map(|(sym, sv)| {
                                            let ts = serde_json::from_value::<TickerStats>(sv)
                                                .map_err(|e| {
                                                    log::warn!(
                                                        "fetch_ticker_stats: parse error \
                                                         for {sym}: {e}"
                                                    );
                                                })
                                                .ok()?;
                                            if !sym.is_ascii() {
                                                log::warn!(
                                                    "fetch_ticker_stats: skipping non-ASCII ticker key: {:?} (exchange={exchange:?})",
                                                    sym
                                                );
                                                return None;
                                            }
                                            Some((Ticker::new(&sym, exchange), ts))
                                        })
                                        .collect());
                                }
                                // Single-ticker fallback (backward compat)
                                let mut m = TickerStatsMap::new();
                                match serde_json::from_value::<TickerStats>(stats) {
                                    Ok(ts) => {
                                        m.insert(Ticker::new(&ticker, exchange), ts);
                                    }
                                    Err(e) => {
                                        log::warn!(
                                            "fetch_ticker_stats: parse error for {ticker}: {e}"
                                        );
                                    }
                                }
                                return Ok(m);
                            }
                            Ok(EngineEvent::Error { request_id: Some(rid), code, message })
                                if rid == request_id =>
                            {
                                return Err(AdapterError::InvalidRequest(format!(
                                    "{code}: {message}"
                                )));
                            }
                            Ok(EngineEvent::ConnectionDropped)
                            | Err(tokio::sync::broadcast::error::RecvError::Closed) => {
                                return Err(AdapterError::EngineRestarting);
                            }
                            _ => continue,
                        }
                    }
                })
                .await
                .map_err(|_| {
                    AdapterError::WebsocketError("fetch_ticker_stats timeout".to_string())
                })??;

                out.extend(market_stats);
            }

            Ok(out)
        })
    }

    fn fetch_klines(
        &self,
        ticker_info: TickerInfo,
        timeframe: Timeframe,
        range: Option<(u64, u64)>,
    ) -> BoxFuture<'_, Result<Vec<Kline>, AdapterError>> {
        let connection = Arc::clone(&self.connection);
        let venue = self.venue.clone();

        Box::pin(async move {
            let request_id = Uuid::new_v4().to_string();
            let ticker_sym = ticker_info.ticker.to_string();
            let tf_str = timeframe_to_str(timeframe);
            let (limit, start_ms, end_ms) = match range {
                Some((s, e)) => {
                    let ms = timeframe.to_milliseconds().max(1);
                    let limit = (e.saturating_sub(s) / ms).min(1500) as u32;
                    (limit, Some(s as i64), Some(e as i64))
                }
                None => (500, None, None),
            };

            let cmd = Command::FetchKlines {
                request_id: request_id.clone(),
                venue,
                ticker: ticker_sym,
                timeframe: tf_str,
                limit,
                start_ms,
                end_ms,
                market: Self::market_kind_to_ipc(ticker_info.market_type()),
            };
            let mut rx = connection.subscribe_events();
            connection
                .send(cmd)
                .await
                .map_err(|e| AdapterError::WebsocketError(e.to_string()))?;

            tokio::time::timeout(FETCH_TIMEOUT, async {
                loop {
                    match rx.recv().await {
                        Ok(EngineEvent::Klines {
                            request_id: rid,
                            klines,
                            ..
                        }) if rid == request_id => {
                            let result: Vec<Kline> =
                                klines.iter().filter_map(|k| k.to_kline()).collect();
                            return Ok(result);
                        }
                        Ok(EngineEvent::Error {
                            request_id: Some(rid),
                            code,
                            message,
                        }) if rid == request_id => {
                            return Err(AdapterError::InvalidRequest(format!("{code}: {message}")));
                        }
                        Ok(EngineEvent::ConnectionDropped)
                        | Err(tokio::sync::broadcast::error::RecvError::Closed) => {
                            return Err(AdapterError::EngineRestarting);
                        }
                        _ => continue,
                    }
                }
            })
            .await
            .map_err(|_| AdapterError::WebsocketError("fetch_klines timeout".to_string()))?
        })
    }

    fn fetch_open_interest(
        &self,
        ticker_info: TickerInfo,
        timeframe: Timeframe,
        range: Option<(u64, u64)>,
    ) -> BoxFuture<'_, Result<Vec<OpenInterest>, AdapterError>> {
        let connection = Arc::clone(&self.connection);
        let venue = self.venue.clone();

        Box::pin(async move {
            let request_id = Uuid::new_v4().to_string();
            let ticker_sym = ticker_info.ticker.to_string();
            let tf_str = timeframe_to_str(timeframe);
            let (limit, start_ms, end_ms) = match range {
                Some((s, e)) => {
                    let ms = timeframe.to_milliseconds().max(1);
                    let limit = (e.saturating_sub(s) / ms).min(200) as u32;
                    (limit, Some(s as i64), Some(e as i64))
                }
                None => (200, None, None),
            };

            let cmd = Command::FetchOpenInterest {
                request_id: request_id.clone(),
                venue,
                ticker: ticker_sym,
                timeframe: tf_str,
                limit,
                start_ms,
                end_ms,
                market: Self::market_kind_to_ipc(ticker_info.market_type()),
            };
            let mut rx = connection.subscribe_events();
            connection
                .send(cmd)
                .await
                .map_err(|e| AdapterError::WebsocketError(e.to_string()))?;

            tokio::time::timeout(FETCH_TIMEOUT, async {
                loop {
                    match rx.recv().await {
                        Ok(EngineEvent::OpenInterest {
                            request_id: rid,
                            data,
                            ..
                        }) if rid == request_id => {
                            let result: Vec<OpenInterest> =
                                data.iter().filter_map(|p| p.to_open_interest()).collect();
                            return Ok(result);
                        }
                        Ok(EngineEvent::Error {
                            request_id: Some(rid),
                            code,
                            message,
                        }) if rid == request_id => {
                            return Err(AdapterError::InvalidRequest(format!("{code}: {message}")));
                        }
                        Ok(EngineEvent::ConnectionDropped)
                        | Err(tokio::sync::broadcast::error::RecvError::Closed) => {
                            return Err(AdapterError::EngineRestarting);
                        }
                        _ => continue,
                    }
                }
            })
            .await
            .map_err(|_| AdapterError::WebsocketError("fetch_open_interest timeout".to_string()))?
        })
    }

    fn fetch_trades(
        &self,
        ticker_info: TickerInfo,
        from_time: u64,
        to_time: u64,
        data_path: Option<PathBuf>,
    ) -> BoxFuture<'_, Result<Vec<Trade>, AdapterError>> {
        let connection = Arc::clone(&self.connection);
        let venue = self.venue.clone();

        Box::pin(async move {
            let request_id = Uuid::new_v4().to_string();
            let ticker_sym = ticker_info.ticker.to_string();
            let data_path_str = data_path.map(|p| p.to_string_lossy().into_owned());

            let cmd = Command::FetchTrades {
                request_id: request_id.clone(),
                venue,
                ticker: ticker_sym,
                market: Self::market_kind_to_ipc(ticker_info.market_type()),
                start_ms: from_time as i64,
                end_ms: to_time as i64,
                data_path: data_path_str,
            };
            let mut rx = connection.subscribe_events();
            connection
                .send(cmd)
                .await
                .map_err(|e| AdapterError::WebsocketError(e.to_string()))?;

            tokio::time::timeout(FETCH_TIMEOUT, async {
                let mut accumulated: Vec<Trade> = Vec::new();
                loop {
                    match rx.recv().await {
                        Ok(EngineEvent::TradesFetched {
                            request_id: rid,
                            trades,
                            is_last,
                            ..
                        }) if rid == request_id => {
                            accumulated.extend(trades.iter().filter_map(|t| t.to_trade()));
                            if is_last {
                                return Ok(accumulated);
                            }
                        }
                        Ok(EngineEvent::Error { request_id: Some(rid), code, message })
                            if rid == request_id =>
                        {
                            return Err(AdapterError::InvalidRequest(format!(
                                "{code}: {message}"
                            )));
                        }
                        Err(tokio::sync::broadcast::error::RecvError::Lagged(n)) => {
                            log::warn!("fetch_trades: broadcast lagged by {n} — aborting to avoid partial result");
                            return Err(AdapterError::WebsocketError(
                                format!("fetch_trades broadcast lagged by {n}")
                            ));
                        }
                        Ok(EngineEvent::ConnectionDropped)
                        | Err(tokio::sync::broadcast::error::RecvError::Closed) => {
                            return Err(AdapterError::EngineRestarting);
                        }
                        _ => continue,
                    }
                }
            })
            .await
            .map_err(|_| AdapterError::WebsocketError("fetch_trades timeout".to_string()))?
        })
    }

    fn request_depth_snapshot(
        &self,
        ticker: Ticker,
    ) -> BoxFuture<'_, Result<DepthPayload, AdapterError>> {
        let connection = Arc::clone(&self.connection);
        let venue = self.venue.clone();

        Box::pin(async move {
            let ticker_sym = ticker.to_string();
            let request_id = Uuid::new_v4().to_string();

            let cmd = Command::RequestDepthSnapshot {
                request_id: request_id.clone(),
                venue: venue.clone(),
                ticker: ticker_sym.clone(),
                market: Self::market_kind_to_ipc(ticker.market_type()),
            };
            let mut rx = connection.subscribe_events();
            connection
                .send(cmd)
                .await
                .map_err(|e| AdapterError::WebsocketError(e.to_string()))?;

            tokio::time::timeout(SNAPSHOT_TIMEOUT, async {
                loop {
                    match rx.recv().await {
                        Ok(EngineEvent::DepthSnapshot {
                            request_id: ref rid,
                            ticker: ref t,
                            sequence_id,
                            ref bids,
                            ref asks,
                            ..
                        }) if t == &ticker_sym && rid.as_deref() == Some(request_id.as_str()) => {
                            return Ok(depth_levels_to_payload(sequence_id, bids, asks));
                        }
                        Ok(EngineEvent::Error {
                            request_id: Some(ref rid),
                            ref code,
                            ref message,
                        }) if rid == &request_id => {
                            return Err(AdapterError::InvalidRequest(format!("{code}: {message}")));
                        }
                        Ok(EngineEvent::ConnectionDropped)
                        | Err(tokio::sync::broadcast::error::RecvError::Closed) => {
                            return Err(AdapterError::EngineRestarting);
                        }
                        _ => continue,
                    }
                }
            })
            .await
            .map_err(|_| {
                AdapterError::WebsocketError("request_depth_snapshot timeout".to_string())
            })?
        })
    }

    fn health(&self) -> BoxFuture<'_, bool> {
        let connection = Arc::clone(&self.connection);
        Box::pin(async move {
            let request_id = Uuid::new_v4().to_string();
            let mut rx = connection.subscribe_events();
            if connection
                .send(Command::Ping {
                    request_id: request_id.clone(),
                })
                .await
                .is_err()
            {
                return false;
            }
            tokio::time::timeout(std::time::Duration::from_secs(5), async {
                loop {
                    match rx.recv().await {
                        Ok(EngineEvent::Pong { request_id: rid }) if rid == request_id => {
                            return true;
                        }
                        Err(tokio::sync::broadcast::error::RecvError::Closed) => return false,
                        Err(tokio::sync::broadcast::error::RecvError::Lagged(_)) => continue,
                        _ => continue,
                    }
                }
            })
            .await
            .unwrap_or(false)
        })
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/// Apply depth diff levels to an existing `Depth` mutably.
fn apply_diff_levels(
    depth: &mut exchange::depth::Depth,
    bids: &[crate::dto::DepthLevel],
    asks: &[crate::dto::DepthLevel],
    min_ticksize: exchange::unit::MinTicksize,
) {
    use exchange::unit::{Price, Qty};

    let apply = |map: &mut std::collections::BTreeMap<Price, Qty>,
                 levels: &[crate::dto::DepthLevel]| {
        for level in levels {
            let Ok(p) = level.price.parse::<f32>() else {
                continue;
            };
            let Ok(q) = level.qty.parse::<f32>() else {
                continue;
            };
            let price = Price::from_f32(p).round_to_min_tick(min_ticksize);
            let qty = Qty::from_f32(q);
            if qty.is_zero() {
                map.remove(&price);
            } else {
                map.insert(price, qty);
            }
        }
    };

    apply(&mut depth.bids, bids);
    apply(&mut depth.asks, asks);
}

/// Convert a `Timeframe` to the IPC string representation expected by the Python engine.
pub fn timeframe_to_str(tf: Timeframe) -> String {
    match tf {
        Timeframe::MS100 => "100ms",
        Timeframe::MS200 => "200ms",
        Timeframe::MS300 => "300ms",
        Timeframe::MS500 => "500ms",
        Timeframe::MS1000 => "1s",
        Timeframe::M1 => "1m",
        Timeframe::M3 => "3m",
        Timeframe::M5 => "5m",
        Timeframe::M15 => "15m",
        Timeframe::M30 => "30m",
        Timeframe::H1 => "1h",
        Timeframe::H2 => "2h",
        Timeframe::H4 => "4h",
        Timeframe::H12 => "12h",
        Timeframe::D1 => "1d",
    }
    .to_string()
}
