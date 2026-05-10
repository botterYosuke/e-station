use exchange::{PushFrequency, Ticker, TickerInfo, Timeframe, adapter::StreamKind};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub enum PersistStreamKind {
    Kline {
        ticker: Ticker,
        timeframe: Timeframe,
    },
    Depth(PersistDepth),
    Trades {
        ticker: Ticker,
    },
    /// Deprecated combined stream, kept for backward compatibility.
    /// Will be converted to separate Depth and Trades on load.
    DepthAndTrades(PersistDepth),
}

#[derive(Debug, Clone, Deserialize, Serialize, PartialEq)]
pub struct PersistDepth {
    pub ticker: Ticker,
    #[serde(default = "default_depth_aggr")]
    pub depth_aggr: exchange::adapter::StreamTicksize,
    #[serde(default = "default_push_freq")]
    pub push_freq: PushFrequency,
}

impl From<StreamKind> for PersistStreamKind {
    fn from(stream: StreamKind) -> Self {
        match stream {
            StreamKind::Kline {
                ticker_info,
                timeframe,
            } => PersistStreamKind::Kline {
                ticker: ticker_info.ticker,
                timeframe,
            },
            StreamKind::Depth {
                ticker_info,
                depth_aggr,
                push_freq,
            } => PersistStreamKind::Depth(PersistDepth {
                ticker: ticker_info.ticker,
                depth_aggr,
                push_freq,
            }),
            StreamKind::Trades { ticker_info } => PersistStreamKind::Trades {
                ticker: ticker_info.ticker,
            },
        }
    }
}

impl PersistStreamKind {
    /// Try to convert into runtime StreamKind list. `resolver` should return Some(TickerInfo) for a ticker,
    /// otherwise the conversion fails (so caller can trigger a refresh / fetch).
    pub fn into_stream_kinds<F>(self, mut resolver: F) -> Result<Vec<StreamKind>, String>
    where
        F: FnMut(&Ticker) -> Option<TickerInfo>,
    {
        match self {
            PersistStreamKind::Kline { ticker, timeframe } => resolver(&ticker)
                .map(|ti| {
                    vec![StreamKind::Kline {
                        ticker_info: ti,
                        timeframe,
                    }]
                })
                .ok_or_else(|| format!("TickerInfo not found for {}", ticker)),
            PersistStreamKind::Depth(d) => resolver(&d.ticker)
                .map(|ti| {
                    vec![StreamKind::Depth {
                        ticker_info: ti,
                        depth_aggr: d.depth_aggr,
                        push_freq: d.push_freq,
                    }]
                })
                .ok_or_else(|| format!("TickerInfo not found for {}", d.ticker)),
            PersistStreamKind::Trades { ticker } => resolver(&ticker)
                .map(|ti| vec![StreamKind::Trades { ticker_info: ti }])
                .ok_or_else(|| format!("TickerInfo not found for {}", ticker)),
            PersistStreamKind::DepthAndTrades(d) => resolver(&d.ticker)
                .map(|ti| {
                    vec![
                        StreamKind::Depth {
                            ticker_info: ti,
                            depth_aggr: d.depth_aggr,
                            push_freq: d.push_freq,
                        },
                        StreamKind::Trades { ticker_info: ti },
                    ]
                })
                .ok_or_else(|| format!("TickerInfo not found for {}", d.ticker)),
        }
    }
}

impl PersistStreamKind {
    /// Returns the `Venue` this stream's ticker belongs to.
    pub fn venue(&self) -> exchange::adapter::Venue {
        let ticker = match self {
            PersistStreamKind::Kline { ticker, .. } => ticker,
            PersistStreamKind::Depth(d) => &d.ticker,
            PersistStreamKind::Trades { ticker } => ticker,
            PersistStreamKind::DepthAndTrades(d) => &d.ticker,
        };
        ticker.exchange.venue()
    }
}

fn default_depth_aggr() -> exchange::adapter::StreamTicksize {
    exchange::adapter::StreamTicksize::Client
}

fn default_push_freq() -> PushFrequency {
    PushFrequency::ServerDefault
}

#[cfg(test)]
mod tests {
    use super::*;
    use exchange::{
        Ticker, Timeframe,
        adapter::{Exchange, Venue},
    };
    use rstest::rstest;

    fn make_kline(exchange: Exchange) -> PersistStreamKind {
        PersistStreamKind::Kline {
            ticker: Ticker::new("BTCUSDT", exchange),
            timeframe: Timeframe::M15,
        }
    }

    fn make_depth(exchange: Exchange) -> PersistStreamKind {
        PersistStreamKind::Depth(PersistDepth {
            ticker: Ticker::new("BTCUSDT", exchange),
            depth_aggr: default_depth_aggr(),
            push_freq: default_push_freq(),
        })
    }

