/// `flowsurface-engine-client` — IPC client to the Python data engine.
///
/// Public surface:
/// - [`EngineClientBackend`]: a `VenueBackend` impl that routes via Python IPC.
/// - [`EngineConnection`]: gRPC connection and event broadcast.
/// - [`PythonProcess`] / [`ProcessManager`]: subprocess management with auto-restart.
/// - [`EngineClientError`]: unified error type.
/// - [`dto`] / [`convert`]: IPC message types and domain-type conversions.
pub mod backend;
pub mod capabilities;
pub mod connection;
pub mod convert;
pub mod depth_tracker;
pub mod dto;
pub mod error;
pub(crate) mod grpc_transport;
pub mod order_session_state;
pub mod process;
pub mod session_file;
pub mod stock_meta;
pub mod venue_caps;

pub use backend::{EngineClientBackend, TickerMetaMap};
pub use connection::EngineConnection;
pub use depth_tracker::DepthTracker;
pub use dto::{CryptoTickerEntry, QtyNormKind, StockTickerEntry, TickerEntry, VenueCaps};
pub use error::EngineClientError;
pub use process::{EngineCommand, ProcessManager, PythonProcess, SubscriptionKey};
pub use venue_caps::VenueCapsStore;

/// IPC schema version — must match the Python engine's `SCHEMA_MAJOR`/`SCHEMA_MINOR`.
///
/// SCHEMA_MINOR 履歴（時系列順、M13 レビュー反映 2026-05-04 ラウンド1）:
///   - 10: F6 SCENARIO 定数 IPC（LoadStrategyScenario / SaveStrategyScenario / 関連 events）
///   - 11: F7 モード切替 (StopReplay / ForceStopReplay / ReplayStopped)
///   - 12: ReplayDataLoaded.instrument_id / granularity を追加（auto_generate_replay_panes 復活用）
///   - 13: LoadReplayData / EngineStartConfig / ReplayDataLoaded に instrument_ids: Vec<String> 追加（複数銘柄対応）
///   - 14: ReplayDataLoaded.session_epoch（リプレイファイル切替時のペイン全閉じ用、Approach B）
///   - 15: LiveBuyingPower 追加 / LiveStateName に TRADING・STOPPING 追加 / EngineStartConfig live フィールド追加
///   - 16: 欠番（誤採番のため使用されず。17 を正規番号として採用）
///   - 17: SCHEMA_MINOR を 16→17 に修正（e-station branch での正しい採番）
///   - 18: K1 Venue::KabuStation / Exchange::KabuStationStock 追加（IPC で "kabu_station" を受理）
///   - 19: P3-1 Exchange::KabuStation* 市場細分化 + 先物・OP バリアント追加
///   - 20: P4-3 venue_capabilities.kabu_station に is_production フィールド追加
///   - 21: ExecutionMarker.commission 追加（fee_total 集計の上流、optional フィールド）
///   - 22: ReplayTimeUpdated 追加（分足・tick 足での current_day 時刻表示、Issue 3）
///   - 23: StrategyScenarioLoaded.resolved_instruments 追加（schema v3 instruments_ref 対応）
///   - 24: RequestVenueLogout コマンド追加（立花セッション明示破棄 IPC）
///   - 25: LoadLiveStrategyScenario / LiveStrategyScenarioLoaded 追加（issue #42 Phase 2）
pub const SCHEMA_MAJOR: u32 = 3;
pub const SCHEMA_MINOR: u32 = 25;
