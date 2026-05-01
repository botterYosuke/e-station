use exchange::{TickMultiplier, TickerInfo, Timeframe};
use serde::{Deserialize, Serialize};

use crate::chart::{comparison, heatmap, kline};
use crate::panel::{ladder, timeandsales};
use crate::stream::PersistStreamKind;
use crate::util::ok_or_default;

use crate::chart::{
    Basis, ViewConfig,
    heatmap::HeatmapStudy,
    indicator::{HeatmapIndicator, KlineIndicator},
    kline::KlineChartKind,
};

#[derive(Debug, Clone, Copy, Deserialize, Serialize)]
pub enum Axis {
    Horizontal,
    Vertical,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub enum Pane {
    Split {
        axis: Axis,
        ratio: f32,
        a: Box<Pane>,
        b: Box<Pane>,
    },
    Starter {
        #[serde(deserialize_with = "ok_or_default", default)]
        link_group: Option<LinkGroup>,
    },
    HeatmapChart {
        layout: ViewConfig,
        #[serde(deserialize_with = "ok_or_default", default)]
        studies: Vec<HeatmapStudy>,
        #[serde(deserialize_with = "ok_or_default", default)]
        stream_type: Vec<PersistStreamKind>,
        #[serde(deserialize_with = "ok_or_default", default)]
        settings: Settings,
        #[serde(deserialize_with = "ok_or_default", default)]
        indicators: Vec<HeatmapIndicator>,
        #[serde(deserialize_with = "ok_or_default", default)]
        link_group: Option<LinkGroup>,
    },
    ShaderHeatmap {
        #[serde(deserialize_with = "ok_or_default", default)]
        studies: Vec<HeatmapStudy>,
        #[serde(deserialize_with = "ok_or_default", default)]
        stream_type: Vec<PersistStreamKind>,
        #[serde(deserialize_with = "ok_or_default", default)]
        settings: Settings,
        #[serde(deserialize_with = "ok_or_default", default)]
        indicators: Vec<HeatmapIndicator>,
        #[serde(deserialize_with = "ok_or_default", default)]
        link_group: Option<LinkGroup>,
    },
    KlineChart {
        layout: ViewConfig,
        kind: KlineChartKind,
        #[serde(deserialize_with = "ok_or_default", default)]
        stream_type: Vec<PersistStreamKind>,
        #[serde(deserialize_with = "ok_or_default", default)]
        settings: Settings,
        #[serde(deserialize_with = "ok_or_default", default)]
        indicators: Vec<KlineIndicator>,
        #[serde(deserialize_with = "ok_or_default", default)]
        link_group: Option<LinkGroup>,
    },
    ComparisonChart {
        #[serde(deserialize_with = "ok_or_default", default)]
        stream_type: Vec<PersistStreamKind>,
        #[serde(deserialize_with = "ok_or_default", default)]
        settings: Settings,
        #[serde(deserialize_with = "ok_or_default", default)]
        link_group: Option<LinkGroup>,
    },
    TimeAndSales {
        #[serde(deserialize_with = "ok_or_default", default)]
        stream_type: Vec<PersistStreamKind>,
        #[serde(deserialize_with = "ok_or_default", default)]
        settings: Settings,
        #[serde(deserialize_with = "ok_or_default", default)]
        link_group: Option<LinkGroup>,
    },
    Ladder {
        #[serde(deserialize_with = "ok_or_default", default)]
        stream_type: Vec<PersistStreamKind>,
        #[serde(deserialize_with = "ok_or_default", default)]
        settings: Settings,
        #[serde(deserialize_with = "ok_or_default", default)]
        link_group: Option<LinkGroup>,
    },
    OrderEntry {
        #[serde(deserialize_with = "ok_or_default", default)]
        link_group: Option<LinkGroup>,
        /// Persisted so `linked_ticker()` works immediately after layout restore.
        #[serde(deserialize_with = "ok_or_default", default)]
        ticker_info: Option<TickerInfo>,
    },
    OrderList {
        #[serde(deserialize_with = "ok_or_default", default)]
        link_group: Option<LinkGroup>,
    },
    BuyingPower {
        #[serde(deserialize_with = "ok_or_default", default)]
        link_group: Option<LinkGroup>,
    },
    Positions {
        #[serde(deserialize_with = "ok_or_default", default)]
        link_group: Option<LinkGroup>,
    },
}

impl Default for Pane {
    fn default() -> Self {
        Pane::Starter { link_group: None }
    }
}

#[derive(Debug, Clone, Deserialize, Serialize, Default)]
#[serde(default)]
pub struct Settings {
    pub tick_multiply: Option<exchange::TickMultiplier>,
    pub visual_config: Option<VisualConfig>,
    pub selected_basis: Option<Basis>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Deserialize, Serialize)]
