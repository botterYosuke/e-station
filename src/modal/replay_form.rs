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

/// M-5 (rust): `validate()` の戻り値を構造体化することで位置引数の取り違えを防ぐ。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ValidatedForm {
    pub instrument_id: String,
    pub start_date: String,
    pub end_date: String,
    pub granularity: Granularity,
    pub strategy_file: std::path::PathBuf,
    pub initial_cash: u64,
}

/// `YYYY-MM-DD` 形式（10 文字・ゼロ埋め 2 桁月日）かつ実在する日付かを検証する。
///
/// - 構文チェック: 長さ 10・`-` 区切り・4+2+2 桁の数字
/// - 意味チェック: `chrono::NaiveDate::parse_from_str` で存在する日付か確認
///   → "9999-99-99" や "2025-02-30" を弾く
fn is_valid_date(s: &str) -> bool {
    // 構文チェック: YYYY-MM-DD (10 文字, ゼロ埋め)
    if s.len() != 10 {
        return false;
    }
    let bytes = s.as_bytes();
    if !(bytes[4] == b'-'
        && bytes[7] == b'-'
        && bytes[..4].iter().all(u8::is_ascii_digit)
        && bytes[5..7].iter().all(u8::is_ascii_digit)
        && bytes[8..10].iter().all(u8::is_ascii_digit))
    {
        return false;
    }
    // 意味チェック: 実在する日付か確認
    chrono::NaiveDate::parse_from_str(s, "%Y-%m-%d").is_ok()
}

