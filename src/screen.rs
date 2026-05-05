pub mod dashboard;

#[derive(thiserror::Error, Debug, Clone)]
pub enum DashboardError {
    #[error("Fetch error: {0}")]
    Fetch(String),
    #[error("Pane set error: {0}")]
    PaneSet(String),
    #[error("Unknown error: {0}")]
    Unknown(String),
}

#[derive(Debug, Clone)]
pub struct ConfirmDialog<M> {
    pub message: String,
    pub on_confirm: Box<M>,
    pub on_confirm_btn_text: Option<String>,
    /// When Some, a third "Save" button is rendered between Cancel and the confirm (Discard) button.
    pub on_save: Option<Box<M>>,
    pub on_save_btn_text: Option<String>,
}

impl<M> ConfirmDialog<M> {
    pub fn new(message: String, on_confirm: Box<M>) -> Self {
        Self {
            message,
            on_confirm,
            on_confirm_btn_text: None,
            on_save: None,
            on_save_btn_text: None,
        }
    }

    pub fn with_confirm_btn_text(mut self, on_confirm_btn_text: String) -> Self {
        self.on_confirm_btn_text = Some(on_confirm_btn_text);
        self
    }

    /// Add a "Save" action as the primary button. Turns the dialog into a 3-button layout:
    /// [Cancel]  [Discard (on_confirm)]  [Save (on_save)]
    pub fn with_save_action(mut self, on_save: M, btn_text: String) -> Self {
        self.on_save = Some(Box::new(on_save));
        self.on_save_btn_text = Some(btn_text);
        self
    }
}
