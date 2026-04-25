/// Python data engine subprocess management.
///
/// `PythonProcess` spawns the Python engine and communicates its `{port, token}`
/// via stdin as a JSON line.  `ProcessManager` wraps it with exponential-backoff
/// restart logic and re-applies subscriptions after each recovery.
use crate::{
    connection::EngineConnection,
    dto::{EngineEvent, TachibanaSessionWire, VenueCredentialsPayload},
    error::EngineClientError,
};

use std::{
    collections::{HashMap, HashSet},
    path::{Path, PathBuf},
    sync::Arc,
    time::Duration,
};
use tokio::{process::Child, sync::Mutex};
use zeroize::Zeroizing;

/// Refresh payload delivered to [`OnVenueCredentialsRefreshed`].
///
/// In addition to the new `session` URLs, this carries the *full*
/// credential triple actually used for the login (when Python supplies
/// it — older emitters did not). Without these fields the keyring's
/// `user_id` / `password` / `is_demo` silently drift away from what
/// the user just authenticated with whenever they switch accounts,
/// toggle demo↔prod, or change the password — and the next cold-start
/// fallback login then re-tries the stale value.
#[derive(Clone)]
pub struct VenueCredentialsRefresh {
    pub session: TachibanaSessionWire,
    pub user_id: Option<String>,
    pub password: Option<Zeroizing<String>>,
    pub is_demo: Option<bool>,
}

/// Callback fired from inside `start()` whenever a `VenueCredentialsRefreshed`
/// event is observed during the `SetVenueCredentials` → `VenueReady` window.
/// Wired by `main.rs` to (a) persist the refreshed credentials into the OS
/// keyring and (b) call back into `set_venue_credentials` so the next
/// restart re-injects the new value. Held in an `Arc<Mutex<Option<...>>>`
/// so it can be installed once and survive across `Arc<ProcessManager>`
/// clones.
pub type OnVenueCredentialsRefreshed =
    Box<dyn Fn(VenueCredentialsRefresh) + Send + Sync + 'static>;

// ── EngineCommand ─────────────────────────────────────────────────────────────

/// How to launch the Python data engine.
///
/// In production the engine is shipped as a single PyInstaller-frozen binary
/// installed next to `flowsurface(.exe)`.  In dev installs the engine is run
/// as `python -m engine` from the repo's virtualenv.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EngineCommand {
    /// A standalone executable (PyInstaller / Nuitka output).
    Bundled(PathBuf),
    /// `python -m engine` — used when no bundled binary is found.
    System { program: String, args: Vec<String> },
}

impl EngineCommand {
    /// Convenience: resolve relative to `std::env::current_exe()`.
    pub fn resolve() -> Result<Self, EngineClientError> {
        let exe = std::env::current_exe()?;
        let dir = exe
            .parent()
            .ok_or_else(|| std::io::Error::other("current_exe has no parent"))?
            .to_path_buf();
        Self::resolve_with(Some(&dir), None)
    }

    /// Resolve the engine command.
    ///
    /// Order of precedence:
    /// 1. `explicit_override` — if provided, treated as a Python interpreter
    ///    when its filename matches `python*` / `py` (in which case we run
    ///    `<override> -m engine`); otherwise treated as a frozen `Bundled`
    ///    engine binary and run with no extra args.
    /// 2. `<base_dir>/flowsurface-engine[.exe]` if the file exists.
    /// 3. `python -m engine` fallback for dev installs.
    pub fn resolve_with(
        base_dir: Option<&Path>,
        explicit_override: Option<&Path>,
    ) -> Result<Self, EngineClientError> {
        if let Some(p) = explicit_override {
            return Ok(if looks_like_python_interpreter(p) {
                EngineCommand::System {
                    program: p.to_string_lossy().into_owned(),
                    args: vec!["-m".to_string(), "engine".to_string()],
                }
            } else {
                EngineCommand::Bundled(p.to_path_buf())
            });
        }

        if let Some(dir) = base_dir {
            let exe_name = if cfg!(windows) {
                "flowsurface-engine.exe"
            } else {
                "flowsurface-engine"
            };
            let candidate = dir.join(exe_name);
            if candidate.exists() {
                return Ok(EngineCommand::Bundled(candidate));
            }
        }

        Ok(EngineCommand::System {
            program: "python".to_string(),
            args: vec!["-m".to_string(), "engine".to_string()],
        })
    }

