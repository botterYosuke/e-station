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
}

// ── ProcessManager ────────────────────────────────────────────────────────────

pub struct ProcessManager {
    pub python_cmd: String,
    pub active_subscriptions: Arc<Mutex<HashSet<SubscriptionKey>>>,
}

impl ProcessManager {
    pub fn new(python_cmd: impl Into<String>) -> Self {
        Self {
            python_cmd: python_cmd.into(),
            active_subscriptions: Arc::new(Mutex::new(HashSet::new())),
        }
    }

    /// Spawn the Python process on `port`, wait for it to be ready,
    /// connect the WebSocket, and return the `EngineConnection`.
    pub async fn start(&self, port: u16) -> Result<EngineConnection, EngineClientError> {
        let mut proc = PythonProcess::spawn(&self.python_cmd, port).await?;

        // Give the Python process a moment to bind.
        tokio::time::sleep(Duration::from_millis(300)).await;

        let url = format!("ws://127.0.0.1:{port}");
        let connection = EngineConnection::connect(&url, proc.token()).await?;

        // Re-apply saved subscriptions (no-op on first start since the set is empty).
        let subs = self.active_subscriptions.lock().await.clone();
        for sub in &subs {
            let _ = connection
                .send(crate::dto::Command::Subscribe {
                    venue: sub.venue.clone(),
                    ticker: sub.ticker.clone(),
                    stream: sub.stream.clone(),
                    timeframe: sub.timeframe.clone(),
                })
                .await;
        }

        // Detach the process — it outlives this function.
        tokio::spawn(async move {
            let _ = proc.wait().await;
        });

        Ok(connection)
    }

    /// Run the engine indefinitely, restarting with exponential backoff on failure.
    ///
    /// `on_restart` is called each time a restart is triggered (e.g., to signal the UI).
    pub async fn run_with_recovery(
        self: Arc<Self>,
        port: u16,
        on_restart: impl Fn() + Send + Sync + 'static,
    ) {
        let mut backoff_ms = BACKOFF_BASE_MS;

        loop {
            match self.start(port).await {
                Ok(conn) => {
                    backoff_ms = BACKOFF_BASE_MS; // reset on success
                    log::info!("engine connection established");

                    // Wait for the connection to drop (read loop exits).
                    let mut rx = conn.subscribe_events();
                    loop {
                        match rx.recv().await {
                            Err(tokio::sync::broadcast::error::RecvError::Closed) => break,
                            _ => continue,
                        }
                    }
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