    fn make_trades(exchange: Exchange) -> PersistStreamKind {
        PersistStreamKind::Trades {
            ticker: Ticker::new("BTCUSDT", exchange),
        }
    }

    fn make_depth_and_trades(exchange: Exchange) -> PersistStreamKind {
        PersistStreamKind::DepthAndTrades(PersistDepth {
            ticker: Ticker::new("BTCUSDT", exchange),
            depth_aggr: default_depth_aggr(),
            push_freq: default_push_freq(),
        })
    }

    // ── 既存テスト（後方互換性保持）────────────────────────────────────────────

    #[test]
    fn kline_bybit_linear_venue_is_bybit() {
        let stream = make_kline(Exchange::BybitLinear);
        assert_eq!(stream.venue(), exchange::adapter::Venue::Bybit);
    }

    #[test]
    fn depth_tachibana_venue_is_tachibana() {
        let stream = make_depth(Exchange::TachibanaStock);
        assert_eq!(stream.venue(), exchange::adapter::Venue::Tachibana);
    }

    #[test]
    fn trades_kabu_station_venue_is_kabu_station() {
        let stream = make_trades(Exchange::KabuStationStock);
        assert_eq!(stream.venue(), exchange::adapter::Venue::KabuStation);
    }

    #[test]
    fn depth_and_trades_binance_linear_venue_is_binance() {
        let stream = make_depth_and_trades(Exchange::BinanceLinear);
        assert_eq!(stream.venue(), exchange::adapter::Venue::Binance);
    }

    // ── rstest パラメタライズテスト — Kline × 複数 Exchange ─────────────────────

    #[rstest]
    #[case(Exchange::BybitLinear, Venue::Bybit)]
    #[case(Exchange::BybitInverse, Venue::Bybit)]
    #[case(Exchange::BybitSpot, Venue::Bybit)]
    #[case(Exchange::BinanceLinear, Venue::Binance)]
    #[case(Exchange::BinanceInverse, Venue::Binance)]
    #[case(Exchange::BinanceSpot, Venue::Binance)]
    #[case(Exchange::HyperliquidLinear, Venue::Hyperliquid)]
    #[case(Exchange::HyperliquidSpot, Venue::Hyperliquid)]
    #[case(Exchange::OkexLinear, Venue::Okex)]
    #[case(Exchange::OkexInverse, Venue::Okex)]
    #[case(Exchange::OkexSpot, Venue::Okex)]
    #[case(Exchange::MexcLinear, Venue::Mexc)]
    #[case(Exchange::MexcInverse, Venue::Mexc)]
    #[case(Exchange::MexcSpot, Venue::Mexc)]
    #[case(Exchange::TachibanaStock, Venue::Tachibana)]
    #[case(Exchange::KabuStationStock, Venue::KabuStation)]
    #[case(Exchange::KabuStationTse, Venue::KabuStation)]
    #[case(Exchange::KabuStationNse, Venue::KabuStation)]
    #[case(Exchange::KabuStationFse, Venue::KabuStation)]
    #[case(Exchange::KabuStationSse, Venue::KabuStation)]
    #[case(Exchange::KabuStationFuture, Venue::KabuStation)]
    #[case(Exchange::KabuStationOption, Venue::KabuStation)]
    #[case(Exchange::ReplayStock, Venue::Replay)]
    fn kline_venue(#[case] exchange: Exchange, #[case] expected: Venue) {
        let stream = make_kline(exchange);
        assert_eq!(
            stream.venue(),
            expected,
            "Kline({exchange:?}).venue() should be {expected:?}"
        );
    }

    // ── rstest パラメタライズテスト — Depth × 複数 Exchange ─────────────────────

    #[rstest]
    #[case(Exchange::BybitLinear, Venue::Bybit)]
    #[case(Exchange::BybitInverse, Venue::Bybit)]
    #[case(Exchange::BybitSpot, Venue::Bybit)]
    #[case(Exchange::BinanceLinear, Venue::Binance)]
    #[case(Exchange::BinanceInverse, Venue::Binance)]
    #[case(Exchange::BinanceSpot, Venue::Binance)]
    #[case(Exchange::HyperliquidLinear, Venue::Hyperliquid)]
    #[case(Exchange::HyperliquidSpot, Venue::Hyperliquid)]
    #[case(Exchange::OkexLinear, Venue::Okex)]
    #[case(Exchange::OkexInverse, Venue::Okex)]
    #[case(Exchange::OkexSpot, Venue::Okex)]
    #[case(Exchange::MexcLinear, Venue::Mexc)]
    #[case(Exchange::MexcInverse, Venue::Mexc)]
    #[case(Exchange::MexcSpot, Venue::Mexc)]
    #[case(Exchange::TachibanaStock, Venue::Tachibana)]
    #[case(Exchange::KabuStationStock, Venue::KabuStation)]
    #[case(Exchange::KabuStationTse, Venue::KabuStation)]
    #[case(Exchange::KabuStationNse, Venue::KabuStation)]
    #[case(Exchange::KabuStationFse, Venue::KabuStation)]
    #[case(Exchange::KabuStationSse, Venue::KabuStation)]
    #[case(Exchange::KabuStationFuture, Venue::KabuStation)]
    #[case(Exchange::KabuStationOption, Venue::KabuStation)]
    #[case(Exchange::ReplayStock, Venue::Replay)]
    fn depth_venue(#[case] exchange: Exchange, #[case] expected: Venue) {
        let stream = make_depth(exchange);
        assert_eq!(
            stream.venue(),
            expected,
            "Depth({exchange:?}).venue() should be {expected:?}"
        );
    }