pub enum LinkGroup {
    A,
    B,
    C,
    D,
    E,
    F,
    G,
    H,
    I,
}

impl LinkGroup {
    pub const ALL: [LinkGroup; 9] = [
        LinkGroup::A,
        LinkGroup::B,
        LinkGroup::C,
        LinkGroup::D,
        LinkGroup::E,
        LinkGroup::F,
        LinkGroup::G,
        LinkGroup::H,
        LinkGroup::I,
    ];
}

impl std::fmt::Display for LinkGroup {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let c = match self {
            LinkGroup::A => "1",
            LinkGroup::B => "2",
            LinkGroup::C => "3",
            LinkGroup::D => "4",
            LinkGroup::E => "5",
            LinkGroup::F => "6",
            LinkGroup::G => "7",
            LinkGroup::H => "8",
            LinkGroup::I => "9",
        };
        write!(f, "{c}")
    }
}

/// Defines the specific configuration for different types of pane settings.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub enum VisualConfig {
    Heatmap(heatmap::Config),
    TimeAndSales(timeandsales::Config),
    Kline(kline::Config),
    Ladder(ladder::Config),
    Comparison(comparison::Config),
}

impl VisualConfig {
    pub fn heatmap(&self) -> Option<heatmap::Config> {
        match self {
            Self::Heatmap(cfg) => Some(*cfg),
            _ => None,
        }
    }

    pub fn time_and_sales(&self) -> Option<timeandsales::Config> {
        match self {
            Self::TimeAndSales(cfg) => Some(*cfg),
            _ => None,
        }
    }

    pub fn kline(&self) -> Option<kline::Config> {
        match self {
            Self::Kline(cfg) => Some(*cfg),
            _ => None,
        }
    }

    pub fn ladder(&self) -> Option<ladder::Config> {
        match self {
            Self::Ladder(cfg) => Some(*cfg),
            _ => None,
        }
    }

