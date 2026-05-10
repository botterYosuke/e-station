use std::time::{Duration, Instant};

use data::stream::PersistStreamKind;
use exchange::adapter::StreamKind;

/// `Depth` ストリームは `ticker_info` のみで照合する。
/// engine-client が `depth_aggr` を常に `Client` で emit するため、
/// `ServerSide` で登録したストリームとも一致させる必要がある。
fn stream_eq(a: &StreamKind, b: &StreamKind) -> bool {
    match (a, b) {
        (
            StreamKind::Depth {
                ticker_info: ta, ..
            },
            StreamKind::Depth {
                ticker_info: tb, ..
            },
        ) => ta == tb,
        _ => a == b,
    }
}

/// Persisted stream resolution to avoid loop retries
const RESOLVE_RETRY_INTERVAL: Duration = Duration::from_secs(2);

#[derive(Debug, Clone, PartialEq)]
pub enum ResolvedStream {
    /// Streams that are persisted but needs to be resolved for use
    Waiting {
        streams: Vec<PersistStreamKind>,
        last_attempt: Option<Instant>,
    },
    /// Streams that are active and ready to use, but can't persist
    Ready(Vec<StreamKind>),
}

impl ResolvedStream {
    pub fn waiting(streams: Vec<PersistStreamKind>) -> Self {
        ResolvedStream::Waiting {
            streams,
            last_attempt: None,
        }
    }

    /// Returns streams to resolve only if the retry interval has elapsed
    pub fn due_streams_to_resolve(&mut self, now: Instant) -> Option<Vec<PersistStreamKind>> {
        let ResolvedStream::Waiting {
            streams,
            last_attempt,
        } = self
        else {
            return None;
        };

        if streams.is_empty() {
            return None;
        }

        let should_retry = last_attempt
            .map(|t| now.duration_since(t) >= RESOLVE_RETRY_INTERVAL)
            .unwrap_or(true);

        if !should_retry {
            return None;
        }

        *last_attempt = Some(now);
        Some(streams.clone())
    }

    pub fn matches_stream(&self, stream: &StreamKind) -> bool {
        match self {
            ResolvedStream::Ready(existing) => existing.iter().any(|s| stream_eq(s, stream)),
            _ => false,
        }
    }

    pub fn ready_iter_mut(&mut self) -> Option<impl Iterator<Item = &mut StreamKind>> {
        match self {
            ResolvedStream::Ready(streams) => Some(streams.iter_mut()),
            _ => None,
        }
    }

    pub fn ready_iter(&self) -> Option<impl Iterator<Item = &StreamKind>> {
        match self {
            ResolvedStream::Ready(streams) => Some(streams.iter()),
            _ => None,
        }
    }

    pub fn find_ready_map<F, T>(&self, f: F) -> Option<T>
    where
        F: FnMut(&StreamKind) -> Option<T>,
    {
        match self {
            ResolvedStream::Ready(streams) => streams.iter().find_map(f),
            _ => None,
        }
    }

    pub fn into_waiting(self) -> Vec<PersistStreamKind> {
        match self {
            ResolvedStream::Waiting { streams, .. } => streams,
            ResolvedStream::Ready(streams) => {
                streams.into_iter().map(PersistStreamKind::from).collect()
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use exchange::{
        PushFrequency, TickMultiplier, Ticker, TickerInfo,
        adapter::{Exchange, StreamKind, StreamTicksize},
    };

    fn kabu_ticker() -> TickerInfo {
        TickerInfo::new_stock(
            Ticker::new("7203", Exchange::KabuStationTse),
            1.0,
            100.0,
            100,
        )
    }

    /// engine-client が常に Client タグで emit する場合でも、
    /// ServerSide タグで登録した Depth ストリームと一致すること
    #[test]
    fn depth_matches_ignores_depth_aggr() {
        let ticker = kabu_ticker();

        let stored = StreamKind::Depth {
            ticker_info: ticker,
            depth_aggr: StreamTicksize::ServerSide(TickMultiplier(50)),
            push_freq: PushFrequency::ServerDefault,
        };
        let resolved = ResolvedStream::Ready(vec![stored]);

        let emitted = StreamKind::Depth {
            ticker_info: ticker,
            depth_aggr: StreamTicksize::Client,
            push_freq: PushFrequency::ServerDefault,
        };

        assert!(
            resolved.matches_stream(&emitted),
            "ServerSide で登録した Depth に Client タグのイベントがマッチすること"
        );
    }

    /// 同じ ticker の Depth ストリーム（両方 Client）は従来通り一致すること
    #[test]
    fn depth_matches_same_depth_aggr() {
        let ticker = kabu_ticker();

        let stored = StreamKind::Depth {
            ticker_info: ticker,
            depth_aggr: StreamTicksize::Client,
            push_freq: PushFrequency::ServerDefault,
        };
        let resolved = ResolvedStream::Ready(vec![stored]);

        let emitted = StreamKind::Depth {
            ticker_info: ticker,
            depth_aggr: StreamTicksize::Client,
            push_freq: PushFrequency::ServerDefault,
        };

        assert!(resolved.matches_stream(&emitted));
    }

    /// 異なる ticker は一致しないこと
    #[test]
    fn depth_does_not_match_different_ticker() {
        let ticker_a = kabu_ticker();
        let ticker_b = TickerInfo::new_stock(
            Ticker::new("6758", Exchange::KabuStationTse),
            1.0,
            100.0,
            100,
        );

        let stored = StreamKind::Depth {
            ticker_info: ticker_a,
            depth_aggr: StreamTicksize::ServerSide(TickMultiplier(50)),
            push_freq: PushFrequency::ServerDefault,
        };
        let resolved = ResolvedStream::Ready(vec![stored]);

        let emitted = StreamKind::Depth {
            ticker_info: ticker_b,
            depth_aggr: StreamTicksize::Client,
            push_freq: PushFrequency::ServerDefault,
        };

        assert!(!resolved.matches_stream(&emitted));
    }

    /// 非 Depth ストリーム（Trades）は完全一致で比較されること
    #[test]
    fn non_depth_stream_uses_exact_match() {
        let ticker = kabu_ticker();

        let stored = StreamKind::Trades {
            ticker_info: ticker,
        };
        let resolved = ResolvedStream::Ready(vec![stored]);

        assert!(resolved.matches_stream(&StreamKind::Trades {
            ticker_info: ticker
        }));
        assert!(!resolved.matches_stream(&StreamKind::Trades {
            ticker_info: TickerInfo::new_stock(
                Ticker::new("6758", Exchange::KabuStationTse),
                1.0,
                100.0,
                100,
            )
        }));
    }
}