impl ReplayFormModal {
    fn validate(&self) -> Result<ValidatedForm, String> {
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
        // M-2 (rust): start_date > end_date を検出（ISO8601 YYYY-MM-DD は
        // 文字列比較で順序が保たれるため lexicographic 比較で十分）。
        if start_date > end_date {
            return Err("開始日は終了日より前にしてください".to_string());
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
        // M-Rust4: 0 は受け付けない（戦略が必ず資金不足で空回りするため事故予防）。
        if initial_cash == 0 {
            return Err("初期資金は 1 以上にしてください".to_string());
        }
        Ok(ValidatedForm {
            instrument_id,
            start_date,
            end_date,
            granularity,
            strategy_file,
            initial_cash,
        })
    }

    /// F6a: Prefill form fields from a SCENARIO JSON object that Python
    /// extracted from a strategy `.py`. Missing or wrongly-typed keys leave the
    /// existing field untouched. `validation_error` is cleared on any successful
    /// touch so the previous error banner does not linger after a fresh Load.
    ///
    /// `path` is recorded as the picked strategy file so Submit can carry it
    /// through to `Command::StartEngine`.
    pub fn prefill_from_scenario(
        &mut self,
        path: std::path::PathBuf,
        scenario: &serde_json::Value,
    ) {
        self.strategy_file = Some(path);
        let Some(obj) = scenario.as_object() else {
            self.validation_error = None;
            return;
        };
        if let Some(s) = obj.get("instrument").and_then(|v| v.as_str()) {
            self.instrument_id = s.to_string();
        }
        if let Some(s) = obj.get("start").and_then(|v| v.as_str()) {
            self.start_date = s.to_string();
        }
        if let Some(s) = obj.get("end").and_then(|v| v.as_str()) {
            self.end_date = s.to_string();
        }
        if let Some(s) = obj.get("granularity").and_then(|v| v.as_str()) {
            // SCENARIO の Literal は schemas.py で "Trade"/"Minute"/"Daily" に固定。
            // 未知文字列は触らない（既存 granularity を保持）。
            match s {
                "Daily" => self.granularity = Some(Granularity::Daily),
                "Minute" => self.granularity = Some(Granularity::Minute),
                "Trade" => self.granularity = Some(Granularity::Trade),
                _ => {}
            }
        }
        if let Some(n) = obj.get("initial_cash").and_then(|v| v.as_u64()) {
            self.initial_cash = n.to_string();
        }
        self.validation_error = None;
    }

    /// F6a: SCENARIO 不在の `.py` を Load した場合のフォールバック。
    /// フィールドはそのまま、`strategy_file` だけセットする。
    pub fn set_strategy_file_only(&mut self, path: std::path::PathBuf) {
        self.strategy_file = Some(path);
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
            Message::InitialCashChanged(v) => {
                self.initial_cash = v;
                None
            }
            Message::Submit => match self.validate() {
                Ok(ValidatedForm {
                    instrument_id,
                    start_date,
                    end_date,
                    granularity,
                    strategy_file,
                    initial_cash,
                }) => {
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
            },
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
            row![text_input("(未選択)", &strategy_label), pick_btn].spacing(4),
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

        container(col)
            .max_width(420)
            .padding(24)
            .style(crate::style::dashboard_modal)
            .into()
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
        assert!(
            form.validation_error
                .as_ref()
                .unwrap()
                .contains("銘柄コード")
        );
    }

    #[test]
    fn validation_fails_bad_start_date_format() {
        let mut form = ReplayFormModal::default();
        form.instrument_id = "1301.TSE".to_string();
        form.start_date = "not-a-date".to_string();
        let action = form.update(Message::Submit);
        assert!(action.is_none());
        assert!(form.validation_error.is_some());
        assert!(form.validation_error.as_ref().unwrap().contains("開始日"));
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
        assert!(form.validation_error.as_ref().unwrap().contains("初期資金"));
    }

    #[test]
    fn validation_fails_zero_cash() {
        // M-Rust4: parse は通るが 0 は事故予防のため弾く。
        let mut form = ReplayFormModal::default();
        form.instrument_id = "1301.TSE".to_string();
        form.start_date = "2025-01-06".to_string();
        form.end_date = "2025-03-31".to_string();
        form.granularity = Some(Granularity::Daily);
        form.strategy_file = Some(std::path::PathBuf::from("/tmp/strategy.py"));
        form.initial_cash = "0".to_string();
        let action = form.update(Message::Submit);
        assert!(action.is_none());
        let msg = form
            .validation_error
            .as_ref()
            .expect("zero initial_cash must produce a validation error");
        assert!(
            msg.contains("初期資金"),
            "expected 初期資金 in error, got: {msg}"
        );
    }

    #[test]
    fn validation_fails_when_start_after_end() {
        // M-2 (rust): 開始日 > 終了日 を検出する。
        let mut form = ReplayFormModal::default();
        form.instrument_id = "1301.TSE".to_string();
        form.start_date = "2025-03-31".to_string();
        form.end_date = "2025-01-06".to_string();
        form.granularity = Some(Granularity::Daily);
        form.strategy_file = Some(std::path::PathBuf::from("/tmp/strategy.py"));
        form.initial_cash = "1000000".to_string();
        let action = form.update(Message::Submit);
        assert!(action.is_none());
        assert!(form.validation_error.is_some());
        let msg = form.validation_error.as_ref().unwrap();
        assert!(
            msg.contains("開始日") && msg.contains("終了日"),
            "expected both 開始日 and 終了日 in error, got: {msg}"
        );
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

    #[test]
    fn prefill_from_scenario_populates_all_fields() {
        let mut form = ReplayFormModal::default();
        let scenario = serde_json::json!({
            "schema_version": 1,
            "instrument": "1301.TSE",
            "start": "2025-01-06",
            "end": "2025-03-31",
            "granularity": "Daily",
            "initial_cash": 1_000_000_u64,
        });
        form.prefill_from_scenario(std::path::PathBuf::from("/tmp/s.py"), &scenario);
        assert_eq!(form.instrument_id, "1301.TSE");
        assert_eq!(form.start_date, "2025-01-06");
        assert_eq!(form.end_date, "2025-03-31");
        assert_eq!(form.granularity, Some(Granularity::Daily));
        assert_eq!(form.initial_cash, "1000000");
        assert_eq!(
            form.strategy_file,
            Some(std::path::PathBuf::from("/tmp/s.py"))
        );
        assert!(form.validation_error.is_none());
    }

    #[test]
    fn prefill_from_scenario_clears_validation_error() {
        let mut form = ReplayFormModal::default();
        form.validation_error = Some("dangling".to_string());
        let scenario = serde_json::json!({
            "instrument": "7203.TSE",
            "granularity": "Minute",
        });
        form.prefill_from_scenario(std::path::PathBuf::from("/tmp/x.py"), &scenario);
        assert!(form.validation_error.is_none());
        assert_eq!(form.granularity, Some(Granularity::Minute));
    }

    #[test]
    fn prefill_from_scenario_unknown_granularity_preserves_existing() {
        let mut form = ReplayFormModal::default();
        form.granularity = Some(Granularity::Trade);
        let scenario = serde_json::json!({"granularity": "weekly"});
        form.prefill_from_scenario(std::path::PathBuf::from("/tmp/x.py"), &scenario);
        assert_eq!(form.granularity, Some(Granularity::Trade));
    }

    #[test]
    fn prefill_from_scenario_partial_keeps_other_fields() {
        let mut form = ReplayFormModal::default();
        form.instrument_id = "OLD".to_string();
        form.start_date = "2024-01-01".to_string();
        form.end_date = "2024-02-01".to_string();
        form.initial_cash = "500".to_string();
        let scenario = serde_json::json!({"instrument": "NEW"});
        form.prefill_from_scenario(std::path::PathBuf::from("/tmp/x.py"), &scenario);
        assert_eq!(form.instrument_id, "NEW");
        assert_eq!(form.start_date, "2024-01-01");
        assert_eq!(form.end_date, "2024-02-01");
        assert_eq!(form.initial_cash, "500");
    }

    #[test]
    fn prefill_from_scenario_non_object_only_sets_path() {
        let mut form = ReplayFormModal::default();
        form.instrument_id = "KEEP".to_string();
        let scenario = serde_json::json!(null);
        form.prefill_from_scenario(std::path::PathBuf::from("/tmp/x.py"), &scenario);
        assert_eq!(form.instrument_id, "KEEP");
        assert_eq!(
            form.strategy_file,
            Some(std::path::PathBuf::from("/tmp/x.py"))
        );
    }

    #[test]
    fn set_strategy_file_only_sets_path_and_keeps_fields() {
        let mut form = ReplayFormModal::default();
        form.instrument_id = "KEEP".to_string();
        form.set_strategy_file_only(std::path::PathBuf::from("/tmp/y.py"));
        assert_eq!(form.instrument_id, "KEEP");
        assert_eq!(
            form.strategy_file,
            Some(std::path::PathBuf::from("/tmp/y.py"))
        );
    }

    #[test]
    fn is_valid_date_rejects_impossible_dates() {
        // M-RS2: 形式は正しいが存在しない日付を弾く。
        assert!(
            !is_valid_date("9999-99-99"),
            "9999-99-99 should be rejected"
        );
        assert!(
            !is_valid_date("2025-02-30"),
            "2025-02-30 should be rejected"
        );
        assert!(
            !is_valid_date("2025-13-01"),
            "2025-13-01 should be rejected"
        );
        assert!(
            !is_valid_date("2025-00-01"),
            "2025-00-01 should be rejected"
        );
        assert!(
            !is_valid_date("2025-01-00"),
            "2025-01-00 should be rejected"
        );
    }
}
