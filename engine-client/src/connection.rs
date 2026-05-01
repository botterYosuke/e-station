/// WebSocket connection to the Python data engine.
///
/// `EngineConnection` owns:
/// - a sender half of a channel that drives a background write task, and
/// - a broadcast channel that fan-outs every incoming `EngineEvent` to
///   any number of subscribers (stream futures, fetch waiters, …).
///
/// The connection performs the `Hello`/`Ready` handshake on construction.
use bytes::Bytes;
use fastwebsockets::{FragmentCollector, Frame, OpCode, Payload};
use http_body_util::Empty;
use hyper::{
    Request,
    header::{CONNECTION, UPGRADE},
    upgrade::Upgraded,
};
use hyper_util::rt::{TokioExecutor, TokioIo};
use serde_json::Value;
use std::{sync::Arc, time::Duration};
use tokio::sync::{broadcast, mpsc};

use crate::{
    SCHEMA_MAJOR, SCHEMA_MINOR,
    dto::{Command, EngineEvent},
    error::EngineClientError,
};

const BROADCAST_CAPACITY: usize = 512;
const COMMAND_BUFFER: usize = 256;
const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(10);
const PROBE_TCP_TIMEOUT: Duration = Duration::from_secs(2);
const CLIENT_VERSION: &str = env!("CARGO_PKG_VERSION");

pub struct EngineConnection {
    sender: mpsc::Sender<Command>,
    events: broadcast::Sender<EngineEvent>,
    /// Notified once when the WS read loop exits (remote close or IO error).
    closed: Arc<tokio::sync::Notify>,
    /// Snapshot of `Ready.capabilities` captured during the handshake. Stays
    /// untyped on the Rust side per F-M8 — the UI probes specific paths via
    /// `engine_client::capabilities`. Wrapped in `Arc` so cheap clones can
    /// be passed into widget views without holding a connection borrow.
    capabilities: Arc<Value>,
}

impl std::fmt::Debug for EngineConnection {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("EngineConnection").finish_non_exhaustive()
    }
}

impl EngineConnection {
    /// Connect to the Python engine at `url` (e.g. `ws://127.0.0.1:9999`),
    /// send `Hello`, and wait for `Ready` — verifying the schema version.
    pub async fn connect(url: &str, token: &str) -> Result<Self, EngineClientError> {
        // Default to AppMode::Live — preserves pre-N1.13 behaviour for callers
        // that don't know about modes. Application code (`src/main.rs`) must
        // call `connect_with_mode` once it has parsed `--mode`.
        Self::connect_with_mode(url, token, crate::dto::AppMode::Live).await
    }

    /// External-probe connect with a 2-second TCP timeout.
    ///
    /// Used by `ProcessManager::start_or_attach` to check whether a
    /// manually-started engine is already listening before spawning a new one.
    /// The 2-second TCP timeout lets the probe fail fast when no engine is
    /// running, without blocking the startup sequence for up to `HANDSHAKE_TIMEOUT`.
    ///
    /// On success the caller should call `ProcessManager::apply_after_handshake`.
    /// On any error the caller should fall through to a fresh Python spawn.
    pub async fn probe(
        url: &str,
        token: &str,
        mode: crate::dto::AppMode,
    ) -> Result<Self, EngineClientError> {
        // PROBE_TCP_TIMEOUT caps the TCP connect; HANDSHAKE_TIMEOUT caps the WS
        // upgrade that follows. A half-open HTTP server on 19876 that accepts TCP
        // but never completes the upgrade would otherwise block indefinitely.
        let ws = tokio::time::timeout(
            HANDSHAKE_TIMEOUT,
            connect_ws_with_tcp_timeout(url, Some(PROBE_TCP_TIMEOUT)),
        )
        .await
        .map_err(|_| EngineClientError::HandshakeTimeout)??;

        let (events_tx, _) = broadcast::channel::<EngineEvent>(BROADCAST_CAPACITY);
        let (cmd_tx, cmd_rx) = mpsc::channel::<Command>(COMMAND_BUFFER);

        let (ws, capabilities) = tokio::time::timeout(
            HANDSHAKE_TIMEOUT,
            perform_handshake(ws, token, mode, events_tx.clone()),
        )
        .await
        .map_err(|_| EngineClientError::HandshakeTimeout)??;

        let closed = Arc::new(tokio::sync::Notify::new());
        spawn_io_tasks(ws, cmd_rx, events_tx.clone(), Arc::clone(&closed));

        Ok(Self {
            sender: cmd_tx,
            events: events_tx,
            closed,
            capabilities: Arc::new(capabilities),
        })
    }

