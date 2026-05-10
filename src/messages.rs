//! Grouped sub-message enums for the top-level `Message` dispatch.
//!
//! Each variant of `crate::Message` wraps one of these enums, reducing the
//! flat 158-variant enum to 7 groups.  `Flowsurface::update()` becomes a
//! thin dispatch hub that delegates to a matching `handle_*` method.

use std::{borrow::Cow, collections::HashMap, sync::Arc};

use iced::window;

use data::layout::WindowSpec;

use crate::{
    modal, native_menu,
    screen::{self, dashboard},
    venue_state::{Trigger, VenueEvent},
    widget::toast::Toast,
};

// ── Engine ──────────────────────────────────────────────────────────────────

/// Engine connection lifecycle.
#[derive(Debug, Clone)]
pub(crate) enum EngineMsg {
    Restarting(bool),
    Connected(Arc<engine_client::EngineConnection>),
    /// IPC コマンド送信が成功したが GUI 側に通知が不要な場合の sink variant。
    /// EngineMsg グループ内にあるのは「engine に対するコマンドの結果」という文脈のため。
    /// Task::none() の代替として使用される。
    Noop,
    /// Python state guard が PauseReplay を拒否した — replay_paused を false に戻す。
    PauseReplayBusy {
        reason: String,
    },
    /// Python state guard が ResumeReplay を拒否した — replay_paused を true に戻す。
    ResumeReplayBusy {
        reason: String,
    },
}

// ── Venue ────────────────────────────────────────────────────────────────────

/// Venue login, order management, and account information.
#[derive(Debug, Clone)]
pub(crate) enum VenueMsg {
    // Tachibana
    TachibanaEvent(VenueEvent),
    RequestTachibanaLogin(Trigger),
    RequestTachibanaLogout,
    TachibanaLoginIpcResult(Result<(), String>),
    DismissTachibanaBanner,
    // Kabu
    KabuEvent(VenueEvent),
    RequestKabuLogin(Trigger),
    RequestKabuLogout,
    KabuLoginIpcResult(Result<(), String>),
    // Second password
    SecondPasswordRequired(String),
    DismissSecondPasswordModal,
    SecondPasswordModal(modal::second_password::Message),
    // Orders
    OrderToast(Toast),
    OrderFilled {
        client_order_id: String,
        last_qty: String,
        last_price: String,
        leaves_qty: String,
    },
    OrderAccepted {
        client_order_id: String,
        venue_order_id: Option<String>,
    },
    OrderRejected {
        client_order_id: String,
        reason: String,
    },
    ConfirmOrderEntrySubmit,
    ConfirmCancelOrder {
        client_order_id: String,
        venue_order_id: String,
    },
    OrderListUpdated(Vec<engine_client::dto::OrderRecordWire>),
    OrderListSendCompleted(Result<(), String>),
    // Buying power / positions
    BuyingPowerSendCompleted(Result<(), String>),
    PositionsSendCompleted(Result<(), String>),
    PositionsUpdated {
        request_id: String,
        #[allow(dead_code)]
        venue: String,
        positions: Vec<engine_client::dto::PositionRecordWire>,
        ts_ms: i64,
    },
    BuyingPowerUpdated {
        cash_available: i64,
        cash_shortfall: i64,
        credit_available: i64,
        ts_ms: i64,
    },
    IpcError {
        request_id: Option<String>,
        code: String,
        message: String,
    },
}

// ── Replay ───────────────────────────────────────────────────────────────────