    // ── rstest パラメタライズテスト — Trades × 複数 Exchange ────────────────────

    #[rstest]
    #[case(Exchange::BybitLinear, Venue::Bybit)]
    #[case(Exchange::BybitInverse, Venue::Bybit)]
    #[case(Exchange::BybitSpot, Venue::Bybit)]
    #[case(Exchange::BinanceLinear, Venue::Binance)]
    #[case(Exchange::BinanceInverse, Venue::Binance)]
    #[case(Exchange::BinanceSpot, Venue::Binance)]
    #[case(Exchange::HyperliquidLinear, Venue::Hyperliquid)]
    #[case(Exchange::HyperliquidSpot, Venue::Hyperliquid)]
    #[case(Exchange::OkexLinear, Venue::Okex)]
    #[case(Exchange::OkexInverse, Venue::Okex)]
    #[case(Exchange::OkexSpot, Venue::Okex)]
    #[case(Exchange::MexcLinear, Venue::Mexc)]
    #[case(Exchange::MexcInverse, Venue::Mexc)]
    #[case(Exchange::MexcSpot, Venue::Mexc)]
    #[case(Exchange::TachibanaStock, Venue::Tachibana)]
    #[case(Exchange::KabuStationStock, Venue::KabuStation)]
    #[case(Exchange::KabuStationTse, Venue::KabuStation)]
    #[case(Exchange::KabuStationNse, Venue::KabuStation)]
    #[case(Exchange::KabuStationFse, Venue::KabuStation)]
    #[case(Exchange::KabuStationSse, Venue::KabuStation)]
    #[case(Exchange::KabuStationFuture, Venue::KabuStation)]
    #[case(Exchange::KabuStationOption, Venue::KabuStation)]
    #[case(Exchange::ReplayStock, Venue::Replay)]
    fn trades_venue(#[case] exchange: Exchange, #[case] expected: Venue) {
        let stream = make_trades(exchange);
        assert_eq!(
            stream.venue(),
            expected,
            "Trades({exchange:?}).venue() should be {expected:?}"
        );
    }

    // ── rstest パラメタライズテスト — DepthAndTrades × 複数 Exchange ─────────────

    #[rstest]
    #[case(Exchange::BybitLinear, Venue::Bybit)]
    #[case(Exchange::BybitInverse, Venue::Bybit)]
    #[case(Exchange::BybitSpot, Venue::Bybit)]
    #[case(Exchange::BinanceLinear, Venue::Binance)]
    #[case(Exchange::BinanceInverse, Venue::Binance)]
    #[case(Exchange::BinanceSpot, Venue::Binance)]
    #[case(Exchange::HyperliquidLinear, Venue::Hyperliquid)]
    #[case(Exchange::HyperliquidSpot, Venue::Hyperliquid)]
    #[case(Exchange::OkexLinear, Venue::Okex)]
    #[case(Exchange::OkexInverse, Venue::Okex)]
    #[case(Exchange::OkexSpot, Venue::Okex)]
    #[case(Exchange::MexcLinear, Venue::Mexc)]
    #[case(Exchange::MexcInverse, Venue::Mexc)]
    #[case(Exchange::MexcSpot, Venue::Mexc)]
    #[case(Exchange::TachibanaStock, Venue::Tachibana)]
    #[case(Exchange::KabuStationStock, Venue::KabuStation)]
    #[case(Exchange::KabuStationTse, Venue::KabuStation)]
    #[case(Exchange::KabuStationNse, Venue::KabuStation)]
    #[case(Exchange::KabuStationFse, Venue::KabuStation)]
    #[case(Exchange::KabuStationSse, Venue::KabuStation)]
    #[case(Exchange::KabuStationFuture, Venue::KabuStation)]
    #[case(Exchange::KabuStationOption, Venue::KabuStation)]
    #[case(Exchange::ReplayStock, Venue::Replay)]
    fn depth_and_trades_venue(#[case] exchange: Exchange, #[case] expected: Venue) {
        let stream = make_depth_and_trades(exchange);
        assert_eq!(
            stream.venue(),
            expected,
            "DepthAndTrades({exchange:?}).venue() should be {expected:?}"
        );
    }
}
