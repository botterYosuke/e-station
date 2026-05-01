#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod api;
mod audio;
mod chart;
mod cli;
mod connector;
mod layout;
mod logger;
mod modal;
mod native_menu;
mod notify;
mod replay_api;
mod screen;
mod style;
mod venue_state;
mod version;
mod widget;
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

/// Receiver end of the HTTP control API channel (port 9876).  Set once in
/// `main()` after `replay_api::spawn` runs.  `replay_api_stream` takes
/// ownership of the inner `Receiver` via `Option::take()` on first poll;
/// subsequent calls (Iced subscription identity is stable so there is only
/// one) see `None` and return immediately — no panic, no double-receive.
static CONTROL_API_RX: std::sync::OnceLock<
    std::sync::Mutex<Option<tokio::sync::mpsc::Receiver<replay_api::ControlApiCommand>>>,
> = std::sync::OnceLock::new();

/// Shared REPLAY API state — used in `Message::ReplayBuyingPower` to cache the
/// portfolio snapshot for `GET /api/replay/portfolio` without a tokio await.
static REPLAY_API_STATE: std::sync::OnceLock<Arc<replay_api::ReplayApiState>> =
    std::sync::OnceLock::new();

/// Shared market-closed flag (N3.B). Set in `main()` after creating the
/// `OrderApiState`, then read from `Flowsurface::new()` so that
/// `Message::TachibanaVenueEvent` can store it in `self.order_api_market_closed`
/// and update it atomically.
static ORDER_API_MARKET_CLOSED: std::sync::OnceLock<Arc<std::sync::atomic::AtomicBool>> =
    std::sync::OnceLock::new();

/// Startup mode (`live` or `replay`) captured from `--mode` before any runtime
/// is created. Allows `Flowsurface::new()` to apply D8 layout isolation without
/// depending on `REPLAY_API_STATE` (which is only set when the HTTP control API
/// runtime builds successfully).
static APP_MODE: std::sync::OnceLock<engine_client::dto::AppMode> = std::sync::OnceLock::new();