    /// Underlying program path / name (for `Command::new`).
    pub fn program(&self) -> &str {
        match self {
            EngineCommand::Bundled(p) => p.to_str().unwrap_or("flowsurface-engine"),
            EngineCommand::System { program, .. } => program.as_str(),
        }
    }

    /// Extra args to prepend before `Command::new(...).args(...)`.
    pub fn args(&self) -> &[String] {
        match self {
            EngineCommand::Bundled(_) => &[],
            EngineCommand::System { args, .. } => args.as_slice(),
        }
    }
}

/// True iff the file name looks like a Python interpreter — used by
/// `EngineCommand::resolve_with` to decide whether `--engine-cmd <path>`
/// should be wrapped as `<path> -m engine` instead of run as a frozen binary.
fn looks_like_python_interpreter(path: &Path) -> bool {
    let Some(name) = path.file_name().and_then(|s| s.to_str()) else {
        return false;
    };
    let stem = name.strip_suffix(".exe").unwrap_or(name).to_ascii_lowercase();
    // Matches: python, python3, python3.12, py, pypy, pypy3 — but not arbitrary
    // binaries that happen to start with "p".
    matches!(stem.as_str(), "py" | "pypy" | "pypy3")
        || stem.starts_with("python")
        || stem.starts_with("pypy")
}

const BACKOFF_BASE_MS: u64 = 500;
const BACKOFF_MAX_MS: u64 = 30_000;

/// Build the JSON line that the Rust supervisor writes to the Python
/// engine's stdin during boot. Exposed at crate level so the dev-flag
/// regression test (`engine-client/tests/dev_login_flag_release.rs`)
/// can call exactly the production builder — that way a future change
/// to the production payload (e.g. dropping `dev_tachibana_login_allowed`)
/// breaks the test instead of going unnoticed.
///
/// Schema (T3 / schema 1.2):
///
/// ```json
/// {
///   "port": <u16>,
///   "token": "<token>",
///   "dev_tachibana_login_allowed": <bool>   // mirrors cfg!(debug_assertions)
/// }
/// ```
///
/// `dev_tachibana_login_allowed` reflects the **build profile** so a
/// release binary can never enable Python's env fast path even if the
/// surrounding shell has the dev variables set (R10 / architecture
/// §2.1.1 / H-2).
pub fn build_stdin_payload(port: u16, token: &str) -> Result<String, EngineClientError> {
    let dev_tachibana_login_allowed = cfg!(debug_assertions);
    let payload = serde_json::json!({
        "port": port,
        "token": token,
        "dev_tachibana_login_allowed": dev_tachibana_login_allowed,
    });
    let mut s = serde_json::to_string(&payload)?;
    s.push('\n');
    Ok(s)
}

// ── PythonProcess ─────────────────────────────────────────────────────────────

pub struct PythonProcess {
    child: Child,
    port: u16,
    token: String,
}

impl PythonProcess {
    /// Backwards-compat shim: spawn via a bare program name (no extra args).
    ///
    /// Preserves the pre-Phase-6 contract used by older tests.  New callers
    /// should prefer [`PythonProcess::spawn_with`], which accepts an
    /// [`EngineCommand`] and pipes stderr/stdout into the Rust logger.
    pub async fn spawn(python_cmd: &str, port: u16) -> Result<Self, EngineClientError> {
        let cmd = EngineCommand::System {
            program: python_cmd.to_string(),
            args: Vec::new(),
        };
        Self::spawn_with(&cmd, port).await
    }

    /// Spawn the Python data engine.
    ///
    /// The engine reads a single JSON line from stdin: `{"port": <port>,
    /// "token": "<token>"}` then binds and serves.  Both stdout and stderr
    /// are piped into the Rust `log` crate (engine messages surface in the
    /// same `flowsurface.log` file as Rust messages — spec §6.4).
    pub async fn spawn_with(cmd: &EngineCommand, port: u16) -> Result<Self, EngineClientError> {
        let token = generate_token();
        let stdin_payload = build_stdin_payload(port, &token)?;

        let mut command = tokio::process::Command::new(cmd.program());
        command
            .args(cmd.args())
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .kill_on_drop(true);

        let mut child = command.spawn()?;

        if let Some(stdin) = child.stdin.take() {
            use tokio::io::AsyncWriteExt;
            let mut stdin = stdin;
            stdin.write_all(stdin_payload.as_bytes()).await?;
            stdin.shutdown().await?;
        }

        if let Some(stdout) = child.stdout.take() {
            tokio::spawn(forward_lines(stdout, log::Level::Info));
        }
        if let Some(stderr) = child.stderr.take() {
            tokio::spawn(forward_lines(stderr, log::Level::Warn));
        }

        Ok(Self { child, port, token })
    }

