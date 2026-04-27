use iced::{
    Element,
    widget::{button, column, container, row, text, text_input},
};

#[derive(Debug, Default)]
pub struct SecondPasswordModal {
    pub request_id: String,
    pub input: String, // Submit 後にゼロクリアする
    pub visible: bool, // false = マスク表示モード
    pub submitting: bool,
}

#[derive(Debug, Clone)]
pub enum Message {
    InputChanged(String),
    ToggleVisibility,
    Submit,
    Cancel,
}

pub enum Action {
    Submit { value: String }, // IPC 送信値 (U0 で使用)
    Cancel,
}

impl SecondPasswordModal {
    pub fn new(request_id: impl Into<String>) -> Self {
        Self {
            request_id: request_id.into(),
            ..Default::default()
        }
    }

    pub fn update(&mut self, message: Message) -> Option<Action> {
        match message {
            Message::InputChanged(v) => {
                self.input = v;
                None
            }
            Message::ToggleVisibility => {
                self.visible = !self.visible;
                None
            }
            Message::Submit => {
                if self.input.is_empty() {
                    return None;
                }
                let value = std::mem::take(&mut self.input); // zeroize
                self.submitting = true;
                Some(Action::Submit { value })
            }
            Message::Cancel => {
                self.input = String::new(); // clear
                Some(Action::Cancel)
            }
        }
    }

    pub fn view(&self) -> Element<'_, Message> {
        let input_field = text_input("第二暗証番号", &self.input)
            .secure(!self.visible)
            .on_input(Message::InputChanged);

        let toggle_btn = {
            let label = if self.visible { "非表示" } else { "表示" };
            button(text(label).size(11)).on_press(Message::ToggleVisibility)
        };

        let submit_btn = {
            let btn = button(text("送信"));
            if self.is_input_empty() {
                btn
            } else {
                btn.on_press(Message::Submit)
            }
        };

        container(
            column![
                text("第二暗証番号を入力してください").size(16),
                row![input_field, toggle_btn].spacing(4),
                row![
                    button(text("キャンセル")).on_press(Message::Cancel),
                    submit_btn
                ]
                .spacing(8),
            ]
            .spacing(8),
        )
        .into()
    }

    /// Returns true if the input is empty (submit should be disabled).
    pub fn is_input_empty(&self) -> bool {
        self.input.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cancel_clears_input() {
        let mut modal = SecondPasswordModal::new("req-1");
        modal.input = "secret".into();
        let action = modal.update(Message::Cancel);
        assert!(matches!(action, Some(Action::Cancel)));
        assert!(modal.input.is_empty(), "Cancel should clear input");
    }

    #[test]
    fn submit_takes_value_and_clears_input() {
        let mut modal = SecondPasswordModal::new("req-1");
        modal.input = "mypassword".into();
        let action = modal.update(Message::Submit);
        assert!(modal.input.is_empty(), "Submit should zeroize input");
        assert!(matches!(action, Some(Action::Submit { value }) if value == "mypassword"));
    }

    #[test]
    fn submit_on_empty_input_returns_none() {
        let mut modal = SecondPasswordModal::new("req-1");
        let action = modal.update(Message::Submit);
        assert!(action.is_none());
    }

    #[test]
    fn toggle_visibility_flips_flag() {
        let mut modal = SecondPasswordModal::new("req-1");
        assert!(!modal.visible);
        modal.update(Message::ToggleVisibility);
        assert!(modal.visible);
        modal.update(Message::ToggleVisibility);
        assert!(!modal.visible);
    }
}