/// Spawn a long-lived bridge that mirrors the connection's broadcast
/// venue lifecycle events into [`VENUE_READY_CACHE`]. Subscribing
/// here, before the connection is published to `ENGINE_CONNECTION_TX`,
/// captures every `VenueReady`/`VenueError` even if iced is still
/// starting up. The task self-terminates when the broadcast channel
/// closes (i.e. when the connection drops).
fn spawn_venue_ready_bridge(rt: &tokio::runtime::Runtime, conn: &engine_client::EngineConnection) {
    let cache = match VENUE_READY_CACHE.get() {
        Some(cache) => Arc::clone(cache),
        None => return,
    };
    let mut event_rx = conn.subscribe_events();
    rt.spawn(async move {
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
    let cli_args = cli::CliArgs::parse();

    // Capture startup mode before any runtime is created so Flowsurface::new()
    // can enforce D8 regardless of whether the HTTP control-API runtime builds.
    APP_MODE
        .set(engine_client::dto::AppMode::from(cli_args.mode))
        .ok();

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
                                    // Re-spawn the bridge against the
                                    // fresh connection — the previous
                                    // bridge's recv loop has already
                                    // exited via RecvError::Closed.
                                    let rt_handle = tokio::runtime::Handle::current();
                                    let bridge_cache = VENUE_READY_CACHE.get().cloned();
                                    if let Some(cache) = bridge_cache {
                                        let mut event_rx = new_conn.subscribe_events();
                                        rt_handle.spawn(async move {
                                            use engine_client::dto::EngineEvent;
                                            use tokio::sync::broadcast::error::RecvError;
                                            loop {
                                                match event_rx.recv().await {
                                                    Ok(EngineEvent::VenueReady {
                                                        venue, ..
                                                    }) => {
                                                        cache.lock().await.insert(venue);
                                                    }
                                                    Ok(EngineEvent::VenueError {
                                                        venue, ..
                                                    }) => {
                                                        cache.lock().await.remove(&venue);
                                                    }
                                                    Ok(EngineEvent::VenueLoginStarted {
                                                        venue,
                                                        ..
                                                    }) => {
                                                        cache.lock().await.remove(&venue);
                                                    }
                                                    Ok(EngineEvent::VenueLoginCancelled {
                                                        venue,
                                                        ..
                                                    }) => {
                                                        cache.lock().await.remove(&venue);
                                                    }
                                                    Ok(_) => {}
                                                    Err(RecvError::Lagged(n)) => {
                                                        log::warn!(
                                                            "venue_ready_bridge lagged, dropped {n}"
                                                        );
                                                    }
                                                    Err(RecvError::Closed) => break,
                                                }
                                            }
                                        });
                                    }
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
                        // Reviewer 2026-04-26 R3 (HIGH-2).
                        let bridge_cache = VENUE_READY_CACHE.get().cloned();
                        if let Some(cache) = bridge_cache {
                            let mut event_rx = conn.subscribe_events();
                            tokio::spawn(async move {
                                use engine_client::dto::EngineEvent;
                                use tokio::sync::broadcast::error::RecvError;
                                loop {
                                    match event_rx.recv().await {
                                        Ok(EngineEvent::VenueReady { venue, .. }) => {
                                            cache.lock().await.insert(venue);
                                        }
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
                                            log::warn!("venue_ready_bridge lagged, dropped {n}");
                                        }
                                        Err(RecvError::Closed) => break,
                                    }
                                }
                            });
                        }

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

    // HTTP control API for E2E tests (T35-U5-RelogE2E, T7).
    // Runs on a dedicated tokio runtime so it stays alive even when the engine
    // runtime shuts down. Port 9876 conflicts are logged but non-fatal.
    {
        let api_rt = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .thread_name("control-api")
            .build()
            .inspect_err(|e| log::error!("replay_api: failed to build runtime — {e}"))
            .ok();
        if let Some(rt) = api_rt {
            let order_api_state = {
                use std::sync::atomic::AtomicBool;
                use tokio::sync::Mutex;
                // A-9 (H-2): 起動時に WAL から当日分を復元する。
                // WAL ファイルが存在しない場合は空 map で初期化される（初回起動 / 昨日以前のみ）。
                let wal_path = data::data_path(Some("tachibana_orders.jsonl"));
                let session = Arc::new(Mutex::new(
                    engine_client::order_session_state::OrderSessionState::load_from_wal(&wal_path),
                ));
                let engine_rx = ENGINE_CONNECTION_TX
                    .get()
                    .expect("ENGINE_CONNECTION_TX must be set before replay_api::spawn")
                    .subscribe();
                // N1.13 / N1.3: REPLAY モードフラグは CLI `--mode` から伝搬する。
                // is_replay_mode=true のとき /api/order/submit は 503 で reject され、
                // 発注は /api/replay/order に流れる。
                let is_replay_mode = Arc::new(AtomicBool::new(cli_args.mode == cli::Mode::Replay));
                // FLOWSURFACE_ORDER_GUARD_ENABLED=1 で発注 API を有効化する（明示 opt-in）。
                // 未設定時はデフォルトの enabled=false のまま 503 で reject（誤発注防止）。
                let guard_config =
                    if std::env::var("FLOWSURFACE_ORDER_GUARD_ENABLED").as_deref() == Ok("1") {
                        api::order_api::OrderGuardConfig::enabled_no_limits()
                    } else {
                        api::order_api::OrderGuardConfig::default()
                    };
                // N3.B: create shared market-closed flag and publish to static
                // so Flowsurface::new() can clone it for TachibanaVenueEvent syncing.
                let market_closed_flag = Arc::new(std::sync::atomic::AtomicBool::new(false));
                if ORDER_API_MARKET_CLOSED
                    .set(Arc::clone(&market_closed_flag))
                    .is_err()
                {
                    log::warn!(
                        "ORDER_API_MARKET_CLOSED already initialized \
                         — flag sharing may be broken"
                    );
                }
                Arc::new(
                    api::order_api::OrderApiState::new(session, engine_rx, is_replay_mode)
                        .with_guard_config(guard_config)
                        .with_market_closed_flag(market_closed_flag),
                )
            };
            // N1.3: ReplayApiState は engine_rx + mode を保持して
            // /api/replay/{load,order,portfolio} を駆動する。
            let replay_api_state = {
                let engine_rx = ENGINE_CONNECTION_TX
                    .get()
                    .expect("ENGINE_CONNECTION_TX must be set before replay_api::spawn")
                    .subscribe();
                Arc::new(replay_api::ReplayApiState::new(
                    engine_rx,
                    // R1b H-E: cli::Mode → AppMode を境界で写す。
                    engine_client::dto::AppMode::from(cli_args.mode),
                ))
            };
            // N1.16: cache Arc for Message::ReplayBuyingPower handler.
            if REPLAY_API_STATE.set(Arc::clone(&replay_api_state)).is_err() {
                log::warn!("replay_api: REPLAY_API_STATE already initialized");
            }
            // N1.6: AgentApiState はインメモリ narrative ストア。
            let agent_api_state = Arc::new(api::agent_api::AgentApiState::new());
            if let Some(rx) = replay_api::spawn(
                rt.handle(),
                Some(order_api_state),
                Some(replay_api_state),
                Some(agent_api_state),
            ) {
                CONTROL_API_RX.set(std::sync::Mutex::new(Some(rx))).ok();
            }
            std::thread::Builder::new()
                .name("control-api-rt".into())
                .spawn(move || rt.block_on(std::future::pending::<()>()))
                .inspect_err(|e| log::error!("replay_api: failed to spawn runtime thread — {e}"))
                .ok();
        }
    }

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
    /// Shared market-closed flag for order_api pre-reject (N3.B).
    /// Synced from `tachibana_state` on every `TachibanaVenueEvent`.
    order_api_market_closed: Arc<std::sync::atomic::AtomicBool>,
    /// N4.3: user-selected strategy `.py` file path. `None` until the user picks
    /// one via the OS file dialog.  Intended for future wiring to `/api/replay/start`
    /// `strategy_file` field when a UI-triggered replay start is implemented.
    replay_strategy_file: Option<std::path::PathBuf>,
    /// N4.4: non-None while a `strategy_load_failed` error banner should be shown.
    /// Cleared by `Message::DismissStrategyLoadError`.
    strategy_load_error: Option<String>,
    /// Pending destination path for the "Save As" native menu action.
    /// Set by `NativeSaveAsPath`, consumed by `NativeSaveAsWithSpecs`.
    pending_save_path: Option<std::path::PathBuf>,
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
    /// Forwarded from the HTTP control API (port 9876). Used by E2E tests to
    /// drive venue login / cancellation without a GUI. (T35-U5-RelogE2E / T7)
    #[allow(dead_code)]
    ControlApi(replay_api::ControlApiCommand),
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
        strategy_id: String,
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
    /// Native OS menu bar — Save As: user picked a destination path.
    NativeSaveAsPath(Option<std::path::PathBuf>),
    /// Native OS menu bar — Save As: window specs collected, ready to write.
    NativeSaveAsWithSpecs(HashMap<window::Id, WindowSpec>),
    /// Native OS menu bar — Open: JSON string read from the user-picked file.
    NativeOpenFileApply(String),
    /// Native OS menu bar — Open: dialog cancelled (user closed the picker).
    NativeOpenFileCancelled,
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

/// Bridge the HTTP control API channel into the Iced message loop.
///
/// Takes ownership of the `mpsc::Receiver` stored in [`CONTROL_API_RX`] on
/// first call (via `Option::take`).  Iced's `Subscription::run` identity is
/// derived from the function pointer so this subscription is only created once
/// per app lifetime — the `take()` on subsequent construction attempts (which
/// don't happen in practice) would safely return `None` and exit the stream.
fn replay_api_stream() -> impl iced::futures::Stream<Item = Message> + Send + 'static {
    let rx_opt = CONTROL_API_RX
        .get()
        .and_then(|m| m.lock().ok())
        .and_then(|mut g| g.take());
    async_stream::stream! {
        let Some(mut rx) = rx_opt else { return; };
        while let Some(cmd) = rx.recv().await {
            yield Message::ControlApi(cmd);
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
        } => {
            let body = if leaves_qty == "0" {
                format!("約定 {client_order_id}: {last_qty} 株 @ {last_price} 円（全約定）")
            } else {
                format!(
                    "約定 {client_order_id}: {last_qty} 株 @ {last_price} 円（残 {leaves_qty} 株）"
                )
            };
            Some(Message::OrderToast(Toast::info(body)))
        }
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
            strategy_id,
            cash,
            buying_power,
            equity,
            ts_event_ms,
        } => Some(Message::ReplayBuyingPower {
            strategy_id,
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
            let is_replay = APP_MODE
                .get()
                .map(|&m| m == engine_client::dto::AppMode::Replay)
                .unwrap_or(false);
            if is_replay {
                Some(Message::ReplayFinished)
            } else {
                None
            }
        }
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
        let is_replay_mode = APP_MODE
            .get()
            .map(|m| *m == engine_client::dto::AppMode::Replay)
            .expect("APP_MODE must be set before Flowsurface::new");

        let saved_state = if is_replay_mode {
            log::info!("replay mode: skipping load_saved_state (D9-load), using defaults");
            layout::SavedState::default()
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
        let initial_conn: Option<Arc<engine_client::EngineConnection>> = ENGINE_CONNECTION_TX
            .get()
            .and_then(|tx| tx.borrow().clone());
        if let Some(conn) = initial_conn.as_ref() {
            for &(venue, name) in VENUE_NAMES {
                let backend = Arc::new(engine_client::EngineClientBackend::new(
                    Arc::clone(conn),
                    name,
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
            // N3.B: reuse the flag that was published to ORDER_API_MARKET_CLOSED
            // by main(). Falls back to a fresh flag (e.g. in tests / hot-reload).
            order_api_market_closed: ORDER_API_MARKET_CLOSED
                .get()
                .map(Arc::clone)
                .unwrap_or_else(|| {
                    // Control API disabled (api_rt = None) — no HTTP handler will read this flag.
                    log::debug!(
                        "ORDER_API_MARKET_CLOSED not set; using standalone flag \
                         (control API disabled)"
                    );
                    Arc::new(std::sync::atomic::AtomicBool::new(false))
                }),
            replay_strategy_file: None,
            strategy_load_error: None,
            pending_save_path: None,
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

        (
            state,
            open_main_window
                .discard()
                .chain(setup_native_menu)
                .chain(load_layout)
                .chain(launch_sidebar.map(Message::Sidebar)),
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
                    self.layout_manager
                        .iter_dashboards_mut()
                        .for_each(|dashboard| {
                            dashboard.distribute_buying_power_loading(main_window, false);
                            dashboard.distribute_order_list_loading(main_window, false);
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
                // H2 fix: sync the AtomicBool after dismiss so the order API
                // pre-reject guard does not remain true after the banner is
                // closed. Without this store() the flag stays `true` and
                // SubmitOrder returns 409 MARKET_CLOSED indefinitely.
                self.order_api_market_closed.store(
                    self.tachibana_state.is_market_closed(),
                    std::sync::atomic::Ordering::Release,
                );
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

                // N3.B: sync market-closed state to order_api pre-reject flag.
                self.order_api_market_closed.store(
                    self.tachibana_state.is_market_closed(),
                    std::sync::atomic::Ordering::Release,
                );

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

                return replay
                    .chain(auto_fetch_buying_power)
                    .chain(auto_fetch_orders);
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
                self.active_dashboard_mut()
                    .distribute_buying_power_loading(main_window, false);
                self.active_dashboard_mut()
                    .distribute_order_list_loading(main_window, false);

                // Rebuild backends with the new connection and bump the generation
                // counter so iced assigns new subscription IDs and restarts streams.
                let mut tachibana_meta_handle = None;
                for &(venue, name) in VENUE_NAMES {
                    let backend = Arc::new(engine_client::EngineClientBackend::new(
                        Arc::clone(&conn),
                        name,
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
            Message::ExitRequested(windows) => {
                self.save_state_to_disk(&windows);
                return iced::exit();
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

                if self.confirm_dialog.is_some() {
                    self.confirm_dialog = None;
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
                                        let is_replay = APP_MODE
                                            .get()
                                            .map(|m| *m == engine_client::dto::AppMode::Replay)
                                            .unwrap_or(false);
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
                        Some(dashboard::Event::PositionsAction(_action)) => {
                            // PP4: full IPC wiring deferred to PP4.
                            // PP1 stub: ignore the action to keep the codebase compiling.
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
            // N1.16: REPLAY 仮想ポートフォリオ更新 — dashboard に配布 + HTTP キャッシュ更新
            Message::ReplayBuyingPower {
                strategy_id,
                cash,
                buying_power,
                equity,
                ts_event_ms,
            } => {
                let main_window = self.main_window.id;
                self.active_dashboard_mut().distribute_replay_buying_power(
                    main_window,
                    cash.clone(),
                    buying_power.clone(),
                    equity.clone(),
                    ts_event_ms,
                );
                if let Some(state) = REPLAY_API_STATE.get() {
                    state.update_replay_portfolio(
                        strategy_id,
                        cash,
                        buying_power,
                        equity,
                        ts_event_ms,
                    );
                } else {
                    log::warn!(
                        "replay_api: REPLAY_API_STATE not initialized, skipping portfolio update"
                    );
                }
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
            // ── Native OS menu bar ──────────────────────────────────────────
            Message::NativeMenuSetup(raw_id) => {
                let app_mode = APP_MODE
                    .get()
                    .copied()
                    .unwrap_or(engine_client::dto::AppMode::Live);
                native_menu::attach(raw_id, app_mode);
                return Task::none();
            }
            Message::NativeMenuAction(action) => {
                use native_menu::Action;
                match action {
                    Action::OpenFile => {
                        return Task::perform(
                            async {
                                // Returns None if the dialog was cancelled; Some(Result) otherwise.
                                let handle = rfd::AsyncFileDialog::new()
                                    .add_filter("JSON", &["json"])
                                    .set_title("設定ファイルを開く")
                                    .pick_file()
                                    .await?;
                                let bytes = handle.read().await;
                                Some(String::from_utf8(bytes).map_err(|e| e.to_string()))
                            },
                            |result| match result {
                                None => Message::NativeOpenFileCancelled,
                                Some(Ok(json)) => Message::NativeOpenFileApply(json),
                                Some(Err(e)) => Message::OrderToast(Toast::error(format!(
                                    "ファイルを読み込めませんでした: {e}"
                                ))),
                            },
                        );
                    }
                    Action::SaveAs => {
                        return Task::perform(
                            async {
                                rfd::AsyncFileDialog::new()
                                    .add_filter("JSON", &["json"])
                                    .set_file_name("saved-state.json")
                                    .set_title("名前を付けて保存")
                                    .save_file()
                                    .await
                                    .map(|h| h.path().to_owned())
                            },
                            Message::NativeSaveAsPath,
                        );
                    }
                    Action::OpenStrategy => {
                        return Task::perform(
                            async {
                                rfd::AsyncFileDialog::new()
                                    .add_filter("Python", &["py"])
                                    .set_title("ストラテジーファイルを開く")
                                    .pick_file()
                                    .await
                                    .map(|h| h.path().to_owned())
                            },
                            Message::StrategyFilePicked,
                        );
                    }
                }
            }
            Message::NativeSaveAsPath(Some(path)) => {
                self.pending_save_path = Some(path);
                let mut active_windows: Vec<window::Id> =
                    self.active_dashboard().popout.keys().copied().collect();
                active_windows.push(self.main_window.id);
                return window::collect_window_specs(
                    active_windows,
                    Message::NativeSaveAsWithSpecs,
                );
            }
            Message::NativeSaveAsPath(None) => {
                return Task::none();
            }
            Message::NativeSaveAsWithSpecs(windows) => {
                let Some(path) = self.pending_save_path.take() else {
                    return Task::none();
                };
                if let Some(json) = self.build_state_json(&windows) {
                    match std::fs::write(&path, &json) {
                        Ok(()) => {
                            log::info!("Saved state to {}", path.display());
                            self.notifications
                                .push(Toast::info(format!("保存しました: {}", path.display())));
                        }
                        Err(e) => {
                            log::error!("Failed to write to {}: {e}", path.display());
                            self.notifications
                                .push(Toast::error(format!("保存に失敗しました: {e}")));
                        }
                    }
                }
                return Task::none();
            }
            Message::NativeOpenFileCancelled => {
                return Task::none();
            }
            Message::NativeOpenFileApply(json) => {
                // Validate the JSON parses as a known state before overwriting.
                match serde_json::from_str::<data::State>(&json) {
                    Ok(_) => {
                        if let Err(e) = data::write_json_to_file(&json, data::SAVED_STATE_PATH) {
                            log::error!("Failed to write imported state: {e}");
                            self.notifications
                                .push(Toast::error(format!("ファイルの適用に失敗しました: {e}")));
                            return Task::none();
                        }
                        return self.restart();
                    }
                    Err(e) => {
                        self.notifications
                            .push(Toast::error(format!("無効な設定ファイルです: {e}")));
                        return Task::none();
                    }
                }
            }
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
            Message::ControlApi(cmd) => {
                use replay_api::ControlApiCommand;
                log::debug!("control-api command received: {cmd:?}");
                match cmd {
                    ControlApiCommand::RequestVenueLogin { venue }
                        if venue == TACHIBANA_VENUE_NAME =>
                    {
                        return iced::Task::done(Message::RequestTachibanaLogin(Trigger::Manual));
                    }
                    ControlApiCommand::ToggleVenue { venue } if venue == TACHIBANA_VENUE_NAME => {
                        return iced::Task::done(Message::RequestTachibanaLogin(Trigger::Auto));
                    }
                    ControlApiCommand::AutoGenerateReplayPanes {
                        instrument_id,
                        strategy_id,
                        granularity,
                        ack,
                    } => {
                        // M-2 (R2 review-fix R2): strategy_id を Option<String> として保持。
                        // None = 単独 LoadReplayData 経路、Some(_) = StartEngine 経由 load。
                        log::debug!(
                            "AutoGenerateReplayPanes: instrument_id={instrument_id:?} \
                             strategy_id={strategy_id:?} granularity={granularity:?}"
                        );
                        use engine_client::dto::ReplayGranularity;
                        // Convert granularity to Option<Timeframe>: None = Trade (no bar chart).
                        let timeframe = match granularity {
                            ReplayGranularity::Daily => Some(exchange::Timeframe::D1),
                            ReplayGranularity::Minute => Some(exchange::Timeframe::M1),
                            ReplayGranularity::Trade => None,
                        };
                        let main_window_id = self.main_window.id;
                        let dashboard = self.active_dashboard_mut();
                        // N1.14: clear any stale overlay markers before loading new replay data.
                        dashboard.clear_chart_overlays(main_window_id);
                        let task = dashboard
                            .auto_generate_replay_panes(main_window_id, &instrument_id, timeframe)
                            .map(move |msg| Message::Dashboard {
                                layout_id: None,
                                event: msg,
                            });
                        // `auto_generate_replay_panes` は内部で `set_content_and_streams`
                        // を同期で呼んで pane に stream を bind する。戻り `Task` には
                        // pane 内 chart の追加 fetch しか含まれないので、ここで ack して
                        // /api/replay/load を解放してよい（pane と subscription は確立済み）。
                        if let Some(ack) = ack {
                            ack.notify_one();
                        }
                        return task;
                    }
                    _ => {}
                }
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
            let is_replay = APP_MODE
                .get()
                .map(|&m| m == engine_client::dto::AppMode::Replay)
                .expect("APP_MODE must be initialised after CLI parsing");

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

            if let Some(menu) = self.sidebar.active_menu() {
                self.view_with_modal(base.into(), dashboard, menu)
            } else {
                base.into()
            }
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

        if let Some(modal) = &self.second_password_modal {
            let modal_view = modal.view().map(Message::SecondPasswordModalMsg);
            main_dialog_modal(toasted, modal_view, Message::DismissSecondPasswordModal)
        } else {
            toasted
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

        Subscription::batch(vec![
            exchange_streams,
            sidebar,
            window_events,
            tick,
            hotkeys,
            engine_status,
            Subscription::run(replay_api_stream),
            native_menu::subscription().map(Message::NativeMenuAction),
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
    fn build_state_json(&mut self, windows: &HashMap<window::Id, WindowSpec>) -> Option<String> {
        if APP_MODE
            .get()
            .map(|m| *m == engine_client::dto::AppMode::Replay)
            .unwrap_or(false)
        {
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

    fn save_state_to_disk(&mut self, windows: &HashMap<window::Id, WindowSpec>) {
        if let Some(json) = self.build_state_json(windows) {
            let file_name = data::SAVED_STATE_PATH;
            if let Err(e) = data::write_json_to_file(&json, file_name) {
                log::error!("Failed to write layout state to file: {}", e);
            } else {
                log::info!("Persisted state to {file_name}");
            }
        } else {
            log::info!("replay mode: skipping save_state_to_disk");
        }
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
        let body = handler_body("            Message::NativeOpenFileApply(json) =>");
        assert!(
            body.contains("serde_json::from_str::<data::State>"),
            "NativeOpenFileApply must validate the JSON as data::State before overwriting"
        );
    }

    #[test]
    fn open_file_apply_valid_json_calls_write_and_restart() {
        let body = handler_body("            Message::NativeOpenFileApply(json) =>");
        assert!(
            body.contains("data::write_json_to_file"),
            "NativeOpenFileApply valid JSON branch must call data::write_json_to_file"
        );
        assert!(
            body.contains("self.restart()"),
            "NativeOpenFileApply valid JSON branch must call self.restart()"
        );
    }

    // ── Test 4b: NativeOpenFileApply — invalid JSON branch ───────────────────

    #[test]
    fn open_file_apply_invalid_json_pushes_error_toast() {
        let body = handler_body("            Message::NativeOpenFileApply(json) =>");
        assert!(
            body.contains("無効な設定ファイルです"),
            "NativeOpenFileApply invalid JSON branch must push '無効な設定ファイルです' error toast"
        );
    }

    #[test]
    fn open_file_apply_invalid_json_does_not_restart() {
        // Verify the Err(_) branch returns Task::none() and does NOT call restart.
        // Locate the Err arm within the handler body.
        let handler = handler_body("            Message::NativeOpenFileApply(json) =>");
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
        let body = handler_body("Message::NativeSaveAsWithSpecs(windows)");
        assert!(
            body.contains("build_state_json("),
            "NativeSaveAsWithSpecs handler must call build_state_json — same serialisation path as save_state_to_disk"
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