/// Replay playback, live strategy, and strategy-file operations.
#[derive(Debug, Clone)]
pub(crate) enum ReplayMsg {
    /// `instrument_id` (single) と `instrument_ids` (multi) は排他的に使用すること。
    /// 両方 Some になることはない。将来的には `InstrumentSpec::Single / Multi` enum に統合予定。
    DataLoaded {
        instrument_id: Option<String>,
        instrument_ids: Option<Vec<String>>,
        granularity: Option<engine_client::dto::ReplayGranularity>,
        #[allow(dead_code)]
        bars_loaded: u64,
        #[allow(dead_code)]
        trades_loaded: u64,
        session_epoch: Option<u64>,
    },
    Finished,
    DateChanged(String),
    HistoryChanged {
        has_history: bool,
    },
    ExecutionMarker {
        side: String,
        price: String,
        ts_event_ms: i64,
    },
    StrategySignal {
        signal_kind: engine_client::dto::SignalKind,
        price: Option<String>,
        ts_event_ms: i64,
        tag: Option<String>,
    },
    BuyingPower {
        cash: String,
        buying_power: String,
        equity: String,
        ts_event_ms: i64,
    },
    LiveBuyingPower {
        cash: String,
        equity: String,
        ts_event_ms: i64,
    },
    RestoreSnapshotPending {
        step_index: u64,
        ts_event_ms: i64,
    },
    StrategyFilePicked(Option<std::path::PathBuf>),
    DismissStrategyLoadError,
    ScenarioLoaded {
        request_id: String,
        path: std::path::PathBuf,
        scenario: Option<serde_json::Value>,
        resolved_instruments: Option<Vec<String>>,
    },
    ScenarioLoadFailed {
        request_id: String,
        path: std::path::PathBuf,
        reason: String,
    },
    /// schema 3.22: per-tick replay time signal (Issue 3).
    TimeUpdated {
        timestamp_ms: i64,
    },
    StopReplayOnly,
    FormMsg(modal::replay_form::Message),
    #[allow(dead_code)]
    ShowDialog,
    NativeOpenStrategyPicked(Option<std::path::PathBuf>),
    // Live strategy
    LiveStrategyFormMsg(modal::live_strategy_form::Message),
    LiveStarted {
        strategy_id: String,
        ts_event_ms: i64,
    },
    LiveStopped {
        strategy_id: String,
    },
    LiveStartFailed(String),
    StopLiveStrategy,
    /// issue #42 Phase 3: live strategy warm_up 完了 → Running 遷移 + 4 ペイン自動生成。
    /// `EngineEvent::LiveStrategyReady` を `map_engine_event_to_message` 経由で受け取る。
    LiveStrategyReady {
        strategy_id: String,
        instrument_id: String,
        venue: String,
        #[allow(dead_code)]
        ts_event_ms: i64,
    },
    /// issue #42 Phase 3: warm_up 進捗（5s 毎、`LiveStrategyReady` 60s timeout のリセットに使う）。
    LiveWarmingUp {
        strategy_id: String,
        #[allow(dead_code)]
        progress: f32,
        message: String,
    },
    /// issue #42 Phase 3: `EngineStarted` 後 60s 以内に `LiveStrategyReady` が来なかったときに
    /// `Task::perform` 経由で発火。`token` が古ければ捨てる（タイマーリセット用）。
    LiveWarmupTimeoutFired {
        strategy_id: String,
        token: u64,
    },
    /// issue #42 Phase 3: warm_up timeout banner の「再試行」ボタンや dismiss 操作で発火。
    /// R2-B H2: view() 内に live_warmup_timeout_banner を strategy_load_error と同じパターン
    /// で描画し、「再試行」ボタンで本 variant を on_press する（dead_code 抑止解除）。
    DismissLiveWarmupTimeoutBanner,
    /// issue #42 Phase 3: `LoadLiveStrategyScenario` 応答 → modal prefill。
    LiveStrategyScenarioLoaded {
        request_id: String,
        instrument_id: Option<String>,
        max_qty: Option<u32>,
        max_notional_jpy: Option<u64>,
        #[allow(dead_code)]
        venue: Option<String>,
        strategy_init_kwargs: Option<serde_json::Map<String, serde_json::Value>>,
    },
    /// issue #42 Phase 3: `LoadLiveStrategyScenario` の 5s timeout / `strategy_parse_failed`
    /// 受信時に pending を解除し手入力モードへ戻す。
    LiveStrategyScenarioFallback {
        request_id: String,
    },
    /// issue #42 Phase 3: `EngineRehello` 受信時に `LiveStrategyState::Running` の
    /// 三つ組で `auto_generate_live_panes` を冪等に再実行する。Rust 内部イベント。
    LiveStrategyRehelloReplay,
    /// H-2: Commit replay_bar state after `LoadReplayData` succeeded.
    /// Emitted from the Submit Task callback for both `BothOk` and
    /// `StartFailed` outcomes — in either case the backend has loaded the new
    /// replay session, so the bar must reflect the new params.
    ///
    /// `start_error` carries the `StartEngine` failure message when only
    /// `LoadReplayData` succeeded; the handler then shows a toast so the user
    /// knows the strategy did not start (UI/backend remain consistent: the
    /// loaded replay is real, but no run is in progress).
    CommitReplayBarState {
        instrument_id: String,
        start_date: String,
        end_date: String,
        granularity: crate::modal::replay_form::Granularity,
        strategy_file: std::path::PathBuf,
        initial_cash: String,
        start_error: Option<String>,
    },
}

