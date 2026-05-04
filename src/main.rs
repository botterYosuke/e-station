#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod audio;
mod chart;
mod cli;
mod connector;
mod layout;
mod logger;
mod mask_secrets;
// `menu` exposes `tools_actions_for_state`, `MenuEntry`, and `Action` used by
// both the Linux widget menu bar and the cross-platform `native_menu::attach`
// Tools enable/disable computation (H5). Cross-platform.
mod menu;
// H1 (F8 R1): `menu_bar_state` houses the pure `update()` state machine for the
// Linux widget menu bar. The module itself is platform-independent — it has no
// iced widget or GTK dependencies — so we expose it on every OS so that the
// state-transition contract tests in `tests/widget_menu_bar_state.rs` can
// compile and inspect the source on Windows / macOS as well as Linux. Only the
// rendering layer (`widget_menu_bar`) is Linux-gated.
mod menu_bar_state;
mod modal;
mod native_menu;
mod notify;
mod screen;
mod style;
mod venue_state;
mod version;
mod wandb_auth;
mod wandb_submit_proc;
mod widget;
#[cfg(target_os = "linux")]
mod widget_menu_bar;
mod window;

use data::config::theme::default_theme;
use data::{layout::WindowSpec, sidebar};
use layout::{LayoutId, configuration};
use modal::{
    LayoutManager, ThemeEditor,
    audio::AudioStream,
    network_manager::{self, NetworkManager},
};
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

/// Startup mode (`live` or `replay`) captured from `--mode` before any runtime
/// is created.  Changed from OnceLock to Mutex<Option<_>> so that
/// `set_app_mode()` can overwrite the value during mode-switch restarts (F7/T1).
/// Lock-acquisition order: MODE_SWITCHING → SUBMIT_IN_FLIGHT → APP_MODE → CURRENT_PATH (統一決定 58).
static APP_MODE: std::sync::Mutex<Option<engine_client::dto::AppMode>> =
    std::sync::Mutex::new(None);

/// F7/T2: set to `true` while a mode-switch restart is in progress.
/// `ModeSwitchGuard` RAII wrapper ensures the flag is reset even on panic.
/// (P7 統一決定 33 / 受け入れ基準 9, 11)
pub static MODE_SWITCHING: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

/// P9 W&B run buffer: set to `true` while an active submit is in progress.
/// SwitchMode is rejected when this is true (5-axis matrix axis-5 / 統一決定 61, 68).
/// P9 will set/clear this around submission; F7 only reads it.
/// Lock-acquisition order: MODE_SWITCHING → SUBMIT_IN_FLIGHT → APP_MODE → CURRENT_PATH
pub static SUBMIT_IN_FLIGHT: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

/// Phase 3-A C2: handle to the in-flight `submit_run.py` child process.
///
/// `submit_wandb_run` stores the spawned `Child` here so that the W&B Cancel
/// path (`modal::wandb_submit::Action::Cancel`) can reach in and kill the
/// subprocess. On Windows `Child::kill()` is effectively `TerminateProcess`
/// (immediate kill — no SIGTERM/grace-period semantics). On Unix tokio's
/// `Child::kill` also issues SIGKILL, so the 5 second grace period documented
/// in 統一決定 46 is best-effort and only meaningful if the kernel itself
/// takes time to reap the process.
pub static SUBMIT_CHILD: tokio::sync::Mutex<Option<tokio::process::Child>> =
    tokio::sync::Mutex::const_new(None);

/// F9d: W&B 送信エラーの種類。`examples/wandb/submit_run.py` の exit code に対応する。
#[derive(Debug, Clone)]
pub enum WandbSubmitError {
    /// exit code 2: W&B 未認証
    AuthFailed,
    /// exit code 3: Rate limit (429)
    RateLimit,
    /// exit code 4: ネットワークエラー
    Network,
    /// exit code 5: サーバーエラー (5xx)
    ServerError,
    /// exit code 6: 部分的成功 / meta.json status != "completed"
    Partial,
    /// 予期しない exit code または spawn 失敗
    ProcessFailed(String),
}

/// `submit_run.py` の exit code を `WandbSubmitError` に変換する。
/// exit code 0 は成功なので呼ばれない。
fn exit_code_to_error(code: i32) -> WandbSubmitError {
    match code {
        2 => WandbSubmitError::AuthFailed,
        3 => WandbSubmitError::RateLimit,
        4 => WandbSubmitError::Network,
        5 => WandbSubmitError::ServerError,
        6 => WandbSubmitError::Partial,
        other => WandbSubmitError::ProcessFailed(format!("unexpected exit code: {other}")),
    }
}

/// Returns the current app mode.  Falls back to `Live` when the static has not
/// yet been initialised (unreachable in normal operation).
fn app_mode() -> engine_client::dto::AppMode {
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

/// F7: check if tachibana_orders.jsonl has any in-flight (submitted/partial) orders.
/// Reads the WAL tail-first (reverse scan), same algorithm as python/engine/wal_in_flight.py.
/// Returns true if any order has latest status "submitted" or "partial".
///
/// Uses `engine_client::process::engine_cache_dir()` — the same path Rust sends to Python
/// via the stdin payload — so both sides always agree on the WAL location.
fn has_wal_in_flight_orders() -> bool {
    let wal_path = engine_client::process::engine_cache_dir().join("tachibana_orders.jsonl");
    let Ok(content) = std::fs::read_to_string(&wal_path) else {
        return false;
    };
    let mut latest_status: std::collections::HashMap<String, String> =
        std::collections::HashMap::new();
    for line in content.lines().rev() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let Ok(record) = serde_json::from_str::<serde_json::Value>(line) else {
            continue;
        };
        let order_id = record
            .get("order_id")
            .or_else(|| record.get("client_order_id"))
            .and_then(|v| v.as_str())
            .map(|s| s.to_string());
        let status = record
            .get("status")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string());
        if let (Some(oid), Some(st)) = (order_id, status) {
            latest_status.entry(oid).or_insert(st);
        }
    }
    latest_status
        .values()
        .any(|st| st == "submitted" || st == "partial")
}

/// F9d streaming events emitted by [`submit_wandb_run_stream`].
///
/// `Line` carries one raw stdout/stderr line from the subprocess (already
/// UTF-8). The modal applies `mask_secrets()` before storing it. `Final`
/// carries the terminal result (success URL or `WandbSubmitError`).
#[derive(Debug, Clone)]
pub enum WandbStreamEvent {
    Line(String),
    Final(Result<String, WandbSubmitError>),
}

/// F9d: `examples/wandb/submit_run.py` を subprocess で起動し、結果を返す。
///
/// 起動コマンド: `uv run --with wandb python examples/wandb/submit_run.py --run-buffer <path>`
/// exit code 0: stdout の `URL: <url>` 行を返す。
/// 非ゼロ exit code: `WandbSubmitError` に変換して返す。
///
/// 実行中の stdout / stderr 行は `log_tx` 経由でリアルタイムに流す。Cancel 経路は
/// `SUBMIT_CHILD` を介して `kill()` を呼ぶ。`try_wait()` ポーリングで Mutex を
/// 長期保持しないため、kill が常に確実に届く。
async fn submit_wandb_run(
    run_buffer_dir: std::path::PathBuf,
    script: std::path::PathBuf,
    project: String,
    run_name: String,
    tags: String,
    notes: String,
    log_tx: tokio::sync::mpsc::UnboundedSender<String>,
) -> Result<String, WandbSubmitError> {
    use tokio::io::{AsyncBufReadExt, BufReader};
    use tokio::process::Command;

    // H8: 非UTF-8 path で空文字列に silent fallback せず、明示的にエラーを返す。
    let script_str = script.to_str().ok_or_else(|| {
        WandbSubmitError::ProcessFailed(format!(
            "path contains non-UTF-8 characters: {}",
            script.display()
        ))
    })?;
    let run_buffer_str = run_buffer_dir.to_str().ok_or_else(|| {
        WandbSubmitError::ProcessFailed(format!(
            "path contains non-UTF-8 characters: {}",
            run_buffer_dir.display()
        ))
    })?;

    let mut cmd = Command::new("uv");
    cmd.args([
        "run",
        "--with",
        "wandb",
        "python",
        script_str,
        "--run-buffer",
        run_buffer_str,
    ]);
    if !project.is_empty() {
        cmd.args(["--project", &project]);
    }
    if !run_name.is_empty() {
        cmd.args(["--run-name", &run_name]);
    }
    if !tags.is_empty() {
        cmd.args(["--tags", &tags]);
    }
    // M6: notes を `--notes` で渡す。
    if !notes.is_empty() {
        cmd.args(["--notes", &notes]);
    }
    // Phase 3-A C2: spawn explicitly so the Child handle can be stored in
    // SUBMIT_CHILD for the Cancel path.
    cmd.stdout(std::process::Stdio::piped());
    cmd.stderr(std::process::Stdio::piped());
    let mut child = cmd
        .spawn()
        .map_err(|e| WandbSubmitError::ProcessFailed(format!("spawn failed: {e}")))?;

    // Stream stdout / stderr line-by-line via dedicated reader tasks.
    // Take the pipes BEFORE moving the Child into SUBMIT_CHILD.
    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    {
        let mut slot = SUBMIT_CHILD.lock().await;
        *slot = Some(child);
    }

    let log_tx_out = log_tx.clone();
    let stdout_handle = tokio::spawn(async move {
        let mut url: Option<String> = None;
        if let Some(stdout) = stdout {
            let mut lines = BufReader::new(stdout).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                if let Some(rest) = line.strip_prefix("URL: ") {
                    url = Some(rest.to_string());
                }
                let _ = log_tx_out.send(line);
            }
        }
        url
    });
    let log_tx_err = log_tx.clone();
    let stderr_handle = tokio::spawn(async move {
        if let Some(stderr) = stderr {
            let mut lines = BufReader::new(stderr).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                let _ = log_tx_err.send(line);
            }
        }
    });
    drop(log_tx);

    // Poll try_wait() in a tight loop so the SUBMIT_CHILD lock is held only
    // for the duration of one syscall. The Cancel path needs the lock to
    // call `kill()` and would be starved out if we held it across a blocking
    // `wait()`.
    let exit_status = loop {
        tokio::time::sleep(std::time::Duration::from_millis(150)).await;
        let mut slot = SUBMIT_CHILD.lock().await;
        let Some(c) = slot.as_mut() else {
            // Cancel reached the slot first and is killing the process.
            break None;
        };
        match c.try_wait() {
            Ok(Some(status)) => {
                let _ = slot.take();
                break Some(status);
            }
            Ok(None) => continue,
            Err(e) => {
                let _ = slot.take();
                return Err(WandbSubmitError::ProcessFailed(format!("wait failed: {e}")));
            }
        }
    };

    // Drain readers — they exit once the subprocess closes its pipes.
    let url = stdout_handle.await.ok().flatten();
    let _ = stderr_handle.await;

    match exit_status {
        Some(status) if status.success() => Ok(url.unwrap_or_default()),
        Some(status) => Err(exit_code_to_error(status.code().unwrap_or(-1))),
        None => Err(WandbSubmitError::ProcessFailed(
            "cancelled before completion".to_string(),
        )),
    }
}

/// Wraps [`submit_wandb_run`] as a stream of [`WandbStreamEvent`] values so
/// `iced::Task::run` can dispatch live `LogLine` updates AND a final
/// completion event in one Task.
fn submit_wandb_run_stream(
    run_buffer_dir: std::path::PathBuf,
    script: std::path::PathBuf,
    project: String,
    run_name: String,
    tags: String,
    notes: String,
) -> impl iced::futures::Stream<Item = WandbStreamEvent> + Send + 'static {
    let (event_tx, event_rx) = tokio::sync::mpsc::unbounded_channel::<WandbStreamEvent>();
    let (log_tx, mut log_rx) = tokio::sync::mpsc::unbounded_channel::<String>();

    let event_tx_for_lines = event_tx.clone();
    tokio::spawn(async move {
        while let Some(line) = log_rx.recv().await {
            let _ = event_tx_for_lines.send(WandbStreamEvent::Line(line));
        }
    });

    tokio::spawn(async move {
        let result = submit_wandb_run(
            run_buffer_dir,
            script,
            project,
            run_name,
            tags,
            notes,
            log_tx,
        )
        .await;
        let _ = event_tx.send(WandbStreamEvent::Final(result));
    });

    iced::futures::stream::unfold(event_rx, |mut rx| async move {
        rx.recv().await.map(|item| (item, rx))
    })
}

/// `wandb login --relogin` を stdin pipe 経由で API キーを渡して実行する。
/// argv に API キーを渡さない（プロセスリスト・シェル履歴への漏洩防止）。
async fn wandb_login(api_key: String) -> Result<(), String> {
    use tokio::io::AsyncWriteExt as _;
    use tokio::process::Command;

    let mut child = Command::new("uv")
        .args(["run", "--with", "wandb", "wandb", "login", "--relogin"])
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .map_err(|e| format!("spawn failed: {e}"))?;

    // M8: stdin write の失敗を握り潰さず Result で伝播する。
    // 旧コードは `let _ = stdin.write_all(...).await` で pipe broken 等の
    // OS-level I/O 失敗を黙殺していたため、原因不明のログイン失敗の温床だった。
    let mut stdin = child
        .stdin
        .take()
        .ok_or_else(|| "stdin not piped".to_string())?;
    stdin
        .write_all(api_key.as_bytes())
        .await
        .map_err(|e| format!("stdin write failed: {e}"))?;
    stdin
        .write_all(b"\n")
        .await
        .map_err(|e| format!("stdin write failed: {e}"))?;
    drop(stdin);

    let output = child
        .wait_with_output()
        .await
        .map_err(|e| format!("wait failed: {e}"))?;

    if output.status.success() {
        Ok(())
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr).to_string();
        Err(if stderr.is_empty() {
            format!("exit code {:?}", output.status.code())
        } else {
            stderr
        })
    }
}