    pub async fn wait(&mut self) -> std::io::Result<std::process::ExitStatus> {
        self.child.wait().await
    }

    pub fn port(&self) -> u16 {
        self.port
    }

    pub fn token(&self) -> &str {
        &self.token
    }
}

// ── SubscriptionKey ───────────────────────────────────────────────────────────

/// Identifies a single engine subscription for re-apply after restart.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct SubscriptionKey {
    pub venue: String,
    pub ticker: String,
    pub stream: String,
    pub timeframe: Option<String>,
    pub market: String,
}

// ── ProcessManager ────────────────────────────────────────────────────────────

pub struct ProcessManager {
    pub command: EngineCommand,
    pub active_subscriptions: Arc<Mutex<HashSet<SubscriptionKey>>>,
    /// Proxy URL kept as source-of-truth on the Rust side (spec §5.3, §5.4).
    /// Sent via `SetProxy` after every `Ready` handshake.
    pub proxy_url: Arc<Mutex<Option<String>>>,
    /// Venue-scoped credentials kept as source-of-truth so we can re-inject
    /// them after a Python restart (Tachibana managed-mode recovery, T3).
    /// One entry per venue, keyed by venue name string.
    pub venue_credentials: Arc<Mutex<Vec<VenueCredentialsPayload>>>,
    /// Optional hook for `VenueCredentialsRefreshed` observed during the
    /// in-`start()` synchronous wait. The hook lives across the whole
    /// process lifetime; main.rs installs it once at boot.
    pub on_venue_credentials_refreshed: Arc<Mutex<Option<OnVenueCredentialsRefreshed>>>,
}

impl ProcessManager {
    /// Backwards-compat constructor: wraps a bare program name into a
    /// `EngineCommand::System { program, args: ["-m", "engine"] }`.
    pub fn new(python_cmd: impl Into<String>) -> Self {
        let cmd = EngineCommand::System {
            program: python_cmd.into(),
            args: Vec::new(),
        };
        Self::with_command(cmd)
    }

    pub fn with_command(command: EngineCommand) -> Self {
        Self {
            command,
            active_subscriptions: Arc::new(Mutex::new(HashSet::new())),
            proxy_url: Arc::new(Mutex::new(None)),
            venue_credentials: Arc::new(Mutex::new(Vec::new())),
            on_venue_credentials_refreshed: Arc::new(Mutex::new(None)),
        }
    }

    /// Install the credentials-refresh callback. Replaces any prior hook.
    /// `Box::new` is the simplest call-site; the manager wraps it further.
    pub async fn set_on_venue_credentials_refreshed(
        &self,
        cb: OnVenueCredentialsRefreshed,
    ) {
        *self.on_venue_credentials_refreshed.lock().await = Some(cb);
    }

    /// Update the stored proxy URL; also re-applies it on the next restart.
    pub async fn set_proxy(&self, url: Option<String>) {
        *self.proxy_url.lock().await = url;
    }

    /// Replace the stored credential payload for the venue identified by the
    /// payload's tag. The setter only updates the in-memory store; the
    /// subsequent `SetVenueCredentials` IPC send is performed by `start()`
    /// after every successful handshake. T3 wires the actual UI / keyring
    /// trigger.
    pub async fn set_venue_credentials(&self, payload: VenueCredentialsPayload) {
        let mut store = self.venue_credentials.lock().await;
        let tag = payload.venue_tag();
        store.retain(|p| p.venue_tag() != tag);
        store.push(payload);
    }

