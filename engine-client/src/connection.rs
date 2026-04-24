/// WebSocket connection to the Python data engine.
///
/// `EngineConnection` owns:
/// - a sender half of a channel that drives a background write task, and
/// - a broadcast channel that fan-outs every incoming `EngineEvent` to
///   any number of subscribers (stream futures, fetch waiters, …).
///
/// The connection performs the `Hello`/`Ready` handshake on construction.
use bytes::Bytes;
use fastwebsockets::{Frame, FragmentCollector, OpCode, Payload};
use http_body_util::Empty;
use hyper::{
    Request,
    header::{CONNECTION, UPGRADE},
    upgrade::Upgraded,
};
use hyper_util::rt::{TokioExecutor, TokioIo};
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
const CLIENT_VERSION: &str = env!("CARGO_PKG_VERSION");

pub struct EngineConnection {
    sender: mpsc::Sender<Command>,
    events: broadcast::Sender<EngineEvent>,
    /// Notified once when the WS read loop exits (remote close or IO error).
    closed: Arc<tokio::sync::Notify>,
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
        let ws = tokio::time::timeout(HANDSHAKE_TIMEOUT, connect_plain_ws(url))
            .await
            .map_err(|_| EngineClientError::HandshakeTimeout)?
            .map_err(|e| EngineClientError::WebSocket(e.to_string()))?;

        let (events_tx, _) = broadcast::channel::<EngineEvent>(BROADCAST_CAPACITY);
        let (cmd_tx, cmd_rx) = mpsc::channel::<Command>(COMMAND_BUFFER);

        // Perform handshake with exclusive ws access before spawning the IO tasks.
        let ws = tokio::time::timeout(HANDSHAKE_TIMEOUT, perform_handshake(ws, token, events_tx.clone()))
            .await
            .map_err(|_| EngineClientError::HandshakeTimeout)??;

        let closed = Arc::new(tokio::sync::Notify::new());

        // Spawn the read/write loops.
        spawn_io_tasks(ws, cmd_rx, events_tx.clone(), Arc::clone(&closed));

        Ok(Self { sender: cmd_tx, events: events_tx, closed })
    }

    /// Send a command to the Python engine.
    pub async fn send(&self, cmd: Command) -> Result<(), EngineClientError> {
        self.sender
            .send(cmd)
            .await
            .map_err(|_| EngineClientError::WebSocket("command channel closed".to_string()))
    }

    /// Subscribe to all events broadcast from the Python engine.
    pub fn subscribe_events(&self) -> broadcast::Receiver<EngineEvent> {
        self.events.subscribe()
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
    let parsed = url::Url::parse(url)
        .map_err(|e| EngineClientError::WebSocket(format!("invalid URL: {e}")))?;

    let host = parsed
        .host_str()
        .ok_or_else(|| EngineClientError::WebSocket("missing host".to_string()))?
        .to_owned();

    let port = parsed.port_or_known_default().ok_or_else(|| {
        EngineClientError::WebSocket("missing port".to_string())
    })?;

    let addr = format!("{host}:{port}");
    let tcp = tokio::net::TcpStream::connect(&addr).await.map_err(|e| {
        if e.kind() == std::io::ErrorKind::ConnectionRefused {
            EngineClientError::ConnectionRefused
        } else {
            EngineClientError::Io(e)
        }
    })?;

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
        .header("Sec-WebSocket-Key", fastwebsockets::handshake::generate_key())
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
    events_tx: broadcast::Sender<EngineEvent>,
) -> Result<FragmentCollector<TokioIo<Upgraded>>, EngineClientError> {
    // Send Hello
    let hello = Command::Hello {
        schema_major: SCHEMA_MAJOR,
        schema_minor: SCHEMA_MINOR,
        client_version: CLIENT_VERSION.to_string(),
        token: token.to_string(),
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

                match &event {
                    EngineEvent::Ready { schema_major, schema_minor, .. } => {
                        if *schema_major != SCHEMA_MAJOR {
                            return Err(EngineClientError::SchemaMismatch {
                                local_major: SCHEMA_MAJOR,
                                local_minor: SCHEMA_MINOR,
                                remote_major: *schema_major,
                                remote_minor: *schema_minor,
                            });
                        }
                        log::info!("engine handshake complete: schema {schema_major}.{schema_minor}");
                        // Broadcast the Ready event so any subscriber can observe it.
                        let _ = events_tx.send(event);
                        return Ok(ws);
                    }
                    EngineEvent::EngineError { code, message } => {
                        return Err(EngineClientError::EngineError {
                            code: code.clone(),
                            message: message.clone(),
                        });
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
    // Split WebSocket into halves using an Arc<Mutex<…>> so we can drive read/write
    // from two separate tasks without moving the same value twice.
    let ws = Arc::new(tokio::sync::Mutex::new(ws));
    let ws_write = Arc::clone(&ws);

    // Write task: drain the command channel and send JSON frames.
    tokio::spawn(async move {
        while let Some(cmd) = cmd_rx.recv().await {
            let json = match serde_json::to_string(&cmd) {
                Ok(j) => j,
                Err(e) => {
                    log::error!("failed to serialize command: {e}");
                    continue;
                }
            };
            let mut guard = ws_write.lock().await;
            if let Err(e) = guard
                .write_frame(Frame::text(Payload::Owned(json.into_bytes())))
                .await
            {
                log::error!("engine ws write error: {e}");
                break;
            }
        }
    });

    // Read task: deserialize incoming frames and broadcast as EngineEvents.
    tokio::spawn(async move {
        loop {
            let frame = {
                let mut guard = ws.lock().await;
                match guard.read_frame().await {
                    Ok(f) => f,
                    Err(e) => {
                        log::warn!("engine ws read error: {e}");
                        break;
                    }
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
                            log::warn!("failed to parse engine event: {e} — frame: {text}");
                        }
                    }
                }
                OpCode::Close => break,
                OpCode::Ping => {
                    // fastwebsockets does not auto-pong; send pong manually.
                    let mut guard = ws.lock().await;
                    let _ = guard.write_frame(Frame::pong(Payload::BorrowedMut(&mut []))).await;
                }
                _ => {} // Binary / Pong — ignored
            }
        }
        log::info!("engine ws read loop exited");
        // Signal any callers of wait_closed() that the connection is gone.
        closed.notify_waiters();
    });
}
