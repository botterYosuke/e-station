use exchange::adapter::{AdapterError, AdapterHandles, Exchange, StreamKind};
use exchange::{Kline, OpenInterest, TickerInfo, Trade};
use iced::{
    Task,
    task::{Handle, Straw, sipper},
};
use rustc_hash::FxHashMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use uuid::Uuid;

static TRADE_FETCH_ENABLED: AtomicBool = AtomicBool::new(false);

pub fn toggle_trade_fetch(value: bool) {
    TRADE_FETCH_ENABLED.store(value, Ordering::Relaxed);
}

pub fn is_trade_fetch_enabled() -> bool {
    TRADE_FETCH_ENABLED.load(Ordering::Relaxed)
}

#[derive(Debug, Clone)]
pub enum FetchedData {
    Trades {
        batch: Vec<Trade>,
        until_time: u64,
    },
    Klines {
        data: Vec<Kline>,
        req_id: Option<uuid::Uuid>,
    },
    OI {
        data: Vec<OpenInterest>,
        req_id: Option<uuid::Uuid>,
    },
}

#[derive(thiserror::Error, Debug, Clone)]
pub enum ReqError {
    #[error("Request is already failed: {0}")]
    Failed(String),
    #[error("Request overlaps with an existing request")]
    Overlaps,
}

#[derive(PartialEq, Debug)]
enum RequestStatus {
    Pending,
    Completed(u64),
    Failed(String),
}

#[derive(Default)]
pub struct RequestHandler {
    requests: FxHashMap<Uuid, FetchRequest>,
}

impl RequestHandler {
    pub fn add_request(&mut self, fetch: FetchRange) -> Result<Option<Uuid>, ReqError> {
        let request = FetchRequest::new(fetch);
        let id = Uuid::new_v4();

        if let Some((existing_id, existing_req)) = self.requests.iter().find_map(|(k, v)| {
            if v.same_with(&request) {
                Some((*k, v))
            } else {
                None
            }
        }) {
            return match &existing_req.status {
                RequestStatus::Failed(error_msg) => Err(ReqError::Failed(error_msg.clone())),
                RequestStatus::Completed(ts) => {
                    // retry completed requests after a cooldown
                    // to handle data source failures or outdated results gracefully
                    if chrono::Utc::now().timestamp_millis() as u64 - ts > 30_000 {
                        Ok(Some(existing_id))
                    } else {
                        Ok(None)
                    }
                }
                RequestStatus::Pending => Err(ReqError::Overlaps),
            };
        }

        self.requests.insert(id, request);
        Ok(Some(id))
    }

    pub fn mark_completed(&mut self, id: Uuid) {
        if let Some(request) = self.requests.get_mut(&id) {
            let timestamp = chrono::Utc::now().timestamp_millis() as u64;
            request.status = RequestStatus::Completed(timestamp);
        } else {
            log::warn!("Request not found: {:?}", id);
        }
    }

    pub fn mark_failed(&mut self, id: Uuid, error: String) {
        if let Some(request) = self.requests.get_mut(&id) {
            request.status = RequestStatus::Failed(error);
        } else {
            log::warn!("Request not found: {:?}", id);
        }
    }
}

#[derive(PartialEq, Debug, Clone, Copy)]
pub enum FetchRange {
    Kline(u64, u64),
    OpenInterest(u64, u64),
    Trades(u64, u64),
}

#[derive(PartialEq, Debug)]
struct FetchRequest {
    fetch_type: FetchRange,
    status: RequestStatus,
}

impl FetchRequest {
    fn new(fetch_type: FetchRange) -> Self {
        FetchRequest {
            fetch_type,
            status: RequestStatus::Pending,
        }
    }

    fn same_with(&self, other: &FetchRequest) -> bool {
        match (&self.fetch_type, &other.fetch_type) {
            (FetchRange::Kline(s1, e1), FetchRange::Kline(s2, e2)) => e1 == e2 && s1 == s2,
            (FetchRange::OpenInterest(s1, e1), FetchRange::OpenInterest(s2, e2)) => {
                e1 == e2 && s1 == s2
            }
            (FetchRange::Trades(s1, e1), FetchRange::Trades(s2, e2)) => e1 == e2 && s1 == s2,
            _ => false,
        }
    }
}

pub struct FetchSpec {
    pub req_id: uuid::Uuid,
    pub fetch: FetchRange,
    pub stream: Option<StreamKind>,
}