    /// Splice a refreshed `TachibanaSessionWire` into the stored Tachibana
    /// credential payload. Called from inside `start()` when a
    /// `VenueCredentialsRefreshed` arrives during the VenueReady wait so
    /// the next restart re-injects the post-login session rather than
    /// the pre-login one.
    ///
    /// Public so the regression test (`process_creds_refresh_hook.rs`)
    /// can call exactly the production helper rather than re-implementing
    /// it (which would defeat the regression check). Future venues add a
    /// `match` arm — the explicit `match` rather than `if let` ensures
    /// adding a new variant forces editing this site.
    /// Single code path for `VenueCredentialsRefreshed` handling — used
    /// both by the in-`start()` `VenueReady` wait *and* by the long-lived
    /// continuation listener spawned at the end of `start()`. Keeping it
    /// in one helper guarantees that an in-flight refresh and a post-
    /// startup refresh apply identical side effects (in-memory patch +
    /// keyring write via the registered hook). Order matters: patch the
    /// in-memory store *before* invoking the hook so a hook that reads
    /// back the manager state (e.g. for diagnostic logging) sees the
    /// refreshed value rather than the stale one.
    pub async fn handle_credentials_refreshed(
        store: &Mutex<Vec<VenueCredentialsPayload>>,
        hook: &Mutex<Option<OnVenueCredentialsRefreshed>>,
        refresh: &VenueCredentialsRefresh,
    ) {
        Self::patch_in_memory_credentials(store, refresh).await;
        if let Some(cb) = hook.lock().await.as_ref() {
            cb(refresh.clone());
        }
    }

    /// Backwards-compat wrapper — patches only the session field. Used
    /// by the existing regression test
    /// `engine-client/tests/process_creds_refresh_hook.rs`.
    pub async fn patch_in_memory_session(
        store: &Mutex<Vec<VenueCredentialsPayload>>,
        new_session: &TachibanaSessionWire,
    ) {
        let refresh = VenueCredentialsRefresh {
            session: new_session.clone(),
            user_id: None,
            password: None,
            is_demo: None,
        };
        Self::patch_in_memory_credentials(store, &refresh).await;
    }

    /// Splice a refresh into every Tachibana payload in the store. The
    /// session is always replaced; `user_id` / `password` / `is_demo`
    /// are replaced *only when present in the refresh*. This means a
    /// refresh from an older Python emitter (session-only) preserves
    /// the existing creds, while a current emitter overwrites all four
    /// so demo/prod and account switches reach the keyring.
    pub async fn patch_in_memory_credentials(
        store: &Mutex<Vec<VenueCredentialsPayload>>,
        refresh: &VenueCredentialsRefresh,
    ) {
        let mut guard = store.lock().await;
        for payload in guard.iter_mut() {
            match payload {
                VenueCredentialsPayload::Tachibana(creds) => {
                    creds.session = Some(refresh.session.clone());
                    if let Some(uid) = &refresh.user_id {
                        creds.user_id = uid.clone();
                    }
                    if let Some(pw) = &refresh.password {
                        creds.password = pw.clone();
                    }
                    if let Some(demo) = refresh.is_demo {
                        creds.is_demo = demo;
                    }
                }
            }
        }
    }

