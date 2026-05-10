use iced::{
    Element,
    widget::{button, checkbox, column, container, row, text, text_input},
};

/// Live 戦略起動フォーム modal の状態。
///
/// issue #42 Phase 3 で 4 フィールドから拡張:
/// - `strategy_init_kwargs` (JSON テキスト) を追加し戦略コンストラクタへ渡す
/// - `prod_mode` を追加し demo / prod を切替（`TACHIBANA_ALLOW_PROD=1` env と AND 条件）
/// - `disabled_reason` で Submit ボタンを disable する理由を提示
/// - `pending_scenario_request_id` で `LoadLiveStrategyScenario` の応答を突合
#[derive(Debug, Default)]
pub struct LiveStrategyFormModal {
    pub instrument_id: String,
    pub strategy_file: std::path::PathBuf,
    pub max_qty: String,
    pub max_notional_jpy: String,
    pub validation_error: Option<String>,
    /// JSON 文字列（空文字許容）。validate() で非空のときのみ JSON parse 検証する。
    pub strategy_init_kwargs: String,
    /// demo / prod スイッチ。`TACHIBANA_ALLOW_PROD=1` env と AND 条件で
    /// 本番投入を許可する（Phase 3.5 で `is_production` cap と連動）。
    pub prod_mode: bool,
    /// Submit ボタンを disable する理由（`VenueReady` 未到達 / 市場閉場 /
    /// `TACHIBANA_ALLOW_PROD` 未設定など）。`Some` のとき `view()` は
    /// Submit ボタンの `on_press` を消し、理由を画面表示する。
    pub disabled_reason: Option<String>,
    /// `LoadLiveStrategyScenario` を engine に送信したときの request_id。
    /// `LiveStrategyScenarioLoaded` / `Error{code:"strategy_parse_failed"}` /
    /// 5s timeout のいずれかで `None` に戻す。
    pub pending_scenario_request_id: Option<String>,
}

#[derive(Debug, Clone)]
pub enum Message {
    InstrumentChanged(String),
    MaxQtyChanged(String),
    MaxNotionalChanged(String),
    StrategyInitKwargsChanged(String),
    ProdModeToggled(bool),
    Submit,
    Cancel,
}

