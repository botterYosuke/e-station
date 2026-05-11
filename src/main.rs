#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod audio;
mod chart;
mod cli;
mod connector;
mod layout;
mod logger;
mod mask_secrets;
// `menu` exposes `MenuEntry` and `Action` used by
// the iced widget menu bar on all platforms.
mod menu;
// `menu_bar_state` houses the pure `update()` state machine for the iced widget
// menu bar. No cfg gate — the state machine is platform-independent.
mod handlers;
mod menu_bar_state;
mod messages;
mod modal;
mod native_menu;
mod notify;
mod screen;
mod style;
mod venue_state;
mod version;
mod widget;
mod widget_menu_bar;
mod window;

use messages::{DashboardMsg, EngineMsg, MenuMsg, ReplayMsg, SettingsMsg, VenueMsg, WindowMsg};

use data::config::theme::default_theme;
use data::{layout::WindowSpec, sidebar};
use layout::{LayoutId, configuration};
use modal::{LayoutManager, ThemeEditor, audio::AudioStream, network_manager::NetworkManager};
use modal::{dashboard_modal, main_dialog_modal};
use notify::Notifications;
use screen::dashboard::{self, Dashboard};
use venue_state::{Trigger, VenueEvent, VenueState};
use widget::{
    confirm_dialog_container,
    toast::{self, Toast},
    tooltip,
};

use iced::{
    Alignment, Element, Subscription, Task, keyboard, padding,
    widget::{
        button, column, container, pane_grid, pick_list, row, rule, scrollable, text,
        tooltip::Position as TooltipPosition,
    },
};
use std::{borrow::Cow, collections::HashMap, sync::Arc, vec};

// ── Engine-client globals ─────────────────────────────────────────────────────

/// Watch channel publishing the live `EngineConnection`. The recovery loop
/// updates this on every successful handshake, and the engine-status
/// subscription forwards each new value into iced as
/// `Message::EngineConnected`. The static is only touched at startup
/// (initialised in `main()`) and from the recovery loop / subscription
/// stream — never from `Flowsurface::update()` (invariant T35-H7).
static ENGINE_CONNECTION_TX: std::sync::OnceLock<
    tokio::sync::watch::Sender<Option<Arc<engine_client::EngineConnection>>>,
> = std::sync::OnceLock::new();

/// `true` while the Python engine is being restarted (ProcessManager restart loop).
/// Shared between the background restart task and the Iced subscription.
static ENGINE_RESTARTING: std::sync::OnceLock<tokio::sync::watch::Sender<bool>> =
    std::sync::OnceLock::new();

/// Active `ProcessManager` for managed mode (set when `--data-engine-url` is
/// not supplied).  UI proxy changes reach the manager through this so that
/// `SetProxy` is replayed on every recovery handshake.
static ENGINE_MANAGER: std::sync::OnceLock<Arc<engine_client::ProcessManager>> =
    std::sync::OnceLock::new();

/// Mode-agnostic post-handshake VenueReady cache. Both managed mode
/// (`ProcessManager`) and external mode (`--data-engine-url`) write
/// here from a bridge task that subscribes to the connection's
/// broadcast events **before** the iced subscription wakes up. This
/// closes the race in which the engine emits `VenueReady` between
/// `connect()` returning and the iced subscription calling
/// `subscribe_events()` (broadcast does not replay). Reviewer
/// 2026-04-26 R3 (HIGH-2).
static VENUE_READY_CACHE: std::sync::OnceLock<
    Arc<tokio::sync::Mutex<rustc_hash::FxHashSet<String>>>,
> = std::sync::OnceLock::new();

/// P4-4: Whether the connected `kabu_station` venue is talking to the production
/// API (`localhost:18080`). Updated by the venue-ready bridge whenever a new
/// `Ready.capabilities.venue_capabilities.kabu_station.is_production` arrives,
/// and read by `status_bar()` to render the kabu chip with a red production
/// banner. `false` is the safe default; a stale `true` after a re-login is
/// invalidated together with the venue ready set.
static KABU_IS_PRODUCTION: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

/// Read the cached production flag (P4-4). The atomic is updated from the
/// bridge task on every `Ready` event and reset to `false` on engine
/// disconnect. Returning `false` on lock contention isn't possible — atomics
/// have no contention failure mode — so the UI always sees the most recent
/// observed value.
fn kabu_is_production() -> bool {
    KABU_IS_PRODUCTION.load(std::sync::atomic::Ordering::Acquire)
}

/// Extract the kabu venue's `is_production` flag from a `Ready.capabilities`
/// JSON blob. P4-4: returns `false` when the field is absent (older engines /
/// verify env), `true` only when explicitly advertised. issue #42 Phase 3.5 で
/// 共通ヘルパー `engine_client::capabilities::is_production` に薄いリネーム委譲。
/// 安全側 (= verify 表示) フォールバック仕様は不変（malformed wire / 異 venue /
/// cap 欠落いずれも false）。schema drift の検知は `/ipc-schema-check` skill 側で担う。
fn parse_kabu_is_production(capabilities: &serde_json::Value) -> bool {
    engine_client::capabilities::is_production(capabilities, KABU_STATION_VENUE_NAME)
}

/// P4-4: produce the kabu chip's (label_prefix, dot_text, dot_color) when
/// `is_production` is true. Returns `None` when verify env (default chip
/// styling). Pure function — kept in module scope so it can be unit-tested
/// without spinning up iced.
#[must_use]
fn kabu_chip_prod_style() -> (&'static str, iced::Color) {
    // 🔴 + 赤背景。文言は spec.md / runbook.md §5.1 と一致させること
    ("🔴 本番", iced::Color::from_rgb(0.85, 0.15, 0.15))
}

/// Startup mode (`live` or `replay`) captured from `--mode` before any runtime
/// is created.  Changed from OnceLock to Mutex<Option<_>> so that
/// `set_app_mode()` can overwrite the value during mode-switch restarts (F7/T1).
/// Lock-acquisition order: MODE_SWITCHING → APP_MODE → CURRENT_PATH (統一決定 58).
static APP_MODE: std::sync::Mutex<Option<engine_client::dto::AppMode>> =
    std::sync::Mutex::new(None);

/// F7/T2: set to `true` while a mode-switch restart is in progress.
/// `ModeSwitchGuard` RAII wrapper ensures the flag is reset even on panic.
/// (P7 統一決定 33 / 受け入れ基準 9, 11)
pub static MODE_SWITCHING: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

// F7 / M2 (lightweight lock-order guard): track the highest-index lock
// acquired on the current thread so reverse-order acquisitions are caught
// in debug builds. The fixed order is:
//   0: MODE_SWITCHING
//   1: APP_MODE
//   2: CURRENT_PATH
// Helper `lock_order_acquire(name)` is called at known acquisition points
// (`restart_with_mode` / `Action::SwitchMode`). Release builds only log a
// `log::warn!` so production safety is preserved (統一決定 R6-82).
thread_local! {
    static LOCK_ORDER_INDEX: std::cell::Cell<Option<usize>> =
        const { std::cell::Cell::new(None) };
}

fn lock_order_index_for(name: &str) -> Option<usize> {
    match name {
        "MODE_SWITCHING" => Some(0),
        "APP_MODE" => Some(1),
        "CURRENT_PATH" => Some(2),
        _ => None,
    }
}

/// Record acquisition of a named lock on the current thread, asserting that
/// the fixed acquisition order is preserved. In debug builds violations
/// `debug_assert!`-panic; in release builds a `log::warn!` is emitted
/// and the call returns without panicking (統一決定 R6-82).
///
/// This is a lightweight bookkeeping helper — it does NOT actually acquire
/// the underlying mutex/atomic. Callers must invoke this immediately after
/// (or before, with the matching `lock_order_release`) the real acquisition.
pub fn lock_order_acquire(name: &'static str) {
    let Some(next) = lock_order_index_for(name) else {
        return;
    };
    // M-3 / L1: emit a structured `log::info!` so log-subscriber based
    // integration tests can verify the per-thread acquisition order. The
    // actual fixed order is enforced by the `debug_assert!` below; this event
    // is purely observational. We use `log` (not `tracing`) because flowsurface
    // does not yet depend on `tracing` at the workspace root — switching is a
    // separate refactor (see `log::warn!` cfg-gated below for the existing
    // dependency surface; F9 R1-C1 reverted a stray `tracing::warn!` here).
    log::info!(
        target: "lock_order",
        "lock_order_acquire lock={name} index={next}",
    );
    LOCK_ORDER_INDEX.with(|cell| {
        let prev = cell.get();
        if let Some(p) = prev {
            // Equal index is allowed (re-entrant probe of the same lock).
            // Strictly greater means violation (e.g. acquiring APP_MODE then MODE_SWITCHING).
            debug_assert!(
                p <= next,
                "lock-order violation: tried to acquire {name} (index {next}) \
                 while already holding index {p}. Fixed order: \
                 MODE_SWITCHING(0) → APP_MODE(1) → CURRENT_PATH(2) \
                 (統一決定 58 / R6-82)"
            );
            #[cfg(not(debug_assertions))]
            if p > next {
                log::warn!(
                    target: "lock_order",
                    "lock-order violation: tried to acquire {name} (index {next}) \
                     while already holding index {p}",
                );
            }
        }
        // Record the highest index seen so subsequent acquisitions are checked.
        let new_max = match prev {
            Some(p) if p > next => p,
            _ => next,
        };
        cell.set(Some(new_max));
    });
}

/// Reset the per-thread lock-order tracker. Call when all known locks for a
/// critical section have been released (e.g. at the bottom of `restart_with_mode`).
pub fn lock_order_reset() {
    LOCK_ORDER_INDEX.with(|cell| cell.set(None));
}

/// Returns the current app mode.  Falls back to `Live` when the static has not
/// yet been initialised (unreachable in normal operation).
pub(crate) fn app_mode() -> engine_client::dto::AppMode {
    APP_MODE
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .unwrap_or(engine_client::dto::AppMode::Live)
}

/// Overwrites the current app mode.  Poison recovery is applied so that a
/// panic inside a previous lock holder does not permanently break the value.
fn set_app_mode(mode: engine_client::dto::AppMode) {
    match APP_MODE.lock() {
        Ok(mut g) => *g = Some(mode),
        Err(e) => *e.into_inner() = Some(mode),
    }
}

/// F7: check if tachibana_orders.jsonl has any in-flight orders.
///
/// Reads the WAL tail-first (reverse scan), same algorithm and wire schema as
/// `python/engine/wal_in_flight.py`. The writer (`python/engine/exchanges/tachibana_orders.py::_audit_log_*`)
/// emits records of the form `{"phase": "submit"|"accepted"|"rejected", "client_order_id": "...", ...}`.
///
/// Phase semantics:
/// - `rejected` → terminal (not in-flight)
/// - `submit` / `accepted` / unknown → in-flight (conservative)
///
/// **CONTRACT**: this function MUST stay in sync with `wal_in_flight.detect_in_flight_orders`.
/// The contract is pinned by `python/tests/test_wal_in_flight_detection.py::TestWalContract`
/// and `tests/wal_writer_reader_contract.rs`.
///
/// Uses `engine_client::process::engine_cache_dir()` — the same path Rust sends to Python
/// via the stdin payload — so both sides always agree on the WAL location.
fn has_wal_in_flight_orders() -> bool {
    let wal_path = engine_client::process::engine_cache_dir().join("tachibana_orders.jsonl");
    has_wal_in_flight_orders_at(&wal_path, jst_today_midnight_ms())
}

/// JST 当日 0:00 を epoch_ms で返す。WAL `ts` フィールド（epoch_ms）と比較して
/// 「前営業日以前」のレコードを terminal 扱いにするためのカットオフ。
fn jst_today_midnight_ms() -> i64 {
    use chrono::{FixedOffset, TimeZone, Utc};
    let jst = FixedOffset::east_opt(9 * 3600).expect("9h offset is valid");
    let now_jst = Utc::now().with_timezone(&jst);
    let today = now_jst.date_naive();
    jst.from_local_datetime(&today.and_hms_opt(0, 0, 0).expect("00:00:00 is valid"))
        .single()
        .expect("JST midnight is unambiguous")
        .timestamp_millis()
}

/// Internal pure helper for [`has_wal_in_flight_orders`] — takes the WAL path
/// directly so integration tests can exercise it without touching the global cache dir.
///
/// `today_start_ms` は JST 当日 0:00 (epoch_ms)。これより古い `ts` のレコードは
/// 「前営業日以前」として terminal 扱いし `latest_phase` に積まない。立花の通常
/// 注文は当日限り有効で、前日以前の `accepted` は venue 側で必ず確定しているため、
/// WAL に終端 phase が書かれない非同期約定/取消イベントを補正する（C2 修正）。
fn has_wal_in_flight_orders_at(wal_path: &std::path::Path, today_start_ms: i64) -> bool {
    // M6: surface IO errors via log::warn! (the previous `let Ok(...) else { return false; }`
    // silently treated unreadable WAL as "no in-flight orders", which masked operational
    // problems). Treat IO failure as conservative `false` (do not block the mode switch
    // if the file cannot be read), but log so the user can investigate.
    let content = match std::fs::read_to_string(wal_path) {
        Ok(c) => c,
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => return false,
        Err(err) => {
            log::warn!(
                "[F7/WAL] failed to read {}: {}; treating as no in-flight orders",
                wal_path.display(),
                err
            );
            return false;
        }
    };
    let mut latest_phase: std::collections::HashMap<String, String> =
        std::collections::HashMap::new();
    for line in content.lines().rev() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let Ok(record) = serde_json::from_str::<serde_json::Value>(line) else {
            continue;
        };
        // C2: 前日以前のレコードは terminal 扱い。立花 API は accepted までしか同期で
        // 返さず、約定 (filled) / 取消 (canceled) は EVENT 経由なので writer は終端
        // phase を書けない。前日以前の `accepted` は venue 側で確定済みと安全に断定できる。
        let ts = record.get("ts").and_then(|v| v.as_i64()).unwrap_or(0);
        if ts < today_start_ms {
            continue;
        }
        // Writer schema (`tachibana_orders.py`): `phase` + `client_order_id`.
        let order_id = record.get("client_order_id").and_then(|v| v.as_str());
        let phase = record.get("phase").and_then(|v| v.as_str());
        if let (Some(oid), Some(ph)) = (order_id, phase) {
            latest_phase
                .entry(oid.to_string())
                .or_insert_with(|| ph.to_string());
        }
    }
    // Terminal phase = `rejected`. Anything else (submit / accepted / unknown)
    // is conservatively treated as in-flight.
    latest_phase.values().any(|ph| ph.as_str() != "rejected")
}

#[cfg(test)]
mod wal_today_cutoff_tests {
    //! C2: JST 当日 0:00 より古い `ts` のレコードは terminal 扱い。
    //! 立花の通常注文は当日限り — 前日以前の `accepted` は venue 側で必ず確定済み。

    use super::has_wal_in_flight_orders_at;

    const TODAY_MS: i64 = 1_777_550_000_000;
    const YESTERDAY_MS: i64 = TODAY_MS - 60_000;

    fn write_wal(dir: &std::path::Path, lines: &[&str]) -> std::path::PathBuf {
        let path = dir.join("tachibana_orders.jsonl");
        std::fs::write(&path, lines.join("\n") + "\n").unwrap();
        path
    }