    /// N1.13 / R1b H-E: connect and announce the runtime mode
    /// (`AppMode::Live` | `AppMode::Replay`). Python uses this to gate
    /// `/api/replay/*` and reject `StartEngine.engine` mismatches early.
    pub async fn connect_with_mode(
        url: &str,
        token: &str,
        mode: crate::dto::AppMode,
    ) -> Result<Self, EngineClientError> {
        let ws = tokio::time::timeout(HANDSHAKE_TIMEOUT, connect_plain_ws(url))
            .await
            .map_err(|_| EngineClientError::HandshakeTimeout)?
            .map_err(|e| EngineClientError::WebSocket(e.to_string()))?;

        let (events_tx, _) = broadcast::channel::<EngineEvent>(BROADCAST_CAPACITY);
        let (cmd_tx, cmd_rx) = mpsc::channel::<Command>(COMMAND_BUFFER);

        // Perform handshake with exclusive ws access before spawning the IO tasks.
        let (ws, capabilities) = tokio::time::timeout(
            HANDSHAKE_TIMEOUT,
            perform_handshake(ws, token, mode, events_tx.clone()),
        )
        .await
        .map_err(|_| EngineClientError::HandshakeTimeout)??;

        let closed = Arc::new(tokio::sync::Notify::new());

        // Spawn the read/write loops.
        spawn_io_tasks(ws, cmd_rx, events_tx.clone(), Arc::clone(&closed));

        Ok(Self {
            sender: cmd_tx,
            events: events_tx,
            closed,
            capabilities: Arc::new(capabilities),
        })
    }

    /// Snapshot of `Ready.capabilities` captured during the handshake.
    ///
    /// Returned as an `Arc<Value>` so callers can hand the blob to the
    /// `engine_client::capabilities::*` helpers without paying a clone per
    /// UI frame. The blob is immutable for the lifetime of the connection
    /// — a fresh handshake produces a new `EngineConnection`.
    ///
    /// UI code MUST NOT cache this `Arc` independently — always re-fetch via
    /// the current `EngineConnection` so capability changes after a restart
    /// are observed.
    pub fn capabilities(&self) -> Arc<Value> {
        Arc::clone(&self.capabilities)
    }

    /// Send a command to the Python engine.
    pub async fn send(&self, cmd: Command) -> Result<(), EngineClientError> {
        self.sender
            .send(cmd)
            .await
            .map_err(|_| EngineClientError::WebSocket("command channel closed".to_string()))
    }

    /// Non-async variant: enqueue `cmd` immediately without awaiting.
    /// Returns `true` on success. Only fails if the channel is full (capacity
    /// 256) or closed, both of which are effectively impossible for a single
    /// command right after connection.
    pub fn try_send_now(&self, cmd: Command) -> bool {
        self.sender.try_send(cmd).is_ok()
    }

    /// Subscribe to all events broadcast from the Python engine.
    pub fn subscribe_events(&self) -> broadcast::Receiver<EngineEvent> {
        self.events.subscribe()
    }

    /// Resolves once the engine has emitted `Ready`. Today this is a synchronous
    /// invariant — `connect()` only returns after `perform_handshake` has
    /// observed `Ready` — but exposing it as an explicit API lets callers
    /// (`AdapterHandles`, fetch wrappers) document the dependency on the spec
    /// §4.5 handshake contract without leaking that invariant. Safe to call
    /// any number of times.
    pub async fn wait_ready(&self) -> Result<(), EngineClientError> {
        Ok(())
    }

    /// Resolves once the underlying WebSocket read loop exits (remote close or IO error).
    ///
    /// Use this in `ProcessManager::run_with_recovery` instead of waiting for
    /// `RecvError::Closed`, which never fires while `EngineConnection` itself
    /// holds a `broadcast::Sender`.
    pub async fn wait_closed(&self) {
        self.closed.notified().await;
    }
}

// ── Plain ws:// connect ───────────────────────────────────────────────────────

async fn connect_plain_ws(
    url: &str,
) -> Result<FragmentCollector<TokioIo<Upgraded>>, EngineClientError> {
    connect_ws_with_tcp_timeout(url, None).await
}