    /// Post-handshake startup sequence — exposed as a separate method so
    /// integration tests can drive it against a mock WebSocket without
    /// having to spawn a real Python subprocess. Production `start()`
    /// calls exactly this method after `EngineConnection::connect`
    /// completes the handshake. Keeping the body here (rather than a
    /// separate parallel implementation in tests) means a regression
    /// in the `SetVenueCredentials → VenueReady → resubscribe` ordering
    /// fails the test instead of going unnoticed.
    ///
    /// Sequence (architecture spec §2.4):
    /// 1. Subscribe to event broadcast BEFORE any send so the
    ///    `SetVenueCredentials → VenueReady` window cannot lose events.
    /// 2. SetProxy (when configured).
    /// 3. SetVenueCredentials per stored payload.
    /// 4. Wait for `VenueReady` / `VenueError` per `request_id`,
    ///    bounded by `VENUE_READY_TIMEOUT`.
    /// 5. Skip resubscribe for any venue whose credential injection
    ///    *failed* terminally (`VenueError`) — a `Subscribe` for an
    ///    un-authenticated venue races the user's re-login and tends
    ///    to surface as "not authenticated" stream errors.
    /// 6. Spawn the long-lived `VenueCredentialsRefreshed` listener.
    /// 7. Re-send saved subscriptions (excluding failed venues).
    pub async fn apply_after_handshake(&self, connection: &EngineConnection) {
        // Step 1: subscribe early.
        let mut event_rx = connection.subscribe_events();

        // Step 2: SetProxy.
        let proxy = self.proxy_url.lock().await.clone();
        if proxy.is_some() {
            let _ = connection
                .send(crate::dto::Command::SetProxy { url: proxy })
                .await;
        }

        // Step 3: re-inject venue credentials. We track each
        // request_id → venue_tag mapping so a `VenueError` can be
        // attributed back to the venue that failed and Subscribe is
        // skipped for it (Findings #1).
        let creds_snapshot = self.venue_credentials.lock().await.clone();
        let mut pending_request_ids: HashSet<String> = HashSet::new();
        let mut request_id_to_venue: HashMap<String, &'static str> = HashMap::new();
        let mut failed_venues: HashSet<&'static str> = HashSet::new();
        for payload in creds_snapshot {
            let request_id = uuid::Uuid::new_v4().to_string();
            pending_request_ids.insert(request_id.clone());
            request_id_to_venue.insert(request_id.clone(), payload.venue_tag());
            let _ = connection
                .send(crate::dto::Command::SetVenueCredentials {
                    request_id,
                    payload,
                })
                .await;
        }

        // Step 4: wait for VenueReady / VenueError per request_id.
        const VENUE_READY_TIMEOUT: Duration = Duration::from_secs(60);
        if !pending_request_ids.is_empty() {
            let deadline = tokio::time::Instant::now() + VENUE_READY_TIMEOUT;
            'wait: while !pending_request_ids.is_empty() {
                let now = tokio::time::Instant::now();
                if now >= deadline {
                    log::warn!(
                        "Timed out waiting for VenueReady after SetVenueCredentials \
                         ({} pending request_id(s)) — proceeding to subscribe anyway",
                        pending_request_ids.len(),
                    );
                    break;
                }
                let remaining = deadline - now;
                match tokio::time::timeout(remaining, event_rx.recv()).await {
                    Ok(Ok(EngineEvent::VenueReady { request_id, .. })) => {
                        match request_id {
                            Some(rid) => {
                                pending_request_ids.remove(&rid);
                            }
                            None if pending_request_ids.len() == 1 => {
                                let only =
                                    pending_request_ids.iter().next().cloned();
                                if let Some(rid) = only {
                                    pending_request_ids.remove(&rid);
                                }
                            }
                            None => {
                                log::warn!(
                                    "VenueReady without request_id while {} are pending — cannot disambiguate",
                                    pending_request_ids.len(),
                                );
                            }
                        }
                    }
                    Ok(Ok(EngineEvent::VenueError {
                        request_id,
                        venue,
                        code,
                        message,
                    })) => {
                        // Attribute the failure to a venue tag — prefer
                        // the request_id mapping (authoritative), fall
                        // back to the event's `venue` string if Python
                        // dropped request_id. Without attribution we
                        // can't safely skip resubscribe and end up
                        // sending Subscribe to an un-authenticated venue.
                        // Fallback: match the event's venue string against
                        // the stored tags. Adding a new venue here forces
                        // editing the match.
                        let venue_fallback: Option<&'static str> = match venue.as_str()
                        {
                            "tachibana" => Some("tachibana"),
                            _ => None,
                        };
                        let failed_tag: Option<&'static str> = request_id
                            .as_ref()
                            .and_then(|rid| request_id_to_venue.get(rid).copied())
                            .or(venue_fallback);
                        if let Some(rid) = &request_id {
                            pending_request_ids.remove(rid);
                        }
                        if let Some(tag) = failed_tag {
                            failed_venues.insert(tag);
                        }
                        log::warn!(
                            "VenueError during startup: venue={venue} code={code} message={message} \
                             — Subscribe will be skipped for failed venue",
                        );
                    }
                    Ok(Ok(EngineEvent::VenueCredentialsRefreshed {
                        session,
                        user_id,
                        password,
                        is_demo,
                        ..
                    })) => {
                        let refresh = VenueCredentialsRefresh {
                            session,
                            user_id,
                            password,
                            is_demo,
                        };
                        Self::handle_credentials_refreshed(
                            &self.venue_credentials,
                            &self.on_venue_credentials_refreshed,
                            &refresh,
                        )
                        .await;
                    }
                    Ok(Ok(_)) => {}
                    Ok(Err(tokio::sync::broadcast::error::RecvError::Lagged(n))) => {
                        log::warn!(
                            "engine event broadcast lagged by {n} during VenueReady wait — resubscribing"
                        );
                        event_rx = connection.subscribe_events();
                        break 'wait;
                    }
                    Ok(Err(tokio::sync::broadcast::error::RecvError::Closed)) => {
                        log::warn!(
                            "engine connection dropped while waiting for VenueReady"
                        );
                        break;
                    }
                    Err(_elapsed) => {
                        log::warn!(
                            "Timed out waiting for VenueReady after SetVenueCredentials \
                             ({} pending request_id(s)) — proceeding to subscribe anyway",
                            pending_request_ids.len(),
                        );
                        break;
                    }
                }
            }
        }

