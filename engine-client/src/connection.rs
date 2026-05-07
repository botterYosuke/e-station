/// gRPC connection to the Python data engine.
///
/// `EngineConnection` owns:
/// - a sender half of a channel that drives a background converter task, and
/// - a broadcast channel that fan-outs every incoming `EngineEvent` to
///   any number of subscribers (stream futures, fetch waiters, …).
///
/// The connection performs the `HelloRequest`/`ReadyResponse` handshake on
/// construction via [`EngineConnection::connect_grpc`].  The WebSocket
/// transport (`connect()` / `connect_with_mode()` / `probe()`) was removed
/// in G3 — all IPC now flows over gRPC (tonic ↔ grpcio).
use serde_json::Value;
use std::sync::Arc;
use tokio::sync::{broadcast, mpsc};

use crate::{
    dto::{Command, EngineEvent},
    error::EngineClientError,
};

pub struct EngineConnection {
    sender: mpsc::Sender<Command>,
    events: broadcast::Sender<EngineEvent>,
    /// Notified once when the gRPC session exits (remote close or IO error).
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
    /// Connect to the Python engine at `target` via gRPC (e.g. `"http://127.0.0.1:19876"`).
    ///
    /// Performs the `HelloRequest` / `ReadyResponse` handshake and starts the
    /// background converter + reader tasks.  The caller should pass a URL with
    /// the `http://` scheme — `grpc://` URLs must be rewritten before calling
    /// (e.g. `target.replace("grpc://", "http://")` in `src/main.rs`).
    pub async fn connect_grpc(
        target: &str,
        token: &str,
        mode: crate::dto::AppMode,
    ) -> Result<Self, EngineClientError> {
        let (sender, events, closed, capabilities) =
            crate::grpc_transport::start_grpc_session(target, token, mode).await?;
        Ok(Self {
            sender,
            events,
            closed,
            capabilities,
        })
    }

    /// Test helper: connects to gRPC with an intentionally wrong schema version,
    /// for testing schema rejection. Only available with the `testing` feature.
    #[cfg(feature = "testing")]
    pub async fn connect_grpc_with_schema(
        target: &str,
        token: &str,
        mode: crate::dto::AppMode,
        schema_major_override: u16,
    ) -> Result<Self, EngineClientError> {
        let (sender, events, closed, capabilities) =
            crate::grpc_transport::start_grpc_session_with_schema(
                target,
                token,
                mode,
                schema_major_override,
            )
            .await?;
        Ok(Self {
            sender,
            events,
            closed,
            capabilities,
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
    /// invariant — `connect_grpc()` only returns after the handshake has
    /// observed `ReadyResponse` — but exposing it as an explicit API lets callers
    /// (`AdapterHandles`, fetch wrappers) document the dependency on the spec
    /// §4.5 handshake contract without leaking that invariant. Safe to call
    /// any number of times.
    pub async fn wait_ready(&self) -> Result<(), EngineClientError> {
        Ok(())
    }

    /// Resolves once the underlying gRPC session exits (remote close or IO error).
    ///
    /// Use this in `ProcessManager::run_with_recovery` instead of waiting for
    /// `RecvError::Closed`, which never fires while `EngineConnection` itself
    /// holds a `broadcast::Sender`.
    pub async fn wait_closed(&self) {
        self.closed.notified().await;
    }
}