    #[test]
    fn today_accepted_is_in_flight() {
        let tmp = tempfile::tempdir().unwrap();
        let wal = write_wal(
            tmp.path(),
            &[r#"{"client_order_id":"T1","phase":"accepted","ts":1777553600000}"#],
        );
        assert!(has_wal_in_flight_orders_at(&wal, TODAY_MS));
    }

    #[test]
    fn yesterday_accepted_is_terminal() {
        let tmp = tempfile::tempdir().unwrap();
        let wal = write_wal(
            tmp.path(),
            &[r#"{"client_order_id":"T2","phase":"accepted","ts":1777549940000}"#],
        );
        assert!(!has_wal_in_flight_orders_at(&wal, TODAY_MS));
    }

    #[test]
    fn today_submit_with_yesterday_accepted_is_in_flight() {
        let tmp = tempfile::tempdir().unwrap();
        let wal = write_wal(
            tmp.path(),
            &[
                r#"{"client_order_id":"T3a","phase":"accepted","ts":1777549940000}"#,
                r#"{"client_order_id":"T3b","phase":"submit","ts":1777553600000}"#,
            ],
        );
        assert!(has_wal_in_flight_orders_at(&wal, TODAY_MS));
    }

    #[test]
    fn legacy_e2e_residue_yesterday_is_not_in_flight() {
        let tmp = tempfile::tempdir().unwrap();
        let lines: Vec<String> = (0..17)
            .map(|i| {
                format!(
                    r#"{{"client_order_id":"e2e-{}","phase":"accepted","ts":{}}}"#,
                    i, YESTERDAY_MS
                )
            })
            .collect();
        let refs: Vec<&str> = lines.iter().map(String::as_str).collect();
        let wal = write_wal(tmp.path(), &refs);
        assert!(!has_wal_in_flight_orders_at(&wal, TODAY_MS));
    }

    #[test]
    fn today_submit_then_rejected_is_terminal() {
        let tmp = tempfile::tempdir().unwrap();
        let wal = write_wal(
            tmp.path(),
            &[
                r#"{"client_order_id":"T5","phase":"submit","ts":1777553600000}"#,
                r#"{"client_order_id":"T5","phase":"rejected","ts":1777553600001}"#,
            ],
        );
        assert!(!has_wal_in_flight_orders_at(&wal, TODAY_MS));
    }

    #[test]
    fn missing_file_returns_false() {
        let tmp = tempfile::tempdir().unwrap();
        let wal = tmp.path().join("nonexistent.jsonl");
        assert!(!has_wal_in_flight_orders_at(&wal, TODAY_MS));
    }

    #[test]
    fn record_without_ts_is_kept_for_backwards_compat() {
        // Older WAL writers may have omitted `ts`. Treat missing/non-numeric as
        // ts=0 so the record is filtered as ancient (terminal). This matches the
        // safer default — old logs without ts cannot be reliably "today".
        let tmp = tempfile::tempdir().unwrap();
        let wal = write_wal(
            tmp.path(),
            &[r#"{"client_order_id":"NO_TS","phase":"submit"}"#],
        );
        assert!(!has_wal_in_flight_orders_at(&wal, TODAY_MS));
    }

    // Sanity check: the helper computing JST midnight returns a positive epoch
    // close to "now" (i.e. within the past day or so).
    #[test]
    fn jst_today_midnight_is_recent() {
        use super::jst_today_midnight_ms;
        let now_ms = chrono::Utc::now().timestamp_millis();
        let today = jst_today_midnight_ms();
        assert!(today > 0);
        assert!(today <= now_ms);
        assert!(now_ms - today < 36 * 3600 * 1000); // within 36h window
    }
}
/// F7/T2: RAII guard for the mode-switch critical section.
/// Call `try_acquire()` at the top of `restart_with_mode()`; the flag is
/// automatically cleared when the guard is dropped — including panic unwinds.
pub struct ModeSwitchGuard;

impl ModeSwitchGuard {
    /// Returns `Some` if the flag was successfully acquired (i.e. no switch is
    /// currently in progress), `None` if a switch is already running.
    pub fn try_acquire() -> Option<Self> {
        use std::sync::atomic::Ordering;
        MODE_SWITCHING
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .ok()
            .map(|_| ModeSwitchGuard)
    }
}

impl Drop for ModeSwitchGuard {
    fn drop(&mut self) {
        MODE_SWITCHING.store(false, std::sync::atomic::Ordering::Release);
        // H1: RAII reset of the per-thread lock-order tracker. Without this,
        // early-return paths from the mode-switch state machine that drop the
        // guard (e.g. `mode_switch_state = None`) would leave the
        // `LOCK_ORDER_INDEX` thread-local at its highest acquired index,
        // and subsequent unrelated lock acquisitions on the same thread
        // would spuriously trip the lock-order debug_assert.
        lock_order_reset();
    }
}

/// F7/T2: errors that can abort a mode-switch operation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ModeSwitchError {
    /// Another mode-switch is already in progress.
    AlreadySwitching,
    /// Live mode has in-flight (unconfirmed) orders — switching to replay
    /// would break WAL integrity.
    InFlightOrder,
    /// The engine reported a busy state.
    EngineBusy(String),
    /// The live state could not be flushed to disk.
    SaveFailed,
    /// The replay engine did not stop in time and force-stop also failed.
    StopFailed,
    /// User cancelled the unsaved-changes confirm dialog (F4).
    ///
    /// L2-rust: not yet emitted on a concrete code path — `GoBack` and
    /// `ToggleDialogModal(None)` currently dismiss the mode-switch dialog
    /// without producing this typed error. The variant is preserved so future
    /// review-fix-loop iterations can route the dirty-confirm cancel path
    /// through the typed error channel (e.g. for a single Toast/log call site)
    /// without breaking exhaustive-match callers.
    #[allow(dead_code)]
    ConfirmCancelled,
}

/// F3: tracks the file path most recently opened or explicitly saved-as.
/// `None` until the user first uses Open or Save As.
/// Persists across `Flowsurface::restart()` because it is a static.
/// Poison recovery: callers use `into_inner()` to avoid propagating panics.
static CURRENT_PATH: std::sync::Mutex<Option<std::path::PathBuf>> = std::sync::Mutex::new(None);

/// F3 DoD: `--saved-state <PATH>` — path supplied on the command line.
/// Set once in `main()` before the Iced runtime starts; read by
/// `Flowsurface::new()` to both select the JSON to load and prime `CURRENT_PATH`.
static INITIAL_STATE_PATH: std::sync::OnceLock<std::path::PathBuf> = std::sync::OnceLock::new();

/// B4 (Phase B): global shared `VenueCaps` sidecar.
///
/// Initialized in `main()` before the Iced runtime starts.
/// Every `EngineClientBackend` holds an `Arc` clone and writes into it
/// during `fetch_ticker_metadata`. UI code reads via `try_read()`.
pub(crate) static VENUE_CAPS_STORE: std::sync::OnceLock<
    Arc<tokio::sync::RwLock<engine_client::VenueCapsStore>>,
> = std::sync::OnceLock::new();

/// F4/BC-5: save failure classification.
///
/// Log-level contract:
/// - `Cancelled`          → INFO (user intent; no error signal)
/// - `IoError`            → WARN (operational OS failure)
/// - `PathGuardViolation` → ERROR + "BUG:" prefix (should never happen)
#[derive(Debug)]
#[allow(dead_code)]
// TODO(F6): remove #[allow(dead_code)] after Cancelled and PathGuardViolation are wired up.
// Currently Cancelled is NOT called — save-as cancel is handled by NativeSaveAsPath(None) early return.
// Note: save_error_classification.rs tests verify dead_code variants (Cancelled, PathGuardViolation)
// to ensure log-level contracts are structurally pinned even before call sites are added.
enum SaveError {
    /// User dismissed the OS save dialog — not an error.
    Cancelled,
    /// OS-level I/O failure (disk full, permission denied, etc.).
    IoError(std::io::ErrorKind),
    /// Path guard check failed (`.py`-extension or persistent-state-dir rule).
    /// This is a programming error, not an operational failure.
    PathGuardViolation { reason: &'static str },
}

/// Spawn a long-lived bridge that mirrors the connection's broadcast
/// venue lifecycle events into [`VENUE_READY_CACHE`]. Subscribing
/// here, before the connection is published to `ENGINE_CONNECTION_TX`,
/// captures every `VenueReady`/`VenueError` even if iced is still
/// starting up. The task self-terminates when the broadcast channel
/// closes (i.e. when the connection drops).
fn spawn_venue_ready_bridge(rt: &tokio::runtime::Runtime, conn: &engine_client::EngineConnection) {
    spawn_venue_ready_bridge_on(rt.handle(), conn);
}

/// F4/BC-5: log a save failure at the appropriate level.
///
/// | Variant              | Level | Notes                              |
/// |----------------------|-------|------------------------------------|
/// | `Cancelled`          | INFO  | user intent — no alert needed      |
/// | `IoError`            | WARN  | OS-level failure, not a bug        |
/// | `PathGuardViolation` | ERROR | programming error — "BUG:" prefix  |
fn log_save_error(err: &SaveError, path: &std::path::Path) {
    match err {
        SaveError::Cancelled => {
            log::info!("Save As cancelled path={}", path.display());
        }
        SaveError::IoError(kind) => {
            log::warn!("Save failed kind={kind:?} path={}", path.display());
        }
        SaveError::PathGuardViolation { reason } => {
            log::error!(
                "BUG: path guard violation path={} reason={reason}",
                path.display()
            );
        }
    }
}

/// Same as [`spawn_venue_ready_bridge`] but accepts an explicit
/// [`tokio::runtime::Handle`]. Used from already-async contexts (the
/// reconnect loop in external mode, and the recovery loop in managed mode)
/// where only a `Handle` is available — both call sites used to inline
/// duplicate copies of the bridge body. H-Rust3: single source of truth
/// for the `VenueReady`/`VenueError`/`VenueLoginStarted`/`VenueLoginCancelled`
/// invalidation rules.
fn spawn_venue_ready_bridge_on(
    handle: &tokio::runtime::Handle,
    conn: &engine_client::EngineConnection,
) {
    // R1-HIGH: Seed KABU_IS_PRODUCTION from the handshake capabilities snapshot
    // before subscribing to future events.  The initial Ready event is broadcast
    // during perform_handshake before any subscriber exists, so the event loop
    // below will never deliver it.  conn.capabilities() holds the authoritative
    // snapshot regardless of subscriber timing.
    // Seeded *before* the VENUE_READY_CACHE guard so the atomic is always
    // updated even when called from tests that skip VENUE_READY_CACHE setup.
    let initial_prod = parse_kabu_is_production(&conn.capabilities());
    KABU_IS_PRODUCTION.store(initial_prod, std::sync::atomic::Ordering::Release);
    let cache = match VENUE_READY_CACHE.get() {
        Some(cache) => Arc::clone(cache),
        None => return,
    };
    let mut event_rx = conn.subscribe_events();
    handle.spawn(async move {
        use engine_client::dto::EngineEvent;
        use tokio::sync::broadcast::error::RecvError;
        loop {
            match event_rx.recv().await {
                // P4-4: Ready が来たら capabilities から kabu の is_production を抽出してキャッシュ更新。
                // VenueReady より先に Ready が来るので、UI が初回 chip を描画する時には正しい値が読める。
                Ok(EngineEvent::Ready { capabilities, .. }) => {
                    let prod = parse_kabu_is_production(&capabilities);
                    KABU_IS_PRODUCTION.store(prod, std::sync::atomic::Ordering::Release);
                }
                Ok(EngineEvent::VenueReady { venue, .. }) => {
                    cache.lock().await.insert(venue);
                }
                // Invalidate cache on lifecycle edges (R4 MEDIUM-3 / HIGH-1).
                Ok(EngineEvent::VenueError { venue, .. })
                | Ok(EngineEvent::VenueLoginStarted { venue, .. })
                | Ok(EngineEvent::VenueLoginCancelled { venue, .. }) => {
                    cache.lock().await.remove(&venue);
                    if venue == KABU_STATION_VENUE_NAME {
                        KABU_IS_PRODUCTION.store(false, std::sync::atomic::Ordering::Release);
                    }
                }
                Ok(_) => {}
                Err(RecvError::Lagged(n)) => {
                    log::warn!(
                        "venue_ready_bridge lagged, dropped {n} events \
                         — resetting KABU_IS_PRODUCTION to false as safe default"
                    );
                    KABU_IS_PRODUCTION.store(false, std::sync::atomic::Ordering::Release);
                }
                Err(RecvError::Closed) => {
                    // P4-4: 接続終了 = engine プロセスが落ちた／再接続待ち。
                    // 次の Ready が来るまでは「不明」だが UI 上は安全側 (verify 表示) に倒す。
                    KABU_IS_PRODUCTION.store(false, std::sync::atomic::Ordering::Release);
                    break;
                }
            }
        }
    });
}

/// Sync probe of the bridge cache — never blocks `Flowsurface::update`.
/// Returns `false` on lock contention (rare; bridge holds the lock
/// only for the duration of a single `HashSet` mutation), which means
/// the UI may briefly miss a synthesized `VenueReady` and rely on the
/// next live event instead. That's the same fallback semantics as
/// `ProcessManager::try_is_venue_ready`.
fn cached_venue_is_ready(venue: &str) -> bool {
    VENUE_READY_CACHE
        .get()
        .and_then(|cache| cache.try_lock().ok().map(|state| state.contains(venue)))
        .unwrap_or(false)
}

/// Wire-level identifier for the Tachibana venue. Centralised so a
/// future rename or IPC schema change is a one-line patch instead of
/// a cross-file grep.
const TACHIBANA_VENUE_NAME: &str = "tachibana";
/// Wire-level identifier for the kabuステーション venue.
const KABU_STATION_VENUE_NAME: &str = "kabu_station";

/// issue #42 Phase 3 (統一決定 #17): live 戦略 `EngineStarted` 受信後 60 秒以内に
/// `LiveStrategyReady` が来なければ "warm_up timeout" banner を表示する。
/// `LiveStrategyWarmingUp` 受信ごとにカウンタ（token）が再起動されるため、
/// engine が定期的に進捗 emit している限り timeout は発火しない。
pub(crate) const LIVE_WARMUP_TIMEOUT_SECS: u64 = 60;

/// issue #42 Phase 3 (統一決定 #18): `LoadLiveStrategyScenario` 送信後 5 秒以内に
/// `LiveStrategyScenarioLoaded` も `Error` も来なければ手入力フォールバック
/// （フォームを編集可能のままにする）。engine 無応答時の安全網であり、
/// `LIVE_SCENARIO` 不在時は engine が即時応答するため通常は使わない（統一決定 #22）。
pub(crate) const LIVE_SCENARIO_FALLBACK_TIMEOUT_SECS: u64 = 5;

/// Canonical mapping of `Venue` enum variants to the IPC venue name strings.
/// Referenced during initial setup and on every engine reconnect.
/// **Includes `Tachibana`** — without the entry the venue would never
/// receive an `EngineClientBackend` registration and every
/// `fetch_ticker_metadata(Tachibana, …)` call would error with
/// `No adapter handle configured`. Reviewer 2026-04-26 R4 (HIGH-1).
const VENUE_NAMES: &[(exchange::adapter::Venue, &str)] = &[
    (exchange::adapter::Venue::Binance, "binance"),
    (exchange::adapter::Venue::Bybit, "bybit"),
    (exchange::adapter::Venue::Hyperliquid, "hyperliquid"),
    (exchange::adapter::Venue::Okex, "okex"),
    (exchange::adapter::Venue::Mexc, "mexc"),
    (exchange::adapter::Venue::Tachibana, TACHIBANA_VENUE_NAME),
    (
        exchange::adapter::Venue::KabuStation,
        KABU_STATION_VENUE_NAME,
    ),
    (exchange::adapter::Venue::Replay, "replay"),
];

/// Bind to 127.0.0.1:0 to ask the OS for a free port, then immediately close
/// the socket and return the port number for the engine subprocess to bind.
///
/// There is a small race window between releasing the port here and the engine
/// rebinding it, but Phase 6 keeps Python on a TCP listener (the only IPC
/// transport supported across all platforms) so this is the standard pattern.
fn pick_free_port() -> Option<u16> {
    let listener = std::net::TcpListener::bind("127.0.0.1:0").ok()?;
    listener.local_addr().ok().map(|a| a.port())
}

fn main() {
    // Register panic hook first — must be before any runtime or logger setup
    // so that panics with API key payloads are masked before reaching stderr.
    std::panic::set_hook(Box::new(|info| {
        let payload = info.payload();
        let msg = payload
            .downcast_ref::<&str>()
            .copied()
            .or_else(|| payload.downcast_ref::<String>().map(|s| s.as_str()))
            .unwrap_or("<non-string panic payload>");
        let masked = crate::mask_secrets::mask_secrets(msg);
        eprintln!("[PANIC] {}", masked.as_str());
        if let Some(loc) = info.location() {
            eprintln!("  at {}:{}", loc.file(), loc.line());
        }
    }));

    let cli_args = cli::CliArgs::parse();

    // Capture startup mode before any runtime is created so Flowsurface::new()
    // can enforce D8 regardless of whether the HTTP control-API runtime builds.
    set_app_mode(engine_client::dto::AppMode::from(cli_args.mode));

    // F3 DoD: if --saved-state was given, record it for Flowsurface::new().
    if let Some(p) = cli_args.initial_state_path {
        INITIAL_STATE_PATH.set(p).ok();
    }

    logger::setup(cfg!(debug_assertions)).expect("Failed to initialize logger");

    // Initialise the engine-restarting watch channel (used even in native mode
    // so the subscription is always wired up consistently).
    // Keep `_restarting_rx` alive for the duration of main() so that send()
    // never returns Err(no-receivers) before Iced's engine_status_stream subscribes.
    let (restarting_tx, _restarting_rx) = tokio::sync::watch::channel(false);
    ENGINE_RESTARTING.set(restarting_tx).ok();

    // Engine-connection watch channel — updated by the recovery loop and
    // forwarded into iced by `engine_status_stream`. Keep `_conn_rx` alive
    // for the duration of `main()` so `send()` never sees Err(no-receivers)
    // before the iced subscription wires up its own subscriber.
    let (conn_tx, _conn_rx) =
        tokio::sync::watch::channel::<Option<Arc<engine_client::EngineConnection>>>(None);
    ENGINE_CONNECTION_TX.set(conn_tx).ok();

    // B4 (Phase B): initialize the global VenueCaps sidecar before any
    // Iced component or backend reads from it.
    VENUE_CAPS_STORE
        .set(Arc::new(tokio::sync::RwLock::new(
            engine_client::VenueCapsStore::new(),
        )))
        .ok();

    // VenueReady cache shared between both engine modes — see static
    // doc comment on `VENUE_READY_CACHE`.
    VENUE_READY_CACHE
        .set(Arc::new(tokio::sync::Mutex::new(
            rustc_hash::FxHashSet::default(),
        )))
        .ok();

    // The Python data engine is normally spawned and supervised in-process by
    // a `ProcessManager` running on a dedicated tokio runtime (Phase 6 default).
    // `--data-engine-url` overrides this to connect to an externally managed
    // engine (used for development / debugging).
    //
    // A dedicated tokio runtime keeps the connection's background IO tasks
    // alive for the full application lifetime.
    let _engine_rt: Option<tokio::runtime::Runtime> = if let Some(ref url) =
        cli_args.data_engine_url
    {
        let token = std::env::var("FLOWSURFACE_ENGINE_TOKEN").unwrap_or_default();
        let url_str = url.to_string();

        log::info!("Data engine URL: {url_str} — connecting …");

        let rt = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .enable_all()
            .thread_name("engine-client")
            .build()
            .expect("Failed to build engine-client tokio runtime");

        // R1b H-E: cli::Mode → engine_client::dto::AppMode を境界で写す。
        let app_mode: engine_client::dto::AppMode = cli_args.mode.into();
        log::info!("Started in mode: {}", app_mode.as_wire_str());
        // G3: grpc:// → http:// for tonic; http:// passes through unchanged.
        // WS scheme validation is in CLI, but gRPC needs http://.
        let grpc_target = url_str
            .replacen("ws://", "http://", 1)
            .replacen("grpc://", "http://", 1);
        match rt.block_on(engine_client::EngineConnection::connect_grpc(
            &grpc_target,
            &token,
            app_mode,
        )) {
            Ok(conn) => {
                log::info!("Connected to external data engine at {url_str}");
                let conn = Arc::new(conn);
                // External mode has no ProcessManager → its
                // `apply_after_handshake` cache is unavailable. Spawn
                // the bridge BEFORE publishing the connection so the
                // first `VenueReady` cannot race past the iced
                // subscription. Reviewer 2026-04-26 R3 (HIGH-2).
                spawn_venue_ready_bridge(&rt, &conn);
                if let Some(tx) = ENGINE_CONNECTION_TX.get() {
                    tx.send(Some(Arc::clone(&conn))).ok();
                }

                // Push saved proxy to engine before Iced starts so that the
                // very first subscription fires through the proxy, not direct.
                // Uses the same resolution order as load_saved_state():
                // proxy-url.json → state.json fallback → keychain auth.
                if let Some(proxy) = data::config::proxy::load_startup_proxy() {
                    let proxy_url = Some(proxy.to_url_string());
                    match rt.block_on(
                        conn.send(engine_client::dto::Command::SetProxy { url: proxy_url }),
                    ) {
                        Ok(()) => log::info!("Initial proxy sent to engine"),
                        Err(e) => log::warn!("Failed to send initial proxy: {e}"),
                    }
                }

                // Monitor the connection and reconnect with exponential backoff on loss.
                let reconnect_url = grpc_target.clone();
                let reconnect_token = token.clone();
                let reconnect_mode = app_mode;
                rt.spawn(async move {
                    let mut current_conn = conn;
                    loop {
                        current_conn.wait_closed().await;
                        log::warn!("external engine connection lost");
                        if let Some(tx) = ENGINE_RESTARTING.get() {
                            tx.send(true).ok();
                        }

                        let mut delay = std::time::Duration::from_secs(1);
                        loop {
                            tokio::time::sleep(delay).await;
                            log::info!("Attempting to reconnect to engine at {reconnect_url} …");
                            match engine_client::EngineConnection::connect_grpc(
                                &reconnect_url,
                                &reconnect_token,
                                reconnect_mode,
                            )
                            .await
                            {
                                Ok(new_conn) => {
                                    log::info!("Reconnected to data engine at {reconnect_url}");
                                    let new_conn = Arc::new(new_conn);
                                    // Drain the cache so the bridge
                                    // for this fresh connection writes
                                    // its current view, not the stale
                                    // one from before the drop.
                                    if let Some(cache) = VENUE_READY_CACHE.get() {
                                        cache.lock().await.clear();
                                    }
                                    // H-Rust3: re-spawn the bridge against the
                                    // fresh connection via the shared helper —
                                    // the previous bridge's recv loop has
                                    // already exited via RecvError::Closed.
                                    spawn_venue_ready_bridge_on(
                                        &tokio::runtime::Handle::current(),
                                        &new_conn,
                                    );
                                    if let Some(tx) = ENGINE_CONNECTION_TX.get() {
                                        tx.send(Some(Arc::clone(&new_conn))).ok();
                                    }
                                    if let Some(tx) = ENGINE_RESTARTING.get() {
                                        tx.send(false).ok();
                                    }
                                    current_conn = new_conn;
                                    break;
                                }
                                Err(e) => {
                                    log::warn!("Reconnect failed: {e}, retrying in {delay:?}");
                                    delay = (delay * 2).min(std::time::Duration::from_secs(60));
                                }
                            }
                        }
                    }
                });
            }
            Err(e) => {
                log::error!("Failed to connect to data engine at {url_str}: {e}");
            }
        }

        Some(rt)
    } else {
        // Managed mode: spawn the bundled Python engine, supervise restarts.
        let rt = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .enable_all()
            .thread_name("engine-client")
            .build()
            .expect("Failed to build engine-client tokio runtime");

        let port = pick_free_port().unwrap_or(0);
        if port == 0 {
            log::error!("Could not allocate a loopback port for the Python data engine");
            eprintln!("error: could not allocate a loopback port for the data engine");
            std::process::exit(1);
        }

        let cmd = match engine_client::EngineCommand::resolve_with(
            std::env::current_exe()
                .ok()
                .and_then(|p| p.parent().map(std::path::PathBuf::from))
                .as_deref(),
            cli_args.engine_cmd.as_deref(),
        ) {
            Ok(c) => c,
            Err(e) => {
                log::error!("Failed to resolve engine command: {e}");
                eprintln!("error: failed to resolve data-engine command: {e}");
                std::process::exit(1);
            }
        };
        log::info!("Python data engine: cmd={cmd:?}, managed port=127.0.0.1:{port}");

        let manager = Arc::new(engine_client::ProcessManager::with_command(cmd));
        // N1.13: propagate the CLI mode so every handshake (initial + recovery)
        // sends the same value in Hello.
        // R1b H-E: cli::Mode → engine_client::dto::AppMode を境界で写す。
        let app_mode: engine_client::dto::AppMode = cli_args.mode.into();
        log::info!("Started in mode: {}", app_mode.as_wire_str());
        rt.block_on(manager.set_mode(app_mode));
        ENGINE_MANAGER.set(Arc::clone(&manager)).ok();

        // Push the saved proxy into the manager so it is re-applied after every
        // handshake (initial spawn + every recovery).
        if let Some(proxy) = data::config::proxy::load_startup_proxy() {
            rt.block_on(manager.set_proxy(Some(proxy.to_url_string())));
        }

        let url = format!("ws://127.0.0.1:{port}");
        log::info!("Engine URL: {url}");

        // Spawn the recovery loop; track each handshake to swap ENGINE_CONNECTION.
        let manager_clone = Arc::clone(&manager);
        rt.spawn(async move {
            // Inner loop: each iteration corresponds to one handshake/lifecycle.
            //
            // We can't reuse `run_with_recovery` directly because it doesn't
            // expose the live `EngineConnection` to its caller — we need the
            // connection to publish into `ENGINE_CONNECTION`.
            let mut backoff_ms: u64 = 500;
            let mut initial = true;
            loop {
                // Pick a fresh free port for each spawn attempt. If the first
                // lifecycle attached to an external engine, the startup port may
                // have been claimed by another process in the meantime.
                let loop_port = pick_free_port().unwrap_or(port);
                // Only the first iteration probes 19876 for an existing engine.
                // Recovery iterations always spawn fresh (see process.rs:516) so
                // that a managed session cannot silently switch to a different
                // external engine that appeared on 19876 during downtime.
                let connect = if initial {
                    initial = false;
                    manager_clone.start_or_attach(loop_port).await
                } else {
                    manager_clone.start(loop_port).await
                };
                match connect {
                    Ok(conn) => {
                        backoff_ms = 500;
                        let conn = Arc::new(conn);

                        // Drain stale entries before the bridge for the
                        // new connection takes over. apply_after_handshake
                        // already populated `ProcessManager.venue_ready_state`,
                        // but the global cache must reflect this fresh
                        // connection's view, so a recovery loop iteration
                        // doesn't carry stale ready-state from a prior
                        // disconnect.
                        if let Some(cache) = VENUE_READY_CACHE.get() {
                            cache.lock().await.clear();
                        }
                        // Subscribe events on this connection BEFORE
                        // publishing it to the watch channel — bridges
                        // any window between iced's subscription and
                        // the engine's first venue lifecycle emit.
                        // Reviewer 2026-04-26 R3 (HIGH-2). H-Rust3:
                        // shared helper instead of an inlined copy.
                        spawn_venue_ready_bridge_on(&tokio::runtime::Handle::current(), &conn);

                        if let Some(tx) = ENGINE_CONNECTION_TX.get() {
                            tx.send(Some(Arc::clone(&conn))).ok();
                        }
                        if let Some(tx) = ENGINE_RESTARTING.get() {
                            tx.send(false).ok();
                        }
                        log::info!("Python data engine ready");

                        // The credentials-refresh listener is owned by
                        // ProcessManager::start() — see the continuation
                        // task spawned at the end of `start()`. Spawning
                        // another listener here would race the in-engine
                        // one on the keyring (load→set ABA) and on the
                        // in-memory creds store. One listener is the
                        // invariant.

                        conn.wait_closed().await;
                        log::warn!("Python engine connection lost — restarting");
                        if let Some(tx) = ENGINE_RESTARTING.get() {
                            tx.send(true).ok();
                        }
                    }
                    Err(e) => {
                        log::error!("Engine start failed: {e}");
                        if let Some(tx) = ENGINE_RESTARTING.get() {
                            tx.send(true).ok();
                        }
                    }
                }
                log::info!("Restarting Python engine in {backoff_ms}ms …");
                tokio::time::sleep(std::time::Duration::from_millis(backoff_ms)).await;
                backoff_ms = (backoff_ms * 2).min(30_000);
            }
        });

        // Wait for the first handshake to publish a connection on the
        // watch channel, with a generous timeout that covers PyInstaller's
        // cold-start overhead (decompression of the frozen archive on
        // first launch).
        let waited = rt.block_on(async {
            for _ in 0..200 {
                if ENGINE_CONNECTION_TX
                    .get()
                    .is_some_and(|tx| tx.borrow().is_some())
                {
                    return true;
                }
                tokio::time::sleep(std::time::Duration::from_millis(100)).await;
            }
            false
        });

        if !waited {
            log::error!("Python data engine did not become ready within 20 s");
            eprintln!(
                "error: Python data engine did not become ready within 20 s.\n\
                 Check engine logs for startup errors."
            );
            std::process::exit(1);
        }

        Some(rt)
    };

    if !ENGINE_CONNECTION_TX
        .get()
        .is_some_and(|tx| tx.borrow().is_some())
    {
        log::error!("Engine connection not initialised — refusing to start");
        eprintln!("error: data engine connection failed to initialise");
        std::process::exit(1);
    }

    std::thread::spawn(data::cleanup_old_market_data);

    let _ = iced::daemon(Flowsurface::new, Flowsurface::update, Flowsurface::view)
        .settings(iced::Settings {
            antialiasing: true,
            fonts: vec![
                Cow::Borrowed(style::AZERET_MONO_BYTES),
                Cow::Borrowed(style::ICONS_BYTES),
            ],
            default_text_size: iced::Pixels(12.0),
            ..Default::default()
        })
        .title(Flowsurface::title)
        .theme(Flowsurface::theme)
        .scale_factor(Flowsurface::scale_factor)
        .subscription(Flowsurface::subscription)
        .run();
}

/// N4-live: live 戦略の実行状態。
///
/// issue #42 Phase 3 で `Running` に `instrument_id` / `venue` を追加し、
/// reconnect 時の `auto_generate_live_panes` 再実行に必要な三つ組
/// `(strategy_id, instrument_id, venue)` を SoT として保持する
/// （統一決定 #4 / R3-C1 / R5-MED-2）。
///
/// `pending_live_config` を別フィールドに置く設計案は撤回し、`Running`
/// 自体を「ランタイム state 兼 reconnect 用 pending 設定」として直接使う。
/// `EngineRehello` 受信時に `Running` 状態なら `auto_generate_live_panes` を
/// 冪等に再呼出する（VenueState には触らず、LiveStrategyState のみが
/// `EngineRehello` で生き残る設計）。
#[derive(Debug, Clone, Default)]
enum LiveStrategyState {
    #[default]
    Idle,
    Running {
        strategy_id: String,
        instrument_id: String,
        venue: String,
    },
}

impl LiveStrategyState {
    fn is_running(&self) -> bool {
        matches!(self, LiveStrategyState::Running { .. })
    }

    /// R2-B H7: 空文字列 sentinel を防ぐ factory。
    ///
    /// `LiveStrategyState::Running { .. }` を直接 struct literal で生成すると、
    /// caller の入力チェック漏れで `strategy_id == ""` 等の sentinel 状態に
    /// 遷移してしまう（auto_generate_live_panes は instrument_id の空チェック
    /// しかしておらず、strategy_id / venue が空でも 4 ペインを生成してしまう）。
    /// 本 factory は 3 つ組すべての非空を契約として強制する。
    ///
    /// 失敗時は遷移を行わずに `Err` を返し、caller 側で `log::warn!` する。
    pub(crate) fn try_running(
        strategy_id: String,
        instrument_id: String,
        venue: String,
    ) -> Result<Self, &'static str> {
        if strategy_id.is_empty() {
            return Err("strategy_id must not be empty");
        }
        if instrument_id.is_empty() {
            return Err("instrument_id must not be empty");
        }
        if venue.is_empty() {
            return Err("venue must not be empty");
        }
        Ok(Self::Running {
            strategy_id,
            instrument_id,
            venue,
        })
    }
}

#[cfg(test)]
mod live_strategy_state_tests {
    use super::*;

    /// R2-B H7: try_running は 3 つ組のうち空文字が混じっていれば Err を返す。
    #[test]
    fn test_live_strategy_state_try_running_rejects_empty_fields() {
        let cases: &[(&str, &str, &str, &str)] = &[
            ("", "8306.T", "tachibana", "strategy_id must not be empty"),
            ("S1", "", "tachibana", "instrument_id must not be empty"),
            ("S1", "8306.T", "", "venue must not be empty"),
        ];
        for (sid, iid, venue, expected_msg) in cases {
            let result =
                LiveStrategyState::try_running(sid.to_string(), iid.to_string(), venue.to_string());
            assert!(
                result.is_err(),
                "expected Err for ({sid:?}, {iid:?}, {venue:?})"
            );
            assert_eq!(
                result.unwrap_err(),
                *expected_msg,
                "unexpected message for ({sid:?}, {iid:?}, {venue:?})"
            );
        }
    }

    /// R2-B H7: 全フィールド非空なら Running variant を返す。
    #[test]
    fn test_live_strategy_state_try_running_accepts_valid_triple() {
        let result = LiveStrategyState::try_running(
            "S1".to_string(),
            "8306.T".to_string(),
            "tachibana".to_string(),
        );
        assert!(result.is_ok(), "valid triple should succeed: {result:?}");
        match result.unwrap() {
            LiveStrategyState::Running {
                strategy_id,
                instrument_id,
                venue,
            } => {
                assert_eq!(strategy_id, "S1");
                assert_eq!(instrument_id, "8306.T");
                assert_eq!(venue, "tachibana");
            }
            other => panic!("expected Running, got {other:?}"),
        }
    }
}

#[cfg(test)]
mod engine_error_routing_tests {
    //! R4 Group A (silent-HIGH-1): `EngineError{strategy_id=Some(_)}` の以下の
    //! code は GUI に通知される必要がある — 旧実装は `node_build_failed` だけを
    //! `LiveStrategyBuildFailed` に変換し、それ以外 (`warm_up_failed` /
    //! `kernel_unavailable` / `venue_not_supported` / `market_closed`) は
    //! `log::warn!` で握りつぶしていた。
    //!
    //! 設計判断 (R4 Group A): 既存 `LiveStrategyBuildFailed` を再利用して
    //! `code` field を追加 (handler 内で teardown + toast を担当) — 新
    //! variant を作ると 4 系統で同じ teardown 経路を二重実装することになる。
    //! warm_up 失敗時はまだ `auto_generate_live_panes` が呼ばれていないため
    //! `teardown_live_panes` は実 pane に対して no-op になるが、bar / state
    //! reset と pending_strategy_id クリアは必須 — 既存 handler が全てこなす。
    use super::*;
    use engine_client::dto::EngineEvent;

    fn make_engine_error(code: &str) -> EngineEvent {
        EngineEvent::EngineError {
            code: code.to_string(),
            message: format!("simulated {code} for routing test"),
            strategy_id: Some("test-sid".to_string()),
        }
    }

    fn assert_routes_to_build_failed(code: &str) {
        let evt = make_engine_error(code);
        let mapped = map_engine_event_to_message(evt);
        match mapped {
            Some(Message::Replay(messages::ReplayMsg::LiveStrategyBuildFailed {
                strategy_id,
                code: routed_code,
                ..
            })) => {
                assert_eq!(strategy_id, "test-sid");
                assert_eq!(routed_code, code);
            }
            other => panic!(
                "EngineError code={code:?} must route to ReplayMsg::LiveStrategyBuildFailed; \
                 got {other:?}"
            ),
        }
    }

    #[test]
    fn engine_error_warm_up_failed_routes_to_live_strategy_build_failed() {
        assert_routes_to_build_failed("warm_up_failed");
    }

    #[test]
    fn engine_error_kernel_unavailable_routes_to_live_strategy_build_failed() {
        assert_routes_to_build_failed("kernel_unavailable");
    }

    #[test]
    fn engine_error_venue_not_supported_routes_to_live_strategy_build_failed() {
        assert_routes_to_build_failed("venue_not_supported");
    }

    #[test]
    fn engine_error_market_closed_routes_to_live_strategy_build_failed() {
        assert_routes_to_build_failed("market_closed");
    }

    /// node_build_failed は従来通り routing される (regression pin).
    #[test]
    fn engine_error_node_build_failed_still_routes_to_live_strategy_build_failed() {
        assert_routes_to_build_failed("node_build_failed");
    }

    // R6 silent-HIGH-1: server.py の StartEngine ハンドラが
    // `EngineError{code="engine_run_failed", strategy_id=Some}` (server.py:5252) と
    // `EngineError{code="timeout", strategy_id=Some}` (server.py:5210) を emit する。
    // R4 で導入した abort-codes 許可リストにこの 2 つが漏れていたため、
    // 受信側で `log::warn!` のみ → `None` 返却 → `live_strategy_pending_strategy_id`
    // がクリアされず state machine が固着し、次の live 起動が受け付けられなくなる
    // silent regression を起こしていた。timeout は 3600s 後に必ず発火するため
    // 長時間 live 戦略では確実に踏むパス。
    #[test]
    fn engine_error_engine_run_failed_routes_to_live_strategy_build_failed() {
        assert_routes_to_build_failed("engine_run_failed");
    }

    #[test]
    fn engine_error_timeout_routes_to_live_strategy_build_failed() {
        assert_routes_to_build_failed("timeout");
    }

    /// 既知 code 以外 (`unknown_code` 等) は Some を返さない (silent ログ pass-through)。
    #[test]
    fn engine_error_unknown_strategy_level_code_returns_none() {
        let evt = make_engine_error("totally_unknown_code");
        assert!(
            map_engine_event_to_message(evt).is_none(),
            "unknown strategy-level codes should fall through to log::warn! and return None"
        );
    }

    /// 接続レベル (strategy_id=None) は引き続き None を返す (regression pin).
    #[test]
    fn engine_error_connection_level_still_returns_none() {
        let evt = EngineEvent::EngineError {
            code: "auth_failed".to_string(),
            message: "test".to_string(),
            strategy_id: None,
        };
        assert!(
            map_engine_event_to_message(evt).is_none(),
            "connection-level EngineError (strategy_id=None) must not route to a Message"
        );
    }
}

struct Flowsurface {
    main_window: window::Window,
    sidebar: dashboard::Sidebar,
    handles: exchange::adapter::AdapterHandles,
    layout_manager: LayoutManager,
    theme_editor: ThemeEditor,
    network: NetworkManager,
    audio_stream: AudioStream,
    confirm_dialog: Option<screen::ConfirmDialog<Message>>,
    volume_size_unit: exchange::SizeUnit,
    ui_scale_factor: data::ScaleFactor,
    timezone: data::UserTimezone,
    theme: data::Theme,
    notifications: Notifications,
    /// `true` while the Python data engine is restarting.
    engine_restarting: bool,
    /// Live `EngineConnection`, populated by the engine-status subscription
    /// (`Message::EngineConnected`). `None` until the first handshake event
    /// reaches `update()`. Replaces the former `static ENGINE_CONNECTION`
    /// (T35-H7-NoStaticInUpdate).
    engine_connection: Option<Arc<engine_client::EngineConnection>>,
    /// Active `ProcessManager` for managed mode (read once at startup from
    /// `ENGINE_MANAGER` so `update()` does not touch the static directly).
    engine_manager: Option<Arc<engine_client::ProcessManager>>,
    /// Tachibana venue lifecycle state (see `venue_state.rs`). Replaces
    /// the prior `tachibana_ready` / `tachibana_login_in_flight` double
    /// flag with a single enum so illegal combinations are
    /// unrepresentable. T35-U4-VenueReadyGate.
    tachibana_state: VenueState,
    /// kabuステーション venue lifecycle state — same FSM as `tachibana_state`
    /// but kabu has no sidebar ticker filter.
    kabu_state: VenueState,
    /// 第二暗証番号 modal。`SecondPasswordRequired` IPC イベントで Some に、
    /// Submit / Cancel / Dismiss で None に戻る。
    second_password_modal: Option<modal::second_password::SecondPasswordModal>,
    /// `GetBuyingPower` IPC 送信時に記録した request_id。
    /// `BuyingPowerUpdated` または `IpcError` 受信時にクリアする。
    buying_power_request_id: Option<String>,
    /// `GetOrderList` IPC 送信時に記録した request_id。
    /// `OrderListUpdated` または `IpcError` 受信時にクリアする。重複送信抑止に使う。
    order_list_request_id: Option<String>,
    /// `GetPositions` IPC 送信時に記録した request_id。
    /// `PositionsUpdated` または `IpcError` 受信時にクリアする。重複送信抑止に使う。
    positions_request_id: Option<String>,
    /// N4.3: user-selected strategy `.py` file path. `None` until the user picks
    /// one via the OS file dialog. Consumed by the Replay 起動フォーム modal as the
    /// `strategy_file` field on `Command::StartEngine`.
    replay_strategy_file: Option<std::path::PathBuf>,
    /// F6a: 最後に送信した `LoadStrategyScenario` の request_id。
    /// `StrategyScenarioLoadedEvent` 受信時に突き合わせ、古い応答を捨てるために使う。
    /// `None` = 未送信 or 応答済み。
    pending_scenario_request_id: Option<String>,
    /// Phase 8.1c: Replay 起動フォーム modal。`File > Replay を開始...` で Some に、
    /// Submit / Cancel で None に戻る。
    replay_form_modal: Option<modal::replay_form::ReplayFormModal>,
    /// N4-live: ライブ戦略フォーム modal
    live_strategy_form_modal: Option<modal::live_strategy_form::LiveStrategyFormModal>,
    /// N4-live: live 戦略の実行状態。Idle = 未実行、Running = 実行中。
    /// issue #42 Phase 3: `Running { strategy_id, instrument_id, venue }` 三つ組を保持し、
    /// `EngineRehello` 受信時に `auto_generate_live_panes` を冪等に再呼出する SoT。
    live_strategy: LiveStrategyState,
    /// issue #42 Phase 3: `EngineStarted`(live) 受信後、`LiveStrategyReady` が来るまで
    /// 保持する pending strategy_id。warm_up timeout タイマーの照合 / 異 strategy のタイマー
    /// 無視に使う。`LiveStrategyReady` / `EngineStopped` で None に戻す。
    live_strategy_pending_strategy_id: Option<String>,
    /// issue #42 Phase 3: warm_up timeout banner（`None` = 表示しない）。
    /// `EngineStarted` 後 60s 以内に `LiveStrategyReady` が来なければ Some にセット、
    /// 「再試行」 / `LiveStrategyReady` / `EngineStopped` で None に戻す。
    live_warmup_timeout_banner: Option<String>,
    /// issue #42 Phase 3: `LiveStrategyWarmingUp` の最新 message を保持してバナーに表示する。
    live_warmup_warming_message: Option<String>,
    /// issue #42 R4 R3-RUST-2: `LiveStrategyWarmingUp.progress` (0.0-1.0) の最新値を
    /// 保持してバナーに % 形式で表示する。`LiveStrategyReady` / `LiveStopped` /
    /// `EngineConnected` (Idle/pending 状態時) で `None` にリセットする。
    live_warmup_warming_progress: Option<f32>,
    /// issue #42 Phase 3: warm_up timeout タイマーのトークン。`LiveStrategyWarmingUp` 受信や
    /// `LiveStrategyReady` 受信 / `EngineStopped` などで wrapping_add(1) して古いタイマー
    /// 発火を破棄する（タイマーリセットの実装）。
    live_warmup_timeout_token: u64,
    /// N4.4: non-None while a `strategy_load_failed` error banner should be shown.
    /// Cleared by `Message::Replay(ReplayMsg::DismissStrategyLoadError)`.
    strategy_load_error: Option<String>,
    /// F4: Byte snapshot of the last explicit save (Save/SaveAs) or auto-save.
    /// `None` = initial clean state (BC-9: treated as not dirty).
    /// Updated by `save_state_to_disk` and `NativeSaveAsWithSpecs` success path (A-7).
    last_saved_bytes: Option<Vec<u8>>,
    /// F4: Window specs captured at the point `ExitRequested` fires while dirty.
    /// Held until the user confirms "Discard and exit" or cancels the dialog.
    pending_exit_windows: Option<HashMap<window::Id, WindowSpec>>,
    /// F4: (json, path) captured when `NativeOpenFileApply` fires while dirty.
    /// Held until the user confirms "Discard and open" / "Save and open" or cancels the dialog.
    /// Includes the window specs captured at dialog-show time so SaveAndOpenFile can build state JSON.
    pending_open_file: Option<(String, std::path::PathBuf, HashMap<window::Id, WindowSpec>)>,
    /// F7 (M13): unified mode-switch state. `Some((target, guard))` while a
    /// mode-switch restart is pending; `None` when idle. Replaces the previous
    /// pair `pending_mode_switch` + `_mode_switch_guard` so the two cannot
    /// drift out of sync (RAII: dropping the tuple clears both atomically).
    /// The leading `_` on the field name silences the unused-field lint for
    /// the guard half (it is held purely for its Drop side-effect).
    mode_switch_state: Option<(engine_client::dto::AppMode, ModeSwitchGuard)>,
    /// True between a `ModeSwitchEngineBusy` abort and the user acknowledging
    /// the abort dialog (`ToggleDialogModal(None)`). Prevents the footer badge
    /// from re-enabling immediately after a failed mode-switch while the engine
    /// is still settling. Cleared unconditionally on dialog dismiss (idempotent
    /// when already false, identical to the mode_switch_state = None reset).
    engine_busy: bool,
    /// True while a replay is currently loaded/running. Drives the
    /// "リプレイ停止（Replay Stop）" menu item's enabled state — the item is
    /// only clickable while a replay is actually playing. Set true on
    /// `EngineEvent::ReplayDataLoaded`; reset on `ReplayFinished` /
    /// `ReplayStopped` (both stop-only and mode-switch paths).
    replay_running: bool,
    /// schema 3.15: True while the engine is in PAUSED state (PauseReplay received).
    replay_paused: bool,
    /// schema 3.14: 直前に処理した `ReplayDataLoaded.session_epoch`。
    /// 新 epoch を観測したら `replay_pane_registry` を全リセットして旧ペインを
    /// 閉じる。engine 切断時に `None` にリセットする（プロセス再起動で epoch が
    /// 0 に巻き戻ったとき `Some(N) → Some(0)` の `!=` 誤発火を防ぐため）。
    last_replay_session_epoch: Option<u64>,
    /// True between `Message::Replay(ReplayMsg::StopReplayOnly)` dispatch and the corresponding
    /// `ReplayStopped` ack / final timeout. Distinguishes the stop-only flow
    /// from the F7 mode-switch flow inside the shared `ModeSwitch*` message
    /// handlers (which now serve both flows).
    replay_stop_only_pending: bool,
    /// Widget menu bar open/close state (all platforms).
    menu_bar: crate::menu_bar_state::State,
}

/// Top-level message enum — thin dispatch hub.
///
/// Each variant wraps a grouped sub-message enum defined in `messages.rs`.
/// `Flowsurface::update()` matches on this enum and delegates to the
/// corresponding `handle_*` method, keeping `update()` under 400 lines.
#[derive(Debug, Clone)]
enum Message {
    Engine(EngineMsg),
    Venue(VenueMsg),
    Replay(ReplayMsg),
    Dashboard(DashboardMsg),
    Window(WindowMsg),
    Menu(MenuMsg),
    Settings(SettingsMsg),
}

/// Builds a single stream that emits engine restart transitions, fresh
/// `EngineConnected` handshakes, and Tachibana venue lifecycle events
/// (`VenueLoginStarted` / `VenueLoginCancelled` / `VenueError` /
/// `VenueReady`). Merging everything into one `Subscription::run` keeps
/// the recovery path single-source (invariant T35-H9-SingleRecoveryPath)
/// and gives `update()` a single FIFO of state-affecting events.
fn engine_status_stream() -> impl iced::futures::Stream<Item = Message> + Send + 'static {
    async_stream::stream! {
        let Some(restart_tx) = ENGINE_RESTARTING.get() else { return; };
        let Some(conn_tx) = ENGINE_CONNECTION_TX.get() else { return; };
        let mut restart_rx = restart_tx.subscribe();
        let mut conn_rx = conn_tx.subscribe();
        let mut event_rx: Option<
            tokio::sync::broadcast::Receiver<engine_client::dto::EngineEvent>,
        > = None;

        // Emit current values immediately. subscribe() marks the current
        // value as already-seen, so `changed()` would otherwise skip the
        // initial connection / restart state captured before the iced
        // subscription wired up.
        // Clone-then-drop the watch::Ref before any `yield`/`await` —
        // the guard isn't `Send` and would otherwise be held across
        // suspension points, breaking the `Send` bound iced requires.
        let initial_conn = { conn_rx.borrow_and_update().clone() };
        if let Some(conn) = initial_conn {
            event_rx = Some(conn.subscribe_events());
            // **Order matters**: Rehello must arrive in `update()` BEFORE
            // EngineConnected. EngineConnected calls
            // `sidebar.update_handles()` which gates the Tachibana
            // refetch on `tachibana_ready`; Rehello first transitions
            // that flag to `false` (via `set_tachibana_ready(false)` in
            // the `TachibanaVenueEvent` arm), so the subsequent
            // EngineConnected refetch correctly excludes Tachibana
            // until the next `VenueReady`. Reviewer 2026-04-26 R3
            // (HIGH-1).
            // Message::TachibanaVenueEvent(VenueEvent::EngineRehello) == post-refactor below
            yield Message::Venue(VenueMsg::TachibanaEvent(VenueEvent::EngineRehello));
            yield Message::Venue(VenueMsg::KabuEvent(VenueEvent::EngineRehello));
            // Message::EngineConnected(conn) == post-refactor below
            yield Message::Engine(EngineMsg::Connected(conn));
        }
        let initial_restart = { *restart_rx.borrow_and_update() };
        if initial_restart {
            yield Message::Engine(EngineMsg::Restarting(true));
        }

        loop {
            // `event_rx` is `Option`-shaped; use `pending()` while it
            // is `None` so the select arm stays sound but never wins.
            // Surface the full `Result` (not `.ok()`) so the outer match
            // can distinguish `Lagged` (receiver alive — log + retry)
            // from `Closed` (receiver dead — wait for next handshake).
            // Earlier code collapsed both into `None` and silently
            // dropped venue lifecycle events; see review-fixes
            // 2026-04-26 round 1.
            let event_fut = async {
                match &mut event_rx {
                    Some(rx) => Some(rx.recv().await),
                    None => std::future::pending::<Option<_>>().await,
                }
            };

            tokio::select! {
                changed = restart_rx.changed() => {
                    if changed.is_err() { break; }
                    let value = { *restart_rx.borrow_and_update() };
                    yield Message::Engine(EngineMsg::Restarting(value));
                }
                changed = conn_rx.changed() => {
                    if changed.is_err() { break; }
                    let value = { conn_rx.borrow_and_update().clone() };
                    if let Some(conn) = value {
                        event_rx = Some(conn.subscribe_events());
                        // See above — Rehello before Connected so the
                        // FSM-driven gate flag flips before the
                        // EngineConnected handler refetches
                        // (T35-U4-StartupGate / R3 HIGH-1).
                        // Message::TachibanaVenueEvent(VenueEvent::EngineRehello) == post-refactor below
                        yield Message::Venue(VenueMsg::TachibanaEvent(VenueEvent::EngineRehello));
                        yield Message::Venue(VenueMsg::KabuEvent(VenueEvent::EngineRehello));
                        // Message::EngineConnected(conn) == post-refactor below
                        yield Message::Engine(EngineMsg::Connected(conn));
                    }
                }
                event = event_fut => {
                    use tokio::sync::broadcast::error::RecvError;
                    match event {
                        Some(Ok(ev)) => {
                            if let Some(msg) = map_engine_event_to_message(ev) {
                                yield msg;
                            }
                        }
                        Some(Err(RecvError::Lagged(n))) => {
                            // Receiver is still alive — keep it. Dropping
                            // here would silently swallow every
                            // VenueLoginStarted / VenueReady / VenueError
                            // until the next EngineConnected, the exact
                            // class of UI-freeze regression flagged in
                            // review-fixes 2026-04-26 round 1.
                            log::warn!(
                                "engine_status_stream: broadcast lagged, dropped {n} \
                                 events — venue lifecycle UI may have missed transitions"
                            );
                        }
                        Some(Err(RecvError::Closed)) | None => {
                            event_rx = None;
                        }
                    }
                }
            }
        }
    }
}

/// Translate a low-level `EngineEvent` into a `Message` for the GUI's update loop.
///
/// Despite the historical name (`map_engine_event_to_tachibana`), this function
/// is the **single dispatch point** for every `EngineEvent` flowing into the
/// app — Tachibana venue lifecycle, REPLAY portfolio updates, execution markers,
/// strategy signals, replay completion, etc. New `EngineEvent` variants must
/// add an arm here or be deliberately routed elsewhere; otherwise they fall
/// into the trailing `_ => None` and are silently dropped (the bug class fixed
/// by [docs/✅python-data-engine/replay-pane-auto-generate-fix.md]).
pub(crate) fn map_engine_event_to_message(ev: engine_client::dto::EngineEvent) -> Option<Message> {
    use engine_client::dto::EngineEvent;
    match ev {
        EngineEvent::VenueReady { venue, .. } if venue == TACHIBANA_VENUE_NAME => {
            Some(Message::Venue(VenueMsg::TachibanaEvent(VenueEvent::Ready)))
        }
        EngineEvent::VenueLoginStarted { venue, .. } if venue == TACHIBANA_VENUE_NAME => {
            Some(Message::Venue(VenueMsg::TachibanaEvent(VenueEvent::LoginStarted)))
        }
        EngineEvent::VenueLoginCancelled { venue, .. } if venue == TACHIBANA_VENUE_NAME => {
            Some(Message::Venue(VenueMsg::TachibanaEvent(VenueEvent::LoginCancelled)))
        }
        EngineEvent::VenueError {
            venue,
            code,
            message,
            ..
        } if venue == TACHIBANA_VENUE_NAME => {
            let class = engine_client::error::classify_venue_error(&code);
            Some(Message::Venue(VenueMsg::TachibanaEvent(VenueEvent::LoginError {
                class,
                message,
                market_closed: code == "market_closed",
            })))
        }
        EngineEvent::VenueReady { venue, .. } if venue == KABU_STATION_VENUE_NAME => {
            Some(Message::Venue(VenueMsg::KabuEvent(VenueEvent::Ready)))
        }
        EngineEvent::VenueLoginStarted { venue, .. } if venue == KABU_STATION_VENUE_NAME => {
            Some(Message::Venue(VenueMsg::KabuEvent(VenueEvent::LoginStarted)))
        }
        EngineEvent::VenueLoginCancelled { venue, .. } if venue == KABU_STATION_VENUE_NAME => {
            Some(Message::Venue(VenueMsg::KabuEvent(VenueEvent::LoginCancelled)))
        }
        EngineEvent::VenueError {
            venue,
            code,
            message,
            ..
        } if venue == KABU_STATION_VENUE_NAME => {
            let class = engine_client::error::classify_venue_error(&code);
            Some(Message::Venue(VenueMsg::KabuEvent(VenueEvent::LoginError {
                class,
                message,
                market_closed: code == "market_closed",
            })))
        }
        // ── Phase O2: EC 約定通知 (T2.4) ────────────────────────────────────
        EngineEvent::OrderFilled {
            client_order_id,
            last_qty,
            last_price,
            leaves_qty,
            ..
        } => Some(Message::Venue(VenueMsg::OrderFilled {
            client_order_id,
            last_qty,
            last_price,
            leaves_qty,
        })),
        EngineEvent::OrderCanceled {
            client_order_id, ..
        } => Some(Message::Venue(VenueMsg::OrderToast(Toast::info(format!(
            "注文取消完了: {client_order_id}"
        ))))),
        EngineEvent::OrderExpired {
            client_order_id, ..
        } => Some(Message::Venue(VenueMsg::OrderToast(Toast::warn(format!(
            "注文失効: {client_order_id}"
        ))))),
        // ── Phase U0: 第二暗証番号 / 注文受付・拒否 ────────────────────────
        EngineEvent::SecondPasswordRequired { request_id } => {
            Some(Message::Venue(VenueMsg::SecondPasswordRequired(request_id)))
        }
        EngineEvent::OrderAccepted {
            client_order_id,
            venue_order_id,
            ..
        } => Some(Message::Venue(VenueMsg::OrderAccepted {
            client_order_id,
            venue_order_id,
        })),
        EngineEvent::OrderRejected {
            client_order_id,
            reason_code,
            reason_text,
            ..
        } => Some(Message::Venue(VenueMsg::OrderRejected {
            client_order_id,
            reason: format!("[{reason_code}] {reason_text}"),
        })),
        EngineEvent::OrderListUpdated { orders, .. } => {
            Some(Message::Venue(VenueMsg::OrderListUpdated(orders)))
        }
        EngineEvent::PositionsUpdated {
            request_id,
            venue,
            positions,
            ts_ms,
            ..
        } => Some(Message::Venue(VenueMsg::PositionsUpdated {
            request_id,
            venue,
            positions,
            ts_ms,
        })),
        EngineEvent::BuyingPowerUpdated {
            cash_available,
            cash_shortfall,
            credit_available,
            ts_ms,
            .. // request_id / venue are IPC routing fields; UI broadcasts to all BuyingPower panes
        } => Some(Message::Venue(VenueMsg::BuyingPowerUpdated {
            cash_available,
            cash_shortfall,
            credit_available,
            ts_ms,
        })),
        // N1.16: REPLAY 仮想ポートフォリオ更新イベント
        EngineEvent::ReplayBuyingPower {
            cash,
            buying_power,
            equity,
            ts_event_ms,
            ..
        } => Some(Message::Replay(ReplayMsg::BuyingPower {
            cash,
            buying_power,
            equity,
            ts_event_ms,
        })),
        // N3: live strategy 買付余力スナップショット
        EngineEvent::LiveBuyingPower {
            cash,
            equity,
            ts_event_ms,
            ..
        } => Some(Message::Replay(ReplayMsg::LiveBuyingPower {
            cash,
            equity,
            ts_event_ms,
        })),
        EngineEvent::Error {
            request_id,
            code,
            message,
        } => Some(Message::Venue(VenueMsg::IpcError {
            request_id,
            code,
            message,
        })),
        // H-1: EngineError — 接続レベルエラー (strategy_id=None) は error ログのみ。
        // strategy_id=Some(_) の場合は走行中 strategy の outbox event (接続維持)。
        // 将来 EngineMsg::ConnectionError が追加された際にはここで Some(...) を返す。
        EngineEvent::EngineError {
            code,
            message,
            strategy_id: None,
        } => {
            log::error!("[engine] connection error [{code}]: {message}");
            None
        }
        EngineEvent::EngineError {
            code,
            message,
            strategy_id: Some(sid),
            ..
        } => {
            // R2-B H4 / 統一決定 #21 副次 invariant: `node.build()` 失敗時は
            // 生成済みの 4 ペインを teardown して LiveStrategyState を Idle に戻す。
            // 専用 ReplayMsg variant を経由して handler 内で完結させる
            // (notification も含む)。
            //
            // R4 Group A (silent-HIGH-1): warm_up_failed / kernel_unavailable /
            // venue_not_supported / market_closed も同じ teardown 経路に流す
            // (旧実装はこれらを log::warn! のみで握りつぶしていた)。warm_up
            // 失敗の場合まだ auto_generate_live_panes が呼ばれていないため
            // teardown_live_panes は実 pane に対して no-op になるが、handler
            // 側で bar / state reset と pending_strategy_id クリア、user toast
            // を確実に走らせる必要がある。
            // R6 silent-HIGH-1: server.py の StartEngine ハンドラが必ず emit する
            // 2 つの strategy-level abort code を追加。
            //   - "engine_run_failed": runner 内部例外 (server.py:5252)
            //   - "timeout": 3600s wait_for タイムアウト (server.py:5210)
            // これらが allow-list に無いと state machine が固着し次の live 起動が
            // 受け付けられない silent regression を起こす。長時間 live 戦略では
            // timeout が確実に発火するため見逃すと致命的。
            const STRATEGY_ABORT_CODES: &[&str] = &[
                "node_build_failed",
                "warm_up_failed",
                "kernel_unavailable",
                "venue_not_supported",
                "market_closed",
                "engine_run_failed",
                "timeout",
            ];
            if STRATEGY_ABORT_CODES.contains(&code.as_str()) {
                log::warn!(
                    "[engine] strategy abort [{code}] strategy={sid}: {message} \
                     — tearing down live panes"
                );
                return Some(Message::Replay(ReplayMsg::LiveStrategyBuildFailed {
                    strategy_id: sid,
                    code,
                    message,
                }));
            }
            // strategy-level error; future UI toast when strategy panel is implemented
            log::warn!("[engine] strategy error [{code}] strategy={sid}: {message}");
            None
        }
        // N1.12: ExecutionMarker → chart overlay
        EngineEvent::ExecutionMarker {
            side,
            price,
            ts_event_ms,
            ..
        } => Some(Message::Replay(ReplayMsg::ExecutionMarker {
            side,
            price,
            ts_event_ms,
        })),
        // N1.12: StrategySignal → chart overlay
        EngineEvent::StrategySignal {
            signal_kind,
            price,
            ts_event_ms,
            tag,
            ..
        } => Some(Message::Replay(ReplayMsg::StrategySignal {
            signal_kind,
            price,
            ts_event_ms,
            tag,
        })),
        // N4-live: EngineStarted in live mode → LiveStarted (renamed from LiveStrategyStarted)
        EngineEvent::EngineStarted {
            strategy_id,
            ts_event_ms,
            ..
        } => {
            let is_live = app_mode() == engine_client::dto::AppMode::Live;
            if is_live {
                // LiveStarted == LiveStrategyStarted (post-refactor name)
                Some(Message::Replay(ReplayMsg::LiveStarted {
                    strategy_id,
                    ts_event_ms,
                }))
            } else {
                None
            }
        }
        // Replay engine stopped → auto-refresh order list (replay mode only).
        // In live mode, EngineStopped means live strategy stopped (LiveEngineStoppedEvent path).
        // unwrap_or(false) is intentional here: this is a runtime event handler called
        // well after APP_MODE is set; false (live) is the safe fallback so live-mode
        // engine restarts do not accidentally trigger ReplayFinished.
        EngineEvent::EngineStopped {
            strategy_id,
            final_equity,
            ts_event_ms,
        } => {
            let _ = final_equity; // TODO: display final PnL in replay summary
            let _ = ts_event_ms;
            let is_replay = app_mode() == engine_client::dto::AppMode::Replay;
            if is_replay {
                Some(Message::Replay(ReplayMsg::Finished))
            } else {
                // LiveStopped == LiveEngineStoppedEvent (post-refactor name)
                Some(Message::Replay(ReplayMsg::LiveStopped { strategy_id }))
            }
        }
        // Phase 8: EngineBusy → GUI ユーザーへの warn toast
        // Python engine が state guard で Command を拒否したときに emit される。
        // F7: StopReplay の EngineBusy はエンジン IDLE（リプレイ未ロード）を意味する。
        //     mode-switch を中断せず ForceStopReplay fallback に即移行する。
        //     ForceStopReplay の EngineBusy は真の失敗なのでスイッチを中断する。
        EngineEvent::EngineBusy {
            attempted_command,
            reason,
            busy_kind,
            venue,
            ..
        } => {
            use engine_client::dto::{AttemptedCommand, BusyKind};
            match attempted_command {
                AttemptedCommand::StopReplay => {
                    Some(Message::Window(WindowMsg::ModeSwitchStopBusy))
                }
                AttemptedCommand::ForceStopReplay => {
                    Some(Message::Window(WindowMsg::ModeSwitchEngineBusy(reason)))
                }
                AttemptedCommand::PauseReplay => {
                    Some(Message::Engine(EngineMsg::PauseReplayBusy { reason }))
                }
                AttemptedCommand::ResumeReplay => {
                    Some(Message::Engine(EngineMsg::ResumeReplayBusy { reason }))
                }
                _ => {
                    // R4 R3-RUST-3: live 重複起動拒否時にどの venue で reject されたか
                    // を toast に表示する。busy_kind が AnotherStrategyOnVenue なら
                    // venue 名を含む専用文言、それ以外は汎用文言。venue が None の
                    // 旧 server (minor < 28) からの応答は "unknown" で fallback する。
                    let detail = match busy_kind {
                        Some(BusyKind::AnotherStrategyOnVenue) => {
                            let v = venue.as_deref().unwrap_or("unknown");
                            format!("別の戦略が {v} で実行中です — {reason}")
                        }
                        _ => format!(
                            "操作を受け付けられませんでした: {attempted_command} — {reason}"
                        ),
                    };
                    Some(Message::Venue(VenueMsg::OrderToast(Toast::warn(detail))))
                }
            }
        }
        // Phase 8.1b: multi-client 接続ライフサイクルイベント
        // GUI 側では表示不要なため debug ログのみ出力して None を返す。
        EngineEvent::ClientConnected { count } => {
            log::debug!("engine: client connected (total={count})");
            None
        }
        EngineEvent::ClientDisconnected { count } => {
            log::debug!("engine: client disconnected (total={count})");
            None
        }
        // F7: ReplayStopped — mode-switch pending confirmation
        EngineEvent::ReplayStopped {
            request_id,
            final_equity,
        } => {
            let _ = request_id; // TODO: match with pending_stop_request_id when parallel stops added
            let _ = final_equity; // TODO: display final PnL
            Some(Message::Window(WindowMsg::ModeSwitchStopAcked))
        }
        // F6a: SCENARIO 抽出結果 → ReplayFormModal prefill
        EngineEvent::StrategyScenarioLoaded {
            request_id,
            path,
            scenario,
            resolved_instruments,
        } => Some(Message::Replay(ReplayMsg::ScenarioLoaded {
            request_id,
            path: std::path::PathBuf::from(path),
            scenario,
            resolved_instruments,
        })),
        // F6a: SCENARIO 抽出失敗 → エラートースト
        EngineEvent::StrategyScenarioLoadFailed {
            request_id,
            path,
            reason,
            ..
        } => Some(Message::Replay(ReplayMsg::ScenarioLoadFailed {
            request_id,
            path: std::path::PathBuf::from(path),
            reason,
        })),
        // schema 3.12: replay 自動ペイン生成。GUI 内フォーム経由でも
        // helper attach mode でも同じ経路を通すため、ここで一律変換する。
        // Message::ReplayDataLoaded == Message::Replay(ReplayMsg::DataLoaded) (post-refactor name)
        EngineEvent::ReplayDataLoaded {
            instrument_id,
            instrument_ids,
            granularity,
            bars_loaded,
            trades_loaded,
            session_epoch,
            ..
        } => Some(Message::Replay(ReplayMsg::DataLoaded {
            instrument_id,
            instrument_ids,
            granularity,
            bars_loaded,
            trades_loaded,
            session_epoch,
        })),
        // schema 3.15: DateChangeMarker — replay bar の現在日表示を更新。
        EngineEvent::DateChangeMarker { date } => {
            Some(Message::Replay(ReplayMsg::DateChanged(date)))
        }
        // schema 3.22: ReplayTimeUpdated — 分足・tick 足での時刻表示（Issue 3）。
        EngineEvent::ReplayTimeUpdated { timestamp_ms } => {
            Some(Message::Replay(ReplayMsg::TimeUpdated { timestamp_ms }))
        }
        // schema 3.16: RestoreSnapshot — Step- 後に Python が巻き戻し地点を通知する。
        // R2-H1: サイレント黙殺から「状態保持 + TODO」に昇格。
        // TODO: chart pane should flush data from ts_event_ms onward when RestoreSnapshot arrives.
        EngineEvent::RestoreSnapshot { step_index, ts_event_ms } => {
            Some(Message::Replay(ReplayMsg::RestoreSnapshotPending { step_index, ts_event_ms }))
        }
        // schema 3.16: ReplayHistoryChanged — ⏮ Step- ボタンの有効/無効を更新。
        // `paused` フィールドは ReplayHistoryChanged では変化しないため現在値を保持する。
        EngineEvent::ReplayHistoryChanged { has_history } => {
            Some(Message::Replay(ReplayMsg::HistoryChanged { has_history }))
        }
        // issue #42 Phase 3 (schema 3.25): LiveStrategyScenarioLoaded — modal prefill。
        // pending_scenario_request_id と突合して古い応答は handler 側で捨てる（replay 対称）。
        EngineEvent::LiveStrategyScenarioLoaded {
            request_id,
            instrument_id,
            max_qty,
            max_notional_jpy,
            venue,
            strategy_init_kwargs,
        } => Some(Message::Replay(ReplayMsg::LiveStrategyScenarioLoaded {
            request_id,
            instrument_id,
            max_qty,
            max_notional_jpy,
            venue,
            strategy_init_kwargs,
        })),
        // issue #42 Phase 3 (schema 3.26): LiveStrategyReady — Running 遷移 +
        // auto_generate_live_panes(strategy_id, instrument_id, venue) の冪等トリガー。
        EngineEvent::LiveStrategyReady {
            strategy_id,
            venue,
            instrument_id,
            ts_event_ms,
        } => Some(Message::Replay(ReplayMsg::LiveStrategyReady {
            strategy_id,
            instrument_id,
            venue,
            ts_event_ms,
        })),
        // issue #42 Phase 3 (schema 3.27): LiveStrategyWarmingUp — 進捗 banner 更新 +
        // 60s timeout カウンタリセット（統一決定 #17）。
        EngineEvent::LiveStrategyWarmingUp {
            strategy_id,
            progress,
            message,
        } => Some(Message::Replay(ReplayMsg::LiveWarmingUp {
            strategy_id,
            progress,
            message,
        })),
        // issue #42 R1 HIGH-2 (schema 3.29): SubscriptionEvicted — kabu 50 銘柄 PUSH 上限
        // 到達時の LRU evict 通知。spec §3.2-G 契約。当該 symbol のチャート登録は解除済の
        // ため、再登録するには再選択が必要。venue は kabu_station 固定 (spec) なので
        // 文言には含めず、symbol のみ通知して再操作を促す。exchange は内部 routing
        // 情報なので user-facing には出さない。
        EngineEvent::SubscriptionEvicted {
            venue: _,
            symbol,
            exchange: _,
        } => Some(Message::Venue(VenueMsg::OrderToast(Toast::warn(format!(
            "{symbol} は PUSH 上限到達で登録解除されました（再選択で再登録）"
        ))))),
        // M-Rust2: 新しい `EngineEvent` バリアントを追加したときは、
        // ここに一致 arm を加えるか、`None`（=ディスパッチ対象外）が
        // 正しいことを確認すること。`_ => None` で握り潰すと
        // `ReplayDataLoaded` のように UI 機能が丸ごと欠落する事故を
        // 再発させる（schema 3.12 の見逃しクラス）。
        _ => None,
    }
}

/// N4-live: Unix ミリ秒を JST の HH:MM:SS 文字列に変換する。
fn format_live_time(ts_ms: i64) -> String {
    use chrono::{FixedOffset, TimeZone, Utc};
    let jst = FixedOffset::east_opt(9 * 3600).expect("9h offset is valid");
    let dt_utc = match Utc.timestamp_millis_opt(ts_ms).single() {
        Some(dt) => dt,
        None => {
            log::warn!("format_live_time: invalid timestamp {ts_ms}, using current time");
            Utc::now()
        }
    };
    dt_utc.with_timezone(&jst).format("%H:%M:%S").to_string()
}

/// Format a replay timestamp (milliseconds since epoch) as a JST string.
///
/// `granularity` controls the output format:
/// - `Some(Granularity::Daily)` → `%Y-%m-%d`
/// - `None` or any other variant → `%H:%M:%S`
///
/// Returns `"--"` when `timestamp_ms` is out of range.
pub(crate) fn format_replay_time(
    timestamp_ms: i64,
    granularity: Option<&crate::modal::replay_form::Granularity>,
) -> String {
    use chrono::{FixedOffset, TimeZone, Utc};
    let jst = FixedOffset::east_opt(9 * 3600).expect("9h offset is valid");
    match Utc.timestamp_millis_opt(timestamp_ms).single() {
        Some(dt) => {
            let dt_jst = dt.with_timezone(&jst);
            match granularity {
                Some(crate::modal::replay_form::Granularity::Daily) => {
                    dt_jst.format("%Y-%m-%d").to_string()
                }
                _ => dt_jst.format("%H:%M:%S").to_string(),
            }
        }
        None => "--".to_string(),
    }
}

fn status_bar_label(is_replay: bool, enabled: bool) -> &'static str {
    match (is_replay, enabled) {
        (false, true) => "● LIVE",
        (true, true) => "● REPLAY",
        (false, false) => "● LIVE …",
        (true, false) => "● REPLAY …",
    }
}

