use crate::{
    Kline, OpenInterest, PushFrequency, Ticker, TickMultiplier, TickerInfo, TickerStats, Timeframe,
    Trade,
    depth::DepthPayload,
};
use super::{AdapterError, Event, Exchange, MarketKind};
use super::hub::{binance, bybit, hyperliquid, mexc, okex};

use futures::future::BoxFuture;
use futures::stream::BoxStream;
use std::{collections::HashMap, path::PathBuf};

pub type TickerMetadataMap = HashMap<Ticker, Option<TickerInfo>>;
pub type TickerStatsMap = HashMap<Ticker, TickerStats>;

/// Per-venue data backend.
///
/// The two planned implementations are:
/// - [`NativeBackend`]: wraps an existing hub handle (current behaviour, Phase 0.5).
/// - `EngineClientBackend`: routes requests to the Python data engine via IPC (Phase 2).
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
        data_path: Option<PathBuf>,
    ) -> BoxFuture<'_, Result<Vec<Trade>, AdapterError>>;

    fn request_depth_snapshot(
        &self,
        ticker: Ticker,
    ) -> BoxFuture<'_, Result<DepthPayload, AdapterError>>;

    fn health(&self) -> BoxFuture<'_, bool>;
}

fn unsupported(feature: &str, exchange: Exchange) -> AdapterError {
    AdapterError::InvalidRequest(format!("{feature} not available for {exchange}"))
}

/// Wraps an existing hub handle, preserving the current native behaviour.
pub enum NativeBackend {
    Binance(binance::BinanceHandle),
    Bybit(bybit::BybitHandle),
    Hyperliquid(hyperliquid::HyperliquidHandle),
    Okex(okex::OkexHandle),
    Mexc(mexc::MexcHandle),
}