/// Connect to a `ws://` URL, optionally applying a per-TCP-connect timeout.
///
/// `tcp_timeout = None`      — no individual TCP timeout (the caller's
///                             `HANDSHAKE_TIMEOUT` wrapper applies to the whole
///                             TCP+WS-upgrade sequence).
/// `tcp_timeout = Some(d)`   — `TcpStream::connect` is wrapped in `timeout(d)`;
///                             used by `EngineConnection::probe` to fail fast (2 s)
///                             when no engine is running, without waiting the full
///                             `HANDSHAKE_TIMEOUT` before falling back to spawn.
async fn connect_ws_with_tcp_timeout(
    url: &str,
    tcp_timeout: Option<Duration>,
) -> Result<FragmentCollector<TokioIo<Upgraded>>, EngineClientError> {
    let parsed = url::Url::parse(url)
        .map_err(|e| EngineClientError::WebSocket(format!("invalid URL: {e}")))?;

    let host = parsed
        .host_str()
        .ok_or_else(|| EngineClientError::WebSocket("missing host".to_string()))?
        .to_owned();

    let port = parsed
        .port_or_known_default()
        .ok_or_else(|| EngineClientError::WebSocket("missing port".to_string()))?;

    let addr = format!("{host}:{port}");

    let map_io = |e: std::io::Error| {
        if e.kind() == std::io::ErrorKind::ConnectionRefused {
            EngineClientError::ConnectionRefused
        } else {
            EngineClientError::Io(e)
        }
    };

    let tcp = if let Some(timeout) = tcp_timeout {
        tokio::time::timeout(timeout, tokio::net::TcpStream::connect(&addr))
            .await
            .map_err(|_| EngineClientError::HandshakeTimeout)?
            .map_err(map_io)?
    } else {
        tokio::net::TcpStream::connect(&addr)
            .await
            .map_err(map_io)?
    };

    let path = {
        let mut p = parsed.path().to_string();
        if let Some(q) = parsed.query() {
            p.push('?');
            p.push_str(q);
        }
        if p.is_empty() {
            p.push('/');
        }
        p
    };

    let req: Request<Empty<Bytes>> = Request::builder()
        .method("GET")
        .uri(&path)
        .header("Host", format!("{host}:{port}"))
        .header(UPGRADE, "websocket")
        .header(CONNECTION, "upgrade")
        .header(
            "Sec-WebSocket-Key",
            fastwebsockets::handshake::generate_key(),
        )
        .header("Sec-WebSocket-Version", "13")
        .body(Empty::<Bytes>::new())
        .map_err(|e| EngineClientError::WebSocket(e.to_string()))?;

    let (ws, _) = fastwebsockets::handshake::client(&TokioExecutor::new(), req, tcp)
        .await
        .map_err(|e| EngineClientError::WebSocket(e.to_string()))?;

    Ok(FragmentCollector::new(ws))
}

// ── Handshake ─────────────────────────────────────────────────────────────────

async fn perform_handshake(
    mut ws: FragmentCollector<TokioIo<Upgraded>>,
    token: &str,
    mode: crate::dto::AppMode,
    events_tx: broadcast::Sender<EngineEvent>,
) -> Result<(FragmentCollector<TokioIo<Upgraded>>, Value), EngineClientError> {
    // Send Hello
    let hello = Command::Hello {
        schema_major: SCHEMA_MAJOR,
        schema_minor: SCHEMA_MINOR,
        client_version: CLIENT_VERSION.to_string(),
        token: token.to_string(),
        mode,
    };
    let hello_json = serde_json::to_string(&hello)?;
    ws.write_frame(Frame::text(Payload::Owned(hello_json.into_bytes())))
        .await
        .map_err(|e| EngineClientError::WebSocket(e.to_string()))?;

    // Wait for Ready
    loop {
        let frame = ws
            .read_frame()
            .await
            .map_err(|e| EngineClientError::WebSocket(e.to_string()))?;

        match frame.opcode {
            OpCode::Text => {
                let text = std::str::from_utf8(&frame.payload)
                    .map_err(|e| EngineClientError::WebSocket(e.to_string()))?;
                let event: EngineEvent = serde_json::from_str(text)?;

                match event {
                    EngineEvent::Ready {
                        schema_major,
                        schema_minor,
                        ref capabilities,
                        ..
                    } => {
                        if schema_major != SCHEMA_MAJOR {
                            return Err(EngineClientError::SchemaMismatch {
                                local_major: SCHEMA_MAJOR,
                                local_minor: SCHEMA_MINOR,
                                remote_major: schema_major,
                                remote_minor: schema_minor,
                            });
                        }
                        log::info!(
                            "engine handshake complete: schema {schema_major}.{schema_minor}"
                        );
                        let caps = capabilities.clone();
                        // Broadcast the Ready event so any subscriber can observe it.
                        let _ = events_tx.send(event);
                        return Ok((ws, caps));
                    }
                    EngineEvent::EngineError { code, message, .. } => {
                        return Err(EngineClientError::EngineError { code, message });
                    }
                    _ => {
                        // Unexpected event before Ready — broadcast it anyway and keep waiting.
                        log::warn!("unexpected event before Ready");
                        let _ = events_tx.send(event);
                    }
                }
            }
            OpCode::Close => {
                return Err(EngineClientError::WebSocket(
                    "connection closed during handshake".to_string(),
                ));
            }
            _ => {} // Ping/Pong/Binary — ignore during handshake
        }
    }
}