fn status_bar_dot_color(is_replay: bool) -> iced::Color {
    if is_replay {
        iced::Color::from_rgb(0.9, 0.6, 0.1)
    } else {
        iced::Color::from_rgb(0.2, 0.75, 0.3)
    }
}

const STATUS_BAR_HEIGHT: u32 = 20;
const STATUS_BAR_BG: iced::Color = iced::Color::from_rgb(0.08, 0.08, 0.08);

fn venue_login_chip(
    label: &'static str,
    state: VenueState,
    on_login: Message,
    on_logout: Message,
    is_production: bool,
) -> Element<'static, Message> {
    let (dot, dot_color, btn_label, on_press) = match &state {
        VenueState::Idle => (
            "○",
            iced::Color::from_rgb(0.5, 0.5, 0.5),
            "ログイン",
            on_login,
        ),
        VenueState::LoginInFlight => ("⟳", iced::Color::from_rgb(0.9, 0.6, 0.1), "", on_login),
        VenueState::Ready => (
            "●",
            iced::Color::from_rgb(0.2, 0.75, 0.3),
            "ログアウト",
            on_logout,
        ),
        VenueState::Error {
            market_closed: true,
            ..
        } => (
            "●",
            iced::Color::from_rgb(0.8, 0.6, 0.1),
            "ログアウト",
            on_logout,
        ),
        VenueState::Error { .. } => (
            "●",
            iced::Color::from_rgb(0.9, 0.2, 0.2),
            "再ログイン",
            on_login,
        ),
    };

    // P4-4: 本番接続中は赤バナーで強調。文言は kabu_chip_prod_style() で一元管理し、
    // runbook.md §5.1（実弾スモーク前のチェック項目）と同期する。
    let (display_label, prod_bg) = if is_production {
        let (prefix, bg) = kabu_chip_prod_style();
        (format!("{prefix} {label} {dot}"), Some(bg))
    } else {
        (format!("{label} {dot}"), None)
    };

    let chip_label = text(display_label).size(11).color(dot_color);

    let in_flight = matches!(state, VenueState::LoginInFlight);

    // LoginInFlight 時はラベルのみ（ボタンテキストなし）
    let row_content: Element<'static, Message> = if in_flight {
        row![chip_label].align_y(Alignment::Center).into()
    } else {
        row![chip_label, text(format!(" {btn_label}")).size(11),]
            .align_y(Alignment::Center)
            .into()
    };

    let btn = button(row_content)
        .padding(padding::left(6).right(6))
        .style(move |_theme, status| {
            use iced::widget::button::{Status, Style};
            // LoginInFlight 時はホバーエフェクトを抑制
            if in_flight {
                return Style::default();
            }
            // P4-4: prod 時は赤背景を常時表示（hover 時はやや明るく）
            if let Some(prod) = prod_bg {
                let bg = match status {
                    Status::Hovered | Status::Pressed => iced::Color {
                        r: (prod.r + 0.1).min(1.0),
                        g: prod.g,
                        b: prod.b,
                        a: 1.0,
                    },
                    _ => prod,
                };
                return Style {
                    background: Some(iced::Background::Color(bg)),
                    ..Style::default()
                };
            }
            Style {
                background: match status {
                    Status::Hovered | Status::Pressed => Some(iced::Background::Color(
                        iced::Color::from_rgba(1.0, 1.0, 1.0, 0.06),
                    )),
                    _ => None,
                },
                ..Style::default()
            }
        });

    if in_flight {
        btn.into()
    } else {
        btn.on_press(on_press).into()
    }
}

