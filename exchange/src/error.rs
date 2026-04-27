#[derive(thiserror::Error, Debug)]
pub enum AdapterError {
    #[error("{0}")]
    FetchError(String),
    #[error("Parsing: {0}")]
    ParseError(String),
    #[error("Stream: {0}")]
    WebsocketError(String),
    #[error("Invalid request: {0}")]
    InvalidRequest(String),
    /// The Python data engine is restarting; callers should retry.
    #[error("Engine restarting")]
    EngineRestarting,
}

impl AdapterError {
    pub fn human_message(&self) -> String {
        match self {
            Self::FetchError(msg) => msg.clone(),
            Self::ParseError(_) => "Invalid server response. Check logs for details.".to_string(),
            Self::WebsocketError(_) => "Stream error. Check logs for details.".to_string(),
            Self::InvalidRequest(message) => message.clone(),
            Self::EngineRestarting => "Data engine restarting. Please retry.".to_string(),
        }
    }

    pub fn ui_message(&self) -> String {
        self.human_message()
    }
}