        // Step 6: continuation listener for refreshes that arrive
        // *after* the start-up wait (user-initiated re-logins).
        let creds_store = Arc::clone(&self.venue_credentials);
        let hook = Arc::clone(&self.on_venue_credentials_refreshed);
        tokio::spawn(async move {
            loop {
                match event_rx.recv().await {
                    Ok(EngineEvent::VenueCredentialsRefreshed {
                        session,
                        user_id,
                        password,
                        is_demo,
                        ..
                    }) => {
                        let refresh = VenueCredentialsRefresh {
                            session,
                            user_id,
                            password,
                            is_demo,
                        };
                        ProcessManager::handle_credentials_refreshed(
                            &creds_store,
                            &hook,
                            &refresh,
                        )
                        .await;
                    }
                    Ok(EngineEvent::ConnectionDropped) => break,
                    Err(tokio::sync::broadcast::error::RecvError::Closed) => break,
                    Err(tokio::sync::broadcast::error::RecvError::Lagged(n)) => {
                        log::warn!(
                            "creds-refresh listener lagged by {n} — continuing on new tail"
                        );
                    }
                    Ok(_) => {}
                }
            }
        });

        // Step 7: re-apply saved subscriptions, skipping any venue whose
        // SetVenueCredentials terminally failed during this window.
        let subs = self.active_subscriptions.lock().await.clone();
        for sub in &subs {
            if failed_venues.contains(sub.venue.as_str()) {
                log::warn!(
                    "Skipping Subscribe for venue={} ticker={} stream={} — credentials failed during this start-up",
                    sub.venue, sub.ticker, sub.stream,
                );
                continue;
            }
            let _ = connection
                .send(crate::dto::Command::Subscribe {
                    venue: sub.venue.clone(),
                    ticker: sub.ticker.clone(),
                    stream: sub.stream.clone(),
                    timeframe: sub.timeframe.clone(),
                    market: sub.market.clone(),
                })
                .await;
        }
    }

    /// Spawn the Python process on `port`, handshake, then apply proxy + subscriptions.
    ///
    /// Recovery sequence (spec §4.5, §5.3):
    /// 1. Hello / Ready  — already performed inside `EngineConnection::connect`
    /// 2. SetProxy       — if a proxy URL is stored
    /// 3. Subscribe      — re-send all active subscriptions
    pub async fn start(&self, port: u16) -> Result<EngineConnection, EngineClientError> {
        let mut proc = PythonProcess::spawn_with(&self.command, port).await?;

        let url = format!("ws://127.0.0.1:{port}");

        // Retry connecting with exponential backoff while the process is starting up.
        // Total wait budget: 50+100+200+400+800+1600 ≈ 3.2 s before giving up.
        const MAX_CONNECT_ATTEMPTS: u32 = 6;
        let connection = {
            let mut delay_ms = 50u64;
            let mut attempt = 0u32;
            loop {
                tokio::time::sleep(Duration::from_millis(delay_ms)).await;
                match EngineConnection::connect(&url, proc.token()).await {
                    Ok(conn) => break conn,
                    Err(EngineClientError::ConnectionRefused) if attempt < MAX_CONNECT_ATTEMPTS => {
                        log::debug!(
                            "engine not ready yet (attempt {}/{}), retrying in {}ms",
                            attempt + 1,
                            MAX_CONNECT_ATTEMPTS,
                            delay_ms * 2,
                        );
                        delay_ms = (delay_ms * 2).min(1_600);
                        attempt += 1;
                    }
                    Err(e) => return Err(e),
                }
            }
        };

        self.apply_after_handshake(&connection).await;

        // Detach the process — it outlives this function.
        // kill_on_drop(true) ensures the child is killed when the task future is dropped.
        tokio::spawn(async move {
            match proc.wait().await {
                Ok(status) if !status.success() => {
                    log::warn!("python engine exited with status: {status}");
                }
                Err(e) => {
                    log::warn!("failed to wait for python engine process: {e}");
                }
                _ => {}
            }
        });

        Ok(connection)
    }

    /// Run the engine indefinitely, restarting with exponential backoff on failure.
    ///
    /// - `on_ready`   — called once after each successful handshake (UI: clear "restarting").
    /// - `on_restart` — called each time a restart is triggered   (UI: show "restarting").
    pub async fn run_with_recovery(
        self: Arc<Self>,
        port: u16,
        on_restart: impl Fn() + Send + Sync + 'static,
        on_ready: impl Fn() + Send + Sync + 'static,
    ) {
        let mut backoff_ms = BACKOFF_BASE_MS;

        loop {
            match self.start(port).await {
                Ok(conn) => {
                    backoff_ms = BACKOFF_BASE_MS; // reset on success
                    log::info!("engine connection established");
                    on_ready();

                    // Wait until the WS read loop exits (remote close or IO error).
                    // Using wait_closed() instead of RecvError::Closed because
                    // EngineConnection itself holds a broadcast::Sender, so the
                    // channel is never "Closed" while the conn is alive.
                    conn.wait_closed().await;
                    log::warn!("engine connection lost — will restart");
                }
                Err(e) => {
                    log::error!("engine start failed: {e}");
                }
            }

            on_restart();

            log::info!("restarting engine in {backoff_ms}ms …");
            tokio::time::sleep(Duration::from_millis(backoff_ms)).await;
            backoff_ms = (backoff_ms * 2).min(BACKOFF_MAX_MS);
        }
    }
}