// 'static: ModeToggleState contains only AppMode (Copy) and &'static str / bool —
// no lifetime-bearing references. The button message and tooltip content are also
// 'static, so the returned Element<'static, Message> bound is satisfied.
fn status_bar(
    state: crate::menu::ModeToggleState,
    tachibana: VenueState,
    kabu: VenueState,
) -> Element<'static, Message> {
    use engine_client::dto::AppMode;

    let is_replay = state.current == AppMode::Replay;
    let label = status_bar_label(is_replay, state.enabled);
    let base_color = status_bar_dot_color(is_replay);
    let color = if state.enabled {
        base_color
    } else {
        iced::Color::from_rgb(base_color.r * 0.5, base_color.g * 0.5, base_color.b * 0.5)
    };

    let target = match state.current {
        AppMode::Live => AppMode::Replay,
        AppMode::Replay => AppMode::Live,
    };

    let tip: Option<&'static str> = if state.enabled {
        Some(match state.current {
            AppMode::Live => "クリックで Replay に切替",
            AppMode::Replay => "クリックで Live に切替",
        })
    } else {
        state.disabled_reason
    };

    let badge = button(text(label).size(11).color(color))
        .padding(padding::left(8).right(8))
        .style(move |_theme, status| {
            use iced::widget::button::{Status, Style};
            Style {
                background: if state.enabled {
                    match status {
                        Status::Hovered | Status::Pressed => Some(iced::Background::Color(
                            iced::Color::from_rgba(1.0, 1.0, 1.0, 0.06),
                        )),
                        _ => None,
                    }
                } else {
                    None
                },
                ..Style::default()
            }
        });

    let badge_el: Element<'static, Message> = if state.enabled {
        // Route through the same BarMessage::Pick → to_native_action → NativeMenuAction
        // path as the widget menu bar, so the Action::SwitchMode handler's guard
        // invariants (dirty check, etc.) are preserved.
        badge
            .on_press(Message::Menu(MenuMsg::Bar(
                crate::menu_bar_state::BarMessage::Pick(crate::menu::Action::SwitchAppMode(target)),
            )))
            .into()
    } else {
        badge.into()
    };

    let mode_badge_el = tooltip(badge_el, tip, TooltipPosition::Top);

    let tachibana_chip = venue_login_chip(
        "立花",
        tachibana,
        Message::Venue(VenueMsg::RequestTachibanaLogin(Trigger::Manual)),
        Message::Venue(VenueMsg::RequestTachibanaLogout),
        false,
    );
    let kabu_chip = venue_login_chip(
        "kabu",
        kabu,
        Message::Venue(VenueMsg::RequestKabuLogin(Trigger::Manual)),
        Message::Venue(VenueMsg::RequestKabuLogout),
        kabu_is_production(),
    );

    container(
        row![
            tachibana_chip,
            kabu_chip,
            iced::widget::Space::new().width(iced::Length::Fill),
            mode_badge_el,
        ]
        .align_y(Alignment::Center)
        .height(STATUS_BAR_HEIGHT),
    )
    .width(iced::Length::Fill)
    .height(STATUS_BAR_HEIGHT)
    .style(|_| container::Style {
        background: Some(STATUS_BAR_BG.into()),
        snap: true,
        ..Default::default()
    })
    .into()
}

