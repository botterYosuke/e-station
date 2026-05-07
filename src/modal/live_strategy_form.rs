use iced::{
    Element,
    widget::{button, column, container, row, text, text_input},
};

/// Live 戦略起動フォーム modal の状態。
#[derive(Debug, Default)]
pub struct LiveStrategyFormModal {
    pub instrument_id: String,
    pub strategy_file: std::path::PathBuf,
    pub max_qty: String,
    pub max_notional_jpy: String,
    pub validation_error: Option<String>,
}

#[derive(Debug, Clone)]
pub enum Message {
    InstrumentChanged(String),
    MaxQtyChanged(String),
    MaxNotionalChanged(String),
    Submit,
    Cancel,
}

pub enum Action {
    Submit {
        instrument_id: String,
        strategy_file: std::path::PathBuf,
        max_qty: u32,
        max_notional_jpy: u64,
    },
    Cancel,
}

/// `validate()` の戻り値を構造体化することで位置引数の取り違えを防ぐ。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ValidatedForm {
    pub instrument_id: String,
    pub strategy_file: std::path::PathBuf,
    pub max_qty: u32,
    pub max_notional_jpy: u64,
}

impl LiveStrategyFormModal {
    fn validate(&self) -> Result<ValidatedForm, String> {
        let instrument_id = self.instrument_id.trim().to_string();
        if instrument_id.is_empty() {
            return Err("銘柄コードを入力してください".to_string());
        }
        if !instrument_id.contains('.') {
            return Err("銘柄コードに \".\" を含めてください (例: 8306.T)".to_string());
        }

        if !self.strategy_file.exists() {
            return Err("戦略ファイルが見つかりません".to_string());
        }

        let ext = self.strategy_file.extension();
        if ext != Some(std::ffi::OsStr::new("py")) {
            return Err("戦略ファイルは .py 拡張子が必要です".to_string());
        }

        let max_qty = self
            .max_qty
            .trim()
            .parse::<u32>()
            .map_err(|_| "最大株数は正の整数で入力してください".to_string())?;
        if !(1..=10_000).contains(&max_qty) {
            return Err("最大株数は 1〜10,000 の範囲で入力してください".to_string());
        }

        let max_notional_jpy = self
            .max_notional_jpy
            .trim()
            .parse::<u64>()
            .map_err(|_| "最大金額（円）は正の整数で入力してください".to_string())?;
        if !(1..=100_000_000).contains(&max_notional_jpy) {
            return Err("最大金額（円）は 1〜100,000,000 の範囲で入力してください".to_string());
        }

        Ok(ValidatedForm {
            instrument_id,
            strategy_file: self.strategy_file.clone(),
            max_qty,
            max_notional_jpy,
        })
    }

