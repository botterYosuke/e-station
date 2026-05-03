use iced::{
    Element,
    widget::{button, column, container, pick_list, row, text, text_input},
};

/// Replay 粒度選択肢。`engine_client::dto::ReplayGranularity` と対応するが、
/// `pick_list` に渡すために `Display` を実装した独立型として定義する。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Granularity {
    Daily,
    Minute,
    Trade,
}

impl Granularity {
    pub const ALL: &'static [Granularity] = &[Self::Daily, Self::Minute, Self::Trade];

    /// IPC の `ReplayGranularity` に変換する。
    pub fn to_dto(&self) -> engine_client::dto::ReplayGranularity {
        match self {
            Self::Daily => engine_client::dto::ReplayGranularity::Daily,
            Self::Minute => engine_client::dto::ReplayGranularity::Minute,
            Self::Trade => engine_client::dto::ReplayGranularity::Trade,
        }
    }
}

impl std::fmt::Display for Granularity {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Daily => write!(f, "Daily"),
            Self::Minute => write!(f, "Minute"),
            Self::Trade => write!(f, "Trade"),
        }
    }
}

/// Replay 起動フォーム modal の状態。
#[derive(Debug, Default)]
pub struct ReplayFormModal {
    pub instrument_id: String,
    pub start_date: String,
    pub end_date: String,
    pub granularity: Option<Granularity>,
    pub strategy_file: Option<std::path::PathBuf>,
    pub initial_cash: String,
    pub validation_error: Option<String>,
    pub submitting: bool,
}

#[derive(Debug, Clone)]
pub enum Message {
    InstrumentChanged(String),
    StartDateChanged(String),
    EndDateChanged(String),
    GranularityChanged(Granularity),
    PickStrategyFile,
    StrategyFilePicked(Option<std::path::PathBuf>),
    InitialCashChanged(String),
    Submit,
    Cancel,
}

pub enum Action {
    Submit {
        instrument_id: String,
        start_date: String,
        end_date: String,
        granularity: Granularity,
        strategy_file: std::path::PathBuf,
        initial_cash: u64,
    },
    PickStrategyFile,
    Cancel,
}

/// `YYYY-MM-DD` 形式の簡易チェック。
fn is_valid_date(s: &str) -> bool {
    // 形式: YYYY-MM-DD (10 chars)
    if s.len() != 10 {
        return false;
    }
    let bytes = s.as_bytes();
    bytes[4] == b'-' && bytes[7] == b'-' && bytes[..4].iter().all(u8::is_ascii_digit)
        && bytes[5..7].iter().all(u8::is_ascii_digit)
        && bytes[8..10].iter().all(u8::is_ascii_digit)
}

impl ReplayFormModal {
    fn validate(
        &self,
    ) -> Result<
        (
            String,
            String,
            String,
            Granularity,
            std::path::PathBuf,
            u64,
        ),
        String,
    > {
        let instrument_id = self.instrument_id.trim().to_string();
        if instrument_id.is_empty() {
            return Err("銘柄コードを入力してください".to_string());
        }
        let start_date = self.start_date.trim().to_string();
        if !is_valid_date(&start_date) {
            return Err("開始日の形式が正しくありません (例: 2025-01-06)".to_string());
        }
        let end_date = self.end_date.trim().to_string();
        if !is_valid_date(&end_date) {
            return Err("終了日の形式が正しくありません (例: 2025-03-31)".to_string());
        }
        let granularity = self
            .granularity
            .clone()
            .ok_or_else(|| "粒度を選択してください".to_string())?;
        let strategy_file = self
            .strategy_file
            .clone()
            .ok_or_else(|| "戦略ファイルを選択してください".to_string())?;
        let initial_cash = self
            .initial_cash
            .trim()
            .parse::<u64>()
            .map_err(|_| "初期資金は正の整数で入力してください".to_string())?;
        Ok((
            instrument_id,
            start_date,
            end_date,
            granularity,
            strategy_file,
            initial_cash,
        ))
    }

    pub fn update(&mut self, message: Message) -> Option<Action> {
        match message {
            Message::InstrumentChanged(v) => {
                self.instrument_id = v;
                None
            }
            Message::StartDateChanged(v) => {
                self.start_date = v;
                None
            }
            Message::EndDateChanged(v) => {
                self.end_date = v;
                None
            }
            Message::GranularityChanged(g) => {
                self.granularity = Some(g);
                None
            }
            Message::PickStrategyFile => Some(Action::PickStrategyFile),
            Message::StrategyFilePicked(path) => {
                self.strategy_file = path;
                None
            }
            Message::InitialCashChanged(v) => {
                self.initial_cash = v;
                None
            }
            Message::Submit => {
                match self.validate() {
                    Ok((instrument_id, start_date, end_date, granularity, strategy_file, initial_cash)) => {
                        self.validation_error = None;
                        self.submitting = true;
                        Some(Action::Submit {
                            instrument_id,
                            start_date,
                            end_date,
                            granularity,
                            strategy_file,
                            initial_cash,
                        })
                    }
                    Err(e) => {
                        self.validation_error = Some(e);
                        None
                    }
                }
            }
            Message::Cancel => Some(Action::Cancel),
        }
    }