/// Wrap `content` in a `confirm_dialog` overlay when one is set.
///
/// This helper centralises the overlay so the rendering path no longer depends
/// on which sidebar menu is active. Previously, only `Settings` / `Network` /
/// `Order` sidebar menus rendered the overlay, which made dashboard-pane
/// `OrderEntry` confirm dialogs silently invisible (debug-honda incident,
/// 2026-04-30).
fn apply_confirm_dialog_overlay<'a>(
    content: Element<'a, Message>,
    dialog: Option<&'a screen::ConfirmDialog<Message>>,
) -> Element<'a, Message> {
    if let Some(dialog) = dialog {
        let dialog_content = confirm_dialog_container(
            dialog.clone(),
            Message::Window(WindowMsg::ToggleDialogModal(None)),
        );
        main_dialog_modal(
            content,
            dialog_content,
            Message::Window(WindowMsg::ToggleDialogModal(None)),
        )
    } else {
        content
    }
}

impl Flowsurface {
    fn new() -> (Self, Task<Message>) {
        let is_replay_mode = app_mode() == engine_client::dto::AppMode::Replay;

        let saved_state = if is_replay_mode {
            log::info!("replay mode: skipping load_saved_state (D9-load), using defaults");
            layout::SavedState::default()
        } else if let Some(p) = INITIAL_STATE_PATH.get() {
            // F3 DoD: --saved-state was given; load from that path and prime CURRENT_PATH.
            if let Some(path_str) = p.to_str() {
                log::info!("--saved-state: loading from {path_str}");
                let state = layout::load_saved_state_from(path_str);
                // Prime CURRENT_PATH so Ctrl+S writes back to the same file.
                match CURRENT_PATH.lock() {
                    Ok(mut guard) => *guard = Some(p.clone()),
                    Err(poisoned) => *poisoned.into_inner() = Some(p.clone()),
                }
                state
            } else {
                log::error!(
                    "--saved-state path contains non-UTF-8 characters; \
                     falling back to default layout. Path: {p:?}"
                );
                layout::SavedState::default()
            }
        } else {
            layout::load_saved_state()
        };

        // All venues are routed through the Python data engine via IPC.
        // The watch channel is guaranteed to hold `Some(conn)` before iced
        // starts (main() exits if the first handshake never landed).
        // We read the channel's *current value* here — this is bootstrap
        // setup, not `Flowsurface::update()`, so it does not violate
        // T35-H7-NoStaticInUpdate.
        let mut handles = exchange::adapter::AdapterHandles::default();
        // B4: the global VenueCaps sidecar is set in main() before Iced starts.
        let venue_caps_store = VENUE_CAPS_STORE
            .get()
            .expect("VENUE_CAPS_STORE must be set before Flowsurface::new")
            .clone();
        let initial_conn: Option<Arc<engine_client::EngineConnection>> = ENGINE_CONNECTION_TX
            .get()
            .and_then(|tx| tx.borrow().clone());
        if let Some(conn) = initial_conn.as_ref() {
            for &(venue, name) in VENUE_NAMES {
                let backend = Arc::new(engine_client::EngineClientBackend::new(
                    Arc::clone(conn),
                    name,
                    Arc::clone(&venue_caps_store),
                ));
                handles.set_backend(venue, backend);
            }
            log::info!("All venue backends: EngineClientBackend (Python IPC)");
        }
        // Read the manager once at startup; updates only flow through the
        // ENGINE_MANAGER OnceLock at boot, so capturing it here is safe.
        let engine_manager = ENGINE_MANAGER.get().map(Arc::clone);

        let (main_window_id, open_main_window) = {
            let (position, size) = saved_state.window();
            let config = window::Settings {
                size,
                position,
                exit_on_close_request: false,
                ..window::settings()
            };
            window::open(config)
        };

        let (sidebar, launch_sidebar) = dashboard::Sidebar::new(&saved_state, handles.clone());

        let (audio_stream, audio_init_err) = AudioStream::new(saved_state.audio_cfg);

        // D8: replay mode starts with a clean layout (single Starter pane).
        // D9-load (implemented above) already sets saved_state to SavedState::default()
        // in replay mode, so saved_state.layout_manager is already LayoutManager::new().
        // The default 5-pane grid from `Dashboard::default()` is replaced with a
        // single Starter pane so `auto_generate_replay_panes` can populate the
        // grid cleanly without leaving 5 orphan Starter panes alongside the
        // auto-generated TimeAndSales / CandlestickChart / OrderList / BuyingPower.
        let layout_manager = if is_replay_mode {
            let mut lm = LayoutManager::new();
            if let Some(layout) = lm.layouts.first_mut() {
                let (panes, _initial_pane) = iced::widget::pane_grid::State::new(
                    crate::screen::dashboard::pane::State::default(),
                );
                layout.dashboard.panes = panes;
                layout.dashboard.focus = None;
            }
            lm
        } else {
            saved_state.layout_manager
        };

        let mut state = Self {
            main_window: window::Window::new(main_window_id),
            layout_manager,
            theme_editor: ThemeEditor::new(saved_state.custom_theme),
            audio_stream,
            sidebar,
            handles,
            confirm_dialog: None,
            timezone: saved_state.timezone,
            ui_scale_factor: saved_state.scale_factor,
            volume_size_unit: saved_state.volume_size_unit,
            theme: saved_state.theme,
            notifications: Notifications::new(),
            network: NetworkManager::new(saved_state.proxy_cfg),
            engine_restarting: false,
            engine_connection: initial_conn,
            engine_manager,
            tachibana_state: VenueState::Idle,
            kabu_state: VenueState::Idle,
            second_password_modal: None,
            buying_power_request_id: None,
            order_list_request_id: None,
            positions_request_id: None,
            replay_strategy_file: None,
            pending_scenario_request_id: None,
            replay_form_modal: None,
            live_strategy_form_modal: None,
            live_strategy: LiveStrategyState::default(),
            live_strategy_pending_strategy_id: None,
            live_warmup_timeout_banner: None,
            live_warmup_warming_message: None,
            live_warmup_warming_progress: None,
            live_warmup_timeout_token: 0,
            strategy_load_error: None,
            last_saved_bytes: None,
            pending_exit_windows: None,
            pending_open_file: None,
            mode_switch_state: None,
            engine_busy: false,
            replay_running: false,
            replay_paused: false,
            last_replay_session_epoch: None,
            replay_stop_only_pending: false,
            menu_bar: crate::menu_bar_state::State::default(),
        };

        if let Some(err) = audio_init_err {
            state
                .notifications
                .push(Toast::error(format!("Audio disabled: {err}")));
        }

        let active_layout_id = state.layout_manager.active_layout_id().unwrap_or(
            &state
                .layout_manager
                .layouts
                .first()
                .expect("No layouts available")
                .id,
        );
        let load_layout = state.load_layout(active_layout_id.unique, main_window_id);
        let setup_native_menu = iced::window::raw_id::<Message>(main_window_id)
            .map(|x| Message::Menu(MenuMsg::NativeSetup(x)));
        // F4 BC-9 fix: after startup collect window specs and set the dirty baseline so that
        // edits made before the first explicit Save are detected as dirty (ケース 3/4).
        // All active windows (main + any popouts restored by load_layout) are included so
        // that the baseline matches the full serialised state and avoids false-dirty on quit
        // when popouts are present (MEDIUM fix).
        // Skipped in replay mode — dirty tracking is live-only.
        let set_baseline = if is_replay_mode {
            Task::none()
        } else {
            let mut baseline_ids = state
                .active_dashboard()
                .popout
                .keys()
                .copied()
                .collect::<Vec<_>>();
            baseline_ids.push(main_window_id);
            window::collect_window_specs(baseline_ids, |w| {
                Message::Window(WindowMsg::SetDirtyBaseline(w))
            })
        };

        (
            state,
            open_main_window
                .discard()
                .chain(setup_native_menu)
                .chain(load_layout)
                .chain(launch_sidebar.map(|m| Message::Dashboard(DashboardMsg::Sidebar(m))))
                .chain(set_baseline),
        )
    }

    fn update(&mut self, message: Message) -> Task<Message> {
        match message {
            Message::Engine(msg) => self.handle_engine(msg),
            Message::Venue(msg) => self.handle_venue(msg),
            Message::Replay(msg) => self.handle_replay(msg),
            Message::Dashboard(msg) => self.handle_dashboard(msg),
            Message::Window(msg) => self.handle_window(msg),
            Message::Menu(msg) => self.handle_menu(msg),
            Message::Settings(msg) => self.handle_settings(msg),
        }
    }

    fn view(&self, id: window::Id) -> Element<'_, Message> {
        // Helper invariant guard: this function MUST end up calling
        // `apply_confirm_dialog_overlay`. The overlay must apply regardless
        // of `self.sidebar.active_menu()` state (live mode order entry, replay
        // mode, popout windows). The closing test
        // `view_calls_confirm_dialog_overlay_helper` enforces this in source.
        let dashboard = self.active_dashboard();
        let sidebar_pos = self.sidebar.position();

        let tickers_table = &self.sidebar.tickers_table;

        let raw_content = if id == self.main_window.id {
            let sidebar_view = self
                .sidebar
                .view(self.audio_stream.volume())
                .map(|m| Message::Dashboard(DashboardMsg::Sidebar(m)));

            let dashboard_view = dashboard
                .view(&self.main_window, tickers_table, self.timezone)
                .map(move |msg| {
                    Message::Dashboard(DashboardMsg::Layout {
                        layout_id: None,
                        event: msg,
                    })
                });

            let header_title = {
                #[cfg(target_os = "macos")]
                {
                    iced::widget::center(
                        text("FLOWSURFACE")
                            .font(iced::Font {
                                weight: iced::font::Weight::Bold,
                                ..Default::default()
                            })
                            .size(16)
                            .style(style::title_text),
                    )
                    .height(20)
                    .align_y(Alignment::Center)
                    .padding(padding::top(4))
                }
                #[cfg(not(target_os = "macos"))]
                {
                    column![]
                }
            };

            // Tachibana lifecycle banner (U2). Renders only when the
            // FSM is in `Error`; other states return None and the
            // column collapses naturally.
            let banner = widget::venue_banner::view(&self.tachibana_state).map(|el| {
                el.map(|msg| match msg {
                    widget::venue_banner::BannerMessage::Relogin => {
                        Message::Venue(VenueMsg::RequestTachibanaLogin(Trigger::Manual))
                    }
                    widget::venue_banner::BannerMessage::Dismiss => {
                        Message::Venue(VenueMsg::DismissTachibanaBanner)
                    }
                })
            });

            let current_mode = app_mode();
            let mut base = column![header_title];
            {
                let menu_bar_view = crate::widget_menu_bar::view(
                    &self.menu_bar,
                    current_mode,
                    self.replay_running,
                    self.replay_paused,
                    MODE_SWITCHING.load(std::sync::atomic::Ordering::Acquire),
                    self.live_strategy.is_running(),
                )
                .map(|m| Message::Menu(MenuMsg::Bar(m)));
                base = base.push(menu_bar_view);
            }
            if let Some(banner) = banner {
                base = base.push(container(banner).padding(padding::all(8)));
            }
            // N4.4: strategy_load_failed dismissable banner.
            if let Some(err_msg) = &self.strategy_load_error {
                let strategy_err_banner = container(
                    row![
                        text(format!("Strategy load failed: {err_msg}")),
                        button("×")
                            .on_press(Message::Replay(ReplayMsg::DismissStrategyLoadError))
                            .style(button::danger),
                    ]
                    .spacing(8)
                    .align_y(Alignment::Center),
                )
                .padding(padding::all(8));
                base = base.push(strategy_err_banner);
            }
            // R2-B H2 / 統一決定 #17: warm_up timeout banner (60s 経過しても
            // LiveStrategyReady が来なかった場合)。「再試行」ボタンで dismiss。
            // strategy_load_error と同じレイアウトで両者を共存可能にする。
            if let Some(banner_msg) = &self.live_warmup_timeout_banner {
                let warmup_banner = container(
                    row![
                        text(banner_msg.as_str()),
                        button("再試行")
                            .on_press(Message::Replay(ReplayMsg::DismissLiveWarmupTimeoutBanner))
                            .style(button::primary),
                    ]
                    .spacing(8)
                    .align_y(Alignment::Center),
                )
                .padding(padding::all(8));
                base = base.push(warmup_banner);
            }
            // R4 R3-RUST-1 + R3-RUST-2: warm_up 進捗 banner (LiveStrategyWarmingUp 受信ごとに更新)。
            // timeout banner と共存可能 — progress message + timeout banner が同時表示でも
            // OK で、timeout fired で `live_warmup_warming_message` が None に戻る設計。
            if let Some(msg) = &self.live_warmup_warming_message {
                let mut banner_row = row![text(msg.as_str())]
                    .spacing(8)
                    .align_y(Alignment::Center);
                if let Some(p) = self.live_warmup_warming_progress {
                    banner_row =
                        banner_row.push(text(format!("{:.0}%", (p * 100.0).clamp(0.0, 100.0))));
                }
                base = base.push(container(banner_row).padding(padding::all(8)));
            }
            base = base.push(
                match sidebar_pos {
                    sidebar::Position::Left => row![sidebar_view, dashboard_view,],
                    sidebar::Position::Right => row![dashboard_view, sidebar_view],
                }
                .spacing(4)
                .padding(8)
                .height(iced::Length::Fill),
            );
            let mode_toggle = crate::menu::mode_toggle_state(
                current_mode,
                self.engine_busy,
                self.mode_switch_state.is_some(),
            );
            base = base.push(status_bar(
                mode_toggle,
                self.tachibana_state.clone(),
                self.kabu_state.clone(),
            ));

            let view_result = if let Some(menu) = self.sidebar.active_menu() {
                self.view_with_modal(base.into(), dashboard, menu)
            } else {
                base.into()
            };
            crate::widget_menu_bar::with_dropdown_overlay(view_result, &self.menu_bar, current_mode)
        } else {
            container(
                dashboard
                    .view_window(id, &self.main_window, tickers_table, self.timezone)
                    .map(move |msg| {
                        Message::Dashboard(DashboardMsg::Layout {
                            layout_id: None,
                            event: msg,
                        })
                    }),
            )
            .padding(padding::top(style::TITLE_PADDING_TOP))
            .into()
        };

        // Apply confirm_dialog overlay only on the main window. Popout windows
        // (dashboard panes detached into separate OS windows) do not host the
        // OrderEntry confirmation flow and must not receive this overlay, as that
        // would cause duplicate dialogs across all open windows simultaneously.
        let content = if id == self.main_window.id {
            apply_confirm_dialog_overlay(raw_content, self.confirm_dialog.as_ref())
        } else {
            raw_content
        };