pub enum Action {
    Submit {
        instrument_id: String,
        strategy_file: std::path::PathBuf,
        max_qty: u32,
        max_notional_jpy: u64,
        /// 戦略コンストラクタに渡す追加 kwargs。JSON object として
        /// `serde_json::Map<String, Value>` 形式。空のときは `None`。
        strategy_init_kwargs: Option<serde_json::Map<String, serde_json::Value>>,
        /// 本番モードフラグ。engine 側 venue 引数に伝搬する（Phase 3.5 で完成）。
        prod_mode: bool,
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
    pub strategy_init_kwargs: Option<serde_json::Map<String, serde_json::Value>>,
    pub prod_mode: bool,
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

        // strategy_init_kwargs: 空文字列はスキップ、非空のときは JSON object として parse 検証。
        let trimmed_kwargs = self.strategy_init_kwargs.trim();
        let strategy_init_kwargs = if trimmed_kwargs.is_empty() {
            None
        } else {
            let parsed: serde_json::Value = serde_json::from_str(trimmed_kwargs)
                .map_err(|e| format!("strategy_init_kwargs の JSON 形式が不正です: {e}"))?;
            match parsed {
                serde_json::Value::Object(map) => Some(map),
                _ => {
                    return Err(
                        "strategy_init_kwargs は JSON object（{} 形式）で指定してください"
                            .to_string(),
                    );
                }
            }
        };

        Ok(ValidatedForm {
            instrument_id,
            strategy_file: self.strategy_file.clone(),
            max_qty,
            max_notional_jpy,
            strategy_init_kwargs,
            prod_mode: self.prod_mode,
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
            Message::StrategyInitKwargsChanged(v) => {
                self.strategy_init_kwargs = v;
                None
            }
            Message::ProdModeToggled(v) => {
                self.prod_mode = v;
                None
            }
            Message::Submit => {
                // disabled_reason がある場合は Submit を発火させない（ボタン側で
                // on_press を消すが、キーボード経由などの予防として二重ガード）。
                if self.disabled_reason.is_some() {
                    return None;
                }
                match self.validate() {
                    Ok(ValidatedForm {
                        instrument_id,
                        strategy_file,
                        max_qty,
                        max_notional_jpy,
                        strategy_init_kwargs,
                        prod_mode,
                    }) => {
                        self.validation_error = None;
                        Some(Action::Submit {
                            instrument_id,
                            strategy_file,
                            max_qty,
                            max_notional_jpy,
                            strategy_init_kwargs,
                            prod_mode,
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

    /// `LiveStrategyScenarioLoaded` 受信時に form を prefill する。
    /// engine から得た値で空欄を埋める。空欄でないフィールドは engine 値で上書きする
    /// （modal を開いたばかりで prefill するのが想定経路のため）。
    pub fn prefill_from_scenario(
        &mut self,
        instrument_id: Option<String>,
        max_qty: Option<u32>,
        max_notional_jpy: Option<u64>,
        strategy_init_kwargs: Option<serde_json::Map<String, serde_json::Value>>,
    ) {
        if let Some(id) = instrument_id {
            self.instrument_id = id;
        }
        if let Some(q) = max_qty {
            self.max_qty = q.to_string();
        }
        if let Some(n) = max_notional_jpy {
            self.max_notional_jpy = n.to_string();
        }
        if let Some(kwargs) = strategy_init_kwargs {
            self.strategy_init_kwargs =
                serde_json::to_string_pretty(&serde_json::Value::Object(kwargs))
                    .unwrap_or_default();
        }
        // engine から応答が返った時点で pending を解除（手入力 fallback の対称）。
        self.pending_scenario_request_id = None;
        self.validation_error = None;
    }

    /// `LoadLiveStrategyScenario` の応答が 5s 経っても来ない / `strategy_parse_failed`
    /// 受信時に呼ぶ。pending を外して手入力モードに戻す。
    pub fn release_scenario_pending(&mut self) {
        self.pending_scenario_request_id = None;
    }

    pub fn view(&self) -> Element<'_, Message> {
        let strategy_label = self
            .strategy_file
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_else(|| "(未選択)".to_string());

        // disabled_reason が Some のとき Submit ボタンの on_press を省略して disable する。
        let mut submit_btn = button(text("▶ ライブ実行"));
        if self.disabled_reason.is_none() {
            submit_btn = submit_btn.on_press(Message::Submit);
        }

        let prod_checkbox = checkbox(self.prod_mode)
            .label("本番モード（prod）")
            .on_toggle(Message::ProdModeToggled);

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
            text("strategy_init_kwargs (JSON, 任意)").size(12),
            text_input("{\"threshold\": 1.5}", &self.strategy_init_kwargs)
                .on_input(Message::StrategyInitKwargsChanged),
            prod_checkbox,
        ]
        .spacing(6);

        if let Some(reason) = &self.disabled_reason {
            // 黄色寄りの警告色で disable 理由を表示。
            col = col.push(text(reason.as_str()).size(12).color([0.85, 0.55, 0.1]));
        }

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

    // ── issue #42 Phase 3: 拡張フィールドのテスト ───────────────────────────

    fn make_valid_form() -> (LiveStrategyFormModal, std::path::PathBuf) {
        let tmp_file = std::env::temp_dir().join(format!(
            "test_live_strategy_form_{}.py",
            uuid::Uuid::new_v4()
        ));
        std::fs::write(&tmp_file, b"").expect("tmp file write failed");
        let form = LiveStrategyFormModal {
            instrument_id: "8306.T".to_string(),
            strategy_file: tmp_file.clone(),
            max_qty: "100".to_string(),
            max_notional_jpy: "1000000".to_string(),
            ..Default::default()
        };
        (form, tmp_file)
    }

    // strategy_init_kwargs が空文字列のとき validate は Ok を返す（None として）。
    #[test]
    fn test_validate_strategy_init_kwargs_empty_ok() {
        let (form, tmp_file) = make_valid_form();
        let result = form.validate();
        assert!(result.is_ok(), "empty kwargs must be Ok: {:?}", result);
        let vf = result.unwrap();
        assert!(
            vf.strategy_init_kwargs.is_none(),
            "empty kwargs must produce None"
        );
        let _ = std::fs::remove_file(&tmp_file);
    }

    // strategy_init_kwargs が不正 JSON のとき validate は Err を返す。
    #[test]
    fn test_validate_strategy_init_kwargs_invalid_json() {
        let (mut form, tmp_file) = make_valid_form();
        form.strategy_init_kwargs = "not a json".to_string();
        let result = form.validate();
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert!(
            err.contains("strategy_init_kwargs") || err.contains("JSON"),
            "expected JSON parse error, got: {err}"
        );
        let _ = std::fs::remove_file(&tmp_file);
    }

    // strategy_init_kwargs が JSON object 以外（配列など）のとき validate は Err を返す。
    #[test]
    fn test_validate_strategy_init_kwargs_array_rejected() {
        let (mut form, tmp_file) = make_valid_form();
        form.strategy_init_kwargs = "[1, 2, 3]".to_string();
        let result = form.validate();
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert!(
            err.contains("object") || err.contains("{}"),
            "expected object error, got: {err}"
        );
        let _ = std::fs::remove_file(&tmp_file);
    }

    // strategy_init_kwargs に正しい JSON object を渡すと map として返る。
    #[test]
    fn test_validate_strategy_init_kwargs_valid_object() {
        let (mut form, tmp_file) = make_valid_form();
        form.strategy_init_kwargs = r#"{"threshold": 1.5, "name": "x"}"#.to_string();
        let result = form.validate();
        assert!(result.is_ok(), "expected Ok, got: {:?}", result);
        let vf = result.unwrap();
        let kwargs = vf.strategy_init_kwargs.expect("kwargs must be Some");
        assert_eq!(kwargs.len(), 2);
        assert!(kwargs.contains_key("threshold"));
        assert!(kwargs.contains_key("name"));
        let _ = std::fs::remove_file(&tmp_file);
    }

    // disabled_reason がある場合、Submit は発火しない。
    #[test]
    fn test_disabled_reason_blocks_submit() {
        let (mut form, tmp_file) = make_valid_form();
        form.disabled_reason = Some("VenueReady 未到達".to_string());
        let action = form.update(Message::Submit);
        assert!(
            action.is_none(),
            "disabled_reason がある場合 Submit Action を返してはならない"
        );
        let _ = std::fs::remove_file(&tmp_file);
    }

    // prod_mode true は ValidatedForm に伝搬する。
    #[test]
    fn test_prod_mode_propagates_to_validated_form() {
        let (mut form, tmp_file) = make_valid_form();
        form.prod_mode = true;
        let result = form.validate();
        assert!(result.is_ok());
        assert!(result.unwrap().prod_mode, "prod_mode must propagate");
        let _ = std::fs::remove_file(&tmp_file);
    }

    // ProdModeToggled メッセージで prod_mode が変化する。
    #[test]
    fn test_prod_mode_toggled_updates_state() {
        let mut form = LiveStrategyFormModal::default();
        assert!(!form.prod_mode);
        form.update(Message::ProdModeToggled(true));
        assert!(form.prod_mode);
        form.update(Message::ProdModeToggled(false));
        assert!(!form.prod_mode);
    }

    // prefill_from_scenario が全フィールドを埋める。
    #[test]
    fn test_prefill_from_scenario_populates_fields() {
        let mut form = LiveStrategyFormModal {
            pending_scenario_request_id: Some("req-1".to_string()),
            ..Default::default()
        };
        let mut kwargs = serde_json::Map::new();
        kwargs.insert(
            "threshold".to_string(),
            serde_json::Value::Number(serde_json::Number::from_f64(2.5).unwrap()),
        );
        form.prefill_from_scenario(
            Some("8306.T".to_string()),
            Some(100),
            Some(500_000),
            Some(kwargs),
        );
        assert_eq!(form.instrument_id, "8306.T");
        assert_eq!(form.max_qty, "100");
        assert_eq!(form.max_notional_jpy, "500000");
        assert!(form.strategy_init_kwargs.contains("threshold"));
        assert!(
            form.pending_scenario_request_id.is_none(),
            "prefill must clear pending_scenario_request_id"
        );
    }

    // prefill_from_scenario で None を渡したフィールドは既存値を保持。
    #[test]
    fn test_prefill_from_scenario_partial_keeps_existing() {
        let mut form = LiveStrategyFormModal {
            instrument_id: "1301.T".to_string(),
            max_qty: "50".to_string(),
            ..Default::default()
        };
        form.prefill_from_scenario(None, None, Some(123_456), None);
        assert_eq!(form.instrument_id, "1301.T");
        assert_eq!(form.max_qty, "50");
        assert_eq!(form.max_notional_jpy, "123456");
        assert!(form.strategy_init_kwargs.is_empty());
    }

    // release_scenario_pending は pending_scenario_request_id を解除する。
    #[test]
    fn test_release_scenario_pending() {
        let mut form = LiveStrategyFormModal {
            pending_scenario_request_id: Some("abc".to_string()),
            ..Default::default()
        };
        form.release_scenario_pending();
        assert!(form.pending_scenario_request_id.is_none());
    }
}
