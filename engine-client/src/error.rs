/// Errors produced by the engine-client crate.
#[derive(Debug, thiserror::Error)]
pub enum EngineClientError {
    #[error("WebSocket error: {0}")]
    WebSocket(String),

    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),

    #[error("Engine restarting")]
    EngineRestarting,

    #[error("Schema version mismatch: local={local_major}.{local_minor}, remote={remote_major}.{remote_minor}")]
    SchemaMismatch {
        local_major: u16,
        local_minor: u16,
        remote_major: u16,
        remote_minor: u16,
    },

    #[error("Handshake timeout")]
    HandshakeTimeout,

    #[error("Connection refused")]
    ConnectionRefused,

    #[error("Engine error: {code}: {message}")]
    EngineError { code: String, message: String },

    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),
}
