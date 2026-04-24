/// Python data engine subprocess management.
///
/// `PythonProcess` spawns the Python engine and communicates its `{port, token}`
/// via stdin as a JSON line.  `ProcessManager` wraps it with exponential-backoff
/// restart logic and re-applies subscriptions after each recovery.
use crate::{connection::EngineConnection, error::EngineClientError};

use std::{collections::HashSet, sync::Arc, time::Duration};
use tokio::{process::Child, sync::Mutex};

const BACKOFF_BASE_MS: u64 = 500;
const BACKOFF_MAX_MS: u64 = 30_000;

// ── PythonProcess ─────────────────────────────────────────────────────────────

pub struct PythonProcess {
    child: Child,
    port: u16,
    token: String,
}

impl PythonProcess {
    /// Spawn the Python data engine.
    ///
    /// The engine is expected to read a single JSON line from stdin:
    /// `{"port": <port>, "token": "<token>"}` then bind and serve.
    pub async fn spawn(python_cmd: &str, port: u16) -> Result<Self, EngineClientError> {
        let token = generate_token();

        let stdin_payload = format!("{{\"port\":{port},\"token\":\"{token}\"}}\n");

        let mut child = tokio::process::Command::new(python_cmd)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::inherit())
            .stderr(std::process::Stdio::inherit())
            .kill_on_drop(true)
            .spawn()?;

        // Write the config to the engine's stdin.
        if let Some(stdin) = child.stdin.take() {
            use tokio::io::AsyncWriteExt;
            let mut stdin = stdin;
            stdin.write_all(stdin_payload.as_bytes()).await?;
            stdin.shutdown().await?;
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
    pub python_cmd: String,
    pub active_subscriptions: Arc<Mutex<HashSet<SubscriptionKey>>>,
    /// Proxy URL kept as source-of-truth on the Rust side (spec §5.3, §5.4).
    /// Sent via `SetProxy` after every `Ready` handshake.
    pub proxy_url: Arc<Mutex<Option<String>>>,
}

impl ProcessManager {
    pub fn new(python_cmd: impl Into<String>) -> Self {
        Self {
            python_cmd: python_cmd.into(),
            active_subscriptions: Arc::new(Mutex::new(HashSet::new())),
            proxy_url: Arc::new(Mutex::new(None)),
        }
    }

    /// Update the stored proxy URL; also re-applies it on the next restart.
    pub async fn set_proxy(&self, url: Option<String>) {
        *self.proxy_url.lock().await = url;
    }

    /// Spawn the Python process on `port`, handshake, then apply proxy + subscriptions.
    ///
    /// Recovery sequence (spec §4.5, §5.3):
    /// 1. Hello / Ready  — already performed inside `EngineConnection::connect`
    /// 2. SetProxy       — if a proxy URL is stored
    /// 3. Subscribe      — re-send all active subscriptions
    pub async fn start(&self, port: u16) -> Result<EngineConnection, EngineClientError> {
        let mut proc = PythonProcess::spawn(&self.python_cmd, port).await?;

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

        // Step 2: SetProxy (spec §5.4) — sent after Ready, before any Subscribe.
        let proxy = self.proxy_url.lock().await.clone();
        if proxy.is_some() {
            let _ = connection
                .send(crate::dto::Command::SetProxy { url: proxy })
                .await;
        }

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