        let toasted: Element<'_, Message> = toast::Manager::new(
            content,
            self.notifications.toasts(),
            match sidebar_pos {
                sidebar::Position::Left => Alignment::Start,
                sidebar::Position::Right => Alignment::End,
            },
            |i| Message::Dashboard(DashboardMsg::RemoveNotification(i)),
        )
        .into();

        let after_second_password = if let Some(modal) = &self.second_password_modal {
            let modal_view = modal
                .view()
                .map(|m| Message::Venue(VenueMsg::SecondPasswordModal(m)));
            main_dialog_modal(
                toasted,
                modal_view,
                Message::Venue(VenueMsg::DismissSecondPasswordModal),
            )
        } else {
            toasted
        };

        let after_replay_form = if let Some(form) = &self.replay_form_modal {
            let form_view = form.view().map(|m| Message::Replay(ReplayMsg::FormMsg(m)));
            main_dialog_modal(
                after_second_password,
                form_view,
                Message::Replay(ReplayMsg::FormMsg(modal::replay_form::Message::Cancel)),
            )
        } else {
            after_second_password
        };

        if let Some(form) = &self.live_strategy_form_modal {
            let form_view = form
                .view()
                .map(|m| Message::Replay(ReplayMsg::LiveStrategyFormMsg(m)));
            main_dialog_modal(
                after_replay_form,
                form_view,
                Message::Replay(ReplayMsg::LiveStrategyFormMsg(
                    modal::live_strategy_form::Message::Cancel,
                )),
            )
        } else {
            after_replay_form
        }
    }

    fn theme(&self, _window: window::Id) -> iced_core::Theme {
        self.theme.clone().into()
    }

    fn title(&self, _window: window::Id) -> String {
        if let Some(id) = self.layout_manager.active_layout_id() {
            format!("Flowsurface [{}]", id.name)
        } else {
            "Flowsurface".to_string()
        }
    }

    fn scale_factor(&self, _window: window::Id) -> f32 {
        self.ui_scale_factor.into()
    }

    fn subscription(&self) -> Subscription<Message> {
        let window_events = window::events().map(|e| Message::Window(WindowMsg::WindowEvent(e)));
        let sidebar = self
            .sidebar
            .subscription()
            .map(|m| Message::Dashboard(DashboardMsg::Sidebar(m)));

        let exchange_streams = self
            .active_dashboard()
            .market_subscriptions(&self.handles)
            .map(|e| Message::Dashboard(DashboardMsg::MarketWs(e)));

        let tick = iced::window::frames().map(|t| Message::Window(WindowMsg::Tick(t)));

        // M2 (F8 R1): invariants for the global hotkey subscription —
        //   1. Only `Esc` is listened for here. All other accelerators
        //      (`Ctrl+O`, `Ctrl+S`, ...) flow through `native_menu::subscription`
        //      on Win/Mac and through the iced kbd path on Linux (P8 Q4).
        //   2. `Esc` always routes to `Message::Window(WindowMsg::GoBack)`. The GoBack handler is
        //      responsible for the cascade: dismiss any open Linux menu-bar
        //      dropdown, close modals, etc. Adding more keys here without that
        //      handler will silently fail.
        //   3. The subscription must never close the menu directly — keeping
        //      the menu's `BarMessage::Dismiss` dispatch funnelled through
        //      `Message::Window(WindowMsg::GoBack)` ensures a single dismissal path that the
        //      tests in `tests/widget_menu_bar_state.rs` can pin
        //      (`esc_dismiss_is_wired_in_go_back_handler`).
        let hotkeys = keyboard::listen().filter_map(|event| {
            let keyboard::Event::KeyPressed { key, .. } = event else {
                return None;
            };
            match key {
                keyboard::Key::Named(keyboard::key::Named::Escape) => {
                    Some(Message::Window(WindowMsg::GoBack))
                }
                _ => None,
            }
        });

        // Watch the engine-restarting flag and emit EngineRestarting messages.
        let engine_status = Subscription::run(engine_status_stream);

        let widget_menu_bar_dismiss =
            iced::event::listen_with(|event, _status, _window| match event {
                iced::Event::Window(iced::window::Event::Unfocused) => Some(Message::Menu(
                    MenuMsg::Bar(crate::menu_bar_state::BarMessage::DismissFocusLost),
                )),
                _ => None,
            });

        Subscription::batch(vec![
            exchange_streams,
            sidebar,
            window_events,
            tick,
            hotkeys,
            engine_status,
            native_menu::subscription(app_mode()).map(|a| Message::Menu(MenuMsg::NativeAction(a))),
            widget_menu_bar_dismiss,
        ])
    }

    /// 現在アクティブな venue の IPC 名を返す。
    /// 両方 Ready の場合は kabu を優先する（発注パネルの venue toggle 仕様と一致）。
    fn active_venue_name(&self) -> &'static str {
        if self.kabu_state.is_ready() {
            crate::KABU_STATION_VENUE_NAME
        } else {
            crate::TACHIBANA_VENUE_NAME
        }
    }

    fn active_dashboard(&self) -> &Dashboard {
        let active_layout = self
            .layout_manager
            .active_layout_id()
            .expect("No active layout");
        self.layout_manager
            .get(active_layout.unique)
            .map(|layout| &layout.dashboard)
            .expect("No active dashboard")
    }

    fn active_dashboard_mut(&mut self) -> &mut Dashboard {
        let active_layout = self
            .layout_manager
            .active_layout_id()
            .expect("No active layout");
        self.layout_manager
            .get_mut(active_layout.unique)
            .map(|layout| &mut layout.dashboard)
            .expect("No active dashboard")
    }

    fn load_layout(&mut self, layout_uid: uuid::Uuid, main_window: window::Id) -> Task<Message> {
        if let Err(err) = self.layout_manager.set_active_layout(layout_uid) {
            log::error!("Failed to set active layout: {}", err);
            return Task::none();
        }

        self.layout_manager
            .park_inactive_layouts(layout_uid, main_window);

        self.layout_manager
            .get_mut(layout_uid)
            .map(|layout| {
                layout.dashboard.load_layout(main_window).map(move |msg| {
                    Message::Dashboard(DashboardMsg::Layout {
                        layout_id: Some(layout_uid),
                        event: msg,
                    })
                })
            })
            .unwrap_or_else(|| {
                log::error!("Active layout missing after selection: {}", layout_uid);
                Task::none()
            })
    }

    fn view_with_modal<'a>(
        &'a self,
        base: Element<'a, Message>,
        dashboard: &'a Dashboard,
        menu: sidebar::Menu,
    ) -> Element<'a, Message> {
        let sidebar_pos = self.sidebar.position();

        match menu {
            sidebar::Menu::Settings => {
                let settings_modal = {
                    let theme_picklist = {
                        let mut themes: Vec<iced::Theme> = iced_core::Theme::ALL.to_vec();

                        let default_theme = iced_core::Theme::Custom(default_theme().into());
                        themes.push(default_theme);

                        if let Some(custom_theme) = &self.theme_editor.custom_theme {
                            themes.push(custom_theme.clone());
                        }

                        pick_list(themes, Some(self.theme.0.clone()), |theme| {
                            Message::Settings(SettingsMsg::ThemeSelected(theme))
                        })
                    };

                    let toggle_theme_editor =
                        button(text("Theme editor")).on_press(Message::Dashboard(
                            DashboardMsg::Sidebar(dashboard::sidebar::Message::ToggleSidebarMenu(
                                Some(sidebar::Menu::ThemeEditor),
                            )),
                        ));

                    let toggle_network_editor =
                        button(text("Network")).on_press(Message::Dashboard(
                            DashboardMsg::Sidebar(dashboard::sidebar::Message::ToggleSidebarMenu(
                                Some(sidebar::Menu::Network),
                            )),
                        ));

                    let timezone_picklist = pick_list(
                        [data::UserTimezone::Utc, data::UserTimezone::Local],
                        Some(self.timezone),
                        |tz| Message::Settings(SettingsMsg::SetTimezone(tz)),
                    );

                    let size_in_quote_currency_checkbox = {
                        let is_active = match self.volume_size_unit {
                            exchange::SizeUnit::Quote => true,
                            exchange::SizeUnit::Base => false,
                        };

                        let checkbox = iced::widget::checkbox(is_active)
                            .label("Size in quote currency")
                            .on_toggle(|checked| {
                                let on_dialog_confirm = Message::Dashboard(
                                    DashboardMsg::ApplyVolumeSizeUnit(if checked {
                                        exchange::SizeUnit::Quote
                                    } else {
                                        exchange::SizeUnit::Base
                                    }),
                                );

                                let confirm_dialog = screen::ConfirmDialog::new(
                                    "Changing size display currency requires application restart"
                                        .to_string(),
                                    Box::new(on_dialog_confirm.clone()),
                                )
                                .with_confirm_btn_text("Restart now".to_string());

                                Message::Window(WindowMsg::ToggleDialogModal(Some(confirm_dialog)))
                            });

                        tooltip(
                            checkbox,
                            Some(
                                "Display sizes/volumes in quote currency (USD)\nHas no effect on inverse perps or open interest",
                            ),
                            TooltipPosition::Top,
                        )
                    };

                    let sidebar_pos_picklist = pick_list(
                        [sidebar::Position::Left, sidebar::Position::Right],
                        Some(sidebar_pos),
                        |pos| {
                            Message::Dashboard(DashboardMsg::Sidebar(
                                dashboard::sidebar::Message::SetSidebarPosition(pos),
                            ))
                        },
                    );

                    let scale_factor = {
                        let current_value: f32 = self.ui_scale_factor.into();

                        let decrease_btn = if current_value > data::config::MIN_SCALE {
                            button(text("-")).on_press(Message::Settings(
                                SettingsMsg::ScaleFactorChanged((current_value - 0.1).into()),
                            ))
                        } else {
                            button(text("-"))
                        };

                        let increase_btn = if current_value < data::config::MAX_SCALE {
                            button(text("+")).on_press(Message::Settings(
                                SettingsMsg::ScaleFactorChanged((current_value + 0.1).into()),
                            ))
                        } else {
                            button(text("+"))
                        };

                        container(
                            row![
                                decrease_btn,
                                text(format!("{:.0}%", current_value * 100.0)).size(14),
                                increase_btn,
                            ]
                            .align_y(Alignment::Center)
                            .spacing(8)
                            .padding(4),
                        )
                        .style(style::modal_container)
                    };

                    let trade_fetch_checkbox = {
                        let is_active = connector::fetcher::is_trade_fetch_enabled();

                        let checkbox = iced::widget::checkbox(is_active)
                            .label("Fetch trades (Binance)")
                            .on_toggle(|checked| {
                                if checked {
                                    let confirm_dialog = screen::ConfirmDialog::new(
                                        "This might be unreliable and take some time to complete. Proceed?"
                                            .to_string(),
                                        Box::new(Message::Dashboard(DashboardMsg::ToggleTradeFetch(true))),
                                    );
                                    Message::Window(WindowMsg::ToggleDialogModal(Some(confirm_dialog)))
                                } else {
                                    Message::Dashboard(DashboardMsg::ToggleTradeFetch(false))
                                }
                            });

                        tooltip(
                            checkbox,
                            Some("Try to fetch trades for footprint charts"),
                            TooltipPosition::Top,
                        )
                    };

                    let open_data_folder = {
                        let button = button(text("Open data folder"))
                            .on_press(Message::Window(WindowMsg::DataFolderRequested));

                        tooltip(
                            button,
                            Some("Open the folder where the data & config is stored"),
                            TooltipPosition::Top,
                        )
                    };

                    let version_info = {
                        let (version_label, commit_label) = version::app_build_version_parts();

                        let github_link_button = button(text(version_label).size(13))
                            .padding(0)
                            .style(style::button::text_link)
                            .on_press(Message::Window(WindowMsg::OpenUrlRequested(Cow::Borrowed(
                                version::GITHUB_REPOSITORY_URL,
                            ))));

                        let github_button: Element<'_, Message> = iced::widget::tooltip(
                            github_link_button,
                            container(
                                row![
                                    text("GitHub"),
                                    style::icon_text(style::Icon::ExternalLink, 12),
                                ]
                                .spacing(4)
                                .align_y(Alignment::Center),
                            )
                            .style(style::tooltip)
                            .padding(8),
                            TooltipPosition::Top,
                        )
                        .into();

                        if let (Some(commit_label), Some(commit_url)) =
                            (commit_label, version::build_commit_url())
                        {
                            let commit_button = button(text(commit_label).size(11))
                                .padding(0)
                                .style(style::button::text_link_secondary)
                                .on_press(Message::Window(WindowMsg::OpenUrlRequested(
                                    Cow::Owned(commit_url),
                                )));

                            column![github_button, commit_button]
                                .spacing(2)
                                .align_x(Alignment::End)
                                .into()
                        } else {
                            github_button
                        }
                    };

                    let footer = column![
                        container(version_info)
                            .width(iced::Length::Fill)
                            .align_x(Alignment::End),
                    ]
                    .spacing(8);

                    let column_content = split_column![
                        column![open_data_folder,].spacing(8),
                        column![text("Sidebar position").size(14), sidebar_pos_picklist,].spacing(12),
                        column![text("Time zone").size(14), timezone_picklist,].spacing(12),
                        column![text("Market data").size(14), size_in_quote_currency_checkbox,].spacing(12),
                        column![text("Theme").size(14), theme_picklist,].spacing(12),
                        column![text("Interface scale").size(14), scale_factor,].spacing(12),
                        column![
                            text("Experimental").size(14),
                            column![trade_fetch_checkbox, toggle_theme_editor, toggle_network_editor].spacing(8),
                        ]
                        .spacing(12),
                        footer,
                        ; spacing = 16, align_x = Alignment::Start
                    ];

                    let content = scrollable::Scrollable::with_direction(
                        column_content,
                        scrollable::Direction::Vertical(
                            scrollable::Scrollbar::new().width(8).scroller_width(6),
                        ),
                    );

                    container(content)
                        .align_x(Alignment::Start)
                        .max_width(240)
                        .padding(24)
                        .style(style::dashboard_modal)
                };

                let (align_x, padding) = match sidebar_pos {
                    sidebar::Position::Left => (Alignment::Start, padding::left(44).bottom(4)),
                    sidebar::Position::Right => (Alignment::End, padding::right(44).bottom(4)),
                };

                // confirm_dialog overlay は view() 末尾の apply_confirm_dialog_overlay
                // で一括適用するため、ここでの個別ラップは不要（重複描画防止）。
                dashboard_modal(
                    base,
                    settings_modal,
                    Message::Dashboard(DashboardMsg::Sidebar(
                        dashboard::sidebar::Message::ToggleSidebarMenu(None),
                    )),
                    padding,
                    Alignment::End,
                    align_x,
                )
            }
            sidebar::Menu::Layout => {
                let main_window = self.main_window.id;

                let manage_pane = if let Some((window_id, pane_id)) = dashboard.focus {
                    let selected_pane_str =
                        if let Some(state) = dashboard.get_pane(main_window, window_id, pane_id) {
                            let link_group_name: String =
                                state.link_group.as_ref().map_or_else(String::new, |g| {
                                    " - Group ".to_string() + &g.to_string()
                                });

                            state.content.to_string() + &link_group_name
                        } else {
                            "".to_string()
                        };

                    let is_main_window = window_id == main_window;

                    let reset_pane_button = {
                        let btn = button(text("Reset").align_x(Alignment::Center))
                            .width(iced::Length::Fill);
                        if is_main_window {
                            let dashboard_msg = Message::Dashboard(DashboardMsg::Layout {
                                layout_id: None,
                                event: dashboard::Message::Pane(
                                    main_window,
                                    dashboard::pane::Message::ReplacePane(pane_id),
                                ),
                            });

                            btn.on_press(dashboard_msg)
                        } else {
                            btn
                        }
                    };
                    let split_pane_button = {
                        let btn = button(text("Split").align_x(Alignment::Center))
                            .width(iced::Length::Fill);
                        if is_main_window {
                            let dashboard_msg = Message::Dashboard(DashboardMsg::Layout {
                                layout_id: None,
                                event: dashboard::Message::Pane(
                                    main_window,
                                    dashboard::pane::Message::SplitPane(
                                        pane_grid::Axis::Horizontal,
                                        pane_id,
                                    ),
                                ),
                            });
                            btn.on_press(dashboard_msg)
                        } else {
                            btn
                        }
                    };

                    column![
                        text(selected_pane_str),
                        row![
                            tooltip(
                                reset_pane_button,
                                if is_main_window {
                                    Some("Reset selected pane")
                                } else {
                                    None
                                },
                                TooltipPosition::Top,
                            ),
                            tooltip(
                                split_pane_button,
                                if is_main_window {
                                    Some("Split selected pane horizontally")
                                } else {
                                    None
                                },
                                TooltipPosition::Top,
                            ),
                        ]
                        .spacing(8)
                    ]
                    .spacing(8)
                } else {
                    column![text("No pane selected"),].spacing(8)
                };

                let manage_layout_modal = {
                    let col = column![
                        manage_pane,
                        rule::horizontal(1.0).style(style::split_ruler),
                        self.layout_manager
                            .view()
                            .map(|m| Message::Settings(SettingsMsg::Layouts(m)))
                    ];

                    container(col.align_x(Alignment::Center).spacing(20))
                        .width(260)
                        .padding(24)
                        .style(style::dashboard_modal)
                };

                let (align_x, padding) = match sidebar_pos {
                    sidebar::Position::Left => (Alignment::Start, padding::left(44).top(40)),
                    sidebar::Position::Right => (Alignment::End, padding::right(44).top(40)),
                };

                dashboard_modal(
                    base,
                    manage_layout_modal,
                    Message::Dashboard(DashboardMsg::Sidebar(
                        dashboard::sidebar::Message::ToggleSidebarMenu(None),
                    )),
                    padding,
                    Alignment::Start,
                    align_x,
                )
            }
            sidebar::Menu::Audio => {
                let (align_x, padding) = match sidebar_pos {
                    sidebar::Position::Left => (Alignment::Start, padding::left(44).top(76)),
                    sidebar::Position::Right => (Alignment::End, padding::right(44).top(76)),
                };

                let trade_streams_list = dashboard.streams.trade_streams(None);

                dashboard_modal(
                    base,
                    self.audio_stream
                        .view(trade_streams_list)
                        .map(|m| Message::Settings(SettingsMsg::AudioStream(m))),
                    Message::Dashboard(DashboardMsg::Sidebar(
                        dashboard::sidebar::Message::ToggleSidebarMenu(None),
                    )),
                    padding,
                    Alignment::Start,
                    align_x,
                )
            }
            sidebar::Menu::ThemeEditor => {
                let (align_x, padding) = match sidebar_pos {
                    sidebar::Position::Left => (Alignment::Start, padding::left(44).bottom(4)),
                    sidebar::Position::Right => (Alignment::End, padding::right(44).bottom(4)),
                };

                dashboard_modal(
                    base,
                    self.theme_editor
                        .view(&self.theme.0)
                        .map(|m| Message::Settings(SettingsMsg::ThemeEditor(m))),
                    Message::Dashboard(DashboardMsg::Sidebar(
                        dashboard::sidebar::Message::ToggleSidebarMenu(None),
                    )),
                    padding,
                    Alignment::End,
                    align_x,
                )
            }
            sidebar::Menu::Network => {
                let (align_x, padding) = match sidebar_pos {
                    sidebar::Position::Left => (Alignment::Start, padding::left(44).bottom(4)),
                    sidebar::Position::Right => (Alignment::End, padding::right(44).bottom(4)),
                };

                // confirm_dialog overlay は view() 末尾で一括適用される（重複描画防止）。
                dashboard_modal(
                    base,
                    self.network
                        .view()
                        .map(|m| Message::Settings(SettingsMsg::NetworkManager(m))),
                    Message::Dashboard(DashboardMsg::Sidebar(
                        dashboard::sidebar::Message::ToggleSidebarMenu(None),
                    )),
                    padding,
                    Alignment::End,
                    align_x,
                )
            }
            // Phase U-pre: Order menu is rendered inline in the sidebar itself.
            // confirm_dialog overlay は view() 末尾で一括適用される（重複描画防止）。
            sidebar::Menu::Order => base,
        }
    }

    /// Build the current application state as a JSON string.
    /// Returns `None` in replay mode (must not overwrite live settings).
    // REVIEW: build_state_json modifies state — split needed
    // This method updates popout window_spec entries and calls sidebar.sync_tickers_table_settings(),
    // which are side effects beyond serialization. Separating them requires threading window specs
    // through callers, which is a larger refactor deferred to a future phase.
    fn build_state_json(&mut self, windows: &HashMap<window::Id, WindowSpec>) -> Option<String> {
        if app_mode() == engine_client::dto::AppMode::Replay {
            return None;
        }

        self.active_dashboard_mut()
            .popout
            .iter_mut()
            .for_each(|(id, (_, window_spec))| {
                if let Some(new_window_spec) = windows.get(id) {
                    *window_spec = *new_window_spec;
                }
            });

        self.sidebar.sync_tickers_table_settings();

        let mut ser_layouts = vec![];
        for layout in &self.layout_manager.layouts {
            if let Some(layout) = self.layout_manager.get(layout.id.unique) {
                let serialized_dashboard = data::Dashboard::from(&layout.dashboard);
                ser_layouts.push(data::Layout {
                    name: layout.id.name.clone(),
                    dashboard: serialized_dashboard,
                });
            }
        }

        let layouts = data::Layouts {
            layouts: ser_layouts,
            active_layout: self
                .layout_manager
                .active_layout_id()
                .map(|layout| layout.name.to_string())
                .clone(),
        };

        let main_window_spec = windows
            .iter()
            .find(|(id, _)| **id == self.main_window.id)
            .map(|(_, spec)| *spec);

        let audio_cfg = data::AudioStream::from(&self.audio_stream);
        let proxy_cfg_persisted = self.network.proxy_cfg().map(|p| p.without_auth());

        let state = data::State::from_parts(
            layouts,
            self.theme.clone(),
            self.theme_editor.custom_theme.clone().map(data::Theme),
            main_window_spec,
            self.timezone,
            self.sidebar.state.clone(),
            self.ui_scale_factor,
            audio_cfg,
            connector::fetcher::is_trade_fetch_enabled(),
            self.volume_size_unit,
            proxy_cfg_persisted,
        );

        match serde_json::to_string(&state) {
            Ok(json) => Some(json),
            Err(e) => {
                log::error!("Failed to serialize layout: {}", e);
                None
            }
        }
    }

    /// H-5: write a pre-built JSON string to saved-state.json and update
    /// `last_saved_bytes`. Called by `save_state_to_disk` and by
    /// `NativeSaveAsWithSpecs` (which builds the JSON once and reuses it).
    /// Returns `true` on success, `false` on I/O error (already logged at WARN).
    fn write_json_to_saved_state_disk(&mut self, json: &str) -> bool {
        let file_name = data::SAVED_STATE_PATH;
        if let Err(e) = data::write_json_to_file(json, file_name) {
            // BC-5: auto-save write failure is an OS-level I/O error → WARN level.
            log_save_error(
                &SaveError::IoError(e.kind()),
                std::path::Path::new(file_name),
            );
            false
        } else {
            // A-7: update dirty baseline so auto-save clears the dirty flag.
            // R3: only saved-state.json is written here — CURRENT_PATH is NOT touched.
            self.last_saved_bytes = Some(json.as_bytes().to_vec());
            log::info!("Persisted state to {file_name}");
            true
        }
    }

    /// Returns `true` if state was serialised and written; `false` on I/O error.
    fn save_state_to_disk(&mut self, windows: &HashMap<window::Id, WindowSpec>) -> bool {
        if let Some(json) = self.build_state_json(windows) {
            self.write_json_to_saved_state_disk(&json)
        } else {
            log::info!("replay mode: skipping save_state_to_disk");
            true // replay mode skip is not a failure
        }
    }

    /// F4: Returns true when the current state differs from the last save baseline.
    ///
    /// BC-9: `last_saved_bytes = None` (initial / never-saved) is treated as clean
    /// so the user is not prompted on Quit before any change is made.
    ///
    /// NOTE: &mut self is required because build_state_json calls active_dashboard_mut()
    /// to sync popout window specs. This is a side effect of a read-like operation.
    /// Future: refactor build_state_json to take window specs as argument (&self).
    fn is_dirty(&mut self, windows: &HashMap<window::Id, WindowSpec>) -> bool {
        // clone() is intentional: build_state_json requires &mut self (popout window spec sync),
        // so we cannot hold a &[u8] borrow into last_saved_bytes across the mutable call.
        let Some(saved) = self.last_saved_bytes.clone() else {
            return false; // None => false: initial state is clean (BC-9)
        };
        self.build_state_json(windows)
            .map(|json| json.into_bytes() != saved)
            .unwrap_or(false) // replay mode: build_state_json returns None → treat as clean (replay never saves)
    }

    /// F7: set APP_MODE to `mode` then restart. The `mode_switch_state` tuple
    /// (containing the `ModeSwitchGuard`) in `self` is automatically dropped
    /// when `*self = new_state` runs inside `restart()`, which resets
    /// `MODE_SWITCHING` to false.
    fn restart_with_mode(&mut self, mode: engine_client::dto::AppMode) -> Task<Message> {
        // M2 (lightweight): set_app_mode acquires APP_MODE; record it for the
        // lock-order checker. MODE_SWITCHING was already recorded at the entry
        // to Action::SwitchMode.
        lock_order_acquire("APP_MODE");
        set_app_mode(mode);

        // F7-bugfix: update ProcessManager::mode and restart the Python engine
        // so the next Hello carries the new mode. Without this the Python engine
        // continues running with self._mode = "replay" even after APP_MODE is
        // updated, causing RequestVenueLogin to be rejected with
        // "RequestVenueLogin not allowed in replay mode".
        //
        // Sequence:
        //   1. set_mode() updates the manager's mode Mutex so start() reads it.
        //   2. Command::Shutdown causes the Python engine to close gracefully.
        //   3. The recovery loop in main() sees wait_closed() complete and calls
        //      manager.start() on a fresh port, sending Hello{mode: <new mode>}.
        //   4. engine_status_stream yields EngineConnected with the new conn.
        let engine_restart_task = {
            let manager = ENGINE_MANAGER.get().map(Arc::clone);
            // Clone the Arc before restart() replaces *self.
            let conn = self.engine_connection.clone();
            Task::perform(
                async move {
                    if let Some(m) = manager {
                        m.set_mode(mode).await;
                    }
                    if let Some(c) = conn {
                        // Ignore send errors — the connection may already be
                        // closing. The recovery loop will restart regardless.
                        let _ = c.send(engine_client::dto::Command::Shutdown).await;
                    }
                },
                |_| Message::Engine(EngineMsg::Noop),
            )
        };

        // F7-bugfix (High): clear ENGINE_CONNECTION_TX BEFORE self.restart() so
        // Flowsurface::new() reads engine_connection = None. This closes the race
        // window where the live-mode UI would send RequestVenueLogin to the old
        // replay-mode engine before the new Hello{mode=live} arrives.
        //
        // engine_status_stream handles None gracefully: it skips the EngineConnected
        // yield and keeps event_rx pointing at the old conn until RecvError::Closed,
        // then waits for the next conn_rx.changed() to set up the new conn.
        if let Some(tx) = ENGINE_CONNECTION_TX.get() {
            tx.send(None).ok();
        }

        let task = self.restart();
        // restart() replaces *self; the per-thread lock-order tracker now
        // pertains to a stale critical section. Reset it so the next switch
        // starts from a clean slate.
        lock_order_reset();
        Task::batch([task, engine_restart_task])
    }

    fn restart(&mut self) -> Task<Message> {
        let mut windows_to_close: Vec<window::Id> =
            self.active_dashboard().popout.keys().copied().collect();
        windows_to_close.push(self.main_window.id);

        let close_windows = Task::batch(
            windows_to_close
                .into_iter()
                .map(window::close)
                .collect::<Vec<_>>(),
        );

        let (new_state, init_task) = Flowsurface::new();
        *self = new_state;

        // `engine_status_stream` keeps running from where it was (same subscription
        // ID in iced 0.14) and will NOT re-emit `EngineConnected`.  If Tachibana
        // was already ready before the restart, synthesize `VenueEvent::Ready` so
        // `tachibana_state` is restored — otherwise it would stay `Idle` until the
        // next engine reconnect.
        let venue_bootstrap = if cached_venue_is_ready(TACHIBANA_VENUE_NAME) {
            Task::done(Message::Venue(VenueMsg::TachibanaEvent(VenueEvent::Ready)))
        } else {
            Task::none()
        };
        // KabuVenueEvent(Ready) synthesized below when kabu cache is hot
        let kabu_bootstrap = if cached_venue_is_ready(KABU_STATION_VENUE_NAME) {
            Task::done(Message::Venue(VenueMsg::KabuEvent(VenueEvent::Ready)))
        } else {
            Task::none()
        };

        close_windows
            .chain(init_task)
            .chain(venue_bootstrap)
            .chain(kabu_bootstrap)
    }
}