/// `wandb logout` を実行して netrc エントリを削除する。
async fn wandb_logout() -> Result<(), String> {
    use tokio::process::Command;

    let output = Command::new("uv")
        .args(["run", "--with", "wandb", "wandb", "logout"])
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .output()
        .await
        .map_err(|e| format!("spawn failed: {e}"))?;

    if output.status.success() {
        Ok(())
    } else {
        let stderr = String::from_utf8_lossy(&output.stderr).to_string();
        Err(if stderr.is_empty() {
            format!("exit code {:?}", output.status.code())
        } else {
            stderr
        })
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
                Ok(EngineEvent::VenueReady { venue, .. }) => {
                    cache.lock().await.insert(venue);
                }
                // Invalidate the readiness cache aggressively when the
                // venue lifecycle leaves `Ready`. Without these arms a
                // stale `Ready` from a previous session could survive
                // a re-login dialog open / cancel pair and a later
                // engine reconnect would resurrect it via
                // `Message::EngineConnected`'s synthesized
                // `VenueEvent::Ready`. Reviewer 2026-04-26 R4
                // (MEDIUM-3).
                Ok(EngineEvent::VenueError { venue, .. }) => {
                    cache.lock().await.remove(&venue);
                }
                Ok(EngineEvent::VenueLoginStarted { venue, .. }) => {
                    cache.lock().await.remove(&venue);
                }
                Ok(EngineEvent::VenueLoginCancelled { venue, .. }) => {
                    cache.lock().await.remove(&venue);
                }
                Ok(_) => {}
                Err(RecvError::Lagged(n)) => {
                    log::warn!(
                        "venue_ready_bridge lagged, dropped {n} events — UI may briefly mis-bootstrap"
                    );
                }
                Err(RecvError::Closed) => break,
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
        match rt.block_on(engine_client::EngineConnection::connect_with_mode(
            &url_str, &token, app_mode,
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
                let reconnect_url = url_str.clone();
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
                            match engine_client::EngineConnection::connect_with_mode(
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
    /// Phase 8.1c: Replay 起動フォーム modal。`File > Replay を開始...` で Some に、
    /// Submit / Cancel で None に戻る。
    replay_form_modal: Option<modal::replay_form::ReplayFormModal>,
    /// N4.4: non-None while a `strategy_load_failed` error banner should be shown.
    /// Cleared by `Message::DismissStrategyLoadError`.
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
    /// F7: mode-switch target while waiting for ReplayStopped or user confirm.
    pending_mode_switch: Option<engine_client::dto::AppMode>,
    /// F7: holds the MODE_SWITCHING guard alive until restart_with_mode() completes.
    /// Dropped automatically when `*self = new_state` runs in restart().
    _mode_switch_guard: Option<ModeSwitchGuard>,
    /// F8: Linux widget menu bar open/close state (DoD-12).
    /// Only compiled on Linux; Win/macOS use the muda OS-native menu.
    #[cfg(target_os = "linux")]
    menu_bar: crate::menu_bar_state::State,
    /// P9: W&B authentication state. Populated when Python check_auth subprocess responds.
    /// Drives Tools submenu enabled/disabled state on all platforms.
    wandb_auth: wandb_auth::WandbAuthState,
    /// P9: local run-buffer index. Populated by run-buffer directory scan.
    /// Drives "Submit to W&B" enabled state (needs at least one completed run).
    run_buffer: wandb_auth::RunBufferIndex,
    /// F9c: W&B サインインモーダル。SignInWandb で Some に、Cancel / ログイン完了で None。
    wandb_signin_modal: Option<modal::wandb_signin::WandbSignInModal>,
    /// F9c: W&B 送信モーダル。SubmitToWandb で Some に、Cancel / 送信完了で None。
    wandb_submit_modal: Option<modal::wandb_submit::WandbSubmitModal>,
    /// F9e: W&B 送信履歴モーダル。OpenSubmissionLog で run-buffer/ をスキャンして開く。
    wandb_submission_log_modal: Option<modal::wandb_submission_log::WandbSubmissionLogModal>,
}

#[derive(Debug, Clone)]
enum Message {
    Sidebar(dashboard::sidebar::Message),
    MarketWsEvent(exchange::Event),
    /// Fired by the engine-status subscription when the Python engine starts or
    /// finishes a restart.  `true` = restarting, `false` = ready.
    EngineRestarting(bool),
    /// Fired by the engine-status subscription on every successful handshake.
    /// Replaces the former `static ENGINE_CONNECTION` global read from
    /// `update()` (T35-H7-NoStaticInUpdate).
    EngineConnected(Arc<engine_client::EngineConnection>),
    /// Fired when an engine event affecting the Tachibana venue
    /// lifecycle (`VenueLoginStarted` / `VenueLoginCancelled` /
    /// `VenueError` / `VenueReady`) arrives. Drives `tachibana_state`
    /// transitions. T35-U4-VenueReadyGate / T35-U2-Banner.
    TachibanaVenueEvent(VenueEvent),
    /// User asked to (re)open the Tachibana login dialog. Sourced
    /// from the inline "ログイン" button (`Trigger::Manual`) and from
    /// the auto-fire path inside `tickers_table` that runs when the
    /// user toggles Tachibana while the venue is still `Idle`
    /// (`Trigger::Auto`). The handler suppresses duplicates while
    /// `tachibana_state` is already `LoginInFlight`. T35-U1 / T35-U3.
    RequestTachibanaLogin(Trigger),
    /// User pressed the banner's "閉じる"-style button. Transitions
    /// `tachibana_state` back to `Idle` so the banner is hidden. The
    /// underlying error condition (e.g. `phone_auth_required`) is
    /// considered acknowledged; a fresh `VenueError` from the engine
    /// will re-show the banner. T35-U2-Banner.
    DismissTachibanaBanner,
    /// Result of the asynchronous `Command::RequestVenueLogin` IPC
    /// send. The handler does not transition the FSM (the engine's
    /// own `VenueLoginStarted` event is the authoritative trigger);
    /// it only logs success and surfaces a toast on send failure so
    /// the user knows their click did not silently disappear.
    /// Review-fixes 2026-04-26 round 1.
    TachibanaLoginIpcResult(Result<(), String>),
    Dashboard {
        /// If `None`, the active layout is used for the event.
        layout_id: Option<uuid::Uuid>,
        event: dashboard::Message,
    },
    Tick(std::time::Instant),
    WindowEvent(window::Event),
    ExitRequested(HashMap<window::Id, WindowSpec>),
    RestartRequested(Option<HashMap<window::Id, WindowSpec>>),
    /// F4: After startup/restart, collect window specs then set last_saved_bytes baseline
    /// so edits made before the first explicit Save can be detected as dirty (BC-9 fix).
    SetDirtyBaseline(HashMap<window::Id, WindowSpec>),
    GoBack,
    DataFolderRequested,
    OpenUrlRequested(Cow<'static, str>),
    ThemeSelected(iced_core::Theme),
    ScaleFactorChanged(data::ScaleFactor),
    SetTimezone(data::UserTimezone),
    ToggleTradeFetch(bool),
    ApplyVolumeSizeUnit(exchange::SizeUnit),
    RemoveNotification(usize),
    ToggleDialogModal(Option<screen::ConfirmDialog<Message>>),
    ThemeEditor(modal::theme_editor::Message),
    NetworkManager(modal::network_manager::Message),
    Layouts(modal::layout_manager::Message),
    AudioStream(modal::audio::Message),
    /// EC 約定通知（Phase O2 T2.4）。`OrderFilled` / `OrderCanceled` /
    /// `OrderExpired` を受信したときに toast を surface する。
    OrderToast(Toast),
    /// `GetOrderList` IPC レスポンス — 全 OrderList ペインに配信する（Phase U1）。
    OrderListUpdated(Vec<engine_client::dto::OrderRecordWire>),
    /// `GetOrderList` IPC 送信完了（Ok）/ 送信失敗（Err）。
    OrderListSendCompleted(Result<(), String>),
    /// `GetBuyingPower` IPC 送信完了（Ok）/ 送信失敗（Err）。
    /// 注: 現状 BuyingPower の Err 経路は全て IpcError にルーティングされるため
    /// Err arm には到達しない。IpcError が request_id 照合で loading を解除する。
    BuyingPowerSendCompleted(Result<(), String>),
    /// `PositionsUpdated` IPC event — distribute to all Positions panes.
    PositionsUpdated {
        /// IPC routing field; not used by the UI (broadcasts to all Positions panes).
        #[allow(dead_code)]
        request_id: String,
        /// IPC routing field; not used by the UI.
        #[allow(dead_code)]
        venue: String,
        positions: Vec<engine_client::dto::PositionRecordWire>,
        ts_ms: i64,
    },
    /// `GetPositions` IPC 送信完了（Ok）/ 送信失敗（Err）。
    PositionsSendCompleted(Result<(), String>),
    /// EC 約定通知（OrderFilled）— positions auto-refresh を含む。
    OrderFilled {
        client_order_id: String,
        last_qty: String,
        last_price: String,
        leaves_qty: String,
    },
    /// Python エンジンが第二暗証番号を要求した。request_id は `SetSecondPassword` に使う。
    SecondPasswordRequired(String),
    /// 第二暗証番号 modal を閉じ、`ForgetSecondPassword` を IPC 送信する。
    DismissSecondPasswordModal,
    /// 第二暗証番号 modal 内部のメッセージ。
    SecondPasswordModalMsg(modal::second_password::Message),
    /// User confirmed the order dialog; forward `ConfirmSubmit` to the focused
    /// `OrderEntryPanel` and then process the resulting `SubmitOrder` IPC call.
    ConfirmOrderEntrySubmit,
    /// User confirmed the cancel-order dialog; send `CancelOrder` IPC.
    ConfirmCancelOrder {
        client_order_id: String,
        venue_order_id: String,
    },
    /// `OrderAccepted` IPC event — reset `submitting` on the matching
    /// `OrderEntryPanel` and surface a toast.
    OrderAccepted {
        client_order_id: String,
        venue_order_id: Option<String>,
    },
    /// `OrderRejected` IPC event — reset `submitting` on the matching
    /// `OrderEntryPanel` with the rejection reason, and surface a toast.
    OrderRejected {
        client_order_id: String,
        reason: String,
    },
    /// `BuyingPowerUpdated` IPC event — distribute to all BuyingPower panes.
    BuyingPowerUpdated {
        cash_available: i64,
        cash_shortfall: i64,
        credit_available: i64,
        ts_ms: i64,
    },
    /// N1.16: `EngineEvent::ReplayBuyingPower` — REPLAY 仮想ポートフォリオ更新。
    ReplayBuyingPower {
        cash: String,
        buying_power: String,
        equity: String,
        ts_event_ms: i64,
    },
    /// `EngineEvent::Error` — routed to the BuyingPower panel if `request_id`
    /// matches the pending buying-power request, otherwise silently ignored.
    IpcError {
        request_id: Option<String>,
        code: String,
        message: String,
    },
    /// N1.12: `EngineEvent::ExecutionMarker` — overlay dot on all Kline charts.
    ExecutionMarkerReceived {
        side: String,
        price: String,
        ts_event_ms: i64,
    },
    /// N1.12: `EngineEvent::StrategySignal` — overlay diamond on all Kline charts.
    StrategySignalReceived {
        signal_kind: engine_client::dto::SignalKind,
        price: Option<String>,
        ts_event_ms: i64,
        tag: Option<String>,
    },
    /// N4.3: result of the async OS file dialog for strategy `.py` file selection.
    /// `Some(path)` when the user picked a file; `None` when they cancelled.
    StrategyFilePicked(Option<std::path::PathBuf>),
    /// N4.4: user dismissed the `strategy_load_failed` error banner.
    DismissStrategyLoadError,
    /// Replay engine finished — auto-refresh the order list.
    ReplayFinished,
    /// Native OS menu bar: HWND / window handle received; attach muda menu.
    NativeMenuSetup(u64),
    /// Native OS menu bar: user selected a menu item.
    NativeMenuAction(native_menu::Action),
    /// F8: Linux widget menu bar message (toggle/pick/dismiss).
    /// On Win/macOS this variant is never constructed; muda drives NativeMenuAction instead.
    #[cfg(target_os = "linux")]
    MenuBar(crate::menu_bar_state::BarMessage),
    /// Native OS menu bar — Save As: user picked a destination path.
    NativeSaveAsPath(Option<std::path::PathBuf>),
    /// Native OS menu bar — Save As: window specs collected, ready to write.
    /// Carries the destination path directly so no shared slot is needed.
    NativeSaveAsWithSpecs {
        path: std::path::PathBuf,
        windows: HashMap<window::Id, WindowSpec>,
    },
    /// Native OS menu bar — Open: JSON content and the source path of the picked file.
    NativeOpenFileApply {
        json: String,
        path: std::path::PathBuf,
    },
    /// Native OS menu bar — Open: dialog cancelled (user closed the picker).
    NativeOpenFileCancelled,
    /// F4: two-step open — window specs collected, ready for dirty comparison.
    /// `NativeOpenFileApply` validates JSON then dispatches here so the dirty
    /// check can compare against bytes that include current window positions.
    NativeOpenFilePendingCheck {
        json: String,
        path: std::path::PathBuf,
        windows: HashMap<window::Id, WindowSpec>,
    },
    /// F6a: replay モードの `File > 開く...` で `.py` が選択されたときの結果。
    /// `Some(path)` なら `Command::LoadStrategyScenario` を engine に送信し、
    /// `None`（cancel）なら何もしない。
    NativeOpenStrategyPicked(Option<std::path::PathBuf>),
    /// F6a: `Event::StrategyScenarioLoaded` 受信時。`scenario` が `Some` なら
    /// `ReplayFormModal` を prefill し、`None`（SCENARIO 不在）なら strategy_file
    /// だけセットしてフォームは空のままにする。`current_path` は両ケースで更新。
    StrategyScenarioLoadedEvent {
        path: std::path::PathBuf,
        scenario: Option<serde_json::Value>,
    },
    /// F6a: `Event::StrategyScenarioLoadFailed` 受信時。エラートーストを出し、
    /// `current_path` はセットしない。
    StrategyScenarioLoadFailedEvent {
        path: std::path::PathBuf,
        reason: String,
    },
    /// Phase 8.1c: `File > Replay を開始...` メニューで表示。
    ShowReplayDialog,
    /// Phase 8.1c: Replay 起動フォーム modal の内部メッセージ。
    ReplayFormMsg(modal::replay_form::Message),
    /// F4: dirty-check confirm — user chose "破棄して終了" (discard and exit).
    DiscardAndExit,
    /// F4: dirty-check confirm — user chose "保存して終了" (save and exit).
    SaveAndExit,
    /// F4: dirty-check confirm — user chose "破棄して開く" (discard and open file).
    DiscardAndOpenFile,
    /// F4: dirty-check confirm — user chose "保存して開く" (save and open file).
    SaveAndOpenFile,
    /// F7: dirty-check confirm (live→replay) — user chose "破棄してモード切替".
    DiscardAndSwitchMode,
    /// F7: dirty-check confirm (live→replay) — user chose "保存してモード切替".
    SaveAndSwitchMode,
    /// F7: window specs collected for live→replay switch; proceed with dirty check.
    SwitchModeWithSpecs {
        target: engine_client::dto::AppMode,
        windows: HashMap<window::Id, WindowSpec>,
    },
    /// F7: `EngineEvent::ReplayStopped` received — proceed with restart_with_mode.
    ModeSwitchStopAcked,
    /// F7: 5-second StopReplay timeout — send ForceStopReplay fallback.
    ModeSwitchStopTimeout,
    /// F7: 2-second ForceStopReplay timeout — give up and show error.
    ModeSwitchForceStopTimeout,
    /// F7: StopReplay or ForceStopReplay send() returned Err — connection is broken; abort immediately.
    ModeSwitchSendFailed,
    /// F7: window specs collected after "保存してモード切替" confirm; save then restart.
    /// Routes through a dedicated message (not SwitchModeWithSpecs) to bypass the
    /// dirty check — the user already confirmed, and re-checking would loop.
    SwitchModeSaveComplete {
        target: engine_client::dto::AppMode,
        windows: HashMap<window::Id, WindowSpec>,
    },
    /// F7: engine returned `EngineBusy` for a `StopReplay` or `ForceStopReplay` command
    /// while a mode switch is pending. Aborts the switch and shows an error dialog.
    ModeSwitchEngineBusy(String),
    /// Internal: genuinely does nothing in update(). Used to discard async Task
    /// completion events where the result is irrelevant (fire-and-forget IPC sends).
    Noop,
    /// F5: Save As overwrite confirm — user confirmed overwriting the existing file.
    /// Carries the path directly to avoid the `pending_save_path` shared slot.
    ConfirmSaveAsOverwrite {
        path: std::path::PathBuf,
    },
    /// H-3: async file save completion — fired by `Task::perform` in the
    /// `NativeSaveAsWithSpecs` handler once the tokio async writes finish.
    /// `error_kind = None` means both writes succeeded; `Some(kind)` means the
    /// user-path write failed (saved-state.json write failure is best-effort and
    /// only emits a WARN log).
    NativeSaveComplete {
        user_path: std::path::PathBuf,
        json_bytes: Vec<u8>,
        /// `None` = success, `Some(kind)` = I/O error on the user-specified path.
        error_kind: Option<std::io::ErrorKind>,
        /// `true` if saved-state.json was written successfully (auto-restore slot).
        /// `false` means the named doc is saved but next startup will auto-restore old state.
        saved_state_ok: bool,
    },
    /// F9d: `examples/wandb/submit_run.py` subprocess の完了通知。
    /// `Ok(url)` = 成功 + W&B run URL。`Err(e)` = 送信失敗の理由。
    WandbSubmitResult(Result<String, WandbSubmitError>),
    /// F9d: `examples/wandb/check_auth.py` subprocess の完了通知。
    /// `WandbAuthState` を受け取ってメニュー状態を更新する。
    WandbAuthRefreshed(wandb_auth::WandbAuthState),
    /// F9c: W&B 送信モーダル内部メッセージ。
    WandbSubmitMsg(modal::wandb_submit::Message),
    /// F9c: W&B サインインモーダル内部メッセージ。
    WandbSignInMsg(modal::wandb_signin::Message),
    /// F9e: W&B 送信履歴モーダル内部メッセージ。
    WandbSubmissionLogMsg(modal::wandb_submission_log::Message),
    /// F9e: `list_run_buffer_entries` 完了通知。Vec を受け取って履歴モーダルを開く。
    WandbSubmissionLogScanned(Vec<wandb_auth::RunBufferEntry>),
    /// F9c: `wandb login --relogin` subprocess の完了通知。
    WandbLoginResult(Result<(), String>),
    /// F9c: ログアウト確認ダイアログで「ログアウト」が押された。
    WandbLogoutConfirmed,
    /// F9c: `wandb logout` subprocess の完了通知。
    WandbLogoutResult(Result<(), String>),
    /// F9e: バッファ削除確認後の実行。
    ClearRunBufferConfirmed,
    /// M3 (R1 Phase 3-C): `RunBufferIndex::scan_async` の完了通知。
    /// 結果でメニュー有効化判定 (`run_buffer`) を更新し、トースト表示も行う。
    RunBufferIndexScanned {
        index: wandb_auth::RunBufferIndex,
        /// `OpenSubmissionLog` 経由ならトーストを出す。`true` のとき `notifications` に push する。
        show_toast: bool,
    },
    /// M3 (R1 Phase 3-C): `tokio::fs::remove_dir_all` の完了通知。
    RunBufferCleared(Result<(), String>),
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
            yield Message::TachibanaVenueEvent(VenueEvent::EngineRehello);
            yield Message::EngineConnected(conn);
        }
        let initial_restart = { *restart_rx.borrow_and_update() };
        if initial_restart {
            yield Message::EngineRestarting(true);
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
                    yield Message::EngineRestarting(value);
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
                        yield Message::TachibanaVenueEvent(VenueEvent::EngineRehello);
                        yield Message::EngineConnected(conn);
                    }
                }
                event = event_fut => {
                    use tokio::sync::broadcast::error::RecvError;
                    match event {
                        Some(Ok(ev)) => {
                            if let Some(msg) = map_engine_event_to_tachibana(ev) {
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

/// Translate a low-level `EngineEvent` into a `Message::TachibanaVenueEvent`
/// when it concerns the Tachibana venue lifecycle, otherwise `None`.
/// Other venues are funnelled through their existing exchange-event
/// path and don't need state-machine treatment.
fn map_engine_event_to_tachibana(ev: engine_client::dto::EngineEvent) -> Option<Message> {
    use engine_client::dto::EngineEvent;
    match ev {
        EngineEvent::VenueReady { venue, .. } if venue == TACHIBANA_VENUE_NAME => {
            Some(Message::TachibanaVenueEvent(VenueEvent::Ready))
        }
        EngineEvent::VenueLoginStarted { venue, .. } if venue == TACHIBANA_VENUE_NAME => {
            Some(Message::TachibanaVenueEvent(VenueEvent::LoginStarted))
        }
        EngineEvent::VenueLoginCancelled { venue, .. } if venue == TACHIBANA_VENUE_NAME => {
            Some(Message::TachibanaVenueEvent(VenueEvent::LoginCancelled))
        }
        EngineEvent::VenueError {
            venue,
            code,
            message,
            ..
        } if venue == TACHIBANA_VENUE_NAME => {
            let class = engine_client::error::classify_venue_error(&code);
            Some(Message::TachibanaVenueEvent(VenueEvent::LoginError {
                class,
                message,
                market_closed: code == "market_closed",
            }))
        }
        // ── Phase O2: EC 約定通知 (T2.4) ────────────────────────────────────
        EngineEvent::OrderFilled {
            client_order_id,
            last_qty,
            last_price,
            leaves_qty,
            ..
        } => Some(Message::OrderFilled {
            client_order_id,
            last_qty,
            last_price,
            leaves_qty,
        }),
        EngineEvent::OrderCanceled {
            client_order_id, ..
        } => Some(Message::OrderToast(Toast::info(format!(
            "注文取消完了: {client_order_id}"
        )))),
        EngineEvent::OrderExpired {
            client_order_id, ..
        } => Some(Message::OrderToast(Toast::warn(format!(
            "注文失効: {client_order_id}"
        )))),
        // ── Phase U0: 第二暗証番号 / 注文受付・拒否 ────────────────────────
        EngineEvent::SecondPasswordRequired { request_id } => {
            Some(Message::SecondPasswordRequired(request_id))
        }
        EngineEvent::OrderAccepted {
            client_order_id,
            venue_order_id,
            ..
        } => Some(Message::OrderAccepted {
            client_order_id,
            venue_order_id,
        }),
        EngineEvent::OrderRejected {
            client_order_id,
            reason_code,
            reason_text,
            ..
        } => Some(Message::OrderRejected {
            client_order_id,
            reason: format!("[{reason_code}] {reason_text}"),
        }),
        EngineEvent::OrderListUpdated { orders, .. } => Some(Message::OrderListUpdated(orders)),
        EngineEvent::PositionsUpdated {
            request_id,
            venue,
            positions,
            ts_ms,
            ..
        } => Some(Message::PositionsUpdated {
            request_id,
            venue,
            positions,
            ts_ms,
        }),
        EngineEvent::BuyingPowerUpdated {
            cash_available,
            cash_shortfall,
            credit_available,
            ts_ms,
            .. // request_id / venue are IPC routing fields; UI broadcasts to all BuyingPower panes
        } => Some(Message::BuyingPowerUpdated {
            cash_available,
            cash_shortfall,
            credit_available,
            ts_ms,
        }),
        // N1.16: REPLAY 仮想ポートフォリオ更新イベント
        EngineEvent::ReplayBuyingPower {
            cash,
            buying_power,
            equity,
            ts_event_ms,
            ..
        } => Some(Message::ReplayBuyingPower {
            cash,
            buying_power,
            equity,
            ts_event_ms,
        }),
        EngineEvent::Error {
            request_id,
            code,
            message,
        } => Some(Message::IpcError {
            request_id,
            code,
            message,
        }),
        // N1.12: ExecutionMarker → chart overlay
        EngineEvent::ExecutionMarker {
            side,
            price,
            ts_event_ms,
            ..
        } => Some(Message::ExecutionMarkerReceived {
            side,
            price,
            ts_event_ms,
        }),
        // N1.12: StrategySignal → chart overlay
        EngineEvent::StrategySignal {
            signal_kind,
            price,
            ts_event_ms,
            tag,
            ..
        } => Some(Message::StrategySignalReceived {
            signal_kind,
            price,
            ts_event_ms,
            tag,
        }),
        // Replay engine stopped → auto-refresh order list (replay mode only).
        // In live mode, EngineStopped means engine restart, not replay completion.
        // unwrap_or(false) is intentional here: this is a runtime event handler called
        // well after APP_MODE is set; false (live) is the safe fallback so live-mode
        // engine restarts do not accidentally trigger ReplayFinished.
        EngineEvent::EngineStopped { .. } => {
            let is_replay = app_mode() == engine_client::dto::AppMode::Replay;
            if is_replay {
                Some(Message::ReplayFinished)
            } else {
                None
            }
        }
        // Phase 8: EngineBusy → GUI ユーザーへの warn toast
        // Python engine が state guard で Command を拒否したときに emit される。
        // F7: StopReplay / ForceStopReplay の EngineBusy のみ mode-switch 中断として扱う
        //     (replay→live 専用)。live→replay の EngineBusy 行は別経路で対処する。
        EngineEvent::EngineBusy {
            attempted_command,
            reason,
            ..
        } => {
            use engine_client::dto::AttemptedCommand;
            if matches!(
                attempted_command,
                AttemptedCommand::StopReplay | AttemptedCommand::ForceStopReplay
            ) {
                Some(Message::ModeSwitchEngineBusy(reason))
            } else {
                Some(Message::OrderToast(Toast::warn(format!(
                    "操作を受け付けられませんでした: {attempted_command} — {reason}"
                ))))
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
        EngineEvent::ReplayStopped { .. } => Some(Message::ModeSwitchStopAcked),
        // F6a: SCENARIO 抽出結果 → ReplayFormModal prefill
        EngineEvent::StrategyScenarioLoaded {
            path, scenario, ..
        } => Some(Message::StrategyScenarioLoadedEvent {
            path: std::path::PathBuf::from(path),
            scenario,
        }),
        // F6a: SCENARIO 抽出失敗 → エラートースト
        EngineEvent::StrategyScenarioLoadFailed { path, reason, .. } => {
            Some(Message::StrategyScenarioLoadFailedEvent {
                path: std::path::PathBuf::from(path),
                reason,
            })
        }
        // M-Rust2: 新しい `EngineEvent` バリアントを追加したときは、
        // ここに一致 arm を加えるか、`None`（このマップは Tachibana 関連
        // イベントだけを `Message` に変換する責務であり、新バリアントは
        // 別経路で処理される）が正しいことを確認すること。
        _ => None,
    }
}

fn status_bar_label(is_replay: bool) -> &'static str {
    if is_replay { "● REPLAY" } else { "● LIVE" }
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

// 'static: no input borrows; all content is &'static str labels and Copy Color constants.
// '_ cannot be used here because lifetime elision requires at least one input reference.
fn status_bar(is_replay: bool) -> Element<'static, Message> {
    container(
        text(status_bar_label(is_replay))
            .size(11)
            .color(status_bar_dot_color(is_replay)),
    )
    .width(iced::Length::Fill)
    .height(STATUS_BAR_HEIGHT)
    .align_y(Alignment::Center)
    .padding(padding::left(8))
    .style(|_theme| container::Style {
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
        let dialog_content =
            confirm_dialog_container(dialog.clone(), Message::ToggleDialogModal(None));
        main_dialog_modal(content, dialog_content, Message::ToggleDialogModal(None))
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
            second_password_modal: None,
            buying_power_request_id: None,
            order_list_request_id: None,
            positions_request_id: None,
            replay_strategy_file: None,
            replay_form_modal: None,
            strategy_load_error: None,
            last_saved_bytes: None,
            pending_exit_windows: None,
            pending_open_file: None,
            pending_mode_switch: None,
            _mode_switch_guard: None,
            #[cfg(target_os = "linux")]
            menu_bar: crate::menu_bar_state::State::default(),
            wandb_auth: wandb_auth::WandbAuthState::unauthenticated(),
            run_buffer: wandb_auth::RunBufferIndex::empty(),
            wandb_signin_modal: None,
            wandb_submit_modal: None,
            wandb_submission_log_modal: None,
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
        let setup_native_menu =
            iced::window::raw_id::<Message>(main_window_id).map(Message::NativeMenuSetup);
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
            window::collect_window_specs(baseline_ids, Message::SetDirtyBaseline)
        };

        // F9d: 起動時に check_auth.py を一度だけ非同期実行して wandb_auth を初期化する
        let init_wandb_auth = Task::perform(
            wandb_auth::refresh_wandb_auth(),
            Message::WandbAuthRefreshed,
        );
        // F9d: 起動時に run-buffer/ をスキャンして run_buffer を初期化する
        {
            let base = data::data_path(Some("run-buffer"));
            state.run_buffer = wandb_auth::RunBufferIndex::scan(&base);
        }

        (
            state,
            open_main_window
                .discard()
                .chain(setup_native_menu)
                .chain(load_layout)
                .chain(launch_sidebar.map(Message::Sidebar))
                .chain(set_baseline)
                .chain(init_wandb_auth),
        )
    }

    fn update(&mut self, message: Message) -> Task<Message> {
        match message {
            Message::EngineRestarting(restarting) => {
                self.engine_restarting = restarting;
                if restarting {
                    self.notifications.push(Toast::error(
                        "データエンジン再起動中 — チャートは復旧後に自動更新されます".to_string(),
                    ));
                    let main_window = self.main_window.id;
                    // [R03] Clear in-flight loading on disconnect so panes don't stay
                    // in "updating" state forever if the engine never comes back.
                    self.buying_power_request_id = None;
                    self.order_list_request_id = None;
                    self.positions_request_id = None;
                    self.layout_manager
                        .iter_dashboards_mut()
                        .for_each(|dashboard| {
                            dashboard.distribute_buying_power_loading(main_window, false);
                            dashboard.distribute_order_list_loading(main_window, false);
                            dashboard.distribute_positions_loading(main_window, false);
                            dashboard.notify_engine_disconnected(main_window);
                        });
                }
                // The actual backend rebuild + recovery toast are emitted
                // by `Message::EngineConnected` so a single source of
                // truth (the live connection) drives the swap. See
                // T35-H9-SingleRecoveryPath.
            }
            Message::DismissTachibanaBanner => {
                // Route the dismiss through the FSM `next()` table so
                // the transition is unit-testable from `venue_state.rs`
                // and `main.rs::update()` does not become a second
                // source of truth for FSM mutations.
                let next = std::mem::replace(&mut self.tachibana_state, VenueState::Idle)
                    .next(VenueEvent::Dismissed);
                self.tachibana_state = next;
            }
            Message::RequestTachibanaLogin(trigger) => {
                // Duplicate-press suppression: claim the LoginInFlight
                // slot atomically BEFORE dispatching the IPC. Without
                // this, two rapid presses (Auto + Manual or two manual
                // double-clicks) both observe the FSM in `Idle` /
                // `Ready` / `Error` and dispatch duplicate
                // `RequestVenueLogin` IPC sends — a tkinter helper
                // spawns twice. Reviewer 2026-04-26 R4 (MEDIUM-2).
                // T35-U1-LoginButton / T35-U3-AutoRequestLogin.
                log::info!("RequestTachibanaLogin trigger={trigger:?}");
                let Some(conn) = self.engine_connection.as_ref().cloned() else {
                    log::warn!(
                        "RequestTachibanaLogin({trigger:?}) ignored — engine connection unavailable"
                    );
                    if matches!(trigger, Trigger::Manual) {
                        // Auto-fire is silent (the user just selected
                        // the venue and may not yet expect feedback);
                        // a manual button press deserves a visible
                        // notice that the click did register.
                        self.notifications.push(Toast::error(
                            "立花ログイン要求を送信できません — エンジン未接続".to_string(),
                        ));
                    }
                    return Task::none();
                };
                if !self.tachibana_state.try_claim_login_in_flight() {
                    log::debug!(
                        "RequestTachibanaLogin({trigger:?}) ignored — login already in flight"
                    );
                    return Task::none();
                }
                return Task::perform(
                    async move {
                        // request_id は Python エンジン側のログ相関 ID として使われる。
                        // Rust 側では TachibanaLoginIpcResult のコールバックに乗らないため、
                        // IPC 送信成功/失敗の照合には使用しない。
                        let request_id = uuid::Uuid::new_v4().to_string();
                        conn.send(engine_client::dto::Command::RequestVenueLogin {
                            request_id,
                            venue: TACHIBANA_VENUE_NAME.to_string(),
                        })
                        .await
                        .map_err(|e| e.to_string())
                    },
                    Message::TachibanaLoginIpcResult,
                );
            }
            Message::TachibanaLoginIpcResult(result) => {
                // The optimistic `try_claim_login_in_flight` already
                // moved the FSM into `LoginInFlight`. Engine's
                // `VenueLoginStarted` is idempotent under that, but
                // an IPC send failure means the engine never received
                // the request and will not emit `VenueLoginStarted`
                // — roll the FSM back to `Idle` so the user can
                // retry. Reviewer 2026-04-26 R4 (MEDIUM-2).
                match result {
                    Ok(()) => {
                        log::debug!("RequestVenueLogin IPC sent");
                    }
                    Err(err) => {
                        log::warn!("RequestVenueLogin IPC failed: {err}");
                        self.notifications.push(Toast::error(format!(
                            "立花ログイン要求の送信に失敗しました: {err}"
                        )));
                        // FSM の next() を意図的に迂回して直接 Idle に戻す。
                        // IPC 送信が失敗した時点でエンジンには RequestVenueLogin が届いておらず、
                        // VenueLoginStarted も来ない。LoginCancelled は「ユーザー操作でキャンセル」の
                        // セマンティクスなので流用せず、ここで直接代入する。
                        if self.tachibana_state.is_login_in_flight() {
                            self.tachibana_state = VenueState::Idle;
                        }
                    }
                }
            }
            Message::TachibanaVenueEvent(event) => {
                // Toast notifications for the in-flight / cancelled
                // states. The banner only renders `Error`
                // (F-Banner1: no Rust string literals in the banner),
                // so the user-facing "ログイン中" / "キャンセル" feedback
                // path goes through the existing toast channel where
                // Rust strings are conventional. Reviewer 2026-04-26
                // R2 (MED-3).
                match &event {
                    VenueEvent::LoginStarted => {
                        self.notifications.push(Toast::info(
                            "立花ログインダイアログを起動しました".to_string(),
                        ));
                    }
                    VenueEvent::LoginCancelled => {
                        self.notifications.push(Toast::warn(
                            "立花ログインがキャンセルされました".to_string(),
                        ));
                    }
                    VenueEvent::Ready => {
                        log::info!("tachibana: VenueReady — venue is now authenticated");
                    }
                    _ => {}
                }

                let old_state = std::mem::replace(&mut self.tachibana_state, VenueState::Idle);
                // Capture before `next()` consumes old_state.
                let needs_bump =
                    old_state.is_login_in_flight() || matches!(old_state, VenueState::Error { .. });
                let next = old_state.next(event);
                let is_ready = next.is_ready();
                self.tachibana_state = next;

                // Bump only when the session *newly* becomes available from a
                // state that required a login round-trip (LoginInFlight) or a
                // re-authentication after an error. Transitions from Idle or
                // Ready → Ready must NOT bump — those paths mean EngineConnected
                // already bumped (Idle) or the event is idempotent (Ready→Ready).
                if needs_bump && is_ready {
                    self.handles.bump_generation();
                    log::info!(
                        "tachibana: session established — restarting subscriptions (gen bumped)"
                    );
                }

                let replay = self
                    .sidebar
                    .tickers_table
                    .set_tachibana_ready(is_ready)
                    .map(|m| Message::Sidebar(dashboard::sidebar::Message::TickersTable(m)));

                // Auto-fetch buying power on venue ready if a pane is visible.
                let main_window = self.main_window.id;
                let auto_fetch_buying_power = if is_ready
                    && self.buying_power_request_id.is_none()
                    && self.active_dashboard().has_buying_power_pane(main_window)
                {
                    if let Some(conn) = self.engine_connection.as_ref().cloned() {
                        let req_id = uuid::Uuid::new_v4().to_string();
                        self.buying_power_request_id = Some(req_id.clone());
                        self.active_dashboard_mut()
                            .distribute_buying_power_loading(main_window, true);
                        let req_id_for_err = req_id.clone();
                        Task::perform(
                            async move {
                                conn.send(engine_client::dto::Command::GetBuyingPower {
                                    request_id: req_id,
                                    venue: crate::TACHIBANA_VENUE_NAME.to_string(),
                                })
                                .await
                                .map_err(|e| e.to_string())
                            },
                            move |res| match res {
                                Ok(()) => Message::BuyingPowerSendCompleted(Ok(())),
                                Err(err) => Message::IpcError {
                                    request_id: Some(req_id_for_err),
                                    code: "send_failed".to_string(),
                                    message: err,
                                },
                            },
                        )
                    } else {
                        Task::none()
                    }
                } else {
                    Task::none()
                };

                // Auto-fetch order list on venue ready if a pane is visible.
                let auto_fetch_orders = if is_ready
                    && self.order_list_request_id.is_none()
                    && self.active_dashboard().has_order_list_pane(main_window)
                {
                    if let Some(conn) = self.engine_connection.as_ref().cloned() {
                        let req_id = uuid::Uuid::new_v4().to_string();
                        self.order_list_request_id = Some(req_id.clone());
                        self.active_dashboard_mut()
                            .distribute_order_list_loading(main_window, true);
                        Task::perform(
                            async move {
                                conn.send(engine_client::dto::Command::GetOrderList {
                                    request_id: req_id,
                                    venue: crate::TACHIBANA_VENUE_NAME.to_string(),
                                    filter: engine_client::dto::OrderListFilter {
                                        status: None,
                                        instrument_id: None,
                                        date: None,
                                    },
                                })
                                .await
                                .map_err(|e| e.to_string())
                            },
                            Message::OrderListSendCompleted,
                        )
                    } else {
                        Task::none()
                    }
                } else {
                    Task::none()
                };

                // Auto-fetch positions on venue ready if a pane is visible.
                let auto_fetch_positions = if is_ready
                    && self.positions_request_id.is_none()
                    && self.active_dashboard().has_positions_pane(main_window)
                {
                    if let Some(conn) = self.engine_connection.as_ref().cloned() {
                        let req_id = uuid::Uuid::new_v4().to_string();
                        self.positions_request_id = Some(req_id.clone());
                        self.active_dashboard_mut()
                            .distribute_positions_loading(main_window, true);
                        let req_id_for_err = req_id.clone();
                        Task::perform(
                            async move {
                                conn.send(engine_client::dto::Command::GetPositions {
                                    request_id: req_id,
                                    venue: crate::TACHIBANA_VENUE_NAME.to_string(),
                                })
                                .await
                                .map_err(|e| e.to_string())
                            },
                            move |res| match res {
                                Ok(()) => Message::PositionsSendCompleted(Ok(())),
                                Err(err) => Message::IpcError {
                                    request_id: Some(req_id_for_err),
                                    code: "send_failed".to_string(),
                                    message: err,
                                },
                            },
                        )
                    } else {
                        Task::none()
                    }
                } else {
                    Task::none()
                };

                return replay
                    .chain(auto_fetch_buying_power)
                    .chain(auto_fetch_orders)
                    .chain(auto_fetch_positions);
            }
            Message::EngineConnected(conn) => {
                let was_restarting = self.engine_restarting;
                self.engine_connection = Some(Arc::clone(&conn));
                // In-flight requests are lost on reconnect; reset to avoid blocking
                // future auto-fetches via the is_none() guard. Also clear loading
                // so panes don't stay in "updating" state forever.
                let main_window = self.main_window.id;
                self.buying_power_request_id = None;
                self.order_list_request_id = None;
                self.positions_request_id = None;
                self.active_dashboard_mut()
                    .distribute_buying_power_loading(main_window, false);
                self.active_dashboard_mut()
                    .distribute_order_list_loading(main_window, false);
                self.active_dashboard_mut()
                    .distribute_positions_loading(main_window, false);

                // Rebuild backends with the new connection and bump the generation
                // counter so iced assigns new subscription IDs and restarts streams.
                // D1: do NOT clear VENUE_CAPS_STORE here — old values remain as the
                // authoritative source during the reconnect window until
                // fetch_ticker_metadata upserts fresh entries. Clearing creates an
                // empty-store window where caps_client_aggr() falls back to the wrong
                // default (Hyperliquid would be misclassified as client-aggregated).
                let mut tachibana_meta_handle = None;
                for &(venue, name) in VENUE_NAMES {
                    let backend = Arc::new(engine_client::EngineClientBackend::new(
                        Arc::clone(&conn),
                        name,
                        VENUE_CAPS_STORE.get().map(Arc::clone).unwrap_or_else(|| {
                            Arc::new(tokio::sync::RwLock::new(
                                engine_client::VenueCapsStore::new(),
                            ))
                        }),
                    ));
                    // B5: capture the Tachibana meta handle before the backend
                    // is moved into the type-erased `AdapterHandles`. This is
                    // the only point where the typed `Arc<EngineClientBackend>`
                    // is available to call `ticker_meta_handle()`.
                    if venue == exchange::adapter::Venue::Tachibana {
                        tachibana_meta_handle = Some(backend.ticker_meta_handle());
                    }
                    self.handles.set_backend(venue, backend);
                }
                // Wire the handle into the sidebar's ticker filter so
                // Japanese-name incremental search works after each reconnect.
                self.sidebar
                    .set_tachibana_meta_handle(tachibana_meta_handle);

                // Re-apply current proxy state before bumping the generation so
                // that stream-subscribe commands are enqueued after SetProxy in
                // the engine's FIFO command channel.  Send unconditionally —
                // including `None` — so a user-cleared proxy cannot be revived
                // by a stale value held in the freshly spawned engine.
                let proxy_url = self.network.proxy_cfg().map(|p| p.to_url_string());
                if !conn.try_send_now(engine_client::dto::Command::SetProxy { url: proxy_url }) {
                    log::warn!("Failed to queue proxy for engine reconnect");
                }

                self.handles.bump_generation();

                // Also propagate to the sidebar's TickersTable so it uses
                // the new connection for metadata/stats fetches.
                let sidebar_refetch = self
                    .sidebar
                    .update_handles(self.handles.clone())
                    .map(Message::Sidebar);

                if was_restarting {
                    self.notifications
                        .push(Toast::info("データエンジン接続を復旧しました".to_string()));
                }

                // Clear the disconnection error from all OrderEntry panes so
                // they return to normal state after reconnect (M-1).
                {
                    let main_window = self.main_window.id;
                    self.layout_manager
                        .iter_dashboards_mut()
                        .for_each(|dashboard| {
                            dashboard.notify_engine_reconnected(main_window);
                        });
                }

                // Bridge the broadcast-replay gap from BOTH directions:
                //   - managed mode: `ProcessManager` caches post-
                //     `apply_after_handshake` readiness internally.
                //   - external mode (`--data-engine-url`): the
                //     mode-agnostic `VENUE_READY_CACHE` bridge task
                //     captured `VenueReady` between connect() and
                //     iced's late `subscribe_events()`.
                // Either source being `true` means the engine
                // currently considers Tachibana ready — synthesize
                // `VenueEvent::Ready` so the FSM bootstraps correctly.
                // Reviewers 2026-04-26 R2 (HIGH-1) / R3 (HIGH-2).
                let is_ready_from_manager = self
                    .engine_manager
                    .as_ref()
                    .is_some_and(|m| m.try_is_venue_ready(TACHIBANA_VENUE_NAME));
                let is_ready_from_bridge = cached_venue_is_ready(TACHIBANA_VENUE_NAME);
                if (is_ready_from_manager || is_ready_from_bridge)
                    && !self.tachibana_state.is_ready()
                {
                    return Task::batch(vec![
                        sidebar_refetch,
                        Task::done(Message::TachibanaVenueEvent(VenueEvent::Ready)),
                    ]);
                }
                return sidebar_refetch;
            }
            Message::MarketWsEvent(event) => {
                // M2: when the Tachibana depth stream reconnects (market
                // reopened after off-hours) while the FSM is stuck in an Error
                // state (e.g. market_closed banner), synthesize VenueReady to
                // clear the banner and re-arm the subscription bump path.
                if let exchange::Event::Connected(exchange::adapter::Exchange::TachibanaStock) =
                    &event
                    && matches!(self.tachibana_state, VenueState::Error { .. })
                {
                    log::info!(
                        "tachibana: depth stream reconnected while in Error state \
                         — synthesizing VenueReady to clear banner"
                    );
                    return Task::done(Message::TachibanaVenueEvent(VenueEvent::Ready));
                }

                let main_window_id = self.main_window.id;
                let dashboard = self.active_dashboard_mut();

                match event {
                    exchange::Event::Connected(exchange) => {
                        log::info!("a stream connected to {exchange} WS");
                    }
                    exchange::Event::Disconnected(exchange, reason) => {
                        log::info!("a stream disconnected from {exchange} WS: {reason:?}");
                    }
                    exchange::Event::DepthReceived(stream, depth_update_t, depth) => {
                        let task = dashboard
                            .ingest_depth(&stream, depth_update_t, &depth, main_window_id)
                            .map(move |msg| Message::Dashboard {
                                layout_id: None,
                                event: msg,
                            });

                        return task;
                    }
                    exchange::Event::TradesReceived(stream, update_t, buffer) => {
                        let task = dashboard
                            .ingest_trades(&stream, &buffer, update_t, main_window_id)
                            .map(move |msg| Message::Dashboard {
                                layout_id: None,
                                event: msg,
                            });

                        if let Some(msg) = self.audio_stream.try_play_sound(&stream, &buffer) {
                            self.notifications.push(Toast::error(msg));
                        }

                        return task;
                    }
                    exchange::Event::KlineReceived(stream, kline) => {
                        return dashboard
                            .update_latest_klines(&stream, &kline, main_window_id)
                            .map(move |msg| Message::Dashboard {
                                layout_id: None,
                                event: msg,
                            });
                    }
                }
            }
            Message::Tick(now) => {
                let main_window_id = self.main_window.id;
                let handles = self.handles.clone();

                return self
                    .active_dashboard_mut()
                    .tick(&handles, now, main_window_id)
                    .map(move |msg| Message::Dashboard {
                        layout_id: None,
                        event: msg,
                    });
            }
            Message::WindowEvent(event) => match event {
                window::Event::CloseRequested(window) => {
                    let main_window = self.main_window.id;
                    let dashboard = self.active_dashboard_mut();

                    if window != main_window {
                        dashboard.popout.remove(&window);
                        return window::close(window);
                    }

                    let mut active_windows = dashboard
                        .popout
                        .keys()
                        .copied()
                        .collect::<Vec<window::Id>>();
                    active_windows.push(main_window);

                    return window::collect_window_specs(active_windows, Message::ExitRequested);
                }
            },
            // F4 BC-9 fix: set dirty baseline after startup so edits before the first
            // explicit Save are detected as dirty (ケース 3/4).
            Message::SetDirtyBaseline(windows) => {
                if let Some(json) = self.build_state_json(&windows) {
                    self.last_saved_bytes = Some(json.into_bytes());
                }
                return Task::none();
            }
            Message::ExitRequested(windows) => {
                // HIGH fix: another dialog is already visible — ignore this close request
                // until the user resolves it. Prevents F4 bypass via overlapping dialogs.
                if self.confirm_dialog.is_some() {
                    return Task::none();
                }
                // F4: check dirty before exiting (live mode only).
                // Replay mode never writes state, so skip the check there.
                let is_live = app_mode() == engine_client::dto::AppMode::Live;
                if is_live && self.is_dirty(&windows) {
                    // Store window specs so Discard/SaveAndExit can proceed later.
                    self.pending_exit_windows = Some(windows);
                    let dialog = screen::ConfirmDialog::new(
                        "未保存の変更があります。".to_string(),
                        Box::new(Message::DiscardAndExit),
                    )
                    .with_confirm_btn_text("破棄して終了".to_string())
                    .with_save_action(Message::SaveAndExit, "保存して終了".to_string());
                    self.confirm_dialog = Some(dialog);
                    return Task::none();
                }
                // Clean exit: auto-save. Failure is shown as a toast but does NOT
                // abort exit — the user explicitly asked to quit.
                if !self.save_state_to_disk(&windows) {
                    self.notifications
                        .push(Toast::error("自動保存に失敗しました".to_string()));
                }
                return iced::exit();
            }
            Message::DiscardAndExit => {
                // User chose "破棄して終了" — do NOT save. saved-state.json stays as-is
                // so the next launch restores the state from before the discarded edits.
                self.confirm_dialog = None;
                self.pending_exit_windows = None;
                return iced::exit();
            }
            Message::SaveAndExit => {
                // User chose "保存して終了" — save then exit.
                // BC-5: if save fails, abort the exit and show an error (do not discard data).
                self.confirm_dialog = None;
                // F-M1 (R6): require a prior ExitRequested dirty check. `unwrap_or_default()`
                // here would silently turn `None` into an empty HashMap and corrupt the saved
                // layout. Log a warning and abort instead.
                let Some(windows) = self.pending_exit_windows.take() else {
                    log::warn!(
                        "[SaveAndExit] pending_exit_windows is None — SaveAndExit dispatched without prior ExitRequested dirty check"
                    );
                    return Task::none();
                };

                let Some(json) = self.build_state_json(&windows) else {
                    // replay mode has no state to save
                    return iced::exit();
                };

                // If a named document is open, write to it first (primary save target).
                let current_path = match CURRENT_PATH.lock() {
                    Ok(g) => g.clone(),
                    Err(poisoned) => poisoned.into_inner().clone(),
                };
                if let Some(p) = &current_path {
                    if let Err(e) = std::fs::write(p, json.as_bytes()) {
                        log_save_error(&SaveError::IoError(e.kind()), p);
                        self.notifications.push(Toast::error(
                            "保存に失敗しました。再試行してください。".to_string(),
                        ));
                        self.pending_exit_windows = Some(windows);
                        let dialog = screen::ConfirmDialog::new(
                            "未保存の変更があります。".to_string(),
                            Box::new(Message::DiscardAndExit),
                        )
                        .with_confirm_btn_text("破棄して終了".to_string())
                        .with_save_action(Message::SaveAndExit, "保存して終了".to_string());
                        self.confirm_dialog = Some(dialog);
                        return Task::none();
                    }
                    // F-H1 (R6 / A-7): the named-doc write succeeded — update the dirty
                    // baseline immediately. Without this, a subsequent saved-state.json
                    // failure still triggers `iced::exit()` (current_path.is_some() branch)
                    // but leaves `last_saved_bytes` stale, violating the
                    // "明示 Save 直後に last_saved_bytes 更新" contract.
                    self.last_saved_bytes = Some(json.as_bytes().to_vec());
                }

                // Also write to saved-state.json (auto-restore slot) and update last_saved_bytes.
                // If CURRENT_PATH was successfully written, a saved-state.json failure is non-fatal
                // (data is safe in the named doc). If there is no CURRENT_PATH, saved-state.json
                // is the only copy — abort on failure.
                let saved_ok = self.write_json_to_saved_state_disk(&json);
                if current_path.is_some() || saved_ok {
                    return iced::exit();
                }

                self.notifications.push(Toast::error(
                    "保存に失敗しました。再試行してください。".to_string(),
                ));
                self.pending_exit_windows = Some(windows);
                let dialog = screen::ConfirmDialog::new(
                    "未保存の変更があります。".to_string(),
                    Box::new(Message::DiscardAndExit),
                )
                .with_confirm_btn_text("破棄して終了".to_string())
                .with_save_action(Message::SaveAndExit, "保存して終了".to_string());
                self.confirm_dialog = Some(dialog);
                return Task::none();
            }
            Message::RestartRequested(Some(windows)) => {
                self.save_state_to_disk(&windows);
                return self.restart();
            }
            Message::RestartRequested(None) => {
                self.confirm_dialog = None;

                let mut active_windows = self
                    .active_dashboard()
                    .popout
                    .keys()
                    .copied()
                    .collect::<Vec<window::Id>>();
                active_windows.push(self.main_window.id);

                return window::collect_window_specs(active_windows, |windows| {
                    Message::RestartRequested(Some(windows))
                });
            }
            Message::GoBack => {
                let main_window = self.main_window.id;

                #[cfg(target_os = "linux")]
                if self.menu_bar.open.is_some() {
                    log::debug!(
                        "widget_menu_bar: dismiss reason=esc open={:?}",
                        self.menu_bar.open
                    );
                    self.menu_bar = crate::menu_bar_state::update(
                        self.menu_bar.clone(),
                        crate::menu_bar_state::BarMessage::Dismiss,
                    );
                    return Task::none();
                }

                if self.confirm_dialog.is_some() {
                    // F5/M: if we're dismissing the overwrite confirm during a save-then-open
                    // flow, restore the dirty-confirm so the user can retry or discard instead
                    // of silently losing the pending open target.
                    if self
                        .confirm_dialog
                        .as_ref()
                        .is_some_and(|d| d.on_save.is_none())
                        && self.pending_open_file.is_some()
                    {
                        self.confirm_dialog = Some(
                            screen::ConfirmDialog::new(
                                "未保存の変更があります。".to_string(),
                                Box::new(Message::DiscardAndOpenFile),
                            )
                            .with_confirm_btn_text("破棄して開く".to_string())
                            .with_save_action(Message::SaveAndOpenFile, "保存して開く".to_string()),
                        );
                        return Task::none();
                    }
                    self.confirm_dialog = None;
                    self.pending_open_file = None;
                    self.pending_exit_windows = None;
                    // F7: release mode-switch guard so the next SwitchMode attempt is not
                    // permanently blocked after the user dismisses the dirty-confirm dialog.
                    self.pending_mode_switch = None;
                    self._mode_switch_guard = None;
                } else if self.sidebar.active_menu().is_some() {
                    self.sidebar.set_menu(None);
                } else {
                    let dashboard = self.active_dashboard_mut();

                    if dashboard.go_back(main_window) {
                        return Task::none();
                    } else if dashboard.focus.is_some() {
                        dashboard.focus = None;
                    } else {
                        self.sidebar.hide_tickers_table();
                    }
                }
            }
            Message::ThemeSelected(theme) => {
                self.theme = data::Theme(theme.clone());

                let main_window = self.main_window.id;
                self.active_dashboard_mut()
                    .theme_updated(main_window, &theme);
            }
            Message::Dashboard {
                layout_id: id,
                event: msg,
            } => {
                let Some(active_layout) = self.layout_manager.active_layout_id() else {
                    log::error!("No active layout to handle dashboard message");
                    return Task::none();
                };

                let main_window = self.main_window;
                let layout_id = id.unwrap_or(active_layout.unique);
                let handles = self.handles.clone();

                if let Some(dashboard) = self.layout_manager.mut_dashboard(layout_id) {
                    let (main_task, event) =
                        dashboard.update(&handles, msg, &main_window, &layout_id);

                    let additional_task = match event {
                        Some(dashboard::Event::DistributeFetchedData {
                            layout_id,
                            pane_id,
                            data,
                            stream,
                        }) => dashboard
                            .distribute_fetched_data(main_window.id, pane_id, data, stream)
                            .map(move |msg| Message::Dashboard {
                                layout_id: Some(layout_id),
                                event: msg,
                            }),
                        Some(dashboard::Event::Notification(toast)) => {
                            self.notifications.push(toast);
                            Task::none()
                        }
                        Some(dashboard::Event::ResolveStreams { pane_id, streams }) => {
                            let tickers_info = self.sidebar.tickers_info();

                            let has_any_ticker_info =
                                tickers_info.values().any(|opt| opt.is_some());
                            if !has_any_ticker_info {
                                log::debug!(
                                    "Deferring persisted stream resolution for pane {pane_id}: ticker metadata not loaded yet"
                                );
                                return Task::none();
                            }

                            let resolved_streams =
                                streams.into_iter().try_fold(vec![], |mut acc, persist| {
                                    let resolver = |t: &exchange::Ticker| {
                                        tickers_info.get(t).and_then(|opt| *opt)
                                    };

                                    match persist.into_stream_kinds(resolver) {
                                        Ok(mut resolved) => {
                                            acc.append(&mut resolved);
                                            Ok(acc)
                                        }
                                        Err(err) => Err(format!(
                                            "Persisted stream still not resolvable: {err}"
                                        )),
                                    }
                                });

                            match resolved_streams {
                                Ok(resolved) => {
                                    if resolved.is_empty() {
                                        Task::none()
                                    } else {
                                        dashboard
                                            .resolve_streams(main_window.id, pane_id, resolved)
                                            .map(move |msg| Message::Dashboard {
                                                layout_id: None,
                                                event: msg,
                                            })
                                    }
                                }
                                Err(err) => {
                                    // This is typically a transient state (e.g. partial metadata, stale symbol)
                                    log::debug!("{err}");
                                    Task::none()
                                }
                            }
                        }
                        Some(dashboard::Event::RequestPalette) => {
                            let theme = self.theme.0.clone();

                            let main_window = self.main_window.id;
                            self.active_dashboard_mut()
                                .theme_updated(main_window, &theme);

                            Task::none()
                        }
                        Some(dashboard::Event::OrderEntryAction(action)) => {
                            use crate::screen::dashboard::panel::order_entry::{
                                Action, CashMarginKind,
                            };

                            fn cash_margin_tag(kind: CashMarginKind) -> String {
                                match kind {
                                    CashMarginKind::Cash => "cash_margin=cash".to_string(),
                                    CashMarginKind::MarginCreditNew => {
                                        "cash_margin=margin_credit_new".to_string()
                                    }
                                    CashMarginKind::MarginCreditRepay => {
                                        "cash_margin=margin_credit_repay".to_string()
                                    }
                                    CashMarginKind::MarginGeneralNew => {
                                        "cash_margin=margin_general_new".to_string()
                                    }
                                    CashMarginKind::MarginGeneralRepay => {
                                        "cash_margin=margin_general_repay".to_string()
                                    }
                                }
                            }

                            match action {
                                Action::OpenInstrumentPicker => Task::none(),
                                Action::RequestConfirm {
                                    instrument_id,
                                    order_side,
                                    order_type,
                                    quantity,
                                    price,
                                } => {
                                    let price_str = price
                                        .as_deref()
                                        .map(|p| format!(" @ {p}"))
                                        .unwrap_or_default();
                                    let side_str = match order_side {
                                        engine_client::dto::OrderSide::Buy => "買い",
                                        engine_client::dto::OrderSide::Sell => "売り",
                                    };
                                    let type_str = match order_type {
                                        engine_client::dto::OrderType::Market => "成行",
                                        engine_client::dto::OrderType::Limit => "指値",
                                        engine_client::dto::OrderType::StopMarket => "逆指値成行",
                                        engine_client::dto::OrderType::StopLimit => "逆指値指値",
                                        engine_client::dto::OrderType::MarketIfTouched => {
                                            "マーケットイフタッチ"
                                        }
                                        engine_client::dto::OrderType::LimitIfTouched => {
                                            "リミットイフタッチ"
                                        }
                                    };
                                    let body = format!(
                                        "{instrument_id} {side_str} {quantity}株 {type_str}{price_str}"
                                    );
                                    let dialog = screen::ConfirmDialog::new(
                                        body,
                                        Box::new(Message::ConfirmOrderEntrySubmit),
                                    )
                                    .with_confirm_btn_text("注文を発注する".to_string());
                                    self.confirm_dialog = Some(dialog);
                                    Task::none()
                                }
                                Action::SubmitOrder {
                                    request_id,
                                    venue,
                                    instrument_id,
                                    order_side,
                                    order_type,
                                    quantity,
                                    price,
                                    trigger_price,
                                    cash_margin,
                                } => {
                                    if let Some(conn) = self.engine_connection.as_ref().cloned() {
                                        let request_key =
                                            xxhash_rust::xxh3::xxh3_64(request_id.as_bytes());
                                        let order = engine_client::dto::SubmitOrderRequest {
                                            client_order_id: request_id.clone(),
                                            instrument_id,
                                            order_side,
                                            order_type,
                                            quantity,
                                            price,
                                            trigger_price,
                                            trigger_type: None,
                                            time_in_force: engine_client::dto::TimeInForce::Day,
                                            expire_time_ns: None,
                                            post_only: false,
                                            reduce_only: false,
                                            tags: vec![cash_margin_tag(cash_margin)],
                                            request_key,
                                        };
                                        let request_id_err = request_id.clone();
                                        return Task::perform(
                                            async move {
                                                conn.send(
                                                    engine_client::dto::Command::SubmitOrder {
                                                        request_id,
                                                        venue,
                                                        order,
                                                    },
                                                )
                                                .await
                                                .map_err(|e| e.to_string())
                                            },
                                            move |res| match res {
                                                Ok(()) => Message::OrderToast(Toast::info(
                                                    "注文送信完了".to_string(),
                                                )),
                                                Err(err) => Message::OrderRejected {
                                                    client_order_id: request_id_err,
                                                    reason: format!("IPC 送信失敗: {err}"),
                                                },
                                            },
                                        );
                                    }
                                    // engine_connection が None — submitting をリセットして toast を出す
                                    Task::done(Message::OrderRejected {
                                        client_order_id: request_id,
                                        reason: "エンジン未接続".to_string(),
                                    })
                                }
                            }
                        }
                        Some(dashboard::Event::BuyingPowerAction(_action)) => {
                            // Guard: skip if a request is already in-flight to avoid
                            // overwriting the pending req_id and breaking IpcError routing.
                            if self.buying_power_request_id.is_some() {
                                return Task::none();
                            }
                            if let Some(conn) = self.engine_connection.as_ref().cloned() {
                                let req_id = uuid::Uuid::new_v4().to_string();
                                self.buying_power_request_id = Some(req_id.clone());
                                let main_window = self.main_window.id;
                                self.active_dashboard_mut()
                                    .distribute_buying_power_loading(main_window, true);
                                let req_id_for_err = req_id.clone();
                                return Task::perform(
                                    async move {
                                        conn.send(engine_client::dto::Command::GetBuyingPower {
                                            request_id: req_id,
                                            venue: crate::TACHIBANA_VENUE_NAME.to_string(),
                                        })
                                        .await
                                        .map_err(|e| e.to_string())
                                    },
                                    move |res| match res {
                                        Ok(()) => Message::BuyingPowerSendCompleted(Ok(())),
                                        Err(err) => Message::IpcError {
                                            request_id: Some(req_id_for_err),
                                            code: "send_failed".to_string(),
                                            message: err,
                                        },
                                    },
                                );
                            }
                            // J-4: エンジン未接続時はユーザーに通知する（loading は立てない）
                            Task::done(Message::OrderToast(Toast::error(
                                "エンジン未接続: 余力情報を取得できません".to_string(),
                            )))
                        }
                        Some(dashboard::Event::OrderListAction(action)) => {
                            use crate::screen::dashboard::panel::orders::Action;
                            match action {
                                Action::RequestOrderList => {
                                    // Guard: skip if a request is already in-flight.
                                    if self.order_list_request_id.is_some() {
                                        return Task::none();
                                    }
                                    if let Some(conn) = self.engine_connection.as_ref().cloned() {
                                        let is_replay =
                                            app_mode() == engine_client::dto::AppMode::Replay;
                                        let venue = if is_replay {
                                            "replay".to_string()
                                        } else {
                                            crate::TACHIBANA_VENUE_NAME.to_string()
                                        };
                                        let req_id = uuid::Uuid::new_v4().to_string();
                                        self.order_list_request_id = Some(req_id.clone());
                                        let main_window = self.main_window.id;
                                        self.active_dashboard_mut()
                                            .distribute_order_list_loading(main_window, true);
                                        return Task::perform(
                                            async move {
                                                conn.send(
                                                    engine_client::dto::Command::GetOrderList {
                                                        request_id: req_id,
                                                        venue,
                                                        filter:
                                                            engine_client::dto::OrderListFilter {
                                                                status: None,
                                                                instrument_id: None,
                                                                date: None,
                                                            },
                                                    },
                                                )
                                                .await
                                                .map_err(|e| e.to_string())
                                            },
                                            Message::OrderListSendCompleted,
                                        );
                                    }
                                    // エンジン未接続時はユーザーに通知する（loading は立てない）
                                    Task::done(Message::OrderToast(Toast::error(
                                        "エンジン未接続: 注文一覧を取得できません".to_string(),
                                    )))
                                }
                                Action::CancelOrder {
                                    client_order_id,
                                    venue_order_id,
                                } => {
                                    let body =
                                        format!("注文 {} を取り消しますか？", client_order_id);
                                    let dialog = screen::ConfirmDialog::new(
                                        body,
                                        Box::new(Message::ConfirmCancelOrder {
                                            client_order_id,
                                            venue_order_id,
                                        }),
                                    )
                                    .with_confirm_btn_text("取消実行".to_string());
                                    self.confirm_dialog = Some(dialog);
                                    Task::none()
                                }
                            }
                        }
                        Some(dashboard::Event::PositionsAction(
                            crate::screen::dashboard::panel::positions::Action::RequestPositions,
                        )) => {
                            if let Some(conn) = self.engine_connection.as_ref().cloned() {
                                if self.positions_request_id.is_none() {
                                    let req_id = uuid::Uuid::new_v4().to_string();
                                    self.positions_request_id = Some(req_id.clone());
                                    let main_window = self.main_window.id;
                                    self.active_dashboard_mut()
                                        .distribute_positions_loading(main_window, true);
                                    let req_id_for_err = req_id.clone();
                                    return Task::perform(
                                        async move {
                                            conn.send(engine_client::dto::Command::GetPositions {
                                                request_id: req_id,
                                                venue: crate::TACHIBANA_VENUE_NAME.to_string(),
                                            })
                                            .await
                                            .map_err(|e| e.to_string())
                                        },
                                        move |res| match res {
                                            Ok(()) => Message::PositionsSendCompleted(Ok(())),
                                            Err(err) => Message::IpcError {
                                                request_id: Some(req_id_for_err),
                                                code: "send_failed".to_string(),
                                                message: err,
                                            },
                                        },
                                    );
                                }
                            } else {
                                return Task::done(Message::OrderToast(Toast::error(
                                    "エンジン未接続: 保有銘柄を取得できません".to_string(),
                                )));
                            }
                            Task::none()
                        }
                        // N1.11-ui: relay speed button press to IPC
                        Some(dashboard::Event::ReplaySpeedAction(multiplier)) => {
                            if let Some(conn) = self.engine_connection.as_ref().cloned() {
                                let request_id = uuid::Uuid::new_v4().to_string();
                                return Task::perform(
                                    async move {
                                        conn.send(engine_client::dto::Command::SetReplaySpeed {
                                            request_id,
                                            multiplier,
                                        })
                                        .await
                                        .map_err(|e| e.to_string())
                                    },
                                    |res| match res {
                                        Ok(()) => Message::OrderToast(Toast::info(
                                            "再生速度を変更しました".to_string(),
                                        )),
                                        Err(err) => Message::OrderToast(Toast::error(format!(
                                            "再生速度変更失敗: {err}"
                                        ))),
                                    },
                                );
                            }
                            Task::none()
                        }
                        // N4.3: open OS file dialog for strategy .py file
                        Some(dashboard::Event::PickStrategyFile) => {
                            return Task::perform(
                                async {
                                    rfd::AsyncFileDialog::new()
                                        .add_filter("Python", &["py"])
                                        .pick_file()
                                        .await
                                        .map(|h| h.path().to_owned())
                                },
                                Message::StrategyFilePicked,
                            );
                        }
                        None => Task::none(),
                    };

                    return main_task
                        .map(move |msg| Message::Dashboard {
                            layout_id: Some(layout_id),
                            event: msg,
                        })
                        .chain(additional_task);
                }
            }
            Message::RemoveNotification(index) => {
                self.notifications.remove(index);
            }
            // EC 約定通知 toast (Phase O2 T2.4)
            Message::OrderToast(toast) => {
                self.notifications.push(toast);
            }
            // OrderFilled — toast + positions auto-refresh
            Message::OrderFilled {
                client_order_id,
                last_qty,
                last_price,
                leaves_qty,
            } => {
                let body = if leaves_qty == "0" {
                    format!("約定 {client_order_id}: {last_qty} 株 @ {last_price} 円（全約定）")
                } else {
                    format!(
                        "約定 {client_order_id}: {last_qty} 株 @ {last_price} 円（残 {leaves_qty} 株）"
                    )
                };
                self.notifications.push(Toast::info(body));

                // live モード（tachibana ログイン済み）のときのみ positions 自動更新。
                if !self.tachibana_state.is_ready() {
                    return Task::none();
                }
                let Some(conn) = self.engine_connection.as_ref().cloned() else {
                    return Task::none();
                };
                let main_window = self.main_window.id;
                if self.positions_request_id.is_none()
                    && self.active_dashboard().has_positions_pane(main_window)
                {
                    let req_id = uuid::Uuid::new_v4().to_string();
                    self.positions_request_id = Some(req_id.clone());
                    self.active_dashboard_mut()
                        .distribute_positions_loading(main_window, true);
                    let req_id_for_err = req_id.clone();
                    return Task::perform(
                        async move {
                            conn.send(engine_client::dto::Command::GetPositions {
                                request_id: req_id,
                                venue: crate::TACHIBANA_VENUE_NAME.to_string(),
                            })
                            .await
                            .map_err(|e| e.to_string())
                        },
                        move |res| match res {
                            Ok(()) => Message::PositionsSendCompleted(Ok(())),
                            Err(err) => Message::IpcError {
                                request_id: Some(req_id_for_err),
                                code: "send_failed".to_string(),
                                message: err,
                            },
                        },
                    );
                }
            }
            // Phase U1: distribute fresh order list to all OrderList panes
            Message::OrderListUpdated(orders) => {
                self.order_list_request_id = None;
                let main_window = self.main_window.id;
                self.active_dashboard_mut()
                    .distribute_order_list(main_window, orders);
            }
            Message::OrderListSendCompleted(Ok(())) => {
                // 送信成功: OrderListUpdated 受信を待つだけ
            }
            Message::OrderListSendCompleted(Err(err)) => {
                self.order_list_request_id = None;
                let main_window = self.main_window.id;
                self.active_dashboard_mut()
                    .distribute_order_list_error(main_window, err.clone());
                self.notifications
                    .push(Toast::error(format!("注文一覧取得失敗: {err}")));
            }
            Message::BuyingPowerSendCompleted(Ok(())) => {
                // 送信成功: BuyingPowerUpdated 受信を待つだけ
            }
            Message::BuyingPowerSendCompleted(Err(err)) => {
                self.buying_power_request_id = None;
                let main_window = self.main_window.id;
                self.active_dashboard_mut()
                    .distribute_buying_power_error(main_window, err.clone());
                self.notifications
                    .push(Toast::error(format!("余力情報取得失敗: {err}")));
            }
            Message::PositionsSendCompleted(Ok(())) => {
                // 送信成功: PositionsUpdated 受信を待つだけ
            }
            Message::PositionsSendCompleted(Err(err)) => {
                self.positions_request_id = None;
                let main_window = self.main_window.id;
                self.active_dashboard_mut()
                    .distribute_positions_error(main_window, err.clone());
            }
            // Positions: broadcast to all Positions panes
            Message::PositionsUpdated {
                request_id,
                venue: _,
                positions,
                ts_ms,
            } => {
                let matches = self.positions_request_id.as_deref() == Some(request_id.as_str());
                if !matches {
                    log::debug!(
                        "[PositionsUpdated] stale/unrouted: request_id={request_id:?}, \
                         in-flight={:?}",
                        self.positions_request_id
                    );
                    return Task::none();
                }
                self.positions_request_id = None;
                let main_window = self.main_window.id;
                self.active_dashboard_mut()
                    .distribute_positions(main_window, positions, ts_ms);
            }
            // Phase U3: broadcast to all BuyingPower panes; silently no-ops if no pane exists
            Message::BuyingPowerUpdated {
                cash_available,
                cash_shortfall,
                credit_available,
                ts_ms,
            } => {
                self.buying_power_request_id = None;
                let main_window = self.main_window.id;
                self.active_dashboard_mut().distribute_buying_power(
                    main_window,
                    cash_available,
                    cash_shortfall,
                    credit_available,
                    ts_ms,
                );
            }
            // N1.16: REPLAY 仮想ポートフォリオ更新 — dashboard に配布
            Message::ReplayBuyingPower {
                cash,
                buying_power,
                equity,
                ts_event_ms,
            } => {
                let main_window = self.main_window.id;
                self.active_dashboard_mut().distribute_replay_buying_power(
                    main_window,
                    cash,
                    buying_power,
                    equity,
                    ts_event_ms,
                );
            }
            // Phase U3: IpcError → route to BuyingPower / OrderList panel if request_id matches
            Message::IpcError {
                request_id,
                code,
                message,
            } => {
                let matches_buying_power = self
                    .buying_power_request_id
                    .as_deref()
                    .zip(request_id.as_deref())
                    .is_some_and(|(bp, err)| bp == err);
                let matches_order_list = self
                    .order_list_request_id
                    .as_deref()
                    .zip(request_id.as_deref())
                    .is_some_and(|(ol, err)| ol == err);
                let matches_positions = self
                    .positions_request_id
                    .as_deref()
                    .zip(request_id.as_deref())
                    .is_some_and(|(p, err)| p == err);
                if matches_buying_power {
                    self.buying_power_request_id = None;
                    let main_window = self.main_window.id;
                    self.active_dashboard_mut()
                        .distribute_buying_power_error(main_window, format!("[{code}] {message}"));
                } else if matches_order_list {
                    self.order_list_request_id = None;
                    let main_window = self.main_window.id;
                    self.active_dashboard_mut()
                        .distribute_order_list_error(main_window, format!("[{code}] {message}"));
                } else if matches_positions {
                    self.positions_request_id = None;
                    let main_window = self.main_window.id;
                    self.active_dashboard_mut()
                        .distribute_positions_error(main_window, format!("[{code}] {message}"));
                } else if code == "strategy_load_failed" {
                    // N4.4: surface the error as a dismissable banner.
                    self.strategy_load_error = Some(message);
                } else {
                    log::debug!(
                        "[IpcError] unrouted: request_id={request_id:?}, code={code}, \
                         message={message}"
                    );
                }
            }
            // N4.3: user picked (or cancelled) the strategy file dialog.
            Message::StrategyFilePicked(path) => {
                self.replay_strategy_file = path;
                return Task::none();
            }
            // N4.4: user dismissed the strategy load error banner.
            Message::DismissStrategyLoadError => {
                self.strategy_load_error = None;
                return Task::none();
            }
            // Replay engine finished → auto-refresh order list from Python's in-memory fills.
            Message::ReplayFinished => {
                if let Some(conn) = self.engine_connection.as_ref().cloned() {
                    return Task::perform(
                        async move {
                            conn.send(engine_client::dto::Command::GetOrderList {
                                request_id: uuid::Uuid::new_v4().to_string(),
                                venue: "replay".to_string(),
                                filter: engine_client::dto::OrderListFilter {
                                    status: None,
                                    instrument_id: None,
                                    date: None,
                                },
                            })
                            .await
                            .map_err(|e| e.to_string())
                        },
                        |res| match res {
                            Ok(()) => Message::OrderToast(Toast::info(
                                "注文一覧を更新しました".to_string(),
                            )),
                            Err(e) => {
                                log::error!("[ReplayFinished] GetOrderList failed: {e}");
                                Message::OrderToast(Toast::error(format!(
                                    "注文一覧の取得に失敗: {e}"
                                )))
                            }
                        },
                    );
                }
                return Task::none();
            }
            // ── Linux widget menu bar ──────────────────────────────────────
            #[cfg(target_os = "linux")]
            Message::MenuBar(bar_msg) => {
                use crate::menu_bar_state::{self, BarMessage};
                let native = if let BarMessage::Pick(ref action) = bar_msg {
                    let mapped = crate::widget_menu_bar::to_native_action(action);
                    if mapped.is_none() {
                        // H5 (F8 R1): a Pick whose `Action` does not map to a
                        // `native_menu::Action` is silently dropped. That can
                        // only happen if a new `menu::Action` variant is added
                        // without extending `to_native_action` — surface it as
                        // a warning so the missing wiring is obvious in logs.
                        log::warn!(
                            "widget_menu_bar: to_native_action returned None for {action:?} \
                             — Pick will be dropped (missing native_menu::Action mapping)"
                        );
                    }
                    mapped
                } else {
                    None
                };
                match &bar_msg {
                    BarMessage::Toggle(top) if self.menu_bar.open != Some(*top) => {
                        log::debug!("widget_menu_bar: open={top:?}");
                    }
                    // M3 (F8 R1): re-toggling the same top-level menu closes
                    // it. Surface this as its own log line so transcript
                    // analysis can tell "user opened then re-clicked the same
                    // button" apart from outside-click / focus-lost dismissals.
                    BarMessage::Toggle(top) => {
                        log::debug!("widget_menu_bar: toggle_close reason=re_toggle top={top:?}");
                    }
                    BarMessage::Dismiss => {
                        log::debug!(
                            "widget_menu_bar: dismiss reason=outside_click open={:?}",
                            self.menu_bar.open
                        );
                    }
                    BarMessage::DismissFocusLost => {
                        log::debug!(
                            "widget_menu_bar: dismiss reason=focus_lost open={:?}",
                            self.menu_bar.open
                        );
                    }
                    // F8 R2 / M-B: exhaustive match. `Pick(_)` is the only
                    // remaining variant after `BarMoved` was abolished — the
                    // `to_native_action` mapping above already handles its
                    // dispatch, so this arm is a deliberate no-op for logging.
                    // Keeping the match exhaustive (no wildcard) means future
                    // `BarMessage` additions surface as compile errors.
                    BarMessage::Pick(_) => {}
                }
                self.menu_bar = menu_bar_state::update(self.menu_bar.clone(), bar_msg);
                if let Some(native_action) = native {
                    return Task::done(Message::NativeMenuAction(native_action));
                }
                return Task::none();
            }
            // ── Native OS menu bar ──────────────────────────────────────────
            Message::NativeMenuSetup(raw_id) => {
                native_menu::attach(raw_id, app_mode(), &self.wandb_auth, &self.run_buffer);
                return Task::none();
            }
            Message::NativeMenuAction(action) => {
                use native_menu::Action;
                match action {
                    Action::OpenFile => {
                        // F6a: replay モードでは `.py` 戦略ファイルを開いて
                        // SCENARIO を Python 側で抽出する。live モードは従来通り
                        // `saved-state.json` を読む。
                        if app_mode() == engine_client::dto::AppMode::Replay {
                            return Task::perform(
                                async {
                                    rfd::AsyncFileDialog::new()
                                        .add_filter("Python", &["py"])
                                        .set_title("戦略ファイルを開く")
                                        .pick_file()
                                        .await
                                        .map(|h| h.path().to_owned())
                                },
                                Message::NativeOpenStrategyPicked,
                            );
                        }
                        return Task::perform(
                            async {
                                // Returns None if cancelled; Some((json_result, path)) otherwise.
                                let handle = rfd::AsyncFileDialog::new()
                                    .add_filter("JSON", &["json"])
                                    .set_title("設定ファイルを開く")
                                    .pick_file()
                                    .await?;
                                let path = handle.path().to_owned();
                                let bytes = handle.read().await;
                                let result = String::from_utf8(bytes).map_err(|e| e.to_string());
                                Some((result, path))
                            },
                            |result| match result {
                                None => Message::NativeOpenFileCancelled,
                                Some((Ok(json), path)) => {
                                    Message::NativeOpenFileApply { json, path }
                                }
                                Some((Err(e), _)) => Message::OrderToast(Toast::error(format!(
                                    "ファイルを読み込めませんでした: {e}"
                                ))),
                            },
                        );
                    }
                    Action::Save => {
                        // F-M2 (R6): suppress when a confirm_dialog is already on screen.
                        // Otherwise Ctrl+S during a dirty/overwrite confirm spawns a second
                        // rfd save_file() dialog, multi-launching the OS picker.
                        if self.confirm_dialog.is_some() {
                            return Task::none();
                        }
                        // If a current path is known, write to it directly.
                        // Otherwise fall back to the Save As dialog.
                        let path = match CURRENT_PATH.lock() {
                            Ok(guard) => guard.clone(),
                            Err(poisoned) => poisoned.into_inner().clone(),
                        };
                        if let Some(p) = path {
                            // Capture path in the closure — no shared slot needed.
                            let mut active_windows: Vec<window::Id> =
                                self.active_dashboard().popout.keys().copied().collect();
                            active_windows.push(self.main_window.id);
                            return window::collect_window_specs(active_windows, move |windows| {
                                Message::NativeSaveAsWithSpecs {
                                    path: p.clone(),
                                    windows,
                                }
                            });
                        } else {
                            return Task::perform(
                                async {
                                    rfd::AsyncFileDialog::new()
                                        .add_filter("JSON", &["json"])
                                        .set_file_name("saved-state.json")
                                        .set_title("保存先を選択")
                                        .save_file()
                                        .await
                                        .map(|h| h.path().to_owned())
                                },
                                Message::NativeSaveAsPath,
                            );
                        }
                    }
                    Action::SaveAs => {
                        // F-M2 (R6): see Action::Save — same dialog re-entrancy guard.
                        if self.confirm_dialog.is_some() {
                            return Task::none();
                        }
                        return Task::perform(
                            async {
                                rfd::AsyncFileDialog::new()
                                    .add_filter("JSON", &["json"])
                                    .set_file_name("saved-state.json")
                                    .set_title("名前を付けて保存\u{2026}（Save As）")
                                    .save_file()
                                    .await
                                    .map(|h| h.path().to_owned())
                            },
                            Message::NativeSaveAsPath,
                        );
                    }
                    Action::OpenReplayDialog => {
                        return Task::done(Message::ShowReplayDialog);
                    }
                    Action::Quit => {
                        let active_windows: Vec<window::Id> = self
                            .active_dashboard()
                            .popout
                            .keys()
                            .copied()
                            .chain(std::iter::once(self.main_window.id))
                            .collect();
                        return window::collect_window_specs(
                            active_windows,
                            Message::ExitRequested,
                        );
                    }
                    // ── F9c: Tools / W&B submenu ──────────────────────────────────────
                    Action::SubmitToWandb => {
                        // ガード 1: 既に送信中なら no-op
                        if SUBMIT_IN_FLIGHT.load(std::sync::atomic::Ordering::Acquire) {
                            self.notifications
                                .push(Toast::info("送信中です...".to_string()));
                            return Task::none();
                        }
                        // ガード 2: 未認証なら no-op
                        if !self.wandb_auth.authenticated {
                            self.notifications
                                .push(Toast::warn("W&B にログインしてください".to_string()));
                            return Task::none();
                        }
                        // ガード 3: completed run がなければ no-op
                        let Some(run_id) = self.run_buffer.latest_completed.clone() else {
                            self.notifications
                                .push(Toast::warn("送信可能な run がありません".to_string()));
                            return Task::none();
                        };

                        // F9c: モーダルを表示してユーザーに project / run_name / tags を
                        // 確認・編集させてから送信する（P9 spec「メニュー押下 → WandbSubmitModal 表示」）
                        if self.wandb_submit_modal.is_none() {
                            let auth_display =
                                modal::wandb_submit::AuthDisplayState::from(&self.wandb_auth);
                            self.wandb_submit_modal =
                                Some(modal::wandb_submit::WandbSubmitModal::new(
                                    &run_id,
                                    run_id.split('-').nth(1).unwrap_or("strategy"),
                                    auth_display,
                                ));
                        }
                        return Task::none();
                    }
                    Action::SignInWandb => {
                        if self.wandb_signin_modal.is_none() {
                            self.wandb_signin_modal =
                                Some(modal::wandb_signin::WandbSignInModal::new());
                        }
                        return Task::none();
                    }
                    Action::SignOutWandb => {
                        if self.confirm_dialog.is_none() {
                            let dialog = screen::ConfirmDialog::new(
                                "W&B からログアウトします。\nnetrc のエントリが削除されます。"
                                    .to_string(),
                                Box::new(Message::WandbLogoutConfirmed),
                            )
                            .with_confirm_btn_text("ログアウト".to_string());
                            self.confirm_dialog = Some(dialog);
                        }
                        return Task::none();
                    }
                    Action::OpenSubmissionLog => {
                        // F9e: run-buffer/ 配下の全 run を async でスキャンして
                        // 履歴モーダルに渡す。iced update loop をブロックしない
                        // よう Task::perform 経由で list_run_buffer_entries を
                        // 呼ぶ。受信側は Message::WandbSubmissionLogScanned。
                        if self.wandb_submission_log_modal.is_some() {
                            return Task::none();
                        }
                        let base = data::data_path(Some("run-buffer"));
                        return Task::perform(
                            async move { wandb_auth::list_run_buffer_entries(&base).await },
                            Message::WandbSubmissionLogScanned,
                        );
                    }
                    Action::ClearRunBuffer => {
                        if self.confirm_dialog.is_none() {
                            let base = data::data_path(Some("run-buffer"));
                            let dialog = screen::ConfirmDialog::new(
                                format!("run-buffer を削除しますか？\nパス: {}", base.display()),
                                Box::new(Message::ClearRunBufferConfirmed),
                            )
                            .with_confirm_btn_text("削除".to_string());
                            self.confirm_dialog = Some(dialog);
                        }
                        return Task::none();
                    }
                    Action::SwitchMode(target) => {
                        use engine_client::dto::AppMode;
                        // Guard: don't start a mode switch if another dialog is already showing
                        if self.confirm_dialog.is_some() {
                            return Task::none();
                        }
                        let current = app_mode();
                        // Same mode — no-op (menu item should be disabled, but be defensive)
                        if current == target {
                            return Task::none();
                        }
                        // Prevent re-entry: if already switching, no-op
                        let Some(guard) = ModeSwitchGuard::try_acquire() else {
                            return Task::none();
                        };
                        self._mode_switch_guard = Some(guard);

                        // Axis-5: reject if a W&B submit is in progress (統一決定 61, 68)
                        if SUBMIT_IN_FLIGHT.load(std::sync::atomic::Ordering::Acquire) {
                            self._mode_switch_guard = None;
                            let dialog = screen::ConfirmDialog::new(
                                "W&B 送信中です。\n送信が完了してからモードを切り替えてください。"
                                    .to_string(),
                                Box::new(Message::ToggleDialogModal(None)),
                            )
                            .with_confirm_btn_text("閉じる".to_string());
                            self.confirm_dialog = Some(dialog);
                            return Task::none();
                        }

                        match (current, target) {
                            (AppMode::Live, AppMode::Replay) => {
                                // WAL in-flight check: reject if there are unconfirmed orders
                                if has_wal_in_flight_orders() {
                                    self._mode_switch_guard = None;
                                    let dialog = screen::ConfirmDialog::new(
                                        "未約定の注文があります。\nモードを切り替えることができません。"
                                            .to_string(),
                                        Box::new(Message::ToggleDialogModal(None)),
                                    )
                                    .with_confirm_btn_text("閉じる".to_string());
                                    self.confirm_dialog = Some(dialog);
                                    return Task::none();
                                }
                                // Collect window specs for dirty check
                                let mut active_windows: Vec<window::Id> =
                                    self.active_dashboard().popout.keys().copied().collect();
                                active_windows.push(self.main_window.id);
                                return window::collect_window_specs(
                                    active_windows,
                                    move |windows| Message::SwitchModeWithSpecs {
                                        target: AppMode::Replay,
                                        windows,
                                    },
                                );
                            }
                            (AppMode::Replay, AppMode::Live) => {
                                // Send StopReplay then wait for ReplayStopped (with timeout)
                                self.pending_mode_switch = Some(AppMode::Live);
                                if let Some(conn) = self.engine_connection.clone() {
                                    let request_id = uuid::Uuid::new_v4().to_string();
                                    // Send StopReplay. If send fails immediately (broken socket),
                                    // abort without waiting for the 5-second timeout.
                                    let send_task = Task::perform(
                                        async move {
                                            conn.send(engine_client::dto::Command::StopReplay {
                                                request_id,
                                            })
                                            .await
                                        },
                                        |result| match result {
                                            Ok(()) => Message::Noop,
                                            Err(_) => Message::ModeSwitchSendFailed,
                                        },
                                    );
                                    // 5-second timeout
                                    let timeout_task = Task::perform(
                                        async {
                                            tokio::time::sleep(std::time::Duration::from_secs(5))
                                                .await;
                                        },
                                        |_| Message::ModeSwitchStopTimeout,
                                    );
                                    return Task::batch([send_task, timeout_task]);
                                } else {
                                    // No engine connection — just restart directly
                                    self.pending_mode_switch = None;
                                    return self.restart_with_mode(AppMode::Live);
                                }
                            }
                            _ => {
                                self._mode_switch_guard = None;
                                return Task::none();
                            }
                        }
                    }
                }
            }
            // F6a: replay モードで `.py` を Open dialog で選択した結果を受け取り、
            // engine に `Command::LoadStrategyScenario` を発行する。
            Message::NativeOpenStrategyPicked(picked) => {
                let Some(path) = picked else {
                    return Task::none();
                };
                // モードガード: live で誤って `.py` が選ばれても無視する。
                if app_mode() != engine_client::dto::AppMode::Replay {
                    log::warn!("NativeOpenStrategyPicked received outside replay mode — dropping");
                    return Task::none();
                }
                let Some(conn) = self.engine_connection.as_ref().cloned() else {
                    self.notifications.push(Toast::error(
                        "engine に接続されていないため SCENARIO を読み込めません".to_string(),
                    ));
                    return Task::none();
                };
                let path_str = path.to_string_lossy().into_owned();
                return Task::perform(
                    async move {
                        let request_id = uuid::Uuid::new_v4().to_string();
                        conn.send(engine_client::dto::Command::LoadStrategyScenario {
                            request_id,
                            path: path_str,
                        })
                        .await
                        .map_err(|e| e.to_string())
                    },
                    |res| match res {
                        Ok(()) => Message::Noop,
                        Err(err) => Message::OrderToast(Toast::error(format!(
                            "戦略ファイルの読み込み要求に失敗しました: {err}"
                        ))),
                    },
                );
            }
            // F6a: SCENARIO 抽出成功 → ReplayFormModal を prefill。modal が
            // 開いていなければ新規生成する（GUI で `Open` 直後はまだ modal が
            // 出ていない正規ルート）。`current_path` は両ケース（scenario
            // None / Some）でセットする。
            Message::StrategyScenarioLoadedEvent { path, scenario } => {
                // M-7: poison recovery via `into_inner()` so a prior panic does
                // not silently drop the new path.
                let mut guard = match CURRENT_PATH.lock() {
                    Ok(g) => g,
                    Err(poisoned) => poisoned.into_inner(),
                };
                *guard = Some(path.clone());
                drop(guard);
                let form = self
                    .replay_form_modal
                    .get_or_insert_with(modal::replay_form::ReplayFormModal::default);
                match scenario {
                    Some(value) => form.prefill_from_scenario(path, &value),
                    None => form.set_strategy_file_only(path),
                }
                return Task::none();
            }
            // F6a: SCENARIO 抽出失敗 → トースト表示。`current_path` は更新しない。
            Message::StrategyScenarioLoadFailedEvent { path, reason } => {
                let name = path
                    .file_name()
                    .map(|n| n.to_string_lossy().into_owned())
                    .unwrap_or_else(|| path.to_string_lossy().into_owned());
                self.notifications.push(Toast::error(format!(
                    "{name} を読み込めませんでした: {reason}"
                )));
                return Task::none();
            }
            // Phase 8.1c: Replay 起動フォームを開く
            Message::ShowReplayDialog => {
                self.replay_form_modal = Some(modal::replay_form::ReplayFormModal::default());
                return Task::none();
            }
            // Phase 8.1c: Replay フォーム内部メッセージの処理
            Message::ReplayFormMsg(modal::replay_form::Message::PickStrategyFile) => {
                return Task::perform(
                    async {
                        rfd::AsyncFileDialog::new()
                            .add_filter("Python", &["py"])
                            .set_title("戦略ファイルを選択")
                            .pick_file()
                            .await
                            .map(|h| h.path().to_owned())
                    },
                    |path| {
                        Message::ReplayFormMsg(modal::replay_form::Message::StrategyFilePicked(
                            path,
                        ))
                    },
                );
            }
            Message::ReplayFormMsg(msg) => {
                if let Some(form) = self.replay_form_modal.as_mut() {
                    match form.update(msg) {
                        Some(modal::replay_form::Action::Cancel) => {
                            self.replay_form_modal = None;
                        }
                        Some(modal::replay_form::Action::Submit {
                            instrument_id,
                            start_date,
                            end_date,
                            granularity,
                            strategy_file,
                            initial_cash,
                        }) => {
                            self.replay_form_modal = None;
                            if let Some(conn) = self.engine_connection.as_ref().cloned() {
                                let strategy_file_str =
                                    strategy_file.to_string_lossy().into_owned();
                                let gran_dto = granularity.to_dto();
                                return Task::perform(
                                    async move {
                                        let load_req_id = uuid::Uuid::new_v4().to_string();
                                        conn.send(engine_client::dto::Command::LoadReplayData {
                                            request_id: load_req_id,
                                            instrument_id: instrument_id.clone(),
                                            start_date: start_date.clone(),
                                            end_date: end_date.clone(),
                                            granularity: gran_dto.clone(),
                                        })
                                        .await
                                        .map_err(|e| e.to_string())?;
                                        let start_req_id = uuid::Uuid::new_v4().to_string();
                                        conn.send(engine_client::dto::Command::StartEngine {
                                            request_id: start_req_id,
                                            engine: engine_client::dto::EngineKind::Backtest,
                                            strategy_id: "user-strategy".to_string(),
                                            config: engine_client::dto::EngineStartConfig {
                                                instrument_id,
                                                start_date,
                                                end_date,
                                                initial_cash: initial_cash.to_string(),
                                                granularity: gran_dto,
                                                strategy_file: Some(strategy_file_str),
                                                strategy_init_kwargs: None,
                                            },
                                        })
                                        .await
                                        .map_err(|e| e.to_string())?;
                                        Ok::<(), String>(())
                                    },
                                    |res| match res {
                                        Ok(()) => Message::OrderToast(Toast::info(
                                            "Replay を開始しました".to_string(),
                                        )),
                                        Err(e) => Message::OrderToast(Toast::error(format!(
                                            "Replay 起動失敗: {e}"
                                        ))),
                                    },
                                );
                            }
                        }
                        Some(modal::replay_form::Action::PickStrategyFile) => {
                            // PickStrategyFile は上の専用アームで処理される
                        }
                        None => {}
                    }
                }
                return Task::none();
            }
            Message::NativeSaveAsPath(Some(path)) => {
                if self.confirm_dialog.is_some() {
                    return Task::none();
                }
                // F5: if the target file already exists, ask the user to confirm overwrite.
                if path.exists() {
                    let file_name = path
                        .file_name()
                        .map(|n| n.to_string_lossy().into_owned())
                        .unwrap_or_else(|| path.display().to_string());
                    let dialog = screen::ConfirmDialog::new(
                        format!("「{file_name}」はすでに存在します。上書きしますか？"),
                        // Carry path in the message so no shared slot is needed.
                        Box::new(Message::ConfirmSaveAsOverwrite { path }),
                    )
                    .with_confirm_btn_text("上書き保存".to_string());
                    self.confirm_dialog = Some(dialog);
                    return Task::none();
                }
                let mut active_windows: Vec<window::Id> =
                    self.active_dashboard().popout.keys().copied().collect();
                active_windows.push(self.main_window.id);
                return window::collect_window_specs(active_windows, move |windows| {
                    Message::NativeSaveAsWithSpecs {
                        path: path.clone(),
                        windows,
                    }
                });
            }
            Message::ConfirmSaveAsOverwrite { path } => {
                // M-3: guard against arriving without an open dialog (race condition).
                // Mirrors the confirm_dialog.is_none() pattern used in NativeOpenFilePendingCheck
                // and ExitRequested to prevent double-processing.
                if self.confirm_dialog.is_none() {
                    return Task::none();
                }
                self.confirm_dialog = None;
                // Proceed to collect specs with the path carried in the message.
                let mut active_windows: Vec<window::Id> =
                    self.active_dashboard().popout.keys().copied().collect();
                active_windows.push(self.main_window.id);
                return window::collect_window_specs(active_windows, move |windows| {
                    Message::NativeSaveAsWithSpecs {
                        path: path.clone(),
                        windows,
                    }
                });
            }
            Message::NativeSaveAsPath(None) => {
                // Save As dialog cancelled. If a "save-then-open" flow was in progress
                // (SaveAndOpenFile triggered this when CURRENT_PATH was None), restore the
                // confirm dialog so the user can choose Discard or retry Save.
                if self.pending_open_file.is_some() {
                    let dialog = screen::ConfirmDialog::new(
                        "未保存の変更があります。".to_string(),
                        Box::new(Message::DiscardAndOpenFile),
                    )
                    .with_confirm_btn_text("破棄して開く".to_string())
                    .with_save_action(Message::SaveAndOpenFile, "保存して開く".to_string());
                    self.confirm_dialog = Some(dialog);
                }
                return Task::none();
            }
            Message::NativeSaveAsWithSpecs { path, windows } => {
                // H-5: build JSON once and reuse for both the user path and saved-state.json,
                // avoiding a second build_state_json call inside save_state_to_disk.
                let Some(json) = self.build_state_json(&windows) else {
                    log::warn!(
                        "[NativeSaveAsWithSpecs] build_state_json returned None \
                         (replay mode or APP_MODE uninitialised) — skipping write to {}",
                        path.display()
                    );
                    return Task::none();
                };
                // H-3: offload both blocking writes to a tokio async task so the
                // iced update loop is never blocked by disk I/O.
                let json_bytes = json.into_bytes();
                let user_path = path.clone();
                let saved_state_path = data::SAVED_STATE_PATH;
                return Task::perform(
                    async move {
                        // A-3: write to user-specified path first; only on success
                        // also write saved-state.json (keep auto-restore slot in sync).
                        match tokio::fs::write(&user_path, &json_bytes).await {
                            Ok(()) => {
                                let saved_state_ok =
                                    tokio::fs::write(saved_state_path, &json_bytes)
                                        .await
                                        .map_err(|e| {
                                            log::warn!(
                                                "[NativeSaveComplete] failed to write \
                                                 saved-state.json: {e}"
                                            );
                                        })
                                        .is_ok();
                                (user_path, json_bytes, None, saved_state_ok)
                            }
                            Err(e) => (user_path, json_bytes, Some(e.kind()), false),
                        }
                    },
                    |(user_path, json_bytes, error_kind, saved_state_ok)| {
                        Message::NativeSaveComplete {
                            user_path,
                            json_bytes,
                            error_kind,
                            saved_state_ok,
                        }
                    },
                );
            }
            // H-3: completion handler for the async NativeSaveAsWithSpecs Task::perform.
            Message::NativeSaveComplete {
                user_path,
                json_bytes,
                error_kind,
                saved_state_ok,
            } => {
                match error_kind {
                    None => {
                        // A-7: update dirty baseline so is_dirty() returns false.
                        self.last_saved_bytes = Some(json_bytes);
                        // Record as current document.
                        match CURRENT_PATH.lock() {
                            Ok(mut guard) => *guard = Some(user_path.clone()),
                            Err(poisoned) => *poisoned.into_inner() = Some(user_path.clone()),
                        }
                        log::info!("Saved state to {}", user_path.display());
                        self.notifications.push(Toast::info(format!(
                            "保存しました: {}",
                            user_path.display()
                        )));
                        // Notify if auto-restore slot (saved-state.json) failed — the named doc
                        // is safe, but next startup will restore from an older state.
                        if !saved_state_ok {
                            self.notifications.push(Toast::error(
                                "自動復元スロット (saved-state.json) の更新に失敗しました。\
                                 次回起動時の復元が古い状態になる可能性があります。"
                                    .to_string(),
                            ));
                        }
                        // If SaveAndOpenFile triggered this save (CURRENT_PATH=None path),
                        // complete the open now that the current state is safely written.
                        if let Some((pending_json, pending_path, _windows)) =
                            self.pending_open_file.take()
                        {
                            if let Err(e) =
                                data::write_json_to_file(&pending_json, data::SAVED_STATE_PATH)
                            {
                                log::warn!("Failed to write imported state: {e}");
                                self.notifications.push(Toast::error(format!(
                                    "ファイルの適用に失敗しました: {e}"
                                )));
                                return Task::none();
                            }
                            match CURRENT_PATH.lock() {
                                Ok(mut guard) => *guard = Some(pending_path),
                                Err(poisoned) => *poisoned.into_inner() = Some(pending_path),
                            }
                            return self.restart();
                        }
                    }
                    Some(kind) => {
                        // BC-5: classify as IoError → WARN level.
                        log_save_error(&SaveError::IoError(kind), &user_path);
                        self.notifications
                            .push(Toast::error("保存に失敗しました".to_string()));
                        // If SaveAndOpenFile triggered this save (CURRENT_PATH=None path),
                        // pending_open_file is still set but confirm_dialog is None.
                        // Restore the dialog so the user can retry or discard; without this
                        // the next Open skips F4 because pending_open_file.is_some() blocks
                        // the dirty-check condition at NativeOpenFilePendingCheck.
                        if self.pending_open_file.is_some() {
                            let dialog = screen::ConfirmDialog::new(
                                "未保存の変更があります。".to_string(),
                                Box::new(Message::DiscardAndOpenFile),
                            )
                            .with_confirm_btn_text("破棄して開く".to_string())
                            .with_save_action(Message::SaveAndOpenFile, "保存して開く".to_string());
                            self.confirm_dialog = Some(dialog);
                        }
                    }
                }
                return Task::none();
            }
            Message::NativeOpenFileCancelled => {
                return Task::none();
            }
            Message::NativeOpenFileApply { json, path } => {
                // Validate the JSON first; reject bad files early before any state change.
                match serde_json::from_str::<data::State>(&json) {
                    Ok(_) => {
                        // F4 fix: collect real window specs before the dirty check so that
                        // last_saved_bytes (which includes window positions) compares correctly.
                        // An empty HashMap would produce main_window_spec=None, always appearing
                        // dirty even immediately after a save.
                        let mut active_windows: Vec<window::Id> =
                            self.active_dashboard().popout.keys().copied().collect();
                        active_windows.push(self.main_window.id);
                        return window::collect_window_specs(active_windows, move |windows| {
                            Message::NativeOpenFilePendingCheck {
                                json: json.clone(),
                                path: path.clone(),
                                windows,
                            }
                        });
                    }
                    Err(e) => {
                        self.notifications
                            .push(Toast::error(format!("無効な設定ファイルです: {e}")));
                        return Task::none();
                    }
                }
            }
            Message::NativeOpenFilePendingCheck {
                json,
                path,
                windows,
            } => {
                // HIGH fix: another dialog is already visible — silently drop this request
                // to prevent F4 bypass via overlapping dialogs.
                if self.confirm_dialog.is_some() {
                    return Task::none();
                }
                // F4: dirty check with real window specs (avoids false positives from empty HashMap).
                if self.is_dirty(&windows) && self.pending_open_file.is_none() {
                    // Store windows so SaveAndOpenFile can build state JSON for the named doc.
                    self.pending_open_file = Some((json, path, windows));
                    let dialog = screen::ConfirmDialog::new(
                        "未保存の変更があります。".to_string(),
                        Box::new(Message::DiscardAndOpenFile),
                    )
                    .with_confirm_btn_text("破棄して開く".to_string())
                    .with_save_action(Message::SaveAndOpenFile, "保存して開く".to_string());
                    self.confirm_dialog = Some(dialog);
                    return Task::none();
                }
                if let Err(e) = data::write_json_to_file(&json, data::SAVED_STATE_PATH) {
                    // BC-5: write failure is an OS-level I/O error → WARN level.
                    log::warn!("Failed to write imported state: {e}");
                    self.notifications
                        .push(Toast::error(format!("ファイルの適用に失敗しました: {e}")));
                    return Task::none();
                }
                match CURRENT_PATH.lock() {
                    Ok(mut guard) => *guard = Some(path),
                    Err(poisoned) => *poisoned.into_inner() = Some(path),
                }
                return self.restart();
            }
            Message::DiscardAndOpenFile => {
                self.confirm_dialog = None;
                let Some((json, path, _windows)) = self.pending_open_file.take() else {
                    return Task::none();
                };
                if let Err(e) = data::write_json_to_file(&json, data::SAVED_STATE_PATH) {
                    // BC-5: write failure is an OS-level I/O error → WARN level.
                    log::warn!("Failed to write imported state: {e}");
                    self.notifications
                        .push(Toast::error(format!("ファイルの適用に失敗しました: {e}")));
                    return Task::none();
                }
                match CURRENT_PATH.lock() {
                    Ok(mut guard) => *guard = Some(path),
                    Err(poisoned) => *poisoned.into_inner() = Some(path),
                }
                return self.restart();
            }
            Message::SaveAndOpenFile => {
                // User chose "保存して開く" — save the current document first, then load the new
                // file. BC-5: if any save step fails, abort and restore the dialog.
                self.confirm_dialog = None;
                let Some((json, new_path, windows)) = self.pending_open_file.take() else {
                    return Task::none();
                };
                let current_path = match CURRENT_PATH.lock() {
                    Ok(g) => g.clone(),
                    Err(poisoned) => poisoned.into_inner().clone(),
                };
                if let Some(p) = &current_path {
                    match self.build_state_json(&windows) {
                        Some(current_json) => {
                            if let Err(e) = std::fs::write(p, current_json.as_bytes()) {
                                // BC-5: abort open; named doc save failed.
                                log_save_error(&SaveError::IoError(e.kind()), p);
                                self.notifications.push(Toast::error(
                                    "保存に失敗しました。再試行してください。".to_string(),
                                ));
                                self.pending_open_file = Some((json, new_path, windows));
                                let dialog = screen::ConfirmDialog::new(
                                    "未保存の変更があります。".to_string(),
                                    Box::new(Message::DiscardAndOpenFile),
                                )
                                .with_confirm_btn_text("破棄して開く".to_string())
                                .with_save_action(
                                    Message::SaveAndOpenFile,
                                    "保存して開く".to_string(),
                                );
                                self.confirm_dialog = Some(dialog);
                                return Task::none();
                            }
                            // MEDIUM fix: update dirty baseline so a subsequent Quit/Open after a
                            // failed saved-state.json write does not re-trigger a spurious dialog.
                            self.last_saved_bytes = Some(current_json.into_bytes());
                        }
                        None => { /* replay mode — no JSON to save, proceed with open */ }
                    }
                } else {
                    // No named document: open Save As dialog so the user can name the current
                    // state. Keep pending_open_file; NativeSaveComplete will pick it up and
                    // complete the open after the user-chosen path is written.
                    self.pending_open_file = Some((json, new_path, windows));
                    return Task::perform(
                        async {
                            rfd::AsyncFileDialog::new()
                                .add_filter("JSON", &["json"])
                                .set_file_name("saved-state.json")
                                .set_title("現在の設定を保存")
                                .save_file()
                                .await
                                .map(|handle| handle.path().to_owned())
                        },
                        Message::NativeSaveAsPath,
                    );
                }
                // R2-M1 (revert of R6 F-M4): saved-state.json is the file that
                // `Flowsurface::new()` reads on restart. If this mirror write fails
                // we MUST NOT update CURRENT_PATH and MUST NOT restart() — doing so
                // would reload the OLD saved-state.json while CURRENT_PATH points to
                // the NEW file, so the next Ctrl+S would silently overwrite the new
                // file with the old layout. Abort cleanly with warn + toast and let
                // the user retry from the menu.
                return match data::write_json_to_file(&json, data::SAVED_STATE_PATH) {
                    Ok(()) => {
                        match CURRENT_PATH.lock() {
                            Ok(mut guard) => *guard = Some(new_path),
                            Err(poisoned) => *poisoned.into_inner() = Some(new_path),
                        }
                        self.restart()
                    }
                    Err(e) => {
                        log::warn!(
                            "[SaveAndOpenFile] failed to write saved-state.json after named-doc save: {e} — aborting open to keep CURRENT_PATH consistent"
                        );
                        self.notifications.push(Toast::error(
                            "saved-state.json への書き込みに失敗しました。開く処理を中止します。"
                                .to_string(),
                        ));
                        Task::none()
                    }
                };
            }
            // F7: SwitchModeWithSpecs — window specs collected, perform dirty check for live→replay
            Message::SwitchModeWithSpecs { target, windows } => {
                use engine_client::dto::AppMode;
                // If mode switch guard was released (e.g. stale message), ignore.
                if self._mode_switch_guard.is_none() {
                    return Task::none();
                }
                if app_mode() == AppMode::Live && self.is_dirty(&windows) {
                    self.pending_mode_switch = Some(target);
                    let dialog = screen::ConfirmDialog::new(
                        "未保存の変更があります。".to_string(),
                        Box::new(Message::DiscardAndSwitchMode),
                    )
                    .with_confirm_btn_text("破棄してモード切替".to_string())
                    .with_save_action(Message::SaveAndSwitchMode, "保存してモード切替".to_string());
                    self.confirm_dialog = Some(dialog);
                    return Task::none();
                }
                // Not dirty: save then restart. Abort if save fails (plan: 保存失敗 → 切替中止).
                if !self.save_state_to_disk(&windows) {
                    self._mode_switch_guard = None;
                    self.notifications.push(Toast::error(
                        "保存に失敗しました。モード切替を中止します。".to_string(),
                    ));
                    return Task::none();
                }
                return self.restart_with_mode(target);
            }
            // F7: user chose "破棄してモード切替"
            Message::DiscardAndSwitchMode => {
                self.confirm_dialog = None;
                let Some(target) = self.pending_mode_switch.take() else {
                    return Task::none();
                };
                return self.restart_with_mode(target);
            }
            // F7: user chose "保存してモード切替" — collect window specs for save, then restart
            Message::SaveAndSwitchMode => {
                self.confirm_dialog = None;
                let Some(target) = self.pending_mode_switch.take() else {
                    return Task::none();
                };
                // Collect current window specs for a proper save.
                // IMPORTANT: must NOT re-route through SwitchModeWithSpecs here because that
                // path re-checks is_dirty() — since we haven't saved yet it would still be true,
                // causing an infinite dialog loop. Route through SwitchModeSaveComplete instead
                // to unconditionally save and restart (F7 review fix 2026-05-04).
                let mut active_windows: Vec<window::Id> =
                    self.active_dashboard().popout.keys().copied().collect();
                active_windows.push(self.main_window.id);
                return window::collect_window_specs(active_windows, move |windows| {
                    Message::SwitchModeSaveComplete { target, windows }
                });
            }
            // F7: window specs collected for the "保存してモード切替" path — save then restart.
            Message::SwitchModeSaveComplete { target, windows } => {
                // Abort if save fails (plan: 保存失敗 → 切替中止).
                if !self.save_state_to_disk(&windows) {
                    self._mode_switch_guard = None;
                    self.notifications.push(Toast::error(
                        "保存に失敗しました。モード切替を中止します。".to_string(),
                    ));
                    return Task::none();
                }
                return self.restart_with_mode(target);
            }
            // F7: ReplayStopped event received — proceed with restart_with_mode
            Message::ModeSwitchStopAcked => {
                let Some(target) = self.pending_mode_switch.take() else {
                    // Stale event (e.g. timeout already handled it) — ignore
                    return Task::none();
                };
                return self.restart_with_mode(target);
            }
            // F7: 5-second StopReplay timeout — send ForceStopReplay fallback
            Message::ModeSwitchStopTimeout => {
                if self.pending_mode_switch.is_none() {
                    // Already handled (ReplayStopped arrived before timeout) — ignore
                    return Task::none();
                };
                if let Some(conn) = self.engine_connection.clone() {
                    let request_id = uuid::Uuid::new_v4().to_string();
                    let force_task = Task::perform(
                        async move {
                            conn.send(engine_client::dto::Command::ForceStopReplay { request_id })
                                .await
                        },
                        |result| match result {
                            Ok(()) => Message::Noop,
                            Err(_) => Message::ModeSwitchSendFailed,
                        },
                    );
                    let timeout_task = Task::perform(
                        async {
                            tokio::time::sleep(std::time::Duration::from_secs(2)).await;
                        },
                        |_| Message::ModeSwitchForceStopTimeout,
                    );
                    return Task::batch([force_task, timeout_task]);
                } else {
                    // No connection — release guard and show error dialog
                    self.pending_mode_switch = None;
                    self._mode_switch_guard = None;
                    let dialog = screen::ConfirmDialog::new(
                        "モード切替に失敗しました。\nエンジンとの接続が切れています。".to_string(),
                        Box::new(Message::ToggleDialogModal(None)),
                    )
                    .with_confirm_btn_text("閉じる".to_string());
                    self.confirm_dialog = Some(dialog);
                    return Task::none();
                }
            }
            // F7: ForceStopReplay also timed out — release guard and show error
            Message::ModeSwitchForceStopTimeout => {
                if self.pending_mode_switch.take().is_none() {
                    // Already handled (ReplayStopped arrived before timeout) — ignore
                    return Task::none();
                }
                self._mode_switch_guard = None;
                let dialog = screen::ConfirmDialog::new(
                    "モード切替に失敗しました。\nエンジンが応答しません。".to_string(),
                    Box::new(Message::ToggleDialogModal(None)),
                )
                .with_confirm_btn_text("閉じる".to_string());
                self.confirm_dialog = Some(dialog);
                return Task::none();
            }
            // F7: send() returned Err — socket is broken; abort mode switch immediately without
            // waiting for the 5-second (StopReplay) or 2-second (ForceStopReplay) timeout.
            // Stale timeout messages that fire later are ignored because pending_mode_switch is None.
            Message::ModeSwitchSendFailed => {
                if self.pending_mode_switch.take().is_some() {
                    self._mode_switch_guard = None;
                    let dialog = screen::ConfirmDialog::new(
                        "モード切替に失敗しました。\nエンジンとの接続が切れています。".to_string(),
                        Box::new(Message::ToggleDialogModal(None)),
                    )
                    .with_confirm_btn_text("閉じる".to_string());
                    self.confirm_dialog = Some(dialog);
                }
                return Task::none();
            }
            // F7: EngineBusy for StopReplay/ForceStopReplay — abort mode switch immediately.
            // This handles the 5-axis matrix row "replay→live, EngineBusy" without waiting
            // for the 5s timeout. The stale ModeSwitchStopTimeout fires later but is ignored
            // because pending_mode_switch will be None at that point.
            Message::ModeSwitchEngineBusy(reason) => {
                if self.pending_mode_switch.take().is_some() {
                    self._mode_switch_guard = None;
                    let dialog = screen::ConfirmDialog::new(
                        format!("モード切替を中止しました。\nエンジンがビジー状態です: {reason}"),
                        Box::new(Message::ToggleDialogModal(None)),
                    )
                    .with_confirm_btn_text("閉じる".to_string());
                    self.confirm_dialog = Some(dialog);
                } else {
                    // No pending switch — fall back to warn toast
                    self.notifications.push(Toast::warn(format!(
                        "操作を受け付けられませんでした: {reason}"
                    )));
                }
                return Task::none();
            }
            // F7: true no-op — used to discard fire-and-forget Task completions.
            Message::Noop => return Task::none(),
            // N1.12: ExecutionMarker → broadcast overlay dot to all Kline charts
            Message::ExecutionMarkerReceived {
                side,
                price,
                ts_event_ms,
            } => {
                let price_f32 = price.parse::<f32>().unwrap_or(0.0);
                let data = crate::chart::kline::ExecutionMarkerData {
                    side,
                    price_f32,
                    ts_event_ms,
                };
                let main_window = self.main_window.id;
                self.active_dashboard_mut()
                    .distribute_execution_markers(main_window, data);
            }
            // N1.12: StrategySignal → broadcast overlay diamond to all Kline charts
            Message::StrategySignalReceived {
                signal_kind,
                price,
                ts_event_ms,
                tag,
            } => {
                let price_f32 = price.and_then(|p| p.parse::<f32>().ok());
                let data = crate::chart::kline::StrategySignalData {
                    signal_kind,
                    price_f32,
                    ts_event_ms,
                    tag,
                };
                let main_window = self.main_window.id;
                self.active_dashboard_mut()
                    .distribute_strategy_signals(main_window, data);
            }
            // Phase U0: OrderAccepted — reset submitting flag + toast
            Message::OrderAccepted {
                client_order_id,
                venue_order_id,
            } => {
                let main_window = self.main_window.id;
                self.active_dashboard_mut()
                    .notify_order_accepted(main_window, &client_order_id);
                let vid = venue_order_id.unwrap_or_default();
                self.notifications.push(Toast::info(format!(
                    "注文受付: {client_order_id} (venue: {vid})"
                )));

                // live モード（tachibana ログイン済み）のときのみ自動更新。
                // replay バックテストも OrderAccepted を emit するため、このガードは必須。
                if !self.tachibana_state.is_ready() {
                    return Task::none();
                }

                let Some(conn) = self.engine_connection.as_ref().cloned() else {
                    return Task::none();
                };

                let refresh_orders = if self.order_list_request_id.is_none() {
                    let req_id = uuid::Uuid::new_v4().to_string();
                    self.order_list_request_id = Some(req_id.clone());
                    let main_window = self.main_window.id;
                    self.active_dashboard_mut()
                        .distribute_order_list_loading(main_window, true);
                    let conn_for_orders = conn.clone();
                    Task::perform(
                        async move {
                            conn_for_orders
                                .send(engine_client::dto::Command::GetOrderList {
                                    request_id: req_id,
                                    venue: crate::TACHIBANA_VENUE_NAME.to_string(),
                                    filter: engine_client::dto::OrderListFilter {
                                        status: None,
                                        instrument_id: None,
                                        date: None,
                                    },
                                })
                                .await
                                .map_err(|e| e.to_string())
                        },
                        Message::OrderListSendCompleted,
                    )
                } else {
                    Task::none()
                };

                let refresh_buying_power = if self.buying_power_request_id.is_none() {
                    let req_id = uuid::Uuid::new_v4().to_string();
                    self.buying_power_request_id = Some(req_id.clone());
                    let main_window = self.main_window.id;
                    self.active_dashboard_mut()
                        .distribute_buying_power_loading(main_window, true);
                    let req_id_for_err = req_id.clone();
                    Task::perform(
                        async move {
                            conn.send(engine_client::dto::Command::GetBuyingPower {
                                request_id: req_id,
                                venue: crate::TACHIBANA_VENUE_NAME.to_string(),
                            })
                            .await
                            .map_err(|e| e.to_string())
                        },
                        move |res| match res {
                            Ok(()) => Message::BuyingPowerSendCompleted(Ok(())),
                            Err(err) => Message::IpcError {
                                request_id: Some(req_id_for_err),
                                code: "send_failed".to_string(),
                                message: err,
                            },
                        },
                    )
                } else {
                    Task::none()
                };

                return Task::batch([refresh_orders, refresh_buying_power]);
            }
            // Phase U0: OrderRejected — reset submitting flag with reason + toast
            Message::OrderRejected {
                client_order_id,
                reason,
            } => {
                let main_window = self.main_window.id;
                self.active_dashboard_mut().notify_order_rejected(
                    main_window,
                    &client_order_id,
                    reason.clone(),
                );
                self.notifications.push(Toast::error(format!(
                    "注文拒否: {client_order_id} {reason}"
                )));
            }
            // ── Phase U0: 注文確認ダイアログ → ConfirmSubmit ──────────────────
            Message::ConfirmOrderEntrySubmit => {
                self.confirm_dialog = None;
                let main_window_id = self.main_window.id;
                let dashboard = self.active_dashboard_mut();
                if let Some((window_id, focused_pane)) = dashboard.focus
                    && window_id == main_window_id
                {
                    // Dispatch ConfirmSubmit to the focused pane through the
                    // standard Pane → PaneEvent → OrderEntryMsg path so that
                    // the `OrderEntryAction` handler picks up the resulting
                    // SubmitOrder and fires the IPC call.
                    return iced::Task::done(Message::Dashboard {
                        layout_id: None,
                        event: dashboard::Message::Pane(
                            main_window_id,
                            dashboard::pane::Message::PaneEvent(
                                focused_pane,
                                dashboard::pane::Event::OrderEntryMsg(
                                    crate::screen::dashboard::panel::order_entry::Message::ConfirmSubmit,
                                ),
                            ),
                        ),
                    });
                }
                self.notifications.push(crate::widget::toast::Toast::error(
                    "注文を確定するには発注ペインをクリックしてください".to_string(),
                ));
                return Task::none();
            }
            // ── Phase U1: 注文取消確認ダイアログ → CancelOrder IPC ─────────────
            Message::ConfirmCancelOrder {
                client_order_id,
                venue_order_id,
            } => {
                self.confirm_dialog = None;
                if let Some(conn) = self.engine_connection.as_ref().cloned() {
                    return Task::perform(
                        async move {
                            conn.send(engine_client::dto::Command::CancelOrder {
                                request_id: uuid::Uuid::new_v4().to_string(),
                                venue: crate::TACHIBANA_VENUE_NAME.to_string(),
                                client_order_id,
                                venue_order_id,
                            })
                            .await
                            .map_err(|e| e.to_string())
                        },
                        |res| match res {
                            Ok(()) => Message::OrderToast(Toast::info("注文取消送信".to_string())),
                            Err(err) => {
                                Message::OrderToast(Toast::error(format!("注文取消失敗: {err}")))
                            }
                        },
                    );
                }
                self.notifications
                    .push(Toast::error("注文取消失敗: エンジン未接続".to_string()));
                return Task::none();
            }
            // ── Phase U0: 第二暗証番号 modal ──────────────────────────────────
            Message::SecondPasswordRequired(request_id) => {
                self.second_password_modal =
                    Some(modal::second_password::SecondPasswordModal::new(request_id));
            }
            Message::DismissSecondPasswordModal => {
                self.second_password_modal = None;
                if let Some(conn) = self.engine_connection.as_ref().cloned() {
                    return Task::perform(
                        async move {
                            conn.send(engine_client::dto::Command::ForgetSecondPassword)
                                .await
                                .map_err(|e| e.to_string())
                        },
                        |res| match res {
                            Ok(()) => Message::OrderToast(Toast::info(
                                "第二暗証番号を解除しました".to_string(),
                            )),
                            Err(err) => Message::OrderToast(Toast::error(format!(
                                "ForgetSecondPassword 送信失敗: {err}"
                            ))),
                        },
                    );
                }
            }
            Message::SecondPasswordModalMsg(msg) => {
                if let Some(modal) = &mut self.second_password_modal {
                    match modal.update(msg) {
                        Some(modal::second_password::Action::Submit { value }) => {
                            let request_id = modal.request_id.clone();
                            self.second_password_modal = None;
                            if let Some(conn) = self.engine_connection.as_ref().cloned() {
                                return Task::perform(
                                    async move {
                                        conn.send(engine_client::dto::Command::SetSecondPassword {
                                            request_id,
                                            value,
                                        })
                                        .await
                                        .map_err(|e| e.to_string())
                                    },
                                    |res| match res {
                                        Ok(()) => Message::OrderToast(Toast::info(
                                            "第二暗証番号を送信しました".to_string(),
                                        )),
                                        Err(err) => Message::OrderToast(Toast::error(format!(
                                            "第二暗証番号送信失敗: {err}"
                                        ))),
                                    },
                                );
                            }
                        }
                        Some(modal::second_password::Action::Cancel) => {
                            self.second_password_modal = None;
                            if let Some(conn) = self.engine_connection.as_ref().cloned() {
                                return Task::perform(
                                    async move {
                                        conn.send(engine_client::dto::Command::ForgetSecondPassword)
                                            .await
                                            .map_err(|e| e.to_string())
                                    },
                                    |res| match res {
                                        Ok(()) => Message::OrderToast(Toast::info(
                                            "第二暗証番号を解除しました".to_string(),
                                        )),
                                        Err(err) => Message::OrderToast(Toast::error(format!(
                                            "ForgetSecondPassword 送信失敗: {err}"
                                        ))),
                                    },
                                );
                            }
                        }
                        None => {}
                    }
                }
            }
            Message::SetTimezone(tz) => {
                self.timezone = tz;
            }
            Message::ScaleFactorChanged(value) => {
                self.ui_scale_factor = value;
            }
            Message::ToggleTradeFetch(checked) => {
                self.layout_manager
                    .iter_dashboards_mut()
                    .for_each(|dashboard| {
                        dashboard.toggle_trade_fetch(checked, &self.main_window);
                    });

                if checked {
                    self.confirm_dialog = None;
                }
            }
            Message::ToggleDialogModal(dialog) => {
                // Fix: when dismissing (None), clear any parked dirty-check state so
                // a subsequent Open does not skip the confirm dialog (Issue 2 fix).
                if dialog.is_none() {
                    // F5/M: if we're dismissing the overwrite confirm during a save-then-open
                    // flow, restore the dirty-confirm so the user can retry or discard instead
                    // of silently losing the pending open target.
                    if self
                        .confirm_dialog
                        .as_ref()
                        .is_some_and(|d| d.on_save.is_none())
                        && self.pending_open_file.is_some()
                    {
                        self.confirm_dialog = Some(
                            screen::ConfirmDialog::new(
                                "未保存の変更があります。".to_string(),
                                Box::new(Message::DiscardAndOpenFile),
                            )
                            .with_confirm_btn_text("破棄して開く".to_string())
                            .with_save_action(Message::SaveAndOpenFile, "保存して開く".to_string()),
                        );
                        return Task::none();
                    }
                    self.pending_open_file = None;
                    self.pending_exit_windows = None;
                    // F7: release mode-switch guard when the dirty-confirm dialog is
                    // dismissed via backdrop click (ToggleDialogModal(None)), so the
                    // next SwitchMode attempt is not permanently blocked.
                    self.pending_mode_switch = None;
                    self._mode_switch_guard = None;
                }
                self.confirm_dialog = dialog;
            }
            Message::Layouts(message) => {
                let action = self.layout_manager.update(message);

                match action {
                    Some(modal::layout_manager::Action::Select(layout)) => {
                        let active_popout_keys = self
                            .active_dashboard()
                            .popout
                            .keys()
                            .copied()
                            .collect::<Vec<_>>();

                        let window_tasks = Task::batch(
                            active_popout_keys
                                .iter()
                                .map(|&popout_id| window::close::<window::Id>(popout_id))
                                .collect::<Vec<_>>(),
                        )
                        .discard();

                        let old_layout_id = self
                            .layout_manager
                            .active_layout_id()
                            .as_ref()
                            .map(|layout| layout.unique);

                        return window::collect_window_specs(
                            active_popout_keys,
                            dashboard::Message::SavePopoutSpecs,
                        )
                        .map(move |msg| Message::Dashboard {
                            layout_id: old_layout_id,
                            event: msg,
                        })
                        .chain(window_tasks)
                        .chain(self.load_layout(layout, self.main_window.id));
                    }
                    Some(modal::layout_manager::Action::Clone(id)) => {
                        let manager = &mut self.layout_manager;

                        let source_data = manager.get(id).map(|layout| {
                            (
                                layout.id.name.clone(),
                                layout.id.unique,
                                data::Dashboard::from(&layout.dashboard),
                            )
                        });

                        if let Some((name, old_id, ser_dashboard)) = source_data {
                            let new_uid = uuid::Uuid::new_v4();
                            let new_layout = LayoutId {
                                unique: new_uid,
                                name: manager.ensure_unique_name(&name, new_uid),
                            };

                            let mut popout_windows = Vec::new();

                            for (pane, window_spec) in &ser_dashboard.popout {
                                let configuration = configuration(pane.clone());
                                popout_windows.push((configuration, *window_spec));
                            }

                            let dashboard = Dashboard::from_config(
                                configuration(ser_dashboard.pane.clone()),
                                popout_windows,
                                old_id,
                            );

                            manager.insert_layout(new_layout.clone(), dashboard);
                        }
                    }
                    None => {}
                }
            }
            Message::AudioStream(message) => {
                if let Some(event) = self.audio_stream.update(message) {
                    match event {
                        modal::audio::UpdateEvent::RetryFailed(err) => {
                            self.notifications
                                .push(Toast::error(format!("Audio still unavailable: {err}")));
                        }
                        modal::audio::UpdateEvent::RetrySucceeded => {
                            self.notifications.push(Toast::info(
                                "Audio output re-initialized successfully".to_string(),
                            ));
                        }
                    }
                }
            }
            Message::DataFolderRequested => {
                if let Err(err) = data::open_data_folder() {
                    self.notifications
                        .push(Toast::error(format!("Failed to open data folder: {err}")));
                }
            }
            Message::OpenUrlRequested(url) => {
                if let Err(err) = data::open_url(url.as_ref()) {
                    self.notifications
                        .push(Toast::error(format!("Failed to open link: {err}")));
                }
            }
            Message::ThemeEditor(msg) => {
                let action = self.theme_editor.update(msg, &self.theme.clone().into());

                match action {
                    Some(modal::theme_editor::Action::Exit) => {
                        self.sidebar.set_menu(Some(sidebar::Menu::Settings));
                    }
                    Some(modal::theme_editor::Action::UpdateTheme(theme)) => {
                        self.theme = data::Theme(theme.clone());

                        let main_window = self.main_window.id;
                        self.active_dashboard_mut()
                            .theme_updated(main_window, &theme);
                    }
                    None => {}
                }
            }
            Message::NetworkManager(msg) => {
                let action = self.network.update(msg);

                match action {
                    Some(network_manager::Action::ApplyProxy) => {
                        let new_proxy = self.network.proxy_cfg();
                        let proxy_url = new_proxy.as_ref().map(|p| p.to_url_string());
                        let proxy_url_no_auth =
                            new_proxy.as_ref().map(|p| p.to_url_string_no_auth());

                        // Apply live to the running engine — no restart required.
                        // Credentials and URL are persisted only after conn.send()
                        // succeeds (i.e. the IPC frame was enqueued without error).
                        // Note: the IPC protocol has no SetProxy ACK; success here
                        // means the engine received the command, not that it completed
                        // stream reconnection.  A subsequent engine-side failure (e.g.
                        // unreachable proxy) would surface as stream disconnects, not
                        // as a ProxyResult::Failed.
                        let engine_conn = self.engine_connection.as_ref().cloned();
                        let manager = self.engine_manager.as_ref().map(Arc::clone);

                        return Task::perform(
                            async move {
                                // Send to the live engine first.  Only after that
                                // succeeds do we update the recovery source-of-truth
                                // and persist credentials — otherwise a failed
                                // Apply would leave a stale "new" proxy queued for
                                // the next engine restart.
                                if let Some(conn) = engine_conn {
                                    conn.send(engine_client::dto::Command::SetProxy {
                                        url: proxy_url.clone(),
                                    })
                                    .await
                                    .map_err(|e| e.to_string())?;
                                }
                                if let Some(manager) = manager {
                                    manager.set_proxy(proxy_url).await;
                                }
                                if let Some(proxy) = &new_proxy {
                                    data::config::proxy::save_proxy_auth(proxy);
                                }
                                data::config::proxy::save_proxy_url(proxy_url_no_auth.as_deref());
                                Ok(())
                            },
                            |result| match result {
                                Ok(()) => {
                                    Message::NetworkManager(network_manager::Message::ProxyResult(
                                        network_manager::ProxyResult::Applied,
                                    ))
                                }
                                Err(e) => {
                                    Message::NetworkManager(network_manager::Message::ProxyResult(
                                        network_manager::ProxyResult::Failed(e),
                                    ))
                                }
                            },
                        );
                    }
                    Some(network_manager::Action::Exit) => {
                        self.sidebar.set_menu(Some(sidebar::Menu::Settings));
                    }
                    None => {}
                }
            }
            Message::Sidebar(message) => {
                let (task, action) = self.sidebar.update(message);

                match action {
                    Some(dashboard::sidebar::Action::TickerSelected(ticker_info, content)) => {
                        let main_window_id = self.main_window.id;
                        let handles = self.handles.clone();

                        let task = {
                            if let Some(kind) = content {
                                self.active_dashboard_mut().init_focused_pane(
                                    &handles,
                                    main_window_id,
                                    ticker_info,
                                    kind,
                                )
                            } else {
                                self.active_dashboard_mut().switch_tickers_in_group(
                                    &handles,
                                    main_window_id,
                                    ticker_info,
                                )
                            }
                        };

                        return task.map(move |msg| Message::Dashboard {
                            layout_id: None,
                            event: msg,
                        });
                    }
                    Some(dashboard::sidebar::Action::ErrorOccurred(err)) => {
                        self.notifications.push(Toast::error(err.to_string()));
                    }
                    Some(dashboard::sidebar::Action::OpenOrderPanel(kind)) => {
                        use data::layout::pane::ContentKind;
                        let main_window = self.main_window;
                        let dashboard = self.active_dashboard_mut();
                        let mut pane_added = false;
                        if let Some((window_id, focused_pane)) = dashboard.focus
                            && window_id == main_window.id
                        {
                            let new_state = dashboard::pane::State::with_kind(kind);
                            if let Some((new_pane, _)) = dashboard.panes.split(
                                pane_grid::Axis::Horizontal,
                                focused_pane,
                                new_state,
                            ) {
                                dashboard.focus = Some((window_id, new_pane));
                                pane_added = true;
                            }
                        } else {
                            self.notifications.push(Toast::error(
                                "注文パネルを開くにはまずペインを選択してください".to_string(),
                            ));
                        }

                        // VenueReady 後にペインを追加した場合の自動フェッチキャッチアップ。
                        // VenueReady 時の自動フェッチは既存ペインだけを対象とするため、
                        // 後から追加したペインはここでフェッチする。
                        // reconnect による VenueReady 再発火も同じ経路をカバーする。
                        if pane_added
                            && kind == ContentKind::BuyingPower
                            && self.tachibana_state.is_ready()
                            && self.buying_power_request_id.is_none()
                        {
                            if let Some(conn) = self.engine_connection.as_ref().cloned() {
                                let req_id = uuid::Uuid::new_v4().to_string();
                                self.buying_power_request_id = Some(req_id.clone());
                                let main_window = self.main_window.id;
                                self.active_dashboard_mut()
                                    .distribute_buying_power_loading(main_window, true);
                                let req_id_for_err = req_id.clone();
                                return Task::batch(vec![
                                    task.map(Message::Sidebar),
                                    Task::perform(
                                        async move {
                                            conn.send(engine_client::dto::Command::GetBuyingPower {
                                                request_id: req_id,
                                                venue: crate::TACHIBANA_VENUE_NAME.to_string(),
                                            })
                                            .await
                                            .map_err(|e| e.to_string())
                                        },
                                        move |res| match res {
                                            Ok(()) => Message::BuyingPowerSendCompleted(Ok(())),
                                            Err(err) => Message::IpcError {
                                                request_id: Some(req_id_for_err),
                                                code: "send_failed".to_string(),
                                                message: err,
                                            },
                                        },
                                    ),
                                ]);
                            } else {
                                log::warn!(
                                    "[BuyingPower auto-fetch] tachibana is ready but \
                                     engine_connection is None"
                                );
                            }
                        }

                        if pane_added
                            && kind == ContentKind::Positions
                            && self.tachibana_state.is_ready()
                            && self.positions_request_id.is_none()
                        {
                            if let Some(conn) = self.engine_connection.as_ref().cloned() {
                                let req_id = uuid::Uuid::new_v4().to_string();
                                self.positions_request_id = Some(req_id.clone());
                                let main_window = self.main_window.id;
                                self.active_dashboard_mut()
                                    .distribute_positions_loading(main_window, true);
                                let req_id_for_err = req_id.clone();
                                return Task::batch(vec![
                                    task.map(Message::Sidebar),
                                    Task::perform(
                                        async move {
                                            conn.send(engine_client::dto::Command::GetPositions {
                                                request_id: req_id,
                                                venue: crate::TACHIBANA_VENUE_NAME.to_string(),
                                            })
                                            .await
                                            .map_err(|e| e.to_string())
                                        },
                                        move |res| match res {
                                            Ok(()) => Message::PositionsSendCompleted(Ok(())),
                                            Err(err) => Message::IpcError {
                                                request_id: Some(req_id_for_err),
                                                code: "send_failed".to_string(),
                                                message: err,
                                            },
                                        },
                                    ),
                                ]);
                            } else {
                                log::warn!(
                                    "[Positions auto-fetch] tachibana is ready but \
                                     engine_connection is None"
                                );
                            }
                        }

                        return task.map(Message::Sidebar);
                    }
                    Some(dashboard::sidebar::Action::RequestTachibanaLogin(trigger)) => {
                        let task = task.map(Message::Sidebar);
                        return Task::batch(vec![
                            task,
                            iced::Task::done(Message::RequestTachibanaLogin(trigger)),
                        ]);
                    }
                    None => {}
                }

                return task.map(Message::Sidebar);
            }
            Message::ApplyVolumeSizeUnit(pref) => {
                self.volume_size_unit = pref;
                self.confirm_dialog = None;

                let mut active_windows: Vec<window::Id> =
                    self.active_dashboard().popout.keys().copied().collect();
                active_windows.push(self.main_window.id);

                return window::collect_window_specs(active_windows, |windows| {
                    Message::RestartRequested(Some(windows))
                });
            }
            // F9d: W&B submit subprocess 完了
            Message::WandbSubmitResult(result) => {
                SUBMIT_IN_FLIGHT.store(false, std::sync::atomic::Ordering::Release);
                match result {
                    Ok(url) => {
                        log::info!("W&B submit succeeded: {url}");
                        // モーダルが開いていれば Done を通知して URL を表示する
                        if let Some(modal) = self.wandb_submit_modal.as_mut() {
                            let url_opt = if url.is_empty() {
                                None
                            } else {
                                Some(url.clone())
                            };
                            modal.update(modal::wandb_submit::Message::Done(url_opt));
                        } else {
                            self.notifications
                                .push(Toast::info(format!("W&B に送信しました: {url}")));
                        }
                        // R2-H2 / M3: run-buffer スキャンを async で行い iced
                        // update スレッドをブロックしない。完了通知は
                        // Message::RunBufferIndexScanned ハンドラ側で
                        // refresh_tools_enable を実行するため戻り値の Task に
                        // 統合する。
                        let base = data::data_path(Some("run-buffer"));
                        return Task::perform(
                            async move { wandb_auth::RunBufferIndex::scan_async(&base).await },
                            |index| Message::RunBufferIndexScanned {
                                index,
                                show_toast: false,
                            },
                        );
                    }
                    Err(ref e) => {
                        let msg = match e {
                            WandbSubmitError::AuthFailed => {
                                log::warn!("W&B submit failed: auth error");
                                "W&B 認証エラー: 再ログインしてください".to_string()
                            }
                            WandbSubmitError::RateLimit => {
                                log::warn!("W&B submit failed: rate limit");
                                "W&B 送信失敗: レートリミット超過".to_string()
                            }
                            WandbSubmitError::Network => {
                                log::warn!("W&B submit failed: network error");
                                "W&B 送信失敗: ネットワークエラー".to_string()
                            }
                            WandbSubmitError::ServerError => {
                                log::warn!("W&B submit failed: server error");
                                "W&B 送信失敗: サーバーエラー".to_string()
                            }
                            WandbSubmitError::Partial => {
                                log::warn!("W&B submit failed: partial");
                                "W&B 送信: 部分的な失敗（run buffer を確認してください）"
                                    .to_string()
                            }
                            WandbSubmitError::ProcessFailed(m) => {
                                log::warn!("W&B submit failed: {m}");
                                format!("W&B 送信失敗: {m}")
                            }
                        };
                        if let Some(modal) = self.wandb_submit_modal.as_mut() {
                            modal.update(modal::wandb_submit::Message::Failed(msg, -1));
                        } else {
                            self.notifications.push(Toast::error(msg));
                        }
                    }
                }
                return Task::none();
            }
            // F9d: W&B check_auth subprocess 完了 — wandb_auth を更新する
            Message::WandbAuthRefreshed(state) => {
                self.wandb_auth = state;
                // H5: ネイティブメニュー Tools 項目の enable/disable を再計算
                native_menu::refresh_tools_enable(&self.wandb_auth, &self.run_buffer);
                return Task::none();
            }
            // F9c: 送信モーダル内部メッセージ
            Message::WandbSubmitMsg(msg) => {
                if let Some(modal) = self.wandb_submit_modal.as_mut() {
                    match modal.update(msg) {
                        Some(modal::wandb_submit::Action::Cancel) => {
                            // Phase 3-A C2: dismiss the modal AND release the
                            // submit_in_flight reentrancy guard. If a subprocess
                            // is currently running, take its handle out of
                            // SUBMIT_CHILD and kill it. On Windows this is an
                            // immediate TerminateProcess; on Unix tokio's
                            // Child::kill also issues SIGKILL, so the 5 second
                            // grace period documented in 統一決定 46 is
                            // best-effort and depends on kernel reaping.
                            // R2-M3: SUBMIT_IN_FLIGHT のリセットは kill が
                            // 完了した「後」に行う。先にリセットすると、
                            // kill 完了前に新しい Submit が再入できてしまい
                            // 同じ run-buffer に対して 2 つの subprocess が
                            // 競合する窓ができる。kill().await 完了後に store
                            // することで、新しい Submit は古い subprocess の
                            // 終焉を必ず観測する。idempotent reset (重複 store
                            // is safe; cf. submit_in_flight_is_idempotent_on_double_release)
                            self.wandb_submit_modal = None;
                            return Task::perform(
                                async {
                                    let mut slot = SUBMIT_CHILD.lock().await;
                                    if let Some(mut child) = slot.take() {
                                        let _ = child.kill().await;
                                    }
                                    SUBMIT_IN_FLIGHT
                                        .store(false, std::sync::atomic::Ordering::Release);
                                },
                                |()| Message::Tick(std::time::Instant::now()),
                            )
                            .discard();
                        }
                        Some(modal::wandb_submit::Action::Submit {
                            project,
                            run_name,
                            tags,
                            notes,
                        }) => {
                            // R2-M1: latest_completed が無い状態で Submit が
                            // 走るのは仕様上ありえないが、防御的に no-op する。
                            // SUBMIT_IN_FLIGHT を立てる前に弾くことで「送信中」
                            // 状態に入って永久ロックされる事故を防ぐ。
                            if self.run_buffer.latest_completed.is_none() {
                                log::warn!(
                                    "[wandb] Submit dispatched with no completed run; ignoring"
                                );
                                return Task::none();
                            }
                            // ガード: 再入禁止
                            if SUBMIT_IN_FLIGHT
                                .compare_exchange(
                                    false,
                                    true,
                                    std::sync::atomic::Ordering::AcqRel,
                                    std::sync::atomic::Ordering::Acquire,
                                )
                                .is_err()
                            {
                                return Task::none();
                            }
                            let run_id = self.run_buffer.latest_completed.clone();
                            let run_buffer_dir = run_id
                                .map(|id| data::data_path(Some("run-buffer")).join(id))
                                .unwrap_or_default();
                            let script = std::path::PathBuf::from("examples/wandb/submit_run.py");
                            // F9d: stream stdout/stderr lines as LogLine
                            // updates so the modal tail is populated while
                            // the subprocess is still running, instead of
                            // appearing only on completion. The terminal
                            // event is dispatched as Message::WandbSubmitResult.
                            let stream = submit_wandb_run_stream(
                                run_buffer_dir,
                                script,
                                project,
                                run_name,
                                tags,
                                notes,
                            );
                            return Task::run(stream, |evt| match evt {
                                WandbStreamEvent::Line(line) => Message::WandbSubmitMsg(
                                    modal::wandb_submit::Message::LogLine(line),
                                ),
                                WandbStreamEvent::Final(result) => {
                                    Message::WandbSubmitResult(result)
                                }
                            });
                        }
                        Some(modal::wandb_submit::Action::OpenUrl(url)) => {
                            let _ = webbrowser::open(&url);
                        }
                        None => {}
                    }
                }
                return Task::none();
            }
            // F9c: サインインモーダル内部メッセージ
            Message::WandbSignInMsg(msg) => {
                if let Some(modal) = self.wandb_signin_modal.as_mut() {
                    match modal.update(msg) {
                        Some(modal::wandb_signin::Action::Cancel) => {
                            self.wandb_signin_modal = None;
                        }
                        Some(modal::wandb_signin::Action::OpenBrowserForKey) => {
                            let _ = webbrowser::open("https://wandb.ai/authorize");
                        }
                        Some(modal::wandb_signin::Action::Login { api_key }) => {
                            return Task::perform(
                                async move { wandb_login(api_key).await },
                                Message::WandbLoginResult,
                            );
                        }
                        None => {}
                    }
                }
                return Task::none();
            }
            // F9e: list_run_buffer_entries の完了 — 履歴モーダルを開く
            Message::WandbSubmissionLogScanned(entries) => {
                self.wandb_submission_log_modal = Some(
                    modal::wandb_submission_log::WandbSubmissionLogModal::new(entries),
                );
                return Task::none();
            }
            // F9e: 履歴モーダル内部メッセージ
            Message::WandbSubmissionLogMsg(msg) => {
                if let Some(modal) = self.wandb_submission_log_modal.as_mut() {
                    match modal.update(msg) {
                        Some(modal::wandb_submission_log::Action::Close) => {
                            self.wandb_submission_log_modal = None;
                        }
                        None => {}
                    }
                }
                return Task::none();
            }
            // F9c: wandb login 完了
            Message::WandbLoginResult(result) => {
                if let Some(modal) = self.wandb_signin_modal.as_mut() {
                    match result {
                        Ok(()) => {
                            self.wandb_signin_modal = None;
                            self.notifications
                                .push(Toast::info("W&B にログインしました".to_string()));
                            // auth キャッシュを invalidate して再取得
                            return Task::perform(
                                wandb_auth::refresh_wandb_auth(),
                                Message::WandbAuthRefreshed,
                            );
                        }
                        Err(err) => {
                            modal.submitting = false;
                            modal.error = Some(format!("ログイン失敗: {err}"));
                        }
                    }
                }
                return Task::none();
            }
            // F9c: ログアウト確認ダイアログで確認 → 実際の subprocess を起動
            Message::WandbLogoutConfirmed => {
                self.confirm_dialog = None;
                return Task::perform(async { wandb_logout().await }, Message::WandbLogoutResult);
            }
            // F9c: wandb logout subprocess の完了通知
            Message::WandbLogoutResult(result) => {
                match result {
                    Ok(()) => {
                        self.notifications
                            .push(Toast::info("W&B からログアウトしました".to_string()));
                        return Task::perform(
                            wandb_auth::refresh_wandb_auth(),
                            Message::WandbAuthRefreshed,
                        );
                    }
                    Err(err) => {
                        log::warn!("W&B logout failed: {err}");
                        self.notifications
                            .push(Toast::error(format!("W&B ログアウト失敗: {err}")));
                    }
                }
                return Task::none();
            }
            // F9e: バッファ削除確認後 — M3: tokio::fs で async 削除する。
            Message::ClearRunBufferConfirmed => {
                self.confirm_dialog = None;
                let base = data::data_path(Some("run-buffer"));
                return Task::perform(
                    async move {
                        match tokio::fs::metadata(&base).await {
                            Ok(_) => tokio::fs::remove_dir_all(&base)
                                .await
                                .map_err(|e| e.to_string()),
                            // 元から存在しない場合は成功扱い（冪等）。
                            Err(_) => Ok(()),
                        }
                    },
                    Message::RunBufferCleared,
                );
            }
            // M3: tokio::fs::remove_dir_all の完了通知。
            Message::RunBufferCleared(result) => {
                match result {
                    Ok(()) => {
                        self.run_buffer = wandb_auth::RunBufferIndex::empty();
                        // H5: Tools 項目 enable/disable を再計算
                        native_menu::refresh_tools_enable(&self.wandb_auth, &self.run_buffer);
                        self.notifications
                            .push(Toast::info("run-buffer を削除しました".to_string()));
                    }
                    Err(e) => {
                        self.notifications
                            .push(Toast::error(format!("削除失敗: {e}")));
                    }
                }
                return Task::none();
            }
            // M3: scan_async の完了通知。run_buffer 更新 + （任意で）トースト表示。
            Message::RunBufferIndexScanned { index, show_toast } => {
                if show_toast {
                    if let Some(ref latest) = index.latest_completed {
                        self.notifications.push(Toast::info(format!(
                            "最新 run: {latest} (completed runs: {})",
                            index.total
                        )));
                    } else {
                        self.notifications
                            .push(Toast::info("送信履歴がまだありません".to_string()));
                    }
                }
                self.run_buffer = index;
                // H5: Tools 項目 enable/disable を再計算
                native_menu::refresh_tools_enable(&self.wandb_auth, &self.run_buffer);
                return Task::none();
            }
        }
        Task::none()
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
                .map(Message::Sidebar);

            let dashboard_view = dashboard
                .view(&self.main_window, tickers_table, self.timezone)
                .map(move |msg| Message::Dashboard {
                    layout_id: None,
                    event: msg,
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
                        Message::RequestTachibanaLogin(Trigger::Manual)
                    }
                    widget::venue_banner::BannerMessage::Dismiss => Message::DismissTachibanaBanner,
                })
            });

            let mut base = column![header_title];
            #[cfg(target_os = "linux")]
            {
                let menu_bar_view =
                    crate::widget_menu_bar::view(&self.menu_bar, &app_mode()).map(Message::MenuBar);
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
                            .on_press(Message::DismissStrategyLoadError)
                            .style(button::danger),
                    ]
                    .spacing(8)
                    .align_y(Alignment::Center),
                )
                .padding(padding::all(8));
                base = base.push(strategy_err_banner);
            }
            let is_replay = app_mode() == engine_client::dto::AppMode::Replay;

            base = base.push(
                match sidebar_pos {
                    sidebar::Position::Left => row![sidebar_view, dashboard_view,],
                    sidebar::Position::Right => row![dashboard_view, sidebar_view],
                }
                .spacing(4)
                .padding(8)
                .height(iced::Length::Fill),
            );
            base = base.push(status_bar(is_replay));

            let view_result = if let Some(menu) = self.sidebar.active_menu() {
                self.view_with_modal(base.into(), dashboard, menu)
            } else {
                base.into()
            };
            #[cfg(target_os = "linux")]
            let view_result = crate::widget_menu_bar::with_dropdown_overlay(
                view_result,
                &self.menu_bar,
                &app_mode(),
                &self.wandb_auth,
                &self.run_buffer,
            );
            view_result
        } else {
            container(
                dashboard
                    .view_window(id, &self.main_window, tickers_table, self.timezone)
                    .map(move |msg| Message::Dashboard {
                        layout_id: None,
                        event: msg,
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
            Message::RemoveNotification,
        )
        .into();

        let after_second_password = if let Some(modal) = &self.second_password_modal {
            let modal_view = modal.view().map(Message::SecondPasswordModalMsg);
            main_dialog_modal(toasted, modal_view, Message::DismissSecondPasswordModal)
        } else {
            toasted
        };

        let after_replay_form = if let Some(form) = &self.replay_form_modal {
            let form_view = form.view().map(Message::ReplayFormMsg);
            main_dialog_modal(
                after_second_password,
                form_view,
                Message::ReplayFormMsg(modal::replay_form::Message::Cancel),
            )
        } else {
            after_second_password
        };

        // Phase 3-A H7: W&B submit / signin modals must only render on the
        // main window. Popout windows (detached panes) host neither modal
        // and would otherwise duplicate the dialog across every open window.
        // The confirm_dialog overlay above (`id == self.main_window.id`)
        // already follows this pattern; mirror it here so the symmetry holds.
        let after_wandb_submit = if id == self.main_window.id {
            if let Some(submit) = &self.wandb_submit_modal {
                let submit_view = submit.view().map(Message::WandbSubmitMsg);
                main_dialog_modal(
                    after_replay_form,
                    submit_view,
                    Message::WandbSubmitMsg(modal::wandb_submit::Message::Cancel),
                )
            } else {
                after_replay_form
            }
        } else {
            after_replay_form
        };

        let after_signin = if id == self.main_window.id {
            if let Some(signin) = &self.wandb_signin_modal {
                let signin_view = signin.view().map(Message::WandbSignInMsg);
                main_dialog_modal(
                    after_wandb_submit,
                    signin_view,
                    Message::WandbSignInMsg(modal::wandb_signin::Message::Cancel),
                )
            } else {
                after_wandb_submit
            }
        } else {
            after_wandb_submit
        };

        if id == self.main_window.id {
            if let Some(log) = &self.wandb_submission_log_modal {
                let log_view = log.view().map(Message::WandbSubmissionLogMsg);
                main_dialog_modal(
                    after_signin,
                    log_view,
                    Message::WandbSubmissionLogMsg(modal::wandb_submission_log::Message::Close),
                )
            } else {
                after_signin
            }
        } else {
            after_signin
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
        let window_events = window::events().map(Message::WindowEvent);
        let sidebar = self.sidebar.subscription().map(Message::Sidebar);

        let exchange_streams = self
            .active_dashboard()
            .market_subscriptions(&self.handles)
            .map(Message::MarketWsEvent);

        let tick = iced::window::frames().map(Message::Tick);

        // M2 (F8 R1): invariants for the global hotkey subscription —
        //   1. Only `Esc` is listened for here. All other accelerators
        //      (`Ctrl+O`, `Ctrl+S`, ...) flow through `native_menu::subscription`
        //      on Win/Mac and through the iced kbd path on Linux (P8 Q4).
        //   2. `Esc` always routes to `Message::GoBack`. The GoBack handler is
        //      responsible for the cascade: dismiss any open Linux menu-bar
        //      dropdown, close modals, etc. Adding more keys here without that
        //      handler will silently fail.
        //   3. The subscription must never close the menu directly — keeping
        //      the menu's `BarMessage::Dismiss` dispatch funnelled through
        //      `Message::GoBack` ensures a single dismissal path that the
        //      tests in `tests/widget_menu_bar_state.rs` can pin
        //      (`esc_dismiss_is_wired_in_go_back_handler`).
        let hotkeys = keyboard::listen().filter_map(|event| {
            let keyboard::Event::KeyPressed { key, .. } = event else {
                return None;
            };
            match key {
                keyboard::Key::Named(keyboard::key::Named::Escape) => Some(Message::GoBack),
                _ => None,
            }
        });

        // Watch the engine-restarting flag and emit EngineRestarting messages.
        let engine_status = Subscription::run(engine_status_stream);

        #[cfg(target_os = "linux")]
        let linux_menu_bar_dismiss =
            iced::event::listen_with(|event, _status, _window| match event {
                iced::Event::Window(iced::window::Event::Unfocused) => Some(Message::MenuBar(
                    crate::menu_bar_state::BarMessage::DismissFocusLost,
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
            native_menu::subscription(app_mode()).map(Message::NativeMenuAction),
            #[cfg(target_os = "linux")]
            linux_menu_bar_dismiss,
        ])
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
                layout
                    .dashboard
                    .load_layout(main_window)
                    .map(move |msg| Message::Dashboard {
                        layout_id: Some(layout_uid),
                        event: msg,
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
                            Message::ThemeSelected(theme)
                        })
                    };

                    let toggle_theme_editor = button(text("Theme editor")).on_press(
                        Message::Sidebar(dashboard::sidebar::Message::ToggleSidebarMenu(Some(
                            sidebar::Menu::ThemeEditor,
                        ))),
                    );

                    let toggle_network_editor = button(text("Network")).on_press(Message::Sidebar(
                        dashboard::sidebar::Message::ToggleSidebarMenu(Some(
                            sidebar::Menu::Network,
                        )),
                    ));

                    let timezone_picklist = pick_list(
                        [data::UserTimezone::Utc, data::UserTimezone::Local],
                        Some(self.timezone),
                        Message::SetTimezone,
                    );

                    let size_in_quote_currency_checkbox = {
                        let is_active = match self.volume_size_unit {
                            exchange::SizeUnit::Quote => true,
                            exchange::SizeUnit::Base => false,
                        };

                        let checkbox = iced::widget::checkbox(is_active)
                            .label("Size in quote currency")
                            .on_toggle(|checked| {
                                let on_dialog_confirm = Message::ApplyVolumeSizeUnit(if checked {
                                    exchange::SizeUnit::Quote
                                } else {
                                    exchange::SizeUnit::Base
                                });

                                let confirm_dialog = screen::ConfirmDialog::new(
                                    "Changing size display currency requires application restart"
                                        .to_string(),
                                    Box::new(on_dialog_confirm.clone()),
                                )
                                .with_confirm_btn_text("Restart now".to_string());

                                Message::ToggleDialogModal(Some(confirm_dialog))
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
                            Message::Sidebar(dashboard::sidebar::Message::SetSidebarPosition(pos))
                        },
                    );

                    let scale_factor = {
                        let current_value: f32 = self.ui_scale_factor.into();

                        let decrease_btn = if current_value > data::config::MIN_SCALE {
                            button(text("-"))
                                .on_press(Message::ScaleFactorChanged((current_value - 0.1).into()))
                        } else {
                            button(text("-"))
                        };

                        let increase_btn = if current_value < data::config::MAX_SCALE {
                            button(text("+"))
                                .on_press(Message::ScaleFactorChanged((current_value + 0.1).into()))
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
                                        Box::new(Message::ToggleTradeFetch(true)),
                                    );
                                    Message::ToggleDialogModal(Some(confirm_dialog))
                                } else {
                                    Message::ToggleTradeFetch(false)
                                }
                            });

                        tooltip(
                            checkbox,
                            Some("Try to fetch trades for footprint charts"),
                            TooltipPosition::Top,
                        )
                    };

                    let open_data_folder = {
                        let button =
                            button(text("Open data folder")).on_press(Message::DataFolderRequested);

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
                            .on_press(Message::OpenUrlRequested(Cow::Borrowed(
                                version::GITHUB_REPOSITORY_URL,
                            )));

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
                                .on_press(Message::OpenUrlRequested(Cow::Owned(commit_url)));

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
                    Message::Sidebar(dashboard::sidebar::Message::ToggleSidebarMenu(None)),
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
                            let dashboard_msg = Message::Dashboard {
                                layout_id: None,
                                event: dashboard::Message::Pane(
                                    main_window,
                                    dashboard::pane::Message::ReplacePane(pane_id),
                                ),
                            };

                            btn.on_press(dashboard_msg)
                        } else {
                            btn
                        }
                    };
                    let split_pane_button = {
                        let btn = button(text("Split").align_x(Alignment::Center))
                            .width(iced::Length::Fill);
                        if is_main_window {
                            let dashboard_msg = Message::Dashboard {
                                layout_id: None,
                                event: dashboard::Message::Pane(
                                    main_window,
                                    dashboard::pane::Message::SplitPane(
                                        pane_grid::Axis::Horizontal,
                                        pane_id,
                                    ),
                                ),
                            };
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
                        self.layout_manager.view().map(Message::Layouts)
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
                    Message::Sidebar(dashboard::sidebar::Message::ToggleSidebarMenu(None)),
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
                        .map(Message::AudioStream),
                    Message::Sidebar(dashboard::sidebar::Message::ToggleSidebarMenu(None)),
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
                        .map(Message::ThemeEditor),
                    Message::Sidebar(dashboard::sidebar::Message::ToggleSidebarMenu(None)),
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
                    self.network.view().map(Message::NetworkManager),
                    Message::Sidebar(dashboard::sidebar::Message::ToggleSidebarMenu(None)),
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

    /// F7: set APP_MODE to `mode` then restart. The `_mode_switch_guard` in `self`
    /// is automatically dropped when `*self = new_state` runs inside `restart()`,
    /// which resets `MODE_SWITCHING` to false.
    fn restart_with_mode(&mut self, mode: engine_client::dto::AppMode) -> Task<Message> {
        set_app_mode(mode);
        self.restart()
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
            Task::done(Message::TachibanaVenueEvent(VenueEvent::Ready))
        } else {
            Task::none()
        };

        close_windows.chain(init_task).chain(venue_bootstrap)
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

    // ── Helpers ──────────────────────────────────────────────────────────────

    /// Extract the source slice for a single handler arm.
    ///
    /// `arm_prefix` must uniquely identify the handler arm — include indentation
    /// and `=>` so it cannot match message-construction sites (e.g.
    /// `None => Message::Foo` vs `            Message::Foo =>`).
    ///
    /// The slice ends just before the next same-level `Message::` arm or EOF.
    fn handler_body(arm_prefix: &str) -> &'static str {
        let start = MAIN_RS
            .find(arm_prefix)
            .unwrap_or_else(|| panic!("handler arm not found: {arm_prefix}"));
        let tail = &MAIN_RS[start..];
        let end = tail[1..]
            .find("\n            Message::")
            .map(|i| i + 1)
            .unwrap_or(tail.len());
        &MAIN_RS[start..start + end]
    }

    // ── Test 2: NativeSaveAsPath(None) ────────────────────────────────────────

    #[test]
    fn save_as_path_none_does_not_push_toast() {
        let body = handler_body("            Message::NativeSaveAsPath(None) =>");
        assert!(
            !body.contains("notifications.push"),
            "NativeSaveAsPath(None) must not push any toast (user cancelled the dialog)"
        );
    }

    #[test]
    fn save_as_path_none_returns_task_none() {
        let body = handler_body("            Message::NativeSaveAsPath(None) =>");
        assert!(
            body.contains("Task::none()"),
            "NativeSaveAsPath(None) must return Task::none()"
        );
    }

    // ── Test 3: NativeOpenFileCancelled ───────────────────────────────────────

    #[test]
    fn open_file_cancelled_does_not_push_toast() {
        let body = handler_body("            Message::NativeOpenFileCancelled =>");
        assert!(
            !body.contains("notifications.push"),
            "NativeOpenFileCancelled must not push any toast (user cancelled the dialog)"
        );
    }

    #[test]
    fn open_file_cancelled_returns_task_none() {
        let body = handler_body("            Message::NativeOpenFileCancelled =>");
        assert!(
            body.contains("Task::none()"),
            "NativeOpenFileCancelled must return Task::none()"
        );
    }

    // ── Test 4a: NativeOpenFileApply — valid JSON branch ─────────────────────

    #[test]
    fn open_file_apply_validates_against_data_state() {
        let body = handler_body("            Message::NativeOpenFileApply { json, path } =>");
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
        let apply_body = handler_body("            Message::NativeOpenFileApply { json, path } =>");
        assert!(
            apply_body.contains("NativeOpenFilePendingCheck"),
            "NativeOpenFileApply valid JSON branch must dispatch NativeOpenFilePendingCheck (F4 two-step fix)"
        );

        let check_body = handler_body(
            "            Message::NativeOpenFilePendingCheck { json, path, windows } =>",
        );
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
        let body = handler_body("            Message::NativeOpenFileApply { json, path } =>");
        assert!(
            body.contains("無効な設定ファイルです"),
            "NativeOpenFileApply invalid JSON branch must push '無効な設定ファイルです' error toast"
        );
    }

    #[test]
    fn open_file_apply_invalid_json_does_not_restart() {
        // Verify the Err(_) branch returns Task::none() and does NOT call restart.
        // Locate the Err arm within the handler body.
        let handler = handler_body("            Message::NativeOpenFileApply { json, path } =>");
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
        // closure constructions like `Message::NativeSaveAsWithSpecs { path: path.clone(), ... }`
        // do not produce a false match.
        let body = handler_body("            Message::NativeSaveAsWithSpecs { path, windows } =>");
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
        let body = handler_body("            Message::SaveAndExit =>");
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
        let body = handler_body("            Message::SaveAndOpenFile =>");
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
        let body = handler_body("            Message::ExitRequested(windows) =>");
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
        let guard_marker =
            "// HIGH fix: another dialog is already visible \u{2014} silently drop this request";
        let marker_pos = MAIN_RS
            .find(guard_marker)
            .expect("NativeOpenFilePendingCheck must contain the HIGH-fix dialog guard comment");
        // Scan backward to the handler arm.
        let arm_pos = MAIN_RS[..marker_pos]
            .rfind("Message::NativeOpenFilePendingCheck")
            .expect("guard marker must be inside NativeOpenFilePendingCheck handler");
        // Extract until the next top-level handler arm.
        let tail = &MAIN_RS[arm_pos..];
        let region_end = tail.find("\n            Message::").unwrap_or(tail.len());
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
            body.contains("Message::NativeOpenStrategyPicked"),
            "Action::OpenFile replay branch must dispatch NativeOpenStrategyPicked \
             after the OS file dialog returns"
        );
    }

    #[test]
    fn open_strategy_picked_some_sends_load_strategy_scenario() {
        let body = handler_body("            Message::NativeOpenStrategyPicked(picked) =>");
        assert!(
            body.contains("AppMode::Replay"),
            "NativeOpenStrategyPicked must guard on replay mode (live must drop the .py)"
        );
        assert!(
            body.contains("Command::LoadStrategyScenario"),
            "NativeOpenStrategyPicked(Some) must send Command::LoadStrategyScenario"
        );
    }

    #[test]
    fn strategy_scenario_loaded_event_prefills_modal() {
        let body =
            handler_body("            Message::StrategyScenarioLoadedEvent { path, scenario } =>");
        assert!(
            body.contains("prefill_from_scenario"),
            "StrategyScenarioLoadedEvent must call prefill_from_scenario when scenario is Some"
        );
        assert!(
            body.contains("set_strategy_file_only"),
            "StrategyScenarioLoadedEvent must fall back to set_strategy_file_only when scenario is None"
        );
        assert!(
            body.contains("CURRENT_PATH"),
            "StrategyScenarioLoadedEvent must update CURRENT_PATH (F3 integration)"
        );
    }

    #[test]
    fn strategy_scenario_load_failed_event_pushes_toast_only() {
        let body = handler_body(
            "            Message::StrategyScenarioLoadFailedEvent { path, reason } =>",
        );
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
}

#[cfg(test)]
mod status_bar_tests {
    use super::*;

    #[test]
    fn t1_status_bar_label_replay() {
        assert_eq!(status_bar_label(true), "● REPLAY");
    }

    #[test]
    fn t2_status_bar_label_live() {
        assert_eq!(status_bar_label(false), "● LIVE");
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
