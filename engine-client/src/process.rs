/// Python data engine subprocess management.
///
/// `PythonProcess` spawns the Python engine and communicates its `{port, token}`
/// via stdin as a JSON line.  `ProcessManager` wraps it with exponential-backoff
/// restart logic and re-applies subscriptions after each recovery.
use crate::{connection::EngineConnection, error::EngineClientError};

use std::{
    collections::HashSet,
    path::{Path, PathBuf},
    sync::Arc,
    time::Duration,
};
use tokio::{process::Child, sync::Mutex};

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

    /// Underlying program path / name (for `Command::new`). Returns
    /// `&OsStr` so paths that aren't valid UTF-8 (or simply contain
    /// non-ASCII characters in user folders, e.g. `C:\Users\日本語\...`
    /// on Windows) are passed through verbatim instead of being
    /// silently replaced by a `"flowsurface-engine"` fallback string
    /// — see invariant T35-H5-PathFidelity.
    pub fn program(&self) -> &std::ffi::OsStr {
        match self {
            EngineCommand::Bundled(p) => p.as_os_str(),
            EngineCommand::System { program, .. } => std::ffi::OsStr::new(program.as_str()),
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
    let stem = name
        .strip_suffix(".exe")
        .unwrap_or(name)
        .to_ascii_lowercase();
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
    /// Set of venue tags currently observed as `VenueReady` by the
    /// post-handshake event drain. Populated inside
    /// [`Self::apply_after_handshake_with_timeout`] as `VenueReady`
    /// arrives, cleared on `VenueError`. Exposes the post-handshake
    /// venue state to clients that subscribe to events **after**
    /// `start()` returns (broadcast does not replay), so the iced UI
    /// can synchronise its local FSM with venues whose initial
    /// `VenueReady` was emitted before the UI subscription wired up.
    /// Reviewer flagged the silent-loss path on 2026-04-26 (R2).
    pub venue_ready_state: Arc<Mutex<HashSet<String>>>,
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
            venue_ready_state: Arc::new(Mutex::new(HashSet::new())),
        }
    }

    /// Non-blocking probe of the post-handshake venue readiness cache.
    /// Returns `true` if the most recent observed lifecycle event for
    /// `venue` was a `VenueReady` (and not a subsequent `VenueError`).
    /// Used by `Flowsurface::Message::EngineConnected` to bootstrap
    /// the FSM without racing the broadcast subscription window. Falls
    /// back to `false` when the lock is contended — a benign miss
    /// that only delays UI sync until the next live event.
    pub fn try_is_venue_ready(&self, venue: &str) -> bool {
        self.venue_ready_state
            .try_lock()
            .map(|state| state.contains(venue))
            .unwrap_or(false)
    }

    /// Update the stored proxy URL; also re-applies it on the next restart.
    pub async fn set_proxy(&self, url: Option<String>) {
        *self.proxy_url.lock().await = url;
    }

    /// Post-handshake startup sequence — exposed as a separate method so
    /// integration tests can drive it against a mock WebSocket without
    /// having to spawn a real Python subprocess. Production `start()`
    /// calls exactly this method after `EngineConnection::connect`
    /// completes the handshake.
    ///
    /// Sequence (schema 2.x — autonomous login):
    /// 1. Clear stale VenueReady cache from the previous cycle.
    /// 2. SetProxy (when configured).
    /// 3. Re-send saved subscriptions.
    pub async fn apply_after_handshake(&self, connection: &EngineConnection) {
        self.apply_after_handshake_with_timeout(connection, Duration::from_secs(60))
            .await;
    }

    /// Same as [`Self::apply_after_handshake`] but accepts an explicit
    /// timeout parameter for test compatibility. The timeout is unused in
    /// schema 2.x (no VenueReady wait loop), but the parameter is kept so
    /// existing integration test call sites compile without modification.
    #[doc(hidden)]
    pub async fn apply_after_handshake_with_timeout(
        &self,
        connection: &EngineConnection,
        _venue_ready_timeout: Duration,
    ) {
        // Step 0: drop any sticky readiness from a previous cycle.
        // `venue_ready_state` survives across recovery loop iterations
        // (the `Arc<Mutex<…>>` is owned by the `ProcessManager` which
        // is reused). Mirror the global `VENUE_READY_CACHE.clear()` that
        // `main.rs` already performs before respawning the bridge.
        // Reviewer 2026-04-26 R6 (HIGH: sticky snapshot invalidate gap).
        self.venue_ready_state.lock().await.clear();

        // Step 1: subscribe to events (needed for subscribe_events call below).
        let event_rx = connection.subscribe_events();

        // Step 2: SetProxy.
        let proxy = self.proxy_url.lock().await.clone();
        if proxy.is_some()
            && let Err(err) = connection
                .send(crate::dto::Command::SetProxy { url: proxy })
                .await
        {
            log::warn!("SetProxy send failed during apply_after_handshake: {err}");
        }

        // Step 3: re-apply saved subscriptions.
        // In schema 2.x the Python engine performs autonomous login (startup_login)
        // so there is no SetVenueCredentials → VenueReady gate. Subscriptions are
        // re-sent immediately after SetProxy.
        let subs = self.active_subscriptions.lock().await.clone();
        for sub in &subs {
            if let Err(err) = connection
                .send(crate::dto::Command::Subscribe {
                    venue: sub.venue.clone(),
                    ticker: sub.ticker.clone(),
                    stream: sub.stream.clone(),
                    timeframe: sub.timeframe.clone(),
                    market: sub.market.clone(),
                })
                .await
            {
                log::warn!(
                    "Subscribe re-send failed for venue={} ticker={} stream={}: {err}",
                    sub.venue,
                    sub.ticker,
                    sub.stream,
                );
            }
        }

        // Keep event_rx alive so the broadcast sender is not dropped early.
        drop(event_rx);
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

    #[tokio::test]
    async fn set_proxy_stores_url() {
        let pm = ProcessManager::new("python");
        pm.set_proxy(Some("socks5://127.0.0.1:1080".to_string()))
            .await;
        let stored = pm.proxy_url.lock().await.clone();
        assert_eq!(stored, Some("socks5://127.0.0.1:1080".to_string()));
        pm.set_proxy(None).await;
        let stored = pm.proxy_url.lock().await.clone();
        assert!(stored.is_none());
    }
}