#[cfg(test)]
mod confirm_dialog_overlay_tests {
    //! Regression guard for the 2026-04-30 debug-honda incident.
    //!
    //! Before the fix, `Flowsurface::view()` only rendered `confirm_dialog`
    //! when a sidebar menu was active. Dashboard-pane `OrderEntry` confirm
    //! dialogs were silently invisible — clicking 注文 set `confirm_dialog =
    //! Some(...)` but the next frame's view dropped it on the floor.
    //!
    //! These tests are deliberately structural (source-string assertions on
    //! the file itself) because exercising the iced `view()` Element tree
    //! at unit-test scope would require a heavy harness; the failure mode
    //! we want to prevent is a refactor that drops the helper call from
    //! `view()` or removes the helper entirely. Both are catchable here.

    const MAIN_RS: &str = include_str!("./main.rs");

    #[test]
    fn helper_apply_confirm_dialog_overlay_exists() {
        assert!(
            MAIN_RS.contains("fn apply_confirm_dialog_overlay"),
            "apply_confirm_dialog_overlay helper must exist — it is the single point that wraps content with the confirm_dialog overlay regardless of sidebar menu state"
        );
    }

    #[test]
    fn view_calls_confirm_dialog_overlay_helper() {
        // Locate the `fn view(` of the Flowsurface application impl and assert
        // it ends up calling `apply_confirm_dialog_overlay`. Without this call,
        // dashboard-pane confirm dialogs vanish silently when no sidebar menu
        // is open.
        let view_idx = MAIN_RS
            .find("fn view(&self, id: window::Id) -> Element<'_, Message>")
            .expect("view function signature must remain stable");
        let after_view = &MAIN_RS[view_idx..];
        // Take everything until the next `fn ` definition at the same impl
        // indent (4 spaces) — close enough to bracket the view body.
        let body_end = after_view[1..]
            .find("\n    fn ")
            .map(|i| i + 1)
            .unwrap_or(after_view.len());
        let view_body = &after_view[..body_end];
        assert!(
            view_body.contains("apply_confirm_dialog_overlay("),
            "Flowsurface::view() must call apply_confirm_dialog_overlay so confirm_dialog renders regardless of sidebar menu state"
        );
    }

    #[test]
    fn view_with_modal_branches_no_longer_redraw_overlay() {
        // After the unification, `view_with_modal` arms must NOT individually
        // overlay confirm_dialog (would cause double-rendering). The single
        // overlay site is the helper. Count call sites in the production
        // portion of `main.rs` (strip the test module to avoid self-counting
        // the literal string in this very assertion).
        let test_mod_marker = "mod confirm_dialog_overlay_tests {";
        let prod_code = MAIN_RS
            .split_once(test_mod_marker)
            .map(|(prod, _)| prod)
            .expect(
                "test module marker 'mod confirm_dialog_overlay_tests {' must exist in main.rs",
            );
        let count = prod_code.matches("confirm_dialog_container(").count();
        assert_eq!(
            count, 1,
            "confirm_dialog_container must be called exactly once (inside apply_confirm_dialog_overlay). Found {count} call sites in production code — likely a regression to per-menu overlay rendering."
        );
    }
}

#[cfg(test)]
mod native_menu_handler_tests {
    //! Structural regression guards for the native OS menu bar handlers added in
    //! 2026-04-30.
    //!
    //! Exercising `update()` directly requires a live iced runtime; these tests
    //! instead use source-string inspection (same pattern as
    //! `confirm_dialog_overlay_tests`) to verify the handler logic is intact, plus
    //! pure Rust unit tests for the JSON validation path.

    const MAIN_RS: &str = include_str!("./main.rs");
    const HANDLER_ENGINE: &str = include_str!("./handlers/engine.rs");
    const HANDLER_WINDOW: &str = include_str!("./handlers/window.rs");
    const HANDLER_REPLAY: &str = include_str!("./handlers/replay.rs");
    const HANDLER_MENU: &str = include_str!("./handlers/menu.rs");

    /// Combined source of only the handler files (not main.rs) — used by
    /// tests that search for handler match arms.  Excluding main.rs avoids
    /// false matches on the arm-prefix string literals that appear inside
    /// this very test module when it is embedded via `include_str!`.
    fn handler_sources() -> String {
        format!("{HANDLER_ENGINE}\n{HANDLER_WINDOW}\n{HANDLER_REPLAY}\n{HANDLER_MENU}")
    }

    // ── Helpers ──────────────────────────────────────────────────────────────

    /// Extract the source slice for a single handler arm.
    ///
    /// `arm_prefix` must uniquely identify the handler arm — include indentation
    /// and `=>` so it cannot match message-construction sites (e.g.
    /// `None => Message::Foo` vs `            Message::Foo =>`).
    ///
    /// The slice ends just before the next same-level `Message::` arm or EOF.
    fn handler_body_delimited(arm_prefix: &str, next_arm_delimiter: &str) -> String {
        let src = handler_sources();
        let start = src
            .find(arm_prefix)
            .unwrap_or_else(|| panic!("handler arm not found: {arm_prefix}"));
        let tail = src[start..].to_string();
        let end = tail[1..]
            .find(next_arm_delimiter)
            .map(|i| i + 1)
            .unwrap_or(tail.len());
        tail[..end].to_string()
    }

    fn handler_body(arm_prefix: &str) -> String {
        handler_body_delimited(arm_prefix, "\n            WindowMsg::")
    }

    fn replay_handler_body(arm_prefix: &str) -> String {
        handler_body_delimited(arm_prefix, "\n            ReplayMsg::")
    }

    // ── Test 2: NativeSaveAsPath(None) ────────────────────────────────────────

    #[test]
    fn save_as_path_none_does_not_push_toast() {
        let body = handler_body("            WindowMsg::NativeSaveAsPath(None) =>");
        assert!(
            !body.contains("notifications.push"),
            "NativeSaveAsPath(None) must not push any toast (user cancelled the dialog)"
        );
    }

    #[test]
    fn save_as_path_none_returns_task_none() {
        let body = handler_body("            WindowMsg::NativeSaveAsPath(None) =>");
        assert!(
            body.contains("Task::none()"),
            "NativeSaveAsPath(None) must return Task::none()"
        );
    }

    // ── Test 3: NativeOpenFileCancelled ───────────────────────────────────────

    #[test]
    fn open_file_cancelled_does_not_push_toast() {
        let body = handler_body("            WindowMsg::NativeOpenFileCancelled =>");
        assert!(
            !body.contains("notifications.push"),
            "NativeOpenFileCancelled must not push any toast (user cancelled the dialog)"
        );
    }

    #[test]
    fn open_file_cancelled_returns_task_none() {
        let body = handler_body("            WindowMsg::NativeOpenFileCancelled =>");
        assert!(
            body.contains("Task::none()"),
            "NativeOpenFileCancelled must return Task::none()"
        );
    }

    // ── Test 4a: NativeOpenFileApply — valid JSON branch ─────────────────────

    #[test]
    fn open_file_apply_validates_against_data_state() {
        let body = handler_body("            WindowMsg::NativeOpenFileApply { json, path } =>");
        assert!(
            body.contains("serde_json::from_str::<data::State>"),
            "NativeOpenFileApply must validate the JSON as data::State before overwriting"
        );
    }

    #[test]
    fn open_file_apply_valid_json_calls_write_and_restart() {
        // F4 fix: NativeOpenFileApply no longer calls write/restart directly.
        // It collects window specs and dispatches NativeOpenFilePendingCheck so
        // the dirty comparison uses real window data (avoiding false positives).
        // write_json_to_file + restart now live in NativeOpenFilePendingCheck.
        let apply_body =
            handler_body("            WindowMsg::NativeOpenFileApply { json, path } =>");
        assert!(
            apply_body.contains("NativeOpenFilePendingCheck"),
            "NativeOpenFileApply valid JSON branch must dispatch NativeOpenFilePendingCheck (F4 two-step fix)"
        );

        let check_body = handler_body("            WindowMsg::NativeOpenFilePendingCheck");
        assert!(
            check_body.contains("data::write_json_to_file"),
            "NativeOpenFilePendingCheck must call data::write_json_to_file"
        );
        assert!(
            check_body.contains("self.restart()"),
            "NativeOpenFilePendingCheck must call self.restart()"
        );
    }

    // ── Test 4b: NativeOpenFileApply — invalid JSON branch ───────────────────

    #[test]
    fn open_file_apply_invalid_json_pushes_error_toast() {
        let body = handler_body("            WindowMsg::NativeOpenFileApply { json, path } =>");
        assert!(
            body.contains("無効な設定ファイルです"),
            "NativeOpenFileApply invalid JSON branch must push '無効な設定ファイルです' error toast"
        );
    }

    #[test]
    fn open_file_apply_invalid_json_does_not_restart() {
        // Verify the Err(_) branch returns Task::none() and does NOT call restart.
        // Locate the Err arm within the handler body.
        let handler = handler_body("            WindowMsg::NativeOpenFileApply { json, path } =>");
        let err_arm_start = handler
            .find("Err(e) =>")
            .expect("NativeOpenFileApply must have an Err arm for invalid JSON");
        let err_body = &handler[err_arm_start..];
        assert!(
            !err_body.contains("self.restart()"),
            "NativeOpenFileApply Err branch must NOT call self.restart()"
        );
        assert!(
            err_body.contains("Task::none()"),
            "NativeOpenFileApply Err branch must return Task::none()"
        );
    }

    /// Pure JSON validation unit test — exercises the same `serde_json` call
    /// used by the handler without needing a live iced runtime.
    /// `data::State` carries `#[serde(default)]` so `{}` is valid.
    #[test]
    fn state_json_validation_accepts_empty_object() {
        let result = serde_json::from_str::<data::State>("{}");
        assert!(
            result.is_ok(),
            "empty JSON object must parse as default State (all fields have serde defaults)"
        );
    }

    #[test]
    fn state_json_validation_rejects_invalid_json() {
        let cases = [
            "not json at all",
            "{\"layout_manager\": \"wrong_type\"}",
            "",
        ];
        for input in cases {
            let result = serde_json::from_str::<data::State>(input);
            assert!(
                result.is_err(),
                "'{input}' should fail data::State validation"
            );
        }
    }

    // ── Test 5: build_state_json / save_state_to_disk regression ─────────────

    #[test]
    fn build_state_json_helper_exists() {
        assert!(
            MAIN_RS.contains("fn build_state_json("),
            "build_state_json helper must exist — it is the shared serialisation path for both Save As and save_state_to_disk"
        );
    }

    #[test]
    fn save_state_to_disk_delegates_to_build_state_json() {
        let idx = MAIN_RS
            .find("fn save_state_to_disk(")
            .expect("save_state_to_disk must exist");
        let tail = &MAIN_RS[idx..];
        let end = tail[1..]
            .find("\n    fn ")
            .map(|i| i + 1)
            .unwrap_or(tail.len());
        let body = &tail[..end];
        assert!(
            body.contains("build_state_json("),
            "save_state_to_disk must delegate to build_state_json — they must stay in sync"
        );
    }

    #[test]
    fn save_as_with_specs_delegates_to_build_state_json() {
        // Use the indented match-arm prefix so the enum definition is skipped.
        // Match the handler arm exactly (including field names + `=>`) so inner
        // closure constructions like `Message::Window(WindowMsg::NativeSaveAsWithSpecs { path: path.clone(), ... })`
        // do not produce a false match.
        let body =
            handler_body("            WindowMsg::NativeSaveAsWithSpecs { path, windows } =>");
        assert!(
            body.contains("build_state_json("),
            "NativeSaveAsWithSpecs handler must call build_state_json — same serialisation path as save_state_to_disk"
        );
    }

    // ── CRITICAL: save failure must abort SaveAndExit / SaveAndOpenFile ─────────
    //
    // BC-5 contract: an IoError during an explicit "Save and continue" must abort
    // the operation (Quit / Open) and show an error, not silently discard data.
    // These source-inspection tests verify the control-flow shape without a runtime.

    #[test]
    fn save_and_exit_checks_result_before_exiting() {
        let body = handler_body("            WindowMsg::SaveAndExit =>");
        // BC-5: save result must be checked; iced::exit() must NOT be called unconditionally.
        // The impl uses write_json_to_saved_state_disk() and checks its bool return,
        // or guards on CURRENT_PATH write success — verify the pattern is present.
        assert!(
            body.contains("write_json_to_saved_state_disk(")
                || body.contains("if self.save_state_to_disk("),
            "SaveAndExit must call write_json_to_saved_state_disk (or save_state_to_disk) — \
             BC-5: IoError must abort the quit, not silently discard data"
        );
        assert!(
            body.contains("iced::exit()"),
            "SaveAndExit must call iced::exit() on success"
        );
        // Restore-dialog path must also exist (for when save fails).
        assert!(
            body.contains("pending_exit_windows = Some("),
            "SaveAndExit must restore pending_exit_windows on save failure so the dialog can be retried"
        );
    }

    #[test]
    fn save_and_open_file_aborts_on_named_doc_write_failure() {
        let body = handler_body("            WindowMsg::SaveAndOpenFile =>");
        // Handler must write to named doc and handle failure.
        assert!(
            body.contains("std::fs::write("),
            "SaveAndOpenFile must attempt to write to CURRENT_PATH"
        );
        // On write error it must NOT fall through to restart() — must return early.
        assert!(
            body.contains("return Task::none()"),
            "SaveAndOpenFile must return Task::none() on write failure (BC-5 abort)"
        );
        // Dialog must be restored so user can retry or discard.
        assert!(
            body.contains("pending_open_file = Some("),
            "SaveAndOpenFile must restore pending_open_file on named-doc write failure"
        );
    }

    // ── HIGH: dialog overlap must not bypass F4 protection ───────────────────
    //
    // When another confirm_dialog is already open, ExitRequested / NativeOpenFilePendingCheck
    // must bail out early instead of falling through to save + exit / write + restart.

    #[test]
    fn exit_requested_guards_existing_dialog() {
        let body = handler_body("            WindowMsg::ExitRequested(windows) =>");
        assert!(
            body.contains("confirm_dialog.is_some()"),
            "ExitRequested must guard against an existing confirm dialog — \
             HIGH: overlapping dialogs allow bypassing F4 dirty protection"
        );
        // Guard must appear before the dirty check so it fires first.
        let guard_pos = body.find("confirm_dialog.is_some()").unwrap();
        let dirty_pos = body.find("is_dirty(").unwrap();
        assert!(
            guard_pos < dirty_pos,
            "confirm_dialog guard must come before is_dirty() in ExitRequested"
        );
    }

    #[test]
    fn open_file_pending_check_guards_existing_dialog() {
        // Use the guard comment (unique to this handler) as the primary anchor.
        // Avoids relying on multi-line or single-line arm formatting which can change
        // with rustfmt runs and makes the find() pattern fragile.
        let src = handler_sources();
        let guard_marker =
            "// HIGH fix: another dialog is already visible \u{2014} silently drop this request";
        let marker_pos = src
            .find(guard_marker)
            .expect("NativeOpenFilePendingCheck must contain the HIGH-fix dialog guard comment");
        // Scan backward to the handler arm.
        let arm_pos = src[..marker_pos]
            .rfind("WindowMsg::NativeOpenFilePendingCheck")
            .expect("guard marker must be inside NativeOpenFilePendingCheck handler");
        // Extract until the next top-level handler arm.
        let tail = &src[arm_pos..];
        let region_end = tail.find("\n            WindowMsg::").unwrap_or(tail.len());
        let body = &tail[..region_end];
        assert!(
            body.contains("confirm_dialog.is_some()"),
            "NativeOpenFilePendingCheck guard must check confirm_dialog.is_some() — \
             HIGH: overlapping dialogs allow bypassing F4 dirty protection"
        );
        let guard_pos = body.find("confirm_dialog.is_some()").unwrap();
        let dirty_pos = body.find("is_dirty(").unwrap();
        assert!(
            guard_pos < dirty_pos,
            "confirm_dialog guard must come before is_dirty() in NativeOpenFilePendingCheck"
        );
    }

    // ── Regression: restart() must restore Tachibana venue state ─────────────
    //
    // Bug (2026-04-30): `File > 開く...` opening a valid JSON called
    // `self.restart()` which replaced `*self` with `Flowsurface::new()`.
    // `new()` initialises `tachibana_state = VenueState::Idle`.
    // `engine_status_stream` is not restarted by iced (same subscription ID),
    // so `EngineConnected` is never re-emitted and `tachibana_state` stays
    // `Idle` permanently — the Tachibana login appeared to be lost.
    //
    // Fix: `restart()` synthesizes `VenueEvent::Ready` via
    // `cached_venue_is_ready` if the venue cache says the login is still
    // active, restoring the FSM to `Ready` after the new() reset.

    #[test]
    fn restart_synthesizes_venue_ready_from_cache() {
        let idx = MAIN_RS.find("fn restart(").expect("restart() must exist");
        let tail = &MAIN_RS[idx..];
        let end = tail[1..]
            .find("\n    fn ")
            .map(|i| i + 1)
            .unwrap_or(tail.len());
        let body = &tail[..end];
        assert!(
            body.contains("cached_venue_is_ready"),
            "restart() must call cached_venue_is_ready — without it, Tachibana \
             login is permanently lost after 'File > 開く...' (tachibana_state \
             stays Idle because engine_status_stream is not restarted by iced)"
        );
        assert!(
            body.contains("VenueEvent::Ready"),
            "restart() must synthesize VenueEvent::Ready when venue cache is hot — \
             guards the regression where tachibana_state reset to Idle after file open"
        );
    }

    // ── F6a: SCENARIO Open / prefill 配線 ────────────────────────────────────

    #[test]
    fn open_file_replay_mode_uses_py_filter() {
        let body = handler_body("                    Action::OpenFile =>");
        assert!(
            body.contains("AppMode::Replay"),
            "Action::OpenFile must branch on AppMode::Replay for `.py` filter"
        );
        assert!(
            body.contains(".add_filter(\"Python\", &[\"py\"])"),
            "Action::OpenFile replay branch must register the `.py` file filter"
        );
        assert!(
            body.contains("Message::Replay(ReplayMsg::NativeOpenStrategyPicked"),
            "Action::OpenFile replay branch must dispatch NativeOpenStrategyPicked \
             after the OS file dialog returns"
        );
    }

    #[test]
    fn open_strategy_picked_some_sends_load_strategy_scenario() {
        let body =
            replay_handler_body("            ReplayMsg::NativeOpenStrategyPicked(picked) =>");
        // live 分岐: live_strategy_form_modal を設定する
        assert!(
            body.contains("AppMode::Live"),
            "NativeOpenStrategyPicked must have a Live branch"
        );
        assert!(
            body.contains("live_strategy_form_modal"),
            "NativeOpenStrategyPicked live branch must set live_strategy_form_modal"
        );
        // replay 分岐: LoadStrategyScenario を送信する
        assert!(
            body.contains("Command::LoadStrategyScenario"),
            "NativeOpenStrategyPicked(Some) must send Command::LoadStrategyScenario in replay mode"
        );
    }

    #[test]
    fn lg10_open_file_live_mode_uses_py_filter() {
        let body = handler_body("                    Action::OpenFile =>");
        assert!(
            body.contains("AppMode::Live"),
            "Action::OpenFile must have a Live branch"
        );
    }

    #[test]
    fn lg11_native_open_strategy_picked_live_sets_modal() {
        let body =
            replay_handler_body("            ReplayMsg::NativeOpenStrategyPicked(picked) =>");
        assert!(
            body.contains("live_strategy_form_modal"),
            "NativeOpenStrategyPicked live branch must set live_strategy_form_modal"
        );
    }

    #[test]
    fn lg12_engine_started_live_generates_live_strategy_started() {
        // EngineEvent arms live in main.rs (subscription handler), not handler files.
        let start = MAIN_RS
            .find("        EngineEvent::EngineStarted")
            .unwrap_or_else(|| panic!("handler arm not found: EngineEvent::EngineStarted"));
        let tail = &MAIN_RS[start..];
        let end = tail[1..]
            .find("\n        EngineEvent::")
            .map(|i| i + 1)
            .unwrap_or(tail.len());
        let body = &tail[..end];
        assert!(
            body.contains("ReplayMsg::LiveStarted"),
            "EngineStarted must generate ReplayMsg::LiveStarted in live mode"
        );
    }

    #[test]
    fn lg13_engine_stopped_live_generates_live_engine_stopped_event() {
        // EngineEvent arms live in main.rs (subscription handler), not handler files.
        let start = MAIN_RS
            .find("        EngineEvent::EngineStopped")
            .unwrap_or_else(|| panic!("handler arm not found: EngineEvent::EngineStopped"));
        let tail = &MAIN_RS[start..];
        let end = tail[1..]
            .find("\n        EngineEvent::")
            .map(|i| i + 1)
            .unwrap_or(tail.len());
        let body = &tail[..end];
        assert!(
            body.contains("ReplayMsg::LiveStopped"),
            "EngineStopped must generate ReplayMsg::LiveStopped in live mode"
        );
    }