// ── IO task loops ─────────────────────────────────────────────────────────────

fn spawn_io_tasks(
    ws: FragmentCollector<TokioIo<Upgraded>>,
    mut cmd_rx: mpsc::Receiver<Command>,
    events_tx: broadcast::Sender<EngineEvent>,
    closed: Arc<tokio::sync::Notify>,
) {
    // A single task owns the WebSocket and multiplexes reads, writes, ping/pong
    // and close via `tokio::select!`. An earlier two-task design used an
    // `Arc<Mutex<WebSocket>>`, but `read_frame().await` holds the mutex across
    // `Pending` states and starves the writer — a write enqueued via `cmd_rx`
    // would not flush until the next inbound frame happened to wake the reader.
    // That deadlock made in-stream recovery (e.g. resync after `DepthGap`)
    // silently never reach the engine.
    tokio::spawn(async move {
        let mut ws = ws;
        loop {
            tokio::select! {
                // Bias unspecified — both branches are equally important, but
                // `cmd_rx` is checked first so pending writes are not held off
                // by a steady stream of inbound frames.
                biased;

                cmd = cmd_rx.recv() => {
                    let Some(cmd) = cmd else {
                        // Sender dropped — connection is shutting down.
                        break;
                    };
                    let json = match serde_json::to_string(&cmd) {
                        Ok(j) => j,
                        Err(e) => {
                            log::error!("failed to serialize command: {e}");
                            continue;
                        }
                    };
                    if let Err(e) = ws
                        .write_frame(Frame::text(Payload::Owned(json.into_bytes())))
                        .await
                    {
                        log::error!("engine ws write error: {e}");
                        break;
                    }
                }

                read = ws.read_frame() => {
                    let frame = match read {
                        Ok(f) => f,
                        Err(e) => {
                            log::warn!("engine ws read error: {e}");
                            break;
                        }
                    };
                    match frame.opcode {
                        OpCode::Text => {
                            let text = match std::str::from_utf8(&frame.payload) {
                                Ok(t) => t.to_owned(),
                                Err(e) => {
                                    log::warn!("non-UTF8 engine frame: {e}");
                                    continue;
                                }
                            };
                            match serde_json::from_str::<EngineEvent>(&text) {
                                Ok(event) => {
                                    if events_tx.send(event).is_err() {
                                        log::warn!("engine event dropped: no active subscribers");
                                    }
                                }
                                Err(e) => {
                                    // Phase F made the IPC strictly typed: a single malformed
                                    // entry kills the whole frame (e.g. one bad TickerEntry =
                                    // every ticker for that venue silently dropped). Escalate
                                    // to error so smoke-test scanners catch it and operators
                                    // see it before users do. See refactor plan §17.1.
                                    log::error!(
                                        "failed to parse engine event (frame DROPPED): {e} \
                                         — frame: {text}"
                                    );
                                }
                            }
                        }
                        OpCode::Close => break,
                        OpCode::Ping => {
                            // fastwebsockets does not auto-pong; send pong manually.
                            let _ = ws
                                .write_frame(Frame::pong(Payload::BorrowedMut(&mut [])))
                                .await;
                        }
                        _ => {} // Binary / Pong — ignored
                    }
                }
            }
        }
        log::info!("engine ws io loop exited");
        // Unblock any in-flight fetch waiters before signalling wait_closed().
        let _ = events_tx.send(crate::dto::EngineEvent::ConnectionDropped);
        closed.notify_waiters();
    });
}