    pub fn comparison(&self) -> Option<comparison::Config> {
        match self {
            Self::Comparison(cfg) => Some(cfg.clone()),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ContentKind {
    Starter,
    HeatmapChart,
    ShaderHeatmap,
    // FootprintChart と CandlestickChart は Pane enum に専用バリアントを持たず、
    // 両者とも Pane::KlineChart バリアントで表現される（kind フィールドで区別）。
    FootprintChart,
    CandlestickChart,
    ComparisonChart,
    TimeAndSales,
    Ladder,
    OrderEntry,
    OrderList,
    BuyingPower,
    Positions,
    /// N1.11: Replay speed control pane skeleton.
    /// TODO(N1.11-ui): 実際の UI 描画は N1.11 UI フェーズで実装する。
    /// 現在は PaneKind enum への variant 追加のみ（iced コントロールバー pane skeleton）。
    ReplayControl,
}

impl ContentKind {
    pub const ALL: [ContentKind; 13] = [
        ContentKind::Starter,
        ContentKind::HeatmapChart,
        ContentKind::ShaderHeatmap,
        ContentKind::FootprintChart,
        ContentKind::CandlestickChart,
        ContentKind::ComparisonChart,
        ContentKind::TimeAndSales,
        ContentKind::Ladder,
        ContentKind::OrderEntry,
        ContentKind::OrderList,
        ContentKind::BuyingPower,
        ContentKind::Positions,
        ContentKind::ReplayControl,
    ];
}

impl std::fmt::Display for ContentKind {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let s = match self {
            ContentKind::Starter => "Starter Pane",
            ContentKind::HeatmapChart => "Heatmap Chart",
            ContentKind::ShaderHeatmap => "Shader Heatmap",
            ContentKind::FootprintChart => "Footprint Chart",
            ContentKind::CandlestickChart => "Candlestick Chart",
            ContentKind::ComparisonChart => "Comparison Chart",
            ContentKind::TimeAndSales => "Time&Sales",
            ContentKind::Ladder => "DOM/Ladder",
            ContentKind::OrderEntry => "注文入力",
            ContentKind::OrderList => "注文一覧",
            ContentKind::BuyingPower => "買余力",
            ContentKind::Positions => "保有銘柄",
            ContentKind::ReplayControl => "リプレイ速度",
        };
        write!(f, "{s}")
    }
}

#[derive(Clone, Copy)]
pub struct PaneSetup {
    pub ticker_info: exchange::TickerInfo,
    pub basis: Option<Basis>,
    pub tick_multiplier: Option<TickMultiplier>,
    pub price_step: exchange::unit::PriceStep,
    pub depth_aggr: exchange::adapter::StreamTicksize,
    pub push_freq: exchange::PushFrequency,
}

impl PaneSetup {
    pub fn new(
        content_kind: ContentKind,
        base_ticker: TickerInfo,
        current_basis: Option<Basis>,
        current_tick_multiplier: Option<TickMultiplier>,
        // D1/D6: resolved by caller from VenueCapsStore (no Exchange fallback).
        is_client_aggr: bool,
        prev_is_client_aggr: bool,
    ) -> Self {
        let exchange = base_ticker.ticker.exchange;

        let basis = match content_kind {
                ContentKind::HeatmapChart => {
                    let current = current_basis.and_then(|b| match b {
                        Basis::Time(tf) if exchange.supports_heatmap_timeframe(tf) => Some(b),
                        _ => None,
                    });

                    Some(current.unwrap_or_else(|| Basis::default_heatmap_time(Some(base_ticker))))
                }
                ContentKind::Ladder => Some(
                    current_basis.unwrap_or_else(|| Basis::default_heatmap_time(Some(base_ticker))),
                ),
                ContentKind::ShaderHeatmap => Some(
                    current_basis.unwrap_or_else(|| Basis::default_heatmap_time(Some(base_ticker))),
                ),
                ContentKind::FootprintChart => {
                    let current = current_basis.and_then(|b| match b {
                        Basis::Time(tf) if exchange.supports_kline_timeframe(tf) => Some(b),
                        Basis::Tick(_) => Some(b),
                        _ => None,
                    });

                    Some(current.unwrap_or_else(|| {
                        Basis::default_kline_time(Some(base_ticker), Timeframe::M5)
                    }))
                }
                ContentKind::CandlestickChart | ContentKind::ComparisonChart => {
                    let current = current_basis.and_then(|b| match b {
                        Basis::Time(tf) if exchange.supports_kline_timeframe(tf) => Some(b),
                        _ => None,
                    });

                    Some(current.unwrap_or_else(|| {
                        Basis::default_kline_time(Some(base_ticker), Timeframe::M15)
                    }))
                }
                ContentKind::Starter
                | ContentKind::TimeAndSales
                | ContentKind::OrderEntry
                | ContentKind::OrderList
                | ContentKind::BuyingPower
                | ContentKind::Positions
                // N1.11: ReplayControl は ticker stream を必要としない
                | ContentKind::ReplayControl => None,
            };

        let tick_multiplier = match content_kind {
            ContentKind::HeatmapChart | ContentKind::Ladder | ContentKind::ShaderHeatmap => {
                let tm = if !is_client_aggr && prev_is_client_aggr {
                    TickMultiplier(10)
                } else if let Some(tm) = current_tick_multiplier {
                    tm
                } else if is_client_aggr {
                    TickMultiplier(5)
                } else {
                    TickMultiplier(10)
                };
                Some(tm)
            }
            ContentKind::FootprintChart => {
                Some(current_tick_multiplier.unwrap_or(TickMultiplier(50)))
            }
            ContentKind::CandlestickChart
            | ContentKind::ComparisonChart
            | ContentKind::TimeAndSales
            | ContentKind::Starter
            | ContentKind::OrderEntry
            | ContentKind::OrderList
            | ContentKind::BuyingPower
            | ContentKind::Positions
            // N1.11: ReplayControl は tick multiplier 不要
            | ContentKind::ReplayControl => current_tick_multiplier,
        };

        let price_step = match tick_multiplier {
            Some(tm) => tm.multiply_with_min_tick_step(base_ticker),
            None => base_ticker.min_ticksize.into(),
        };

        let depth_aggr = if is_client_aggr {
            exchange::adapter::StreamTicksize::Client
        } else {
            exchange::adapter::StreamTicksize::ServerSide(
                tick_multiplier.unwrap_or(TickMultiplier(50)),
            )
        };

        let push_freq = match content_kind {
            ContentKind::HeatmapChart if exchange.is_custom_push_freq() => match basis {
                Some(Basis::Time(tf)) if exchange.supports_heatmap_timeframe(tf) => {
                    exchange::PushFrequency::Custom(tf)
                }
                _ => exchange::PushFrequency::ServerDefault,
            },
            _ => exchange::PushFrequency::ServerDefault,
        };

        Self {
            ticker_info: base_ticker,
            basis,
            tick_multiplier,
            price_step,
            depth_aggr,
            push_freq,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pane_order_entry_roundtrip() {
        let pane = Pane::OrderEntry {
            link_group: None,
            ticker_info: None,
        };
        let json = serde_json::to_string(&pane).unwrap();
        let restored: Pane = serde_json::from_str(&json).unwrap();
        assert!(matches!(
            restored,
            Pane::OrderEntry {
                link_group: None,
                ..
            }
        ));
    }

    #[test]
    fn pane_order_list_roundtrip_with_link_group() {
        let pane = Pane::OrderList {
            link_group: Some(LinkGroup::A),
        };
        let json = serde_json::to_string(&pane).unwrap();
        let restored: Pane = serde_json::from_str(&json).unwrap();
        assert!(matches!(
            restored,
            Pane::OrderList {
                link_group: Some(LinkGroup::A)
            }
        ));
    }

    #[test]
    fn pane_buying_power_roundtrip() {
        let pane = Pane::BuyingPower { link_group: None };
        let json = serde_json::to_string(&pane).unwrap();
        let restored: Pane = serde_json::from_str(&json).unwrap();
        assert!(matches!(restored, Pane::BuyingPower { link_group: None }));
    }

    #[test]
    fn pane_order_entry_missing_link_group_defaults_to_none() {
        let json = r#"{"OrderEntry": {}}"#;
        let pane: Pane = serde_json::from_str(json).unwrap();
        assert!(matches!(
            pane,
            Pane::OrderEntry {
                link_group: None,
                ..
            }
        ));
    }

    #[test]
    fn pane_order_entry_ticker_info_roundtrip() {
        use exchange::adapter::Exchange;
        use exchange::{Ticker, TickerInfo};
        let ticker = Ticker::new("7203", Exchange::TachibanaStock);
        let ti = TickerInfo::new_stock(ticker, 1.0, 100.0, 100);
        let pane = Pane::OrderEntry {
            link_group: None,
            ticker_info: Some(ti),
        };
        let json = serde_json::to_string(&pane).unwrap();
        let restored: Pane = serde_json::from_str(&json).unwrap();
        assert!(matches!(
            restored,
            Pane::OrderEntry {
                ticker_info: Some(_),
                ..
            }
        ));
    }

    #[test]
    fn pane_order_entry_old_format_without_ticker_info_deserializes_ok() {
        // Old saved-state.json files have no ticker_info field — must default to None.
        let json = r#"{"OrderEntry": {"link_group": null}}"#;
        let pane: Pane = serde_json::from_str(json).unwrap();
        assert!(matches!(
            pane,
            Pane::OrderEntry {
                ticker_info: None,
                ..
            }
        ));
    }

    #[test]
    fn pane_old_ladder_state_deserializes_ok() {
        let json = r#"{"Ladder":{"stream_type":[],"settings":{},"link_group":null}}"#;
        let pane: Pane = serde_json::from_str(json).unwrap();
        assert!(matches!(pane, Pane::Ladder { .. }));
    }

    #[test]
    fn pane_positions_roundtrip() {
        let pane = Pane::Positions { link_group: None };
        let json = serde_json::to_string(&pane).unwrap();
        let pane2: Pane = serde_json::from_str(&json).unwrap();
        assert!(matches!(pane2, Pane::Positions { link_group: None }));
    }

    #[test]
    fn content_kind_positions_display() {
        assert_eq!(ContentKind::Positions.to_string(), "保有銘柄");
    }

    #[test]
    fn content_kind_all_contains_positions() {
        assert!(ContentKind::ALL.contains(&ContentKind::Positions));
        assert_eq!(ContentKind::ALL.len(), 13);
    }

    #[test]
    fn pane_positions_forward_compat_from_old_json() {
        // 旧版 JSON（Positions バリアント無し）は問題なくロードできる
        let json = r#"{"Starter":{"link_group":null}}"#;
        let pane: Pane = serde_json::from_str(json).unwrap();
        assert!(matches!(pane, Pane::Starter { .. }));
    }

    #[test]
    fn pane_positions_rollback_compat() {
        // 新版 JSON に Positions が含まれているとき、Positions を知らない型でデシリアライズすると失敗する
        // （§3.3.3 の挙動確定テスト）
        #[derive(serde::Deserialize)]
        #[allow(dead_code)]
        enum OldPane {
            Starter { link_group: Option<LinkGroup> },
            OrderList { link_group: Option<LinkGroup> },
            BuyingPower { link_group: Option<LinkGroup> },
        }
        let json = r#"{"Positions":{"link_group":null}}"#;
        let result: Result<OldPane, _> = serde_json::from_str(json);
        assert!(
            result.is_err(),
            "旧版バイナリは Positions を知らないためエラーになる"
        );
    }
}