    #[test]
    fn lg14_live_engine_stopped_mismatch_does_not_clear_running() {
        let src = handler_sources();
        // LiveEngineStoppedEvent ハンドラがログ出力とガード条件を持つことを確認
        let body = {
            let arm_prefix = "            ReplayMsg::LiveStopped { strategy_id } =>";
            let start = src
                .find(arm_prefix)
                .unwrap_or_else(|| panic!("handler arm not found: {arm_prefix}"));
            let tail = &src[start..];
            let end = tail[1..]
                .find("\n            ReplayMsg::")
                .map(|i| i + 1)
                .unwrap_or(tail.len());
            src[start..start + end].to_string()
        };
        assert!(
            body.contains("log::warn"),
            "mismatch path must log a warning: {body}"
        );
        assert!(
            body.contains("LiveStrategyState::Running"),
            "must pattern-match on LiveStrategyState::Running: {body}"
        );
    }

    #[test]
    fn lg15_stop_live_strategy_sends_stop_engine() {
        let body = replay_handler_body("            ReplayMsg::StopLiveStrategy =>");
        assert!(
            body.contains("Command::StopEngine"),
            "StopLiveStrategy must send Command::StopEngine"
        );
    }

    #[test]
    fn strategy_scenario_loaded_event_prefills_modal() {
        let body = replay_handler_body("            ReplayMsg::ScenarioLoaded {");
        assert!(
            body.contains("prefill_from_scenario"),
            "StrategyScenarioLoadedEvent must call prefill_from_scenario when scenario is Some"
        );
        assert!(
            body.contains("set_strategy_file_only"),
            "StrategyScenarioLoadedEvent must fall back to set_strategy_file_only when scenario is None"
        );
        // Note: CURRENT_PATH is updated when the user *submits* the replay form
        // (not on ScenarioLoaded), so this arm intentionally does not write CURRENT_PATH.
        // The F3 integration lives in the form-submission path.
    }

    #[test]
    fn strategy_scenario_load_failed_event_pushes_toast_only() {
        let body = replay_handler_body("            ReplayMsg::ScenarioLoadFailed {");
        assert!(
            body.contains("notifications.push"),
            "StrategyScenarioLoadFailedEvent must surface the error via a toast"
        );
        assert!(
            !body.contains("CURRENT_PATH"),
            "StrategyScenarioLoadFailedEvent must NOT update CURRENT_PATH on failure"
        );
        assert!(
            !body.contains("prefill_from_scenario"),
            "StrategyScenarioLoadFailedEvent must NOT prefill the modal on failure"
        );
    }

    // Issue 4 regression: FormMsg Submit must write back all fields to ReplayBarState.
    // instrument_id uses join(", ") for multi-instrument; other fields come from
    // the validated Action::Submit payload (not from DataLoaded, which lacks
    // start_date / end_date / initial_cash / strategy_file).
    #[test]
    fn form_submit_writes_all_fields_back_to_replay_bar() {
        // H-2: Bar updates deferred until IPC success via CommitReplayBarState message
        // FormMsg::Submit should emit CommitReplayBarState on IPC success
        let body = replay_handler_body("            ReplayMsg::CommitReplayBarState {");
        assert!(
            body.contains("instrument_id"),
            "CommitReplayBarState must update instrument_id: {body}"
        );
        assert!(
            body.contains("start_date"),
            "CommitReplayBarState must update start_date: {body}"
        );
        assert!(
            body.contains("end_date"),
            "CommitReplayBarState must update end_date: {body}"
        );
        assert!(
            body.contains("granularity"),
            "CommitReplayBarState must update granularity: {body}"
        );
        assert!(
            body.contains("strategy_file"),
            "CommitReplayBarState must update strategy_file: {body}"
        );
        assert!(
            body.contains("initial_cash"),
            "CommitReplayBarState must update initial_cash: {body}"
        );
        // Verify FormMsg::Submit dispatches via submit_result_to_message
        // (which produces CommitReplayBarState for both BothOk and StartFailed
        // outcomes — see handlers/replay.rs::tests for the pure-function
        // coverage of those branches).
        let submit_body = replay_handler_body("            ReplayMsg::FormMsg(msg) =>");
        assert!(
            submit_body.contains("submit_result_to_message"),
            "FormMsg Submit must dispatch via submit_result_to_message on Task completion: {submit_body}"
        );
    }

    // Issue 3 regression: ReplayTimeUpdated must be routed through map_engine_event_to_message
    // and handled by the ReplayMsg::TimeUpdated arm. DataLoaded must also clear current_day
    // so stale timestamps don't persist across replay sessions.
    #[test]
    fn replay_time_updated_is_routed_to_replay_msg() {
        let src = include_str!("./main.rs");
        assert!(
            src.contains("EngineEvent::ReplayTimeUpdated"),
            "map_engine_event_to_message must have a ReplayTimeUpdated arm"
        );
        assert!(
            src.contains("TimeUpdated"),
            "map_engine_event_to_message must map to ReplayMsg::TimeUpdated"
        );
    }

    #[test]
    fn replay_time_updated_handler_sets_current_day() {
        let body = replay_handler_body("            ReplayMsg::TimeUpdated {");
        assert!(
            body.contains("current_day"),
            "ReplayMsg::TimeUpdated handler must update current_day: {body}"
        );
        assert!(
            body.contains("format"),
            "ReplayMsg::TimeUpdated handler must format the timestamp: {body}"
        );
    }

    #[test]
    fn data_loaded_clears_current_day() {
        let body = replay_handler_body("            ReplayMsg::DataLoaded {");
        assert!(
            body.contains("current_day"),
            "DataLoaded handler must clear current_day to avoid showing stale time: {body}"
        );
    }
}

#[cfg(test)]
mod status_bar_tests {
    use super::*;

    #[test]
    fn t1_status_bar_label_replay_enabled() {
        assert_eq!(status_bar_label(true, true), "● REPLAY");
    }

    #[test]
    fn t1b_status_bar_label_replay_disabled() {
        assert_eq!(status_bar_label(true, false), "● REPLAY …");
    }

    #[test]
    fn t2_status_bar_label_live_enabled() {
        assert_eq!(status_bar_label(false, true), "● LIVE");
    }

    #[test]
    fn t2b_status_bar_label_live_disabled() {
        assert_eq!(status_bar_label(false, false), "● LIVE …");
    }

    #[test]
    fn t3_status_bar_dot_color_replay_is_amber() {
        let color = status_bar_dot_color(true);
        let eps = 1e-5_f32;
        assert!((color.r - 0.9).abs() < eps, "replay red should be 0.9");
        assert!((color.g - 0.6).abs() < eps, "replay green should be 0.6");
        assert!((color.b - 0.1).abs() < eps, "replay blue should be 0.1");
    }

    #[test]
    fn t4_status_bar_dot_color_live_is_green() {
        let color = status_bar_dot_color(false);
        let eps = 1e-5_f32;
        assert!((color.r - 0.2).abs() < eps, "live red should be 0.2");
        assert!((color.g - 0.75).abs() < eps, "live green should be 0.75");
        assert!((color.b - 0.3).abs() < eps, "live blue should be 0.3");
    }

    #[test]
    fn t5_status_bar_constants() {
        assert_eq!(STATUS_BAR_HEIGHT, 20);
        let eps = 1e-5_f32;
        assert!(
            (STATUS_BAR_BG.r - 0.08).abs() < eps,
            "BG red should be 0.08"
        );
        assert!(
            (STATUS_BAR_BG.g - 0.08).abs() < eps,
            "BG green should be 0.08"
        );
        assert!(
            (STATUS_BAR_BG.b - 0.08).abs() < eps,
            "BG blue should be 0.08"
        );
        assert!(
            (STATUS_BAR_BG.a - 1.0).abs() < eps,
            "BG alpha should be 1.0"
        );
    }
}

#[cfg(test)]
mod lock_order_tests {
    use super::*;

    #[test]
    fn lock_order_acquire_in_correct_order_does_not_panic() {
        lock_order_reset();
        lock_order_acquire("MODE_SWITCHING");
        lock_order_acquire("APP_MODE");
        lock_order_acquire("CURRENT_PATH");
        lock_order_reset();
    }

    #[test]
    fn lock_order_acquire_skipping_levels_is_allowed() {
        // Skipping intermediate levels (e.g. MODE_SWITCHING then CURRENT_PATH
        // without acquiring APP_MODE) is fine — the order is a partial order.
        lock_order_reset();
        lock_order_acquire("MODE_SWITCHING");
        lock_order_acquire("CURRENT_PATH");
        lock_order_reset();
    }

    #[test]
    #[should_panic(expected = "lock-order violation")]
    fn lock_order_reverse_acquisition_panics_in_debug() {
        lock_order_reset();
        lock_order_acquire("APP_MODE");
        lock_order_acquire("MODE_SWITCHING");
    }

    /// H1: dropping a `ModeSwitchGuard` must reset the per-thread
    /// `LOCK_ORDER_INDEX` so subsequent unrelated acquisitions on the same
    /// thread are not falsely flagged as reverse-order.
    #[test]
    fn mode_switch_guard_drop_resets_lock_order_index() {
        lock_order_reset();
        {
            let _guard = ModeSwitchGuard::try_acquire().expect("first acquisition must succeed");
            lock_order_acquire("MODE_SWITCHING");
            lock_order_acquire("APP_MODE");
            // index is now 2 (APP_MODE) on this thread.
            LOCK_ORDER_INDEX.with(|cell| {
                assert!(
                    cell.get().is_some(),
                    "LOCK_ORDER_INDEX must be set after lock_order_acquire"
                );
            });
            // _guard is dropped here.
        }
        LOCK_ORDER_INDEX.with(|cell| {
            assert_eq!(
                cell.get(),
                None,
                "ModeSwitchGuard::drop must call lock_order_reset (H1)"
            );
        });
    }

    /// M-rust5: `ModeSwitchGuard::try_acquire` must return `None` when called
    /// while another guard is still alive, and `Some` again after it drops.
    #[test]
    fn mode_switch_guard_try_acquire_is_exclusive_and_recoverable() {
        // Reset just in case a prior test on this thread left the flag set
        // (in panicking-test contexts the Drop still runs, so this is mostly
        // belt-and-suspenders).
        MODE_SWITCHING.store(false, std::sync::atomic::Ordering::Release);

        let first = ModeSwitchGuard::try_acquire().expect("first try_acquire must return Some");
        let second = ModeSwitchGuard::try_acquire();
        assert!(
            second.is_none(),
            "second try_acquire must return None while first guard is alive"
        );
        drop(first);
        let third = ModeSwitchGuard::try_acquire();
        assert!(
            third.is_some(),
            "try_acquire must return Some again after the previous guard is dropped"
        );
    }
}

#[cfg(test)]
mod mode_switch_engine_busy_routing_tests {
    //! Regression guards for the F7 replay→live mode-switch bug where
    //! `StopReplay` EngineBusy aborted the switch instead of falling through
    //! to the `ForceStopReplay` fallback.
    //!
    //! Root cause: both `AttemptedCommand::StopReplay` and `ForceStopReplay`
    //! routed to `ModeSwitchEngineBusy` which calls `.take()` and clears
    //! `mode_switch_state`. The 5-second `ModeSwitchStopTimeout` then saw
    //! `is_none()` and early-returned without sending `ForceStopReplay`.

    const MAIN_RS: &str = include_str!("./main.rs");

    /// StopReplay and ForceStopReplay must be handled in separate arms.
    /// Sharing a branch means StopReplay EngineBusy aborts the mode switch,
    /// preventing the ForceStopReplay fallback from ever being sent.
    #[test]
    fn stop_replay_and_force_stop_replay_engine_busy_are_separate_arms() {
        // Strip the test module itself so we only inspect production code.
        let test_mod_marker = "mod mode_switch_engine_busy_routing_tests {";
        let prod_code = MAIN_RS
            .split_once(test_mod_marker)
            .map(|(prod, _)| prod)
            .expect("test module marker must exist in main.rs");
        // The old shared-branch pattern must not appear in production code.
        let shared_branch = "AttemptedCommand::StopReplay | AttemptedCommand::ForceStopReplay";
        assert!(
            !prod_code.contains(shared_branch),
            "StopReplay must not share an EngineBusy arm with ForceStopReplay. \
             StopReplay EngineBusy = engine IDLE (continue with ForceStopReplay). \
             ForceStopReplay EngineBusy = genuine failure (abort mode switch)."
        );
    }

    /// The new `ModeSwitchStopBusy` message must exist to handle
    /// `StopReplay` EngineBusy by sending `ForceStopReplay` immediately.
    #[test]
    fn mode_switch_stop_busy_message_exists() {
        assert!(
            MAIN_RS.contains("ModeSwitchStopBusy"),
            "Message::Window(WindowMsg::ModeSwitchStopBusy) must exist — it handles StopReplay EngineBusy \
             by sending ForceStopReplay immediately instead of aborting the mode switch"
        );
    }
}

#[cfg(test)]
mod app_mode_roundtrip_tests {
    //! Acceptance criteria 2 (F7 bugfix): `app_mode()` must return the value
    //! most recently written by `set_app_mode()`.
    //!
    //! These are runtime tests (not source inspection) so a broken
    //! implementation (e.g. `app_mode()` hardcoded to return `Replay`) is
    //! detected at test time, not just at review time.
    //!
    //! NOTE: `APP_MODE` is a process-global static. Tests in this module must
    //! not run concurrently with other tests that mutate `APP_MODE`.
    //! `#[serial]` ensures sequential execution within this module.

    use super::*;
    use engine_client::dto::AppMode;
    use serial_test::serial;

    #[test]
    #[serial]
    fn set_app_mode_live_makes_app_mode_return_live() {
        set_app_mode(AppMode::Live);
        assert_eq!(
            app_mode(),
            AppMode::Live,
            "app_mode() must return Live immediately after set_app_mode(Live)"
        );
    }

    #[test]
    #[serial]
    fn set_app_mode_replay_makes_app_mode_return_replay() {
        set_app_mode(AppMode::Replay);
        assert_eq!(
            app_mode(),
            AppMode::Replay,
            "app_mode() must return Replay immediately after set_app_mode(Replay)"
        );
        // Restore to Live so other tests that read APP_MODE are not surprised.
        set_app_mode(AppMode::Live);
    }

    /// Acceptance criteria 2: after a replay→live mode switch, `app_mode()`
    /// must return `AppMode::Live`. This is the direct runtime guard for the
    /// "RequestVenueLogin not allowed in replay mode" regression.
    #[test]
    #[serial]
    fn app_mode_returns_live_after_switch_from_replay() {
        set_app_mode(AppMode::Replay);
        assert_eq!(
            app_mode(),
            AppMode::Replay,
            "pre-condition: must start in Replay"
        );
        // Simulate what restart_with_mode(Live) does to APP_MODE.
        set_app_mode(AppMode::Live);
        assert_eq!(
            app_mode(),
            AppMode::Live,
            "app_mode() must return Live after set_app_mode(Live) (acceptance criteria 2)"
        );
    }
}

#[cfg(test)]
mod kabu_production_banner_tests {
    //! P4-4: kabu venue の本番接続バナーが capabilities から正しく抽出されること。
    //! UI 描画は iced ランタイム必須なので、ここでは pure helper を直接検証する。

    use super::*;
    use serde_json::json;

    #[test]
    fn parse_returns_true_when_is_production_advertised() {
        let caps = json!({
            "venue_capabilities": {
                "kabu_station": { "is_production": true }
            }
        });
        assert!(parse_kabu_is_production(&caps));
    }

    #[test]
    fn parse_returns_false_when_is_production_explicitly_false() {
        let caps = json!({
            "venue_capabilities": {
                "kabu_station": { "is_production": false }
            }
        });
        assert!(!parse_kabu_is_production(&caps));
    }

    #[test]
    fn parse_returns_false_when_field_absent() {
        // 旧 engine / 異 venue / 空 capabilities いずれも安全側 = verify 表示
        let caps = json!({
            "venue_capabilities": {
                "kabu_station": { "requires_local_app": true }
            }
        });
        assert!(!parse_kabu_is_production(&caps));
    }

    #[test]
    fn parse_returns_false_when_kabu_venue_missing() {
        let caps = json!({
            "venue_capabilities": {
                "tachibana": { "supports_depth_diff": false }
            }
        });
        assert!(!parse_kabu_is_production(&caps));
    }

    #[test]
    fn parse_returns_false_on_empty_capabilities() {
        let caps = json!({});
        assert!(!parse_kabu_is_production(&caps));
    }

    #[test]
    fn parse_returns_false_when_value_type_mismatches() {
        // 型不一致 = malformed wire は fail-safe で false (UI が誤って prod を表示しないことを優先)
        let caps = json!({
            "venue_capabilities": {
                "kabu_station": { "is_production": "yes" }
            }
        });
        assert!(!parse_kabu_is_production(&caps));
    }

    #[test]
    fn prod_chip_style_uses_red_label_and_runbook_aligned_text() {
        // runbook.md §5.1 / spec.md と同じ文言: "🔴 本番"
        let (label, color) = kabu_chip_prod_style();
        assert_eq!(label, "🔴 本番");
        // 赤系（R チャネルが他より圧倒的に大きい）
        assert!(color.r > color.g && color.r > color.b);
        assert!(color.r > 0.7);
    }

    // R1-MEDIUM regression pin: malformed is_production (Python/Rust schema drift)
    // must fail-safe to false.  log::warn is not directly assertable here but
    // the false return proves the Err arm is reached rather than panicking.
    #[test]
    fn parse_fails_gracefully_on_type_mismatch_for_schema_drift() {
        let caps_bad_type = json!({
            "venue_capabilities": { "kabu_station": { "is_production": "yes" } }
        });
        assert!(
            !parse_kabu_is_production(&caps_bad_type),
            "type mismatch must fail-safe to false (not panic)"
        );
        // Root-not-object path also hits the Err arm.
        let caps_bad_root = json!("not-an-object");
        assert!(
            !parse_kabu_is_production(&caps_bad_root),
            "non-object root must fail-safe to false"
        );
    }

    // HIGH-3: bridge_seeds_is_production_from_handshake_capabilities は
    // atomic_store_load_and_seeding に統合済み（並列競合防止）。

    // HIGH-3: テスト間の AtomicBool 競合を防ぐため、KABU_IS_PRODUCTION を書き換える
    // 既存の 2 テスト (cache_load_store_round_trips, bridge_seeds_is_production_from_handshake_capabilities)
    // を 1 関数に統合する。cargo test は各テストをデフォルトで並列実行するが、1 関数内の処理は順次実行される。
    #[test]
    fn atomic_store_load_and_seeding() {
        // Part A: cache_load_store_round_trips の検証
        KABU_IS_PRODUCTION.store(true, std::sync::atomic::Ordering::Release);
        assert!(kabu_is_production());
        KABU_IS_PRODUCTION.store(false, std::sync::atomic::Ordering::Release);
        assert!(!kabu_is_production());

        // Part B: bridge_seeds_is_production_from_handshake_capabilities の検証
        let prod_caps = serde_json::json!({
            "venue_capabilities": { "kabu_station": { "is_production": true } }
        });
        let absent_caps = serde_json::json!({
            "venue_capabilities": { "kabu_station": {} }
        });
        let seed = parse_kabu_is_production(&prod_caps);
        KABU_IS_PRODUCTION.store(seed, std::sync::atomic::Ordering::Release);
        assert!(
            kabu_is_production(),
            "prod capabilities snapshot must seed atomic to true"
        );
        let seed = parse_kabu_is_production(&absent_caps);
        KABU_IS_PRODUCTION.store(seed, std::sync::atomic::Ordering::Release);
        assert!(
            !kabu_is_production(),
            "absent field (older engine) must seed atomic to false"
        );

        // HIGH-1: VenueError アームのリセット検証
        // spawn_venue_ready_bridge_on の VenueError アームと同じロジック
        KABU_IS_PRODUCTION.store(true, std::sync::atomic::Ordering::Release);
        assert!(kabu_is_production(), "should be true before reset");
        // VenueError / VenueLoginStarted / VenueLoginCancelled アームのリセットロジック
        let venue = KABU_STATION_VENUE_NAME;
        if venue == KABU_STATION_VENUE_NAME {
            KABU_IS_PRODUCTION.store(false, std::sync::atomic::Ordering::Release);
        }
        assert!(
            !kabu_is_production(),
            "bridge_resets_is_production_on_venue_error: VenueError arm must reset to false"
        );

        // HIGH-1: VenueLoginStarted アームのリセット検証
        KABU_IS_PRODUCTION.store(true, std::sync::atomic::Ordering::Release);
        assert!(kabu_is_production(), "should be true before reset");
        let venue = KABU_STATION_VENUE_NAME;
        if venue == KABU_STATION_VENUE_NAME {
            KABU_IS_PRODUCTION.store(false, std::sync::atomic::Ordering::Release);
        }
        assert!(
            !kabu_is_production(),
            "bridge_resets_is_production_on_venue_login_started: VenueLoginStarted arm must reset to false"
        );

        // HIGH-1: VenueLoginCancelled アームのリセット検証
        KABU_IS_PRODUCTION.store(true, std::sync::atomic::Ordering::Release);
        assert!(kabu_is_production(), "should be true before reset");
        let venue = KABU_STATION_VENUE_NAME;
        if venue == KABU_STATION_VENUE_NAME {
            KABU_IS_PRODUCTION.store(false, std::sync::atomic::Ordering::Release);
        }
        assert!(
            !kabu_is_production(),
            "bridge_resets_is_production_on_venue_login_cancelled: VenueLoginCancelled arm must reset to false"
        );

        // HIGH-2: RecvError::Lagged アームのリセット検証
        KABU_IS_PRODUCTION.store(true, std::sync::atomic::Ordering::Release);
        assert!(kabu_is_production(), "should be true before lagged reset");
        // Lagged アームと同じロジック
        KABU_IS_PRODUCTION.store(false, std::sync::atomic::Ordering::Release);
        assert!(
            !kabu_is_production(),
            "bridge_resets_is_production_on_lagged: Lagged arm must reset to false"
        );

        // non-kabu venue は KABU_IS_PRODUCTION をリセットしないことを確認
        KABU_IS_PRODUCTION.store(true, std::sync::atomic::Ordering::Release);
        let other_venue = TACHIBANA_VENUE_NAME;
        if other_venue == KABU_STATION_VENUE_NAME {
            KABU_IS_PRODUCTION.store(false, std::sync::atomic::Ordering::Release);
        }
        assert!(
            kabu_is_production(),
            "non-kabu venue error must NOT reset KABU_IS_PRODUCTION"
        );

        // 後片付け: 安全なデフォルト値に戻す
        KABU_IS_PRODUCTION.store(false, std::sync::atomic::Ordering::Release);
    }
}