    pub fn update(&mut self, message: Message) -> Option<Action> {
        match message {
            Message::InstrumentChanged(v) => {
                self.instrument_id = v;
                None
            }
            Message::MaxQtyChanged(v) => {
                self.max_qty = v;
                None
            }
            Message::MaxNotionalChanged(v) => {
                self.max_notional_jpy = v;
                None
            }
            Message::Submit => match self.validate() {
                Ok(ValidatedForm {
                    instrument_id,
                    strategy_file,
                    max_qty,
                    max_notional_jpy,
                }) => {
                    self.validation_error = None;
                    Some(Action::Submit {
                        instrument_id,
                        strategy_file,
                        max_qty,
                        max_notional_jpy,
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
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_else(|| "(未選択)".to_string());

        let submit_btn = button(text("▶ ライブ実行")).on_press(Message::Submit);

        let mut col = column![
            text("Live 戦略を起動").size(18),
            text("銘柄コード").size(12),
            text_input("8306.T", &self.instrument_id).on_input(Message::InstrumentChanged),
            text("戦略ファイル").size(12),
            text(strategy_label).size(13),
            text("最大株数").size(12),
            text_input("100", &self.max_qty).on_input(Message::MaxQtyChanged),
            text("最大金額（円）").size(12),
            text_input("1000000", &self.max_notional_jpy).on_input(Message::MaxNotionalChanged),
        ]
        .spacing(6);

        if let Some(err) = &self.validation_error {
            col = col.push(text(err.as_str()).size(12).color([0.8, 0.1, 0.1]));
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

    // LG-1: validate() 全フィールド正常 → Ok(ValidatedForm)
    #[test]
    fn lg1_validate_all_valid_returns_ok() {
        let tmp_file = std::env::temp_dir().join("test_lg1.py");
        std::fs::write(&tmp_file, b"").expect("tmp file write failed");

        let form = LiveStrategyFormModal {
            instrument_id: "8306.T".to_string(),
            strategy_file: tmp_file.clone(),
            max_qty: "100".to_string(),
            max_notional_jpy: "1000000".to_string(),
            ..Default::default()
        };
        let result = form.validate();
        assert!(result.is_ok(), "expected Ok, got: {:?}", result);
        let vf = result.unwrap();
        assert_eq!(vf.instrument_id, "8306.T");
        assert_eq!(vf.strategy_file, tmp_file);
        assert_eq!(vf.max_qty, 100u32);
        assert_eq!(vf.max_notional_jpy, 1_000_000u64);

        let _ = std::fs::remove_file(&tmp_file);
    }

    // LG-2: validate() instrument_id に "." なし → Err
    #[test]
    fn lg2_validate_instrument_without_dot_returns_err() {
        let tmp_file = std::env::temp_dir().join("test_lg2.py");
        std::fs::write(&tmp_file, b"").expect("tmp file write failed");

        let form = LiveStrategyFormModal {
            instrument_id: "8306".to_string(),
            strategy_file: tmp_file.clone(),
            max_qty: "100".to_string(),
            max_notional_jpy: "1000000".to_string(),
            ..Default::default()
        };
        let result = form.validate();
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert!(
            err.contains("銘柄") || err.contains('.'),
            "expected error containing '銘柄' or '.', got: {err}"
        );

        let _ = std::fs::remove_file(&tmp_file);
    }

    // LG-3: validate() max_qty = "0" → Err
    #[test]
    fn lg3_validate_max_qty_zero_returns_err() {
        let tmp_file = std::env::temp_dir().join("test_lg3.py");
        std::fs::write(&tmp_file, b"").expect("tmp file write failed");

        let form = LiveStrategyFormModal {
            instrument_id: "8306.T".to_string(),
            strategy_file: tmp_file.clone(),
            max_qty: "0".to_string(),
            max_notional_jpy: "1000000".to_string(),
            ..Default::default()
        };
        let result = form.validate();
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert!(
            err.contains("最大株数") || err.contains('1'),
            "expected error containing '最大株数' or '1', got: {err}"
        );

        let _ = std::fs::remove_file(&tmp_file);
    }

    // LG-4: validate() max_notional_jpy = "abc" → Err
    #[test]
    fn lg4_validate_max_notional_non_numeric_returns_err() {
        let tmp_file = std::env::temp_dir().join("test_lg4.py");
        std::fs::write(&tmp_file, b"").expect("tmp file write failed");

        let form = LiveStrategyFormModal {
            instrument_id: "8306.T".to_string(),
            strategy_file: tmp_file.clone(),
            max_qty: "100".to_string(),
            max_notional_jpy: "abc".to_string(),
            ..Default::default()
        };
        let result = form.validate();
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert!(
            err.contains("最大金額") || err.contains("円"),
            "expected error containing '最大金額' or '円', got: {err}"
        );

        let _ = std::fs::remove_file(&tmp_file);
    }

    // LG-5: validate() strategy_file 拡張子が ".rs" → Err
    #[test]
    fn lg5_validate_strategy_file_wrong_extension_returns_err() {
        let tmp_file = std::env::temp_dir().join("test_lg5.rs");
        std::fs::write(&tmp_file, b"").expect("tmp file write failed");

        let form = LiveStrategyFormModal {
            instrument_id: "8306.T".to_string(),
            strategy_file: tmp_file.clone(),
            max_qty: "100".to_string(),
            max_notional_jpy: "1000000".to_string(),
            ..Default::default()
        };
        let result = form.validate();
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert!(
            err.contains(".py") || err.contains("拡張子") || err.contains("戦略"),
            "expected error about .py extension, got: {err}"
        );

        let _ = std::fs::remove_file(&tmp_file);
    }

    // LG-5b: validate() 存在しない .py ファイル → Err（ファイル不在エラー）
    #[test]
    fn lg5b_validate_nonexistent_py_returns_err() {
        let form = LiveStrategyFormModal {
            instrument_id: "8306.T".to_string(),
            strategy_file: std::path::PathBuf::from("/nonexistent/path/strategy.py"),
            max_qty: "100".to_string(),
            max_notional_jpy: "1000000".to_string(),
            ..Default::default()
        };
        let result = form.validate();
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert!(
            err.contains("見つかりません") || err.contains("存在"),
            "expected file-not-found error, got: {err}"
        );
    }

    // 追加: Cancel → Action::Cancel
    #[test]
    fn cancel_returns_cancel_action() {
        let mut form = LiveStrategyFormModal::default();
        let action = form.update(Message::Cancel);
        assert!(matches!(action, Some(Action::Cancel)));
    }

    // 追加: Submit で validate 失敗 → validation_error がセットされ None を返す
    #[test]
    fn submit_with_invalid_form_sets_validation_error() {
        let mut form = LiveStrategyFormModal::default();
        let action = form.update(Message::Submit);
        assert!(action.is_none());
        assert!(form.validation_error.is_some());
    }
}