    pub fn view(&self) -> Element<'_, Message> {
        let strategy_label = self
            .strategy_file
            .as_ref()
            .and_then(|p| p.file_name())
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_else(|| "(未選択)".to_string());

        let pick_btn = button(text("選択")).on_press(Message::PickStrategyFile);

        let submit_btn = {
            let btn = button(text("開始"));
            if self.submitting {
                btn
            } else {
                btn.on_press(Message::Submit)
            }
        };

        let mut col = column![
            text("Replay を開始").size(18),
            text("銘柄コード (例: 1301.TSE)").size(12),
            text_input("1301.TSE", &self.instrument_id).on_input(Message::InstrumentChanged),
            text("開始日 (例: 2025-01-06)").size(12),
            text_input("2025-01-06", &self.start_date).on_input(Message::StartDateChanged),
            text("終了日 (例: 2025-03-31)").size(12),
            text_input("2025-03-31", &self.end_date).on_input(Message::EndDateChanged),
            text("粒度").size(12),
            pick_list(
                Granularity::ALL,
                self.granularity.as_ref(),
                Message::GranularityChanged,
            ),
            text("戦略ファイル").size(12),
            row![
                text_input("(未選択)", &strategy_label),
                pick_btn
            ]
            .spacing(4),
            text("初期資金 (円)").size(12),
            text_input("1000000", &self.initial_cash).on_input(Message::InitialCashChanged),
        ]
        .spacing(6);

        if let Some(err) = &self.validation_error {
            col = col.push(text(err.as_str()).size(12));
        }

        let buttons = row![
            button(text("キャンセル")).on_press(Message::Cancel),
            submit_btn,
        ]
        .spacing(8);

        col = col.push(buttons);

        container(col).padding(16).into()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validation_fails_empty_instrument() {
        let mut form = ReplayFormModal::default();
        // Submit で validation_error が Some になる
        let action = form.update(Message::Submit);
        assert!(action.is_none());
        assert!(
            form.validation_error.is_some(),
            "validation_error should be set on empty instrument"
        );
        assert!(form
            .validation_error
            .as_ref()
            .unwrap()
            .contains("銘柄コード"));
    }

    #[test]
    fn validation_fails_bad_start_date_format() {
        let mut form = ReplayFormModal::default();
        form.instrument_id = "1301.TSE".to_string();
        form.start_date = "not-a-date".to_string();
        let action = form.update(Message::Submit);
        assert!(action.is_none());
        assert!(form.validation_error.is_some());
        assert!(form
            .validation_error
            .as_ref()
            .unwrap()
            .contains("開始日"));
    }

    #[test]
    fn validation_fails_bad_end_date_format() {
        let mut form = ReplayFormModal::default();
        form.instrument_id = "1301.TSE".to_string();
        form.start_date = "2025-01-06".to_string();
        form.end_date = "2025/03/31".to_string();
        let action = form.update(Message::Submit);
        assert!(action.is_none());
        assert!(form.validation_error.is_some());
        assert!(form.validation_error.as_ref().unwrap().contains("終了日"));
    }

    #[test]
    fn validation_fails_invalid_cash() {
        let mut form = ReplayFormModal::default();
        form.instrument_id = "1301.TSE".to_string();
        form.start_date = "2025-01-06".to_string();
        form.end_date = "2025-03-31".to_string();
        form.granularity = Some(Granularity::Daily);
        form.strategy_file = Some(std::path::PathBuf::from("/tmp/strategy.py"));
        form.initial_cash = "abc".to_string();
        let action = form.update(Message::Submit);
        assert!(action.is_none());
        assert!(form.validation_error.is_some());
        assert!(form
            .validation_error
            .as_ref()
            .unwrap()
            .contains("初期資金"));
    }

    #[test]
    fn validation_succeeds_with_valid_inputs() {
        let mut form = ReplayFormModal::default();
        form.instrument_id = "1301.TSE".to_string();
        form.start_date = "2025-01-06".to_string();
        form.end_date = "2025-03-31".to_string();
        form.granularity = Some(Granularity::Daily);
        form.strategy_file = Some(std::path::PathBuf::from("/tmp/strategy.py"));
        form.initial_cash = "1000000".to_string();
        let action = form.update(Message::Submit);
        assert!(
            matches!(action, Some(Action::Submit { .. })),
            "valid inputs should produce Submit action"
        );
        assert!(form.validation_error.is_none());
    }

    #[test]
    fn cancel_returns_cancel_action() {
        let mut form = ReplayFormModal::default();
        let action = form.update(Message::Cancel);
        assert!(matches!(action, Some(Action::Cancel)));
    }

    #[test]
    fn pick_strategy_file_returns_action() {
        let mut form = ReplayFormModal::default();
        let action = form.update(Message::PickStrategyFile);
        assert!(matches!(action, Some(Action::PickStrategyFile)));
    }

    #[test]
    fn is_valid_date_accepts_correct_format() {
        assert!(is_valid_date("2025-01-06"));
        assert!(is_valid_date("2025-03-31"));
        assert!(!is_valid_date("2025/01/06"));
        assert!(!is_valid_date("not-a-date"));
        assert!(!is_valid_date("20250106"));
        assert!(!is_valid_date("2025-1-6"));
    }
}