impl VenueBackend for NativeBackend {
    fn kline_stream(
        &self,
        streams: Vec<(TickerInfo, Timeframe)>,
        market_kind: MarketKind,
    ) -> BoxStream<'static, Event> {
        use futures::StreamExt;
        match self {
            NativeBackend::Binance(h) => {
                h.clone().connect_kline_stream(streams, market_kind).boxed()
            }
            NativeBackend::Bybit(h) => {
                h.clone().connect_kline_stream(streams, market_kind).boxed()
            }
            NativeBackend::Hyperliquid(h) => {
                h.clone().connect_kline_stream(streams, market_kind).boxed()
            }
            NativeBackend::Okex(h) => {
                h.clone().connect_kline_stream(streams, market_kind).boxed()
            }
            NativeBackend::Mexc(h) => {
                h.clone().connect_kline_stream(streams, market_kind).boxed()
            }
        }
    }

    fn trade_stream(
        &self,
        tickers: Vec<TickerInfo>,
        market_kind: MarketKind,
    ) -> BoxStream<'static, Event> {
        use futures::StreamExt;
        match self {
            NativeBackend::Binance(h) => {
                h.clone().connect_trade_stream(tickers, market_kind).boxed()
            }
            NativeBackend::Bybit(h) => {
                h.clone().connect_trade_stream(tickers, market_kind).boxed()
            }
            NativeBackend::Hyperliquid(h) => {
                h.clone().connect_trade_stream(tickers, market_kind).boxed()
            }
            NativeBackend::Okex(h) => {
                h.clone().connect_trade_stream(tickers, market_kind).boxed()
            }
            NativeBackend::Mexc(h) => {
                h.clone().connect_trade_stream(tickers, market_kind).boxed()
            }
        }
    }

    fn depth_stream(
        &self,
        ticker_info: TickerInfo,
        tick_multiplier: Option<TickMultiplier>,
        push_freq: PushFrequency,
    ) -> BoxStream<'static, Event> {
        use futures::StreamExt;
        match self {
            NativeBackend::Binance(h) => {
                h.clone().connect_depth_stream(ticker_info, push_freq).boxed()
            }
            NativeBackend::Bybit(h) => {
                h.clone().connect_depth_stream(ticker_info, push_freq).boxed()
            }
            NativeBackend::Hyperliquid(h) => h
                .clone()
                .connect_depth_stream(ticker_info, tick_multiplier, push_freq)
                .boxed(),
            NativeBackend::Okex(h) => {
                h.clone().connect_depth_stream(ticker_info, push_freq).boxed()
            }
            NativeBackend::Mexc(h) => {
                h.clone().connect_depth_stream(ticker_info, push_freq).boxed()
            }
        }
    }

    fn fetch_ticker_metadata(
        &self,
        markets: &[MarketKind],
    ) -> BoxFuture<'_, Result<TickerMetadataMap, AdapterError>> {
        match self {
            NativeBackend::Binance(h) => {
                let h = h.clone();
                let markets = markets.to_vec();
                Box::pin(async move {
                    let mut out = HashMap::default();
                    for market in &markets {
                        out.extend(
                            h.fetch_ticker_metadata(binance::BinanceMarketScope::metadata(
                                *market,
                            ))
                            .await?,
                        );
                    }
                    Ok(out)
                })
            }
            NativeBackend::Bybit(h) => {
                let h = h.clone();
                let markets = markets.to_vec();
                Box::pin(async move {
                    let mut out = HashMap::default();
                    for market in &markets {
                        out.extend(h.fetch_ticker_metadata(*market).await?);
                    }
                    Ok(out)
                })
            }
            NativeBackend::Hyperliquid(h) => {
                let h = h.clone();
                let markets = markets.to_vec();
                Box::pin(async move {
                    let mut out = HashMap::default();
                    for market in &markets {
                        out.extend(h.fetch_ticker_metadata(*market).await?);
                    }
                    Ok(out)
                })
            }
            NativeBackend::Okex(h) => {
                let h = h.clone();
                let markets = markets.to_vec();
                Box::pin(async move { h.fetch_ticker_metadata(markets).await })
            }
            NativeBackend::Mexc(h) => {
                let h = h.clone();
                let markets = markets.to_vec();
                Box::pin(async move {
                    h.fetch_ticker_metadata(mexc::MexcMarketScope::metadata(&markets))
                        .await
                })
            }
        }
    }

    fn fetch_ticker_stats(
        &self,
        markets: &[MarketKind],
        contract_sizes: Option<HashMap<Ticker, f32>>,
    ) -> BoxFuture<'_, Result<TickerStatsMap, AdapterError>> {
        match self {
            NativeBackend::Binance(h) => {
                let h = h.clone();
                let markets = markets.to_vec();
                Box::pin(async move {
                    let mut out = HashMap::default();
                    for market in &markets {
                        out.extend(
                            h.fetch_ticker_stats(binance::BinanceMarketScope::stats(
                                *market,
                                contract_sizes.clone(),
                            ))
                            .await?,
                        );
                    }
                    Ok(out)
                })
            }
            NativeBackend::Bybit(h) => {
                let h = h.clone();
                let markets = markets.to_vec();
                Box::pin(async move {
                    let mut out = HashMap::default();
                    for market in &markets {
                        out.extend(h.fetch_ticker_stats(*market).await?);
                    }
                    Ok(out)
                })
            }
            NativeBackend::Hyperliquid(h) => {
                let h = h.clone();
                let markets = markets.to_vec();
                Box::pin(async move {
                    let mut out = HashMap::default();
                    for market in &markets {
                        out.extend(h.fetch_ticker_stats(*market).await?);
                    }
                    Ok(out)
                })
            }
            NativeBackend::Okex(h) => {
                let h = h.clone();
                let markets = markets.to_vec();
                Box::pin(async move { h.fetch_ticker_stats(markets).await })
            }
            NativeBackend::Mexc(h) => {
                let h = h.clone();
                let markets = markets.to_vec();
                Box::pin(async move {
                    h.fetch_ticker_stats(mexc::MexcMarketScope::stats(&markets, contract_sizes))
                        .await
                })
            }
        }
    }

    fn fetch_klines(
        &self,
        ticker_info: TickerInfo,
        timeframe: Timeframe,
        range: Option<(u64, u64)>,
    ) -> BoxFuture<'_, Result<Vec<Kline>, AdapterError>> {
        match self {
            NativeBackend::Binance(h) => {
                let h = h.clone();
                Box::pin(async move { h.fetch_klines(ticker_info, timeframe, range).await })
            }
            NativeBackend::Bybit(h) => {
                let h = h.clone();
                Box::pin(async move { h.fetch_klines(ticker_info, timeframe, range).await })
            }
            NativeBackend::Hyperliquid(h) => {
                let h = h.clone();
                Box::pin(async move { h.fetch_klines(ticker_info, timeframe, range).await })
            }
            NativeBackend::Okex(h) => {
                let h = h.clone();
                Box::pin(async move { h.fetch_klines(ticker_info, timeframe, range).await })
            }
            NativeBackend::Mexc(h) => {
                let h = h.clone();
                Box::pin(async move { h.fetch_klines(ticker_info, timeframe, range).await })
            }
        }
    }

    fn fetch_open_interest(
        &self,
        ticker_info: TickerInfo,
        timeframe: Timeframe,
        range: Option<(u64, u64)>,
    ) -> BoxFuture<'_, Result<Vec<OpenInterest>, AdapterError>> {
        match self {
            NativeBackend::Binance(h) => {
                let exchange = ticker_info.ticker.exchange;
                match exchange {
                    Exchange::BinanceLinear | Exchange::BinanceInverse => {
                        let h = h.clone();
                        Box::pin(async move {
                            h.fetch_open_interest(ticker_info, timeframe, range).await
                        })
                    }
                    _ => Box::pin(async move { Err(unsupported("Open interest", exchange)) }),
                }
            }
            NativeBackend::Bybit(h) => {
                let exchange = ticker_info.ticker.exchange;
                match exchange {
                    Exchange::BybitLinear | Exchange::BybitInverse => {
                        let h = h.clone();
                        Box::pin(async move {
                            h.fetch_open_interest(ticker_info, timeframe, range).await
                        })
                    }
                    _ => Box::pin(async move { Err(unsupported("Open interest", exchange)) }),
                }
            }
            NativeBackend::Okex(h) => {
                let exchange = ticker_info.ticker.exchange;
                match exchange {
                    Exchange::OkexLinear | Exchange::OkexInverse => {
                        let h = h.clone();
                        Box::pin(async move {
                            h.fetch_open_interest(ticker_info, timeframe, range).await
                        })
                    }
                    _ => Box::pin(async move { Err(unsupported("Open interest", exchange)) }),
                }
            }
            NativeBackend::Hyperliquid(_) | NativeBackend::Mexc(_) => {
                let exchange = ticker_info.ticker.exchange;
                Box::pin(async move { Err(unsupported("Open interest", exchange)) })
            }
        }
    }

    fn fetch_trades(
        &self,
        ticker_info: TickerInfo,
        from_time: u64,
        data_path: Option<PathBuf>,
    ) -> BoxFuture<'_, Result<Vec<Trade>, AdapterError>> {
        match self {
            NativeBackend::Binance(h) => {
                let h = h.clone();
                Box::pin(async move {
                    h.fetch_trades(ticker_info, from_time, data_path).await
                })
            }
            NativeBackend::Hyperliquid(h) => {
                let h = h.clone();
                Box::pin(async move {
                    h.fetch_trades(ticker_info, from_time, data_path).await
                })
            }
            NativeBackend::Bybit(_) | NativeBackend::Okex(_) | NativeBackend::Mexc(_) => {
                let exchange = ticker_info.ticker.exchange;
                Box::pin(async move { Err(unsupported("Trade fetch", exchange)) })
            }
        }
    }

    fn request_depth_snapshot(
        &self,
        ticker: Ticker,
    ) -> BoxFuture<'_, Result<DepthPayload, AdapterError>> {
        match self {
            NativeBackend::Binance(h) => {
                let h = h.clone();
                Box::pin(async move { h.fetch_depth_snapshot(ticker).await })
            }
            NativeBackend::Hyperliquid(h) => {
                let h = h.clone();
                Box::pin(async move { h.fetch_depth_snapshot(ticker).await })
            }
            NativeBackend::Mexc(h) => {
                let h = h.clone();
                Box::pin(async move { h.fetch_depth_snapshot(ticker).await })
            }
            NativeBackend::Bybit(_) | NativeBackend::Okex(_) => Box::pin(async {
                Err(AdapterError::InvalidRequest(
                    "Depth snapshot not supported for this venue".to_string(),
                ))
            }),
        }
    }

    fn health(&self) -> BoxFuture<'_, bool> {
        Box::pin(async { true })
    }
}
