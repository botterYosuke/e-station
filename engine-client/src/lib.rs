/// `flowsurface-engine-client` — IPC client to the Python data engine.
///
/// Public surface:
/// - [`EngineClientBackend`]: a `VenueBackend` impl that routes via Python IPC.
/// - [`EngineConnection`]: low-level WS connection and event broadcast.
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
///   - 17: SCHEMA_MINOR を 16→17 に修正（e-station branch での正しい採番）
pub const SCHEMA_MAJOR: u16 = 3;
pub const SCHEMA_MINOR: u16 = 17;
