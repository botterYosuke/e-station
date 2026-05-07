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
    Noop,
}

// ── Venue ────────────────────────────────────────────────────────────────────

/// Venue login, order management, and account information.
#[derive(Debug, Clone)]
pub(crate) enum VenueMsg {
    // Tachibana
    TachibanaEvent(VenueEvent),
    RequestTachibanaLogin(Trigger),
    TachibanaLoginIpcResult(Result<(), String>),
    DismissTachibanaBanner,
    // Kabu
    KabuEvent(VenueEvent),
    RequestKabuLogin(Trigger),
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
        #[allow(dead_code)]
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
    },
    ScenarioLoadFailed {
        request_id: String,
        path: std::path::PathBuf,
        reason: String,
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
}

// ── Dashboard ────────────────────────────────────────────────────────────────

/// Dashboard layout events and market-data streams.
#[derive(Debug, Clone)]
pub(crate) enum DashboardMsg {
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