// ── stdout/stderr → log forwarding ────────────────────────────────────────────

/// Read `reader` line by line and emit each line via `log::log!(level, ...)`.
///
/// Lines are tagged with `target = "engine"` so the fern dispatch can route
/// them to the same sink as Rust-side `flowsurface_*` log targets.
async fn forward_lines<R>(reader: R, level: log::Level)
where
    R: tokio::io::AsyncRead + Unpin,
{
    use tokio::io::{AsyncBufReadExt, BufReader};
    let mut lines = BufReader::new(reader).lines();
    loop {
        match lines.next_line().await {
            Ok(Some(line)) => {
                if !line.trim().is_empty() {
                    log::log!(target: "engine", level, "{line}");
                }
            }
            Ok(None) => break,
            Err(e) => {
                log::warn!(target: "engine", "engine pipe read error: {e}");
                break;
            }
        }
    }
}

// ── Token generation ──────────────────────────────────────────────────────────

fn generate_token() -> String {
    use std::fmt::Write;
    let bytes: [u8; 16] = rand::random();
    let mut s = String::with_capacity(32);
    for b in &bytes {
        let _ = write!(s, "{b:02x}");
    }
    s
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dto::{TachibanaCredentialsWire, VenueCredentialsPayload};

    fn dummy_tachibana(user: &str) -> VenueCredentialsPayload {
        VenueCredentialsPayload::Tachibana(TachibanaCredentialsWire {
            user_id: user.to_string(),
            password: "p".to_string().into(),
            second_password: None,
            is_demo: true,
            session: None,
        })
    }

    #[test]
    fn venue_tag_returns_tachibana_for_tachibana_variant() {
        let p = dummy_tachibana("alice");
        assert_eq!(p.venue_tag(), "tachibana");
    }

    #[tokio::test]
    async fn set_venue_credentials_replaces_same_venue_last_wins() {
        let pm = ProcessManager::new("python");
        pm.set_venue_credentials(dummy_tachibana("alice")).await;
        pm.set_venue_credentials(dummy_tachibana("bob")).await;
        let store = pm.venue_credentials.lock().await;
        assert_eq!(store.len(), 1, "same venue tag must dedupe to last entry");
        let VenueCredentialsPayload::Tachibana(c) = &store[0];
        assert_eq!(c.user_id, "bob", "last-write-wins for same venue tag");
    }
}