impl From<(uuid::Uuid, FetchRange, Option<StreamKind>)> for FetchSpec {
    fn from(t: (uuid::Uuid, FetchRange, Option<StreamKind>)) -> Self {
        FetchSpec {
            req_id: t.0,
            fetch: t.1,
            stream: t.2,
        }
    }
}

impl std::fmt::Debug for FetchSpec {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("FetchSpec")
            .field("req_id", &self.req_id)
            .field("fetch", &self.fetch)
            .field("stream", &self.stream)
            .finish()
    }
}

impl Clone for FetchSpec {
    fn clone(&self) -> Self {
        FetchSpec {
            req_id: self.req_id,
            fetch: self.fetch,
            stream: self.stream,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum InfoKind {
    FetchingKlines,
    FetchingTrades(usize),
    FetchingOI,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum FetchTaskStatus {
    Loading(InfoKind),
    Completed,
}

#[derive(Debug, Clone)]
pub enum FetchUpdate {
    Status {
        pane_id: Uuid,
        status: FetchTaskStatus,
    },
    Data {
        layout_id: Uuid,
        pane_id: Uuid,
        stream: StreamKind,
        data: FetchedData,
    },
    Error {
        pane_id: Uuid,
        error: String,
    },
}

pub fn request_fetch(
    handles: AdapterHandles,
    pane_id: Uuid,
    ready_streams: &[StreamKind],
    layout_id: Uuid,
    req_id: Uuid,
    fetch: FetchRange,
    stream: Option<StreamKind>,
    on_trade_handle: &mut impl FnMut(Handle),
) -> Task<FetchUpdate> {
    match fetch {
        FetchRange::Kline(from, to) => {
            let kline_stream = if let Some(s) = stream {
                Some((s, pane_id))
            } else {
                ready_streams.iter().find_map(|stream| {
                    if let StreamKind::Kline { .. } = stream {
                        Some((*stream, pane_id))
                    } else {
                        None
                    }
                })
            };

            if let Some((stream, pane_uid)) = kline_stream {
                return kline_fetch_task(
                    handles.clone(),
                    layout_id,
                    pane_uid,
                    stream,
                    Some(req_id),
                    Some((from, to)),
                );
            }
        }
        FetchRange::OpenInterest(from, to) => {
            let kline_stream = if let Some(s) = stream {
                Some((s, pane_id))
            } else {
                ready_streams.iter().find_map(|stream| {
                    if let StreamKind::Kline { .. } = stream {
                        Some((*stream, pane_id))
                    } else {
                        None
                    }
                })
            };

            if let Some((stream, pane_uid)) = kline_stream {
                return oi_fetch_task(
                    handles.clone(),
                    layout_id,
                    pane_uid,
                    stream,
                    Some(req_id),
                    Some((from, to)),
                );
            }
        }
        FetchRange::Trades(from_time, to_time) => {
            let trade_info = ready_streams.iter().find_map(|stream| {
                if let StreamKind::Trades { ticker_info } = stream {
                    Some((*ticker_info, pane_id, *stream))
                } else {
                    None
                }
            });

            if let Some((ticker_info, pane_id, stream)) = trade_info {
                let is_binance = matches!(
                    ticker_info.exchange(),
                    Exchange::BinanceSpot | Exchange::BinanceLinear | Exchange::BinanceInverse
                );

                if is_binance {
                    let data_path = data::data_path(Some("market_data/binance/"));

                    let (task, handle) = Task::sip(
                        fetch_trades_batched(
                            handles.clone(),
                            ticker_info,
                            from_time,
                            to_time,
                            data_path,
                        ),
                        move |batch| {
                            let data = FetchedData::Trades {
                                batch,
                                until_time: to_time,
                            };

                            FetchUpdate::Data {
                                layout_id,
                                pane_id,
                                data,
                                stream,
                            }
                        },
                        move |result| match result {
                            Ok(()) => FetchUpdate::Status {
                                pane_id,
                                status: FetchTaskStatus::Completed,
                            },
                            Err(err) => {
                                log::error!("Trade fetch failed: {err}");
                                FetchUpdate::Error {
                                    pane_id,
                                    error: err.ui_message(),
                                }
                            }
                        },
                    )
                    .abortable();

                    on_trade_handle(handle.abort_on_drop());

                    return task;
                }
            }
        }
    }

    Task::none()
}

pub fn request_fetch_many(
    handles: AdapterHandles,
    pane_id: Uuid,
    ready_streams: &[StreamKind],
    layout_id: Uuid,
    reqs: impl IntoIterator<Item = (Uuid, FetchRange, Option<StreamKind>)>,
    mut on_trade_handle: impl FnMut(Handle),
) -> Task<FetchUpdate> {
    let mut tasks = Vec::new();

    for (req_id, fetch, stream) in reqs {
        tasks.push(request_fetch(
            handles.clone(),
            pane_id,
            ready_streams,
            layout_id,
            req_id,
            fetch,
            stream,
            &mut on_trade_handle,
        ));
    }

    Task::batch(tasks)
}

pub fn oi_fetch_task(
    handles: AdapterHandles,
    layout_id: Uuid,
    pane_id: Uuid,
    stream: StreamKind,
    req_id: Option<Uuid>,
    range: Option<(u64, u64)>,
) -> Task<FetchUpdate> {
    let update_status = Task::done(FetchUpdate::Status {
        pane_id,
        status: FetchTaskStatus::Loading(InfoKind::FetchingOI),
    });

    let fetch_task = match stream {
        StreamKind::Kline {
            ticker_info,
            timeframe,
        } => {
            let fetch = async move {
                handles
                    .fetch_open_interest(ticker_info, timeframe, range)
                    .await
            };

            Task::perform(
                iced::futures::TryFutureExt::map_err(fetch, |err| {
                    log::error!("Open interest fetch failed: {err}");
                    err.ui_message()
                }),
                move |result| match result {
                    Ok(oi) => {
                        let data = FetchedData::OI { data: oi, req_id };
                        FetchUpdate::Data {
                            layout_id,
                            pane_id,
                            data,
                            stream,
                        }
                    }
                    Err(err) => FetchUpdate::Error {
                        pane_id,
                        error: err,
                    },
                },
            )
        }
        _ => Task::none(),
    };

    update_status.chain(fetch_task)
}

pub fn kline_fetch_task(
    handles: AdapterHandles,
    layout_id: Uuid,
    pane_id: Uuid,
    stream: StreamKind,
    req_id: Option<Uuid>,
    range: Option<(u64, u64)>,
) -> Task<FetchUpdate> {
    let update_status = Task::done(FetchUpdate::Status {
        pane_id,
        status: FetchTaskStatus::Loading(InfoKind::FetchingKlines),
    });

    let fetch_task = match stream {
        StreamKind::Kline {
            ticker_info,
            timeframe,
        } => {
            let fetch = async move { handles.fetch_klines(ticker_info, timeframe, range).await };

            Task::perform(
                iced::futures::TryFutureExt::map_err(fetch, |err| {
                    log::error!("Kline fetch failed: {err}");
                    err.ui_message()
                }),
                move |result| match result {
                    Ok(klines) => {
                        let data = FetchedData::Klines {
                            data: klines,
                            req_id,
                        };
                        FetchUpdate::Data {
                            layout_id,
                            pane_id,
                            data,
                            stream,
                        }
                    }
                    Err(err) => FetchUpdate::Error {
                        pane_id,
                        error: err,
                    },
                },
            )
        }
        _ => Task::none(),
    };

    update_status.chain(fetch_task)
}

pub fn fetch_trades_batched(
    handles: AdapterHandles,
    ticker_info: TickerInfo,
    from_time: u64,
    to_time: u64,
    data_path: PathBuf,
) -> impl Straw<(), Vec<Trade>, AdapterError> {
    sipper(async move |mut progress| {
        let mut latest_trade_t = from_time;
        const DAY_MS: u64 = 86_400_000;
        const EMPTY_DAYS_WARN_THRESHOLD: u32 = 7;
        let mut consecutive_empty_days: u32 = 0;

        while latest_trade_t < to_time {
            match handles
                .fetch_trades(
                    ticker_info,
                    latest_trade_t,
                    to_time,
                    Some(data_path.clone()),
                )
                .await
            {
                Ok(batch) => {
                    let prev_cursor = latest_trade_t;

                    if batch.is_empty() {
                        consecutive_empty_days += 1;
                        if consecutive_empty_days == EMPTY_DAYS_WARN_THRESHOLD {
                            log::warn!(
                                "fetch_trades_batched: {} consecutive empty days at t={}, continuing toward to_time={}",
                                consecutive_empty_days,
                                latest_trade_t,
                                to_time,
                            );
                        }
                        latest_trade_t = (latest_trade_t / DAY_MS + 1) * DAY_MS;
                    } else {
                        consecutive_empty_days = 0;
                        let last_trade_t = batch.last().map_or(latest_trade_t, |trade| trade.time);
                        latest_trade_t = (last_trade_t / DAY_MS + 1) * DAY_MS;
                        let () = progress.send(batch).await;
                    }

                    // The cursor must advance every iteration, otherwise we loop
                    // forever. The day-aligned formula above guarantees a strict
                    // increment, but we double-check to avoid silent runaway if
                    // a future change breaks that invariant.
                    if latest_trade_t <= prev_cursor {
                        return Err(AdapterError::InvalidRequest(format!(
                            "fetch_trades_batched: cursor failed to advance at t={prev_cursor} — aborting to avoid infinite loop",
                        )));
                    }
                }
                Err(err) => return Err(err),
            }
        }

        Ok(())
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use exchange::adapter::Event;
    use exchange::adapter::venue_backend::{TickerMetadataMap, TickerStatsMap};
    use exchange::adapter::{
        AdapterError, AdapterHandles, Exchange, MarketKind, Venue, VenueBackend,
    };
    use exchange::depth::DepthPayload;
    use exchange::{
        Kline, OpenInterest, PushFrequency, TickMultiplier, Ticker, TickerInfo, Timeframe, Trade,
    };
    use iced::futures::StreamExt as _;
    use iced::futures::future::BoxFuture;
    use iced::futures::stream::BoxStream;
    use iced::task::Sipper as _;
    use std::collections::HashMap;
    use std::path::PathBuf;
    use std::sync::Arc;

    fn make_trade(ts_ms: u64) -> Trade {
        use exchange::unit::{Price, Qty};
        Trade {
            time: ts_ms,
            is_sell: false,
            price: Price::from_f32(100.0),
            qty: Qty::from_f32(1.0),
        }
    }

    /// Mock backend that always returns an empty trade list.
    struct AlwaysEmptyTrades;

    impl VenueBackend for AlwaysEmptyTrades {
        fn fetch_trades(
            &self,
            _: TickerInfo,
            _: u64,
            _: u64,
            _: Option<PathBuf>,
        ) -> BoxFuture<'_, Result<Vec<Trade>, AdapterError>> {
            Box::pin(std::future::ready(Ok(vec![])))
        }

        fn kline_stream(
            &self,
            _: Vec<(TickerInfo, Timeframe)>,
            _: MarketKind,
        ) -> BoxStream<'static, Event> {
            unimplemented!()
        }
        fn trade_stream(&self, _: Vec<TickerInfo>, _: MarketKind) -> BoxStream<'static, Event> {
            unimplemented!()
        }
        fn depth_stream(
            &self,
            _: TickerInfo,
            _: Option<TickMultiplier>,
            _: PushFrequency,
        ) -> BoxStream<'static, Event> {
            unimplemented!()
        }
        fn fetch_ticker_metadata(
            &self,
            _: &[MarketKind],
        ) -> BoxFuture<'_, Result<TickerMetadataMap, AdapterError>> {
            unimplemented!()
        }
        fn fetch_ticker_stats(
            &self,
            _: &[MarketKind],
            _: Option<HashMap<Ticker, f32>>,
        ) -> BoxFuture<'_, Result<TickerStatsMap, AdapterError>> {
            unimplemented!()
        }
        fn fetch_klines(
            &self,
            _: TickerInfo,
            _: Timeframe,
            _: Option<(u64, u64)>,
        ) -> BoxFuture<'_, Result<Vec<Kline>, AdapterError>> {
            unimplemented!()
        }
        fn fetch_open_interest(
            &self,
            _: TickerInfo,
            _: Timeframe,
            _: Option<(u64, u64)>,
        ) -> BoxFuture<'_, Result<Vec<OpenInterest>, AdapterError>> {
            unimplemented!()
        }
        fn request_depth_snapshot(
            &self,
            _: Ticker,
        ) -> BoxFuture<'_, Result<DepthPayload, AdapterError>> {
            unimplemented!()
        }
        fn health(&self) -> BoxFuture<'_, bool> {
            unimplemented!()
        }
    }

    /// Mock that returns a pre-programmed sequence of responses, one per call.
    /// After the sequence is exhausted every subsequent call returns `Ok(vec![])`.
    struct SequencedTrades {
        responses: Arc<tokio::sync::Mutex<std::collections::VecDeque<Vec<Trade>>>>,
    }

    impl SequencedTrades {
        fn new(responses: Vec<Vec<Trade>>) -> Self {
            Self {
                responses: Arc::new(tokio::sync::Mutex::new(responses.into())),
            }
        }
    }

    impl VenueBackend for SequencedTrades {
        fn fetch_trades(
            &self,
            _: TickerInfo,
            _: u64,
            _: u64,
            _: Option<PathBuf>,
        ) -> BoxFuture<'_, Result<Vec<Trade>, AdapterError>> {
            let responses = Arc::clone(&self.responses);
            Box::pin(async move {
                let mut q = responses.lock().await;
                Ok(q.pop_front().unwrap_or_default())
            })
        }

        fn kline_stream(
            &self,
            _: Vec<(TickerInfo, Timeframe)>,
            _: MarketKind,
        ) -> BoxStream<'static, Event> {
            unimplemented!()
        }
        fn trade_stream(&self, _: Vec<TickerInfo>, _: MarketKind) -> BoxStream<'static, Event> {
            unimplemented!()
        }
        fn depth_stream(
            &self,
            _: TickerInfo,
            _: Option<TickMultiplier>,
            _: PushFrequency,
        ) -> BoxStream<'static, Event> {
            unimplemented!()
        }
        fn fetch_ticker_metadata(
            &self,
            _: &[MarketKind],
        ) -> BoxFuture<'_, Result<TickerMetadataMap, AdapterError>> {
            unimplemented!()
        }
        fn fetch_ticker_stats(
            &self,
            _: &[MarketKind],
            _: Option<HashMap<Ticker, f32>>,
        ) -> BoxFuture<'_, Result<TickerStatsMap, AdapterError>> {
            unimplemented!()
        }
        fn fetch_klines(
            &self,
            _: TickerInfo,
            _: Timeframe,
            _: Option<(u64, u64)>,
        ) -> BoxFuture<'_, Result<Vec<Kline>, AdapterError>> {
            unimplemented!()
        }
        fn fetch_open_interest(
            &self,
            _: TickerInfo,
            _: Timeframe,
            _: Option<(u64, u64)>,
        ) -> BoxFuture<'_, Result<Vec<OpenInterest>, AdapterError>> {
            unimplemented!()
        }
        fn request_depth_snapshot(
            &self,
            _: Ticker,
        ) -> BoxFuture<'_, Result<DepthPayload, AdapterError>> {
            unimplemented!()
        }
        fn health(&self) -> BoxFuture<'_, bool> {
            unimplemented!()
        }
    }

    #[tokio::test]
    async fn consecutive_empty_days_resets_after_data() {
        // 3 empty days → 1 data day → 3 empty days → 1 data day → loop ends → Ok
        const DAY_MS: u64 = 86_400_000;
        let responses: Vec<Vec<Trade>> = vec![
            vec![],                           // day 0 — empty
            vec![],                           // day 1 — empty
            vec![],                           // day 2 — empty
            vec![make_trade(3 * DAY_MS + 1)], // day 3 — data; counter resets
            vec![],                           // day 4 — empty
            vec![],                           // day 5 — empty
            vec![],                           // day 6 — empty
            vec![make_trade(7 * DAY_MS + 1)], // day 7 — data; counter resets
                                              // to_time = 8*DAY_MS reached → loop exits
        ];
        let mut handles = AdapterHandles::default();
        handles.set_backend(Venue::Binance, Arc::new(SequencedTrades::new(responses)));

        let ticker = Ticker::new("BTCUSDT", Exchange::BinanceLinear);
        let ticker_info = TickerInfo::new(ticker, 0.1, 0.001, None);

        let mut straw =
            fetch_trades_batched(handles, ticker_info, 0, 8 * DAY_MS, PathBuf::from(".")).pin();

        while straw.next().await.is_some() {}
        let result = straw.await;
        assert!(
            result.is_ok(),
            "expected Ok after counter reset, got {result:?}"
        );
    }

    #[tokio::test]
    async fn latest_trade_t_advances_to_next_day_boundary() {
        // Trade arrives mid-day; next fetch should start at the following day boundary.
        const DAY_MS: u64 = 86_400_000;
        // Trade is at 12h into day 0; from_time=0, to_time=DAY_MS.
        // After processing the batch, latest_trade_t becomes DAY_MS (day 1 midnight).
        // DAY_MS >= to_time=DAY_MS, so the loop terminates → Ok.
        let responses = vec![vec![make_trade(DAY_MS / 2)]];
        let mut handles = AdapterHandles::default();
        handles.set_backend(Venue::Binance, Arc::new(SequencedTrades::new(responses)));

        let ticker = Ticker::new("BTCUSDT", Exchange::BinanceLinear);
        let ticker_info = TickerInfo::new(ticker, 0.1, 0.001, None);

        let mut straw =
            fetch_trades_batched(handles, ticker_info, 0, DAY_MS, PathBuf::from(".")).pin();

        let mut emitted = 0usize;
        while straw.next().await.is_some() {
            emitted += 1;
        }
        assert_eq!(emitted, 1, "expected exactly one progress emission");

        let result = straw.await;
        assert!(
            result.is_ok(),
            "expected Ok after day-boundary advance, got {result:?}"
        );
    }

    #[tokio::test]
    async fn long_empty_span_completes_without_error() {
        // Illiquid symbol / data gap: even 30+ consecutive empty days must not
        // abort the fetch. The loop should advance through every day and
        // terminate naturally at to_time with Ok.
        let mut handles = AdapterHandles::default();
        handles.set_backend(Venue::Binance, Arc::new(AlwaysEmptyTrades));

        let ticker = Ticker::new("BTCUSDT", Exchange::BinanceLinear);
        let ticker_info = TickerInfo::new(ticker, 0.1, 0.001, None);

        const DAY_MS: u64 = 86_400_000;
        let from_time = 0u64;
        let to_time = 30 * DAY_MS;

        let mut straw =
            fetch_trades_batched(handles, ticker_info, from_time, to_time, PathBuf::from("."))
                .pin();

        while straw.next().await.is_some() {}

        let result = straw.await;
        assert!(
            result.is_ok(),
            "Expected Ok when every day is empty, got {result:?}"
        );
    }

    #[tokio::test]
    async fn span_beyond_legacy_365_day_cap_completes_without_error() {
        // Regression: previously a hard `MAX_EMPTY_DAYS = 365` cap aborted any
        // fetch that contained 365+ consecutive empty days, which broke valid
        // long-history requests for illiquid symbols or ranges spanning the
        // pre-listing era. The cursor advances monotonically by one day per
        // empty batch, so the loop is bounded by `to_time` and needs no cap.
        let mut handles = AdapterHandles::default();
        handles.set_backend(Venue::Binance, Arc::new(AlwaysEmptyTrades));

        let ticker = Ticker::new("BTCUSDT", Exchange::BinanceLinear);
        let ticker_info = TickerInfo::new(ticker, 0.1, 0.001, None);

        const DAY_MS: u64 = 86_400_000;
        let from_time = 0u64;
        let to_time = 400 * DAY_MS;

        let mut straw =
            fetch_trades_batched(handles, ticker_info, from_time, to_time, PathBuf::from("."))
                .pin();

        while straw.next().await.is_some() {}

        let result = straw.await;
        assert!(
            result.is_ok(),
            "Expected Ok over 400 empty days (>365), got {result:?}"
        );
    }

    #[test]
    fn fetch_range_trades_dedup_same_with() {
        // Regression: same_with() must detect identical Trades ranges to prevent
        // duplicate parallel fetches of the same time span.

        let trades_1 = FetchRequest::new(FetchRange::Trades(1000, 2000));
        let trades_2 = FetchRequest::new(FetchRange::Trades(1000, 2000));
        let trades_3 = FetchRequest::new(FetchRange::Trades(1000, 3000));

        assert!(
            trades_1.same_with(&trades_2),
            "Identical Trades ranges (1000..2000) must be detected as the same"
        );
        assert!(
            !trades_1.same_with(&trades_3),
            "Different Trades ranges (1000..2000 vs 1000..3000) must be different"
        );

        let kline = FetchRequest::new(FetchRange::Kline(1000, 2000));
        assert!(
            !trades_1.same_with(&kline),
            "Trades and Kline must never be the same, even with identical time ranges"
        );
    }
}
