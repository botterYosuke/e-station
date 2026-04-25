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
    collections::HashSet,
    path::{Path, PathBuf},
    sync::Arc,
    time::Duration,
};
use tokio::{process::Child, sync::Mutex};

/// Callback fired from inside `start()` whenever a `VenueCredentialsRefreshed`
/// event is observed during the `SetVenueCredentials` → `VenueReady` window.
/// Wired by `main.rs` to (a) persist the refreshed session into the OS
/// keyring and (b) call back into `set_venue_credentials` so the next
/// restart re-injects the new value. Held in an `Arc<Mutex<Option<...>>>`
/// so it can be installed once and survive across `Arc<ProcessManager>`
/// clones.
pub type OnVenueCredentialsRefreshed =
    Box<dyn Fn(TachibanaSessionWire) + Send + Sync + 'static>;

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
        new_session: &TachibanaSessionWire,
    ) {
        Self::patch_in_memory_session(store, new_session).await;
        if let Some(cb) = hook.lock().await.as_ref() {
            cb(new_session.clone());
        }
    }

    pub async fn patch_in_memory_session(
        store: &Mutex<Vec<VenueCredentialsPayload>>,
        new_session: &TachibanaSessionWire,
    ) {
        let mut guard = store.lock().await;
        for payload in guard.iter_mut() {
            match payload {
                VenueCredentialsPayload::Tachibana(creds) => {
                    creds.session = Some(new_session.clone());
                }
            }
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

        // Subscribe to engine events BEFORE any send so credentials-side
        // events emitted during the synchronous `SetVenueCredentials →
        // VenueReady` window cannot be missed by the broadcast channel
        // (Findings #2). `subscribe_events` returns a fresh `Receiver`
        // that captures every event from this point forward.
        let mut event_rx = connection.subscribe_events();

        // Step 2: SetProxy (spec §5.4) — sent after Ready, before any Subscribe.
        let proxy = self.proxy_url.lock().await.clone();
        if proxy.is_some() {
            let _ = connection
                .send(crate::dto::Command::SetProxy { url: proxy })
                .await;
        }

        // Step 2b: re-inject venue credentials (Tachibana managed-mode
        // recovery — docs/plan/tachibana/architecture.md §2.4). Each payload
        // is cloned so the stored copy survives this restart cycle and can
        // be re-sent on the next one.
        let creds_snapshot = self.venue_credentials.lock().await.clone();
        let mut pending_request_ids: HashSet<String> = HashSet::new();
        for payload in creds_snapshot {
            let request_id = uuid::Uuid::new_v4().to_string();
            pending_request_ids.insert(request_id.clone());
            let _ = connection
                .send(crate::dto::Command::SetVenueCredentials {
                    request_id,
                    payload,
                })
                .await;
        }

        // Step 2c: wait until every `SetVenueCredentials` has produced a
        // matching `VenueReady` (or `VenueError`) before resubscribing to
        // streams (Findings #1). Architecture spec §2.4 sequence is
        // `SetProxy → SetVenueCredentials → VenueReady → resubscribe`.
        // The wait is bounded by `VENUE_READY_TIMEOUT` so a stuck Python
        // can never block the manager indefinitely.
        //
        // While we wait we also handle `VenueCredentialsRefreshed`: the
        // optional callback persists the new session, and the in-memory
        // `venue_credentials` store is patched in-place so the next
        // restart cycle re-injects the refreshed session rather than the
        // pre-login one.
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
                                // Fallback for Python emitters that drop
                                // request_id (legacy / future schema): if
                                // only one is outstanding, attribute it.
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
                        code,
                        message,
                        ..
                    })) => {
                        if let Some(rid) = &request_id {
                            pending_request_ids.remove(rid);
                        }
                        log::warn!(
                            "VenueError during startup: code={code} message={message}"
                        );
                    }
                    Ok(Ok(EngineEvent::VenueCredentialsRefreshed { session, .. })) => {
                        Self::handle_credentials_refreshed(
                            &self.venue_credentials,
                            &self.on_venue_credentials_refreshed,
                            &session,
                        )
                        .await;
                    }
                    Ok(Ok(_)) => {
                        // Other events flow past — the broadcast channel
                        // has many other consumers (the stream handlers
                        // in main.rs / backend.rs), so we just ignore
                        // anything not addressed to us.
                    }
                    Ok(Err(tokio::sync::broadcast::error::RecvError::Lagged(n))) => {
                        log::warn!(
                            "engine event broadcast lagged by {n} during VenueReady wait — resubscribing"
                        );
                        // A lagged Receiver may have skipped a
                        // VenueCredentialsRefreshed mid-window. Resubscribe
                        // so the continuation listener (spawned below)
                        // picks up future refreshes, and break out of the
                        // wait so we don't stall on a request_id we already
                        // dropped.
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

        // Continuation listener: same `event_rx` carries forward so any
        // `VenueCredentialsRefreshed` arriving *after* the start-up wait
        // (user-initiated re-login via `RequestVenueLogin`, or a trailing
        // refresh emitted right after `VenueReady`) is handled by the
        // same code path. This is the *single* listener for refreshes —
        // main.rs no longer spawns its own to avoid dual-write races on
        // the keyring / in-memory store.
        let creds_store = Arc::clone(&self.venue_credentials);
        let hook = Arc::clone(&self.on_venue_credentials_refreshed);
        tokio::spawn(async move {
            loop {
                match event_rx.recv().await {
                    Ok(EngineEvent::VenueCredentialsRefreshed { session, .. }) => {
                        ProcessManager::handle_credentials_refreshed(
                            &creds_store,
                            &hook,
                            &session,
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

        // Step 3: re-apply saved subscriptions (no-op on first start).
        let subs = self.active_subscriptions.lock().await.clone();
        for sub in &subs {
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
