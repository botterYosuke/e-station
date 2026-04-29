/// `flowsurface-engine-client` — IPC client to the Python data engine.
///
/// Public surface:
/// - [`EngineClientBackend`]: a `VenueBackend` impl that routes via Python IPC.
/// - [`EngineConnection`]: low-level WS connection and event broadcast.
/// - [`PythonProcess`] / [`ProcessManager`]: subprocess management with auto-restart.
/// - [`EngineClientError`]: unified error type.
/// - [`dto`] / [`convert`]: IPC message types and domain-type conversions.
pub mod backend;
pub mod capabilities;
pub mod connection;
pub mod convert;
pub mod depth_tracker;
pub mod dto;
pub mod error;
pub mod order_session_state;
pub mod process;
pub mod tachibana_meta;

pub use backend::{EngineClientBackend, TickerMetaMap};
pub use connection::EngineConnection;
pub use depth_tracker::DepthTracker;
pub use error::EngineClientError;
pub use process::{EngineCommand, ProcessManager, PythonProcess, SubscriptionKey};

/// IPC schema version — must match the Python engine's `SCHEMA_MAJOR`/`SCHEMA_MINOR`.
pub const SCHEMA_MAJOR: u16 = 2;
pub const SCHEMA_MINOR: u16 = 6;