// ── Dashboard ────────────────────────────────────────────────────────────────

/// Dashboard layout events and market-data streams.
#[derive(Debug, Clone)]
pub(crate) enum DashboardMsg {
    /// Route `event` to a specific layout (by UUID) or to the active layout when `None`.
    ///
    /// `layout_id: None` is a sentinel meaning "active layout" — not an absent value.
    /// Future refactor: replace with `enum LayoutTarget { Active, Specific(uuid::Uuid) }`.
    Layout {
        layout_id: Option<uuid::Uuid>,
        event: dashboard::Message,
    },
    Sidebar(dashboard::sidebar::Message),
    MarketWs(exchange::Event),
    ToggleTradeFetch(bool),
    ApplyVolumeSizeUnit(exchange::SizeUnit),
    RemoveNotification(usize),
}

// ── Window ───────────────────────────────────────────────────────────────────

/// Window lifecycle, file I/O, and mode-switch FSM.
#[derive(Debug, Clone)]
pub(crate) enum WindowMsg {
    Tick(std::time::Instant),
    WindowEvent(crate::window::Event),
    SetDirtyBaseline(HashMap<window::Id, WindowSpec>),
    ExitRequested(HashMap<window::Id, WindowSpec>),
    RestartRequested(Option<HashMap<window::Id, WindowSpec>>),
    GoBack,
    DataFolderRequested,
    OpenUrlRequested(Cow<'static, str>),
    CaptureScreenshot,
    ScreenshotReady(iced::window::Screenshot),
    ScreenshotSaved(std::path::PathBuf),
    ScreenshotFailed(String),
    ToggleDialogModal(Option<screen::ConfirmDialog<crate::Message>>),
    // File save / open
    NativeSaveAsPath(Option<std::path::PathBuf>),
    ConfirmSaveAsOverwrite {
        path: std::path::PathBuf,
    },
    NativeSaveAsWithSpecs {
        path: std::path::PathBuf,
        windows: HashMap<window::Id, WindowSpec>,
    },
    NativeSaveComplete {
        user_path: std::path::PathBuf,
        json_bytes: Vec<u8>,
        error_kind: Option<std::io::ErrorKind>,
        saved_state_ok: bool,
    },
    NativeOpenFileCancelled,
    NativeOpenFileApply {
        json: String,
        path: std::path::PathBuf,
    },
    NativeOpenFilePendingCheck {
        json: String,
        path: std::path::PathBuf,
        windows: HashMap<window::Id, WindowSpec>,
    },
    // Dirty-check confirm actions
    DiscardAndExit,
    SaveAndExit,
    DiscardAndOpenFile,
    SaveAndOpenFile,
    // Mode-switch FSM
    DiscardAndSwitchMode,
    SaveAndSwitchMode,
    SwitchModeWithSpecs {
        target: engine_client::dto::AppMode,
        windows: HashMap<window::Id, WindowSpec>,
    },
    SwitchModeSaveComplete {
        target: engine_client::dto::AppMode,
        windows: HashMap<window::Id, WindowSpec>,
    },
    ModeSwitchStopAcked,
    ModeSwitchStopTimeout,
    ModeSwitchForceStopTimeout,
    ModeSwitchSendFailed,
    ModeSwitchStopBusy,
    ModeSwitchEngineBusy(String),
}

// ── Menu ─────────────────────────────────────────────────────────────────────

/// Native OS menu and widget menu-bar actions.
#[derive(Debug, Clone)]
pub(crate) enum MenuMsg {
    NativeSetup(u64),
    NativeAction(native_menu::Action),
    Bar(crate::menu_bar_state::BarMessage),
}

// ── Settings ─────────────────────────────────────────────────────────────────

/// Application-level preferences.
#[derive(Debug, Clone)]
pub(crate) enum SettingsMsg {
    ThemeSelected(iced_core::Theme),
    ScaleFactorChanged(data::ScaleFactor),
    SetTimezone(data::UserTimezone),
    ThemeEditor(modal::theme_editor::Message),
    NetworkManager(modal::network_manager::Message),
    Layouts(modal::layout_manager::Message),
    AudioStream(modal::audio::Message),
}
