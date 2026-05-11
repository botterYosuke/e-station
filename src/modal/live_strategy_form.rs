use std::collections::HashMap;

use iced::{
    Element,
    widget::{button, checkbox, column, container, pick_list, row, text, text_input},
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
    /// issue #42 R1 MEDIUM-1: venue → `is_production` cap (engine 再起動時に固定)。
    /// `Ready.capabilities.venue_capabilities[<venue>].is_production` から
    /// `engine_client::capabilities::is_production(caps, venue)` 経由で venue 毎に
    /// 読み取って格納する（cap 欠落 / 旧 server / malformed wire / 未含有 venue は
    /// 安全側 `false`）。`prod_mode=true && is_production_by_venue.get(venue) != Some(&true)`
    /// で validate() は reject する。engine プロセスの env (`TACHIBANA_ALLOW_PROD=1` /
    /// `KABU_ALLOW_PROD=1 + KABU_ENV=prod`) を書き換えるには engine 再起動が必要なので、
    /// 本フィールドは modal 側で勝手に True に変更してはならない。
    ///
    /// 旧実装は `tachibana_is_production: bool` 単一フィールドで、kabu_station の
    /// prod を恒久的に hardcode reject していた（spec §違反）。
    pub is_production_by_venue: HashMap<String, bool>,
    /// issue #42 R1 MEDIUM-1: dropdown で選択中の venue（"tachibana" / "kabu_station"）。
    /// `LiveStrategyScenarioLoaded.venue` の prefill 経路（handlers/replay.rs）と
    /// `Message::VenueChanged` 経由のユーザー選択経路の両方で更新される。
    /// `validate()` で `available_venues` 含有チェック + `connected_venue` 一致チェックを行う。
    pub venue: String,
    /// issue #42 R1 MEDIUM-1: dropdown に表示する venue のリスト。
    /// modal 構築時に `engine_client::capabilities::supports_live_strategy(caps, venue)`
    /// が true な venue だけを filter して渡す。`view()` は要素数 0/1 を考慮して
    /// dropdown を出すか read-only にするかを決める（ただし validate() は常に
    /// `available_venues` 含有を要求する）。
    pub available_venues: Vec<String>,
    /// issue #42 R1 MEDIUM-1: 現在 engine が login 済の venue（`_connected_venue` 相当）。
    /// `Some("tachibana")` で `venue = "kabu_station"` を Submit すると validate() で
    /// reject する。`None` のときは GUI 側で venue 不一致検出を skip し、server.py 側
    /// の `_handle_start_engine` の `_connected_venue` 判定に委ねる（既存契約）。
    pub connected_venue: Option<String>,
}

#[derive(Debug, Clone)]
pub enum Message {
    InstrumentChanged(String),
    MaxQtyChanged(String),
    MaxNotionalChanged(String),
    StrategyInitKwargsChanged(String),
    ProdModeToggled(bool),
    /// issue #42 R1 MEDIUM-1: dropdown 選択イベント。
    VenueChanged(String),
    Submit,
    Cancel,
}

#[derive(Debug)]
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
        /// issue #42 R1 MEDIUM-1: form で選択した venue。後続の `StartEngine`
        /// 構築では現状未使用（server.py 側が `_connected_venue` を SoT として
        /// dispatch する既存契約）だが、form 値と connected_venue の不整合検出
        /// を validate() が済ませている前提で flow する。将来 `EngineStartConfig`
        /// に venue field を載せる際の wire 経路となる。
        venue: String,
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
    /// issue #42 R1 MEDIUM-1: validate() が `available_venues` 含有チェックと
    /// `connected_venue` 一致チェックを済ませた後の venue 値。
    pub venue: String,
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

        // R3 M3: venue check は prod_mode check より **前** に行う。
        // 旧実装は prod_mode check が先で venue 未選択時にも
        // 「TACHIBANA_ALLOW_PROD env が未設定…」と誤誘導していた。
        //
        // issue #42 R1 MEDIUM-1: venue 選択値の検証。
        // `available_venues` が空のときは Phase 3.5 以前の compat 経路として
        // venue 検査を skip し、server.py 側の `_connected_venue` 判定に委ねる。
        //
        // R3 M12: ⚠️ skip is intentional but UX risk — capability 未受信
        // (engine 未接続 / Handshake 未完了 / 旧バージョン engine) 経路だが、
        // 設計上「server.py 側で reject される」ことを前提にしている。
        // UX 上は modal で先に検出してほしいが、現状の実装は engine 接続前に
        // modal を構築する経路をサポートするためこの skip が必要。
        // 改善案 (Phase 5): `available_venues` 空 + `connected_venue=None` の
        // 組合せのときは modal 表示時に「engine 未接続です」status banner を
        // 出し、Submit 前にユーザに気付かせる経路の追加。詳細は計画書の
        // 「Phase 5 への引き継ぎ」TODO 参照。
        let venue = self.venue.trim().to_string();
        if !self.available_venues.is_empty() {
            if venue.is_empty() {
                return Err("venue を選択してください".to_string());
            }
            if !self.available_venues.iter().any(|v| v == &venue) {
                return Err(format!(
                    "venue '{venue}' は live strategy に対応していません（対応 venue: {available}）",
                    available = self.available_venues.join(", ")
                ));
            }
            // `connected_venue` が Some なら戦略 venue と接続 venue の一致を要求する。
            // None なら GUI 側で判定を skip し、server.py の `_handle_start_engine`
            // の `_connected_venue` 判定に委ねる（既存 契約）。
            if let Some(connected) = self.connected_venue.as_deref()
                && connected != venue
            {
                return Err(format!(
                    "選択した venue '{venue}' は engine 接続 venue '{connected}' と一致しません。\
                     engine 接続 venue にログインしてから再試行してください"
                ));
            }
        }

        // R3 M3 + M4 + R1 MEDIUM-1: prod_mode check は venue check の後 (順序契約)。
        // venue-aware に文言を分岐する (kabu_station 経路で `TACHIBANA_ALLOW_PROD` を
        // 文中に含めると誤誘導になる)。
        //
        // issue #42 R1 MEDIUM-1 (2026-05-11): kabu_station prod の hardcode reject を撤廃。
        // server.py は既に `kabu_station.is_production = (_kabu_env == "prod")` を expose
        // 済 (`python/engine/server.py::_build_ready`)。form は `is_production_by_venue`
        // HashMap で venue 毎に cap を読めば、`KABU_ALLOW_PROD=1 + KABU_ENV=prod` を立てた
        // engine プロセスから kabu prod も解放できる。
        //
        // engine プロセスの env が SoT なので runtime での切替は不可 (統一決定 #14)。
        if self.prod_mode {
            let is_prod = self
                .is_production_by_venue
                .get(&venue)
                .copied()
                .unwrap_or(false);
            if !is_prod {
                let env_hint = match venue.as_str() {
                    "tachibana" => "TACHIBANA_ALLOW_PROD",
                    "kabu_station" => "KABU_ALLOW_PROD",
                    // 未知 venue (新規追加 venue の cap 未受信時) は generic 文言。
                    _ => "production env",
                };
                return Err(format!(
                    "prod_mode は {venue} の production env ({env_hint}) 設定が必要です（engine 再起動が必要）"
                ));
            }
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
            venue,
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
            Message::VenueChanged(v) => {
                self.venue = v;
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
                        venue,
                    }) => {
                        self.validation_error = None;
                        Some(Action::Submit {
                            instrument_id,
                            strategy_file,
                            max_qty,
                            max_notional_jpy,
                            strategy_init_kwargs,
                            prod_mode,
                            venue,
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
        venue: Option<String>,
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
        // issue #42 R1 MEDIUM-1: scenario.venue を form に prefill する。
        // engine が venue を渡してこなければ既存値 (modal 構築時の connected_venue
        // 由来) を維持する。
        if let Some(v) = venue {
            self.venue = v;
        }
        if let Some(kwargs) = strategy_init_kwargs {
            // R2-B M6: 旧実装は `unwrap_or_default()` で silent fallback だったが、
            // serde_json::Value::Object のシリアライズが失敗するのは std fmt 内部の
            // memory 系エラーに限る（typical な map は必ず通る）。万一失敗した場合は
            // 既存値（modal が開いたばかりなら空文字）を保ち、log warn を残す。
            match serde_json::to_string_pretty(&serde_json::Value::Object(kwargs)) {
                Ok(s) => self.strategy_init_kwargs = s,
                Err(e) => {
                    log::warn!(
                        "[LiveStrategyFormModal] strategy_init_kwargs serialization failed: {e} \
                         — keeping existing value"
                    );
                    // 既存値を維持（明示的に何もしない）。
                }
            }
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

    /// R2-B M7: modal を表示中に venue state が変わったとき、`disabled_reason`
    /// を動的に更新する setter。`Some(reason)` を渡すと Submit ボタンが disable に、
    /// `None` を渡すと有効化される。`VenueReady` 受信時 → `set_disabled_reason(None)`、
    /// `VenueLoginError{market_closed:true}` 受信時 → `set_disabled_reason(Some("市場が
    /// 閉場中です".to_string()))` を呼ぶ運用。
    ///
    /// 注意: `is_production` cap は engine プロセスの env 変更が必要なため、
    /// modal 表示中に動的に切り替わることはない（統一決定 #14）。本 setter は
    /// venue 接続状態 / 市場開閉状態のみを対象とする。
    pub fn set_disabled_reason(&mut self, reason: Option<String>) {
        self.disabled_reason = reason;
    }

    /// R3 M2: `LiveStrategyScenarioLoaded` arrival 後、両 venue が ready の場合
    /// (= 旧 implementation の `connected_venue` 計算が tachibana 固定優先で
    /// silent UX failure を起こす状況) に、scenario.venue を SoT として
    /// `connected_venue` を上書きする setter。
    ///
    /// 設計判断: modal 表示中の venue state 変化 (片方のみ ready → 両 ready)
    /// 経路でも安全に呼べるよう、無条件に値を差し替える (caller が事前に
    /// 「scenario.venue Some + 該当 venue ready」を判定する)。
    pub fn set_connected_venue(&mut self, venue: Option<String>) {
        self.connected_venue = venue;
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
        ]
        .spacing(6);

        // issue #42 R1 MEDIUM-1: venue dropdown を `prod_checkbox` の上に挿入する。
        // - `available_venues` が空: capability 未受信の compat 経路。dropdown を出さない。
        // - 1 件: read-only 表示（ユーザーには選択肢が無いので text で出すだけ）。
        // - 2 件以上: pick_list で選択可能にする。
        match self.available_venues.len() {
            0 => {}
            1 => {
                col = col.push(text("venue").size(12));
                col = col.push(text(self.available_venues[0].as_str()).size(13));
            }
            _ => {
                col = col.push(text("venue").size(12));
                // R3 M5: `available_venues.clone()` / `venue.clone()` を削除し、
                // iced 0.14 の `pick_list` に slice / borrow を渡す。
                // `pick_list(L, Option<V>, Fn)` で L: AsRef<[V]>, V: Clone + Eq.
                let selected: Option<&String> = if self.venue.is_empty() {
                    None
                } else {
                    Some(&self.venue)
                };
                col = col.push(pick_list(
                    self.available_venues.as_slice(),
                    selected,
                    Message::VenueChanged,
                ));
            }
        }

        col = col.push(prod_checkbox);

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
    // issue #42 Phase 3.5: validate() が prod_mode + cap を要求するため、
    // 既存テスト互換のため `is_production_by_venue` 経由で cap=true をセットする。
    // R1 MEDIUM-1 (2026-05-11): 旧 `tachibana_is_production: bool` を venue 別 HashMap
    // (`is_production_by_venue`) に置き換え。venue 既定値 (空文字) でも fallback で
    // 動くよう、tachibana key を立てる必要はない（validate() の `get(&venue)` は
    // 該当 venue を見る）。本テストは venue 未指定 (available_venues 空) で通すため
    // tachibana を入れない経路でも prod_mode 伝搬を pin する目的。
    #[test]
    fn test_prod_mode_propagates_to_validated_form() {
        let (mut form, tmp_file) = make_valid_form();
        form.prod_mode = true;
        // available_venues 空 → venue 検査 skip。venue は空文字。
        // is_production_by_venue["" ] = true で fallback を満たす。
        form.is_production_by_venue.insert(String::new(), true);
        let result = form.validate();
        assert!(result.is_ok(), "expected Ok, got: {:?}", result);
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

    // ── issue #42 Phase 3.5: tachibana is_production cap 連動 ────────────────

    /// R1 MEDIUM-1: `is_production_by_venue` に該当 venue エントリが無い / false のとき
    /// `prod_mode=true` で reject される。固定文言（venue + 該当 env）を返すこと。
    /// 旧名: `test_validate_rejects_prod_mode_when_is_production_cap_false`.
    #[test]
    fn test_validate_rejects_prod_mode_when_is_production_cap_false() {
        let (mut form, tmp_file) = make_valid_form();
        form.prod_mode = true;
        // available_venues 空 / venue 空文字 → venue 検査 skip。
        // is_production_by_venue は空 → fallback で false (env 未設定相当)。
        let result = form.validate();
        assert!(
            result.is_err(),
            "prod_mode=true + cap=false は reject すべき"
        );
        let err = result.unwrap_err();
        // venue が空文字のときは generic "production env" が出る。
        assert!(
            err.contains("production env"),
            "汎用文言に production env が含まれるべき: {err}"
        );
        assert!(
            err.contains("再起動"),
            "engine 再起動を促す文言が必要: {err}"
        );
        let _ = std::fs::remove_file(&tmp_file);
    }

    /// R1 MEDIUM-1: `is_production_by_venue` に対応 venue エントリで cap=true なら
    /// prod_mode=true で validate() 成功。
    #[test]
    fn test_validate_allows_prod_mode_when_is_production_cap_true() {
        let (mut form, tmp_file) = make_valid_form();
        form.prod_mode = true;
        // venue 空文字 fallback パスでも HashMap に "" -> true を入れれば通る。
        form.is_production_by_venue.insert(String::new(), true);
        let result = form.validate();
        assert!(
            result.is_ok(),
            "prod_mode=true + cap=true は OK: {:?}",
            result
        );
        let _ = std::fs::remove_file(&tmp_file);
    }

    /// R1 MEDIUM-1: cap=false でも prod_mode=false なら validate() は副作用なし。
    #[test]
    fn test_validate_ignores_is_production_cap_when_prod_mode_false() {
        let (mut form, tmp_file) = make_valid_form();
        form.prod_mode = false;
        // is_production_by_venue は空 (= 全 venue false 相当) のままで OK。
        let result = form.validate();
        assert!(result.is_ok(), "prod_mode=false なら cap 値に関係なく OK");
        let _ = std::fs::remove_file(&tmp_file);
    }

    /// R1 MEDIUM-1: `is_production_by_venue` フィールドの default は空 HashMap (安全側
    /// = 全 venue 未対応扱い)。modal 構築は handlers 側で struct literal による直代入で
    /// `engine_client::capabilities::is_production` の戻り値を venue 毎に流す
    /// （`src/handlers/replay.rs::NativeOpenStrategyPicked` live 分岐）。
    /// 旧名: `test_tachibana_is_production_default_is_false`.
    #[test]
    fn test_is_production_by_venue_default_is_empty() {
        let form = LiveStrategyFormModal::default();
        assert!(
            form.is_production_by_venue.is_empty(),
            "default は空 HashMap (安全側 = 全 venue demo 扱い)"
        );
    }

    // ── R3 M3 + M4: venue check / prod_mode check 順序 + venue-aware 文言 ────

    /// R3 M3: venue 未選択 + prod_mode=true で **venue エラーが先** に返る。
    /// 旧実装は prod_mode check が venue check より前にあったため、venue 未選択
    /// 時にも「TACHIBANA_ALLOW_PROD env が未設定…」と誤誘導していた。
    #[test]
    fn test_validate_returns_venue_error_before_prod_mode_error() {
        let (mut form, tmp_file) = make_valid_form();
        form.venue = "".to_string();
        form.available_venues = vec!["tachibana".to_string(), "kabu_station".to_string()];
        form.prod_mode = true;
        // R1 MEDIUM-1: tachibana_is_production → is_production_by_venue HashMap に置換。
        // tachibana = false で旧挙動と同等に。
        form.is_production_by_venue
            .insert("tachibana".to_string(), false);
        let result = form.validate();
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert!(
            err.contains("venue"),
            "venue 未選択は venue 関連エラーが先に返るべき (M3); got: {err}"
        );
        assert!(
            !err.contains("TACHIBANA_ALLOW_PROD"),
            "venue 未選択時に prod env エラーは出ないこと; got: {err}"
        );
        let _ = std::fs::remove_file(&tmp_file);
    }

    /// R3 M12: `available_venues` 空のときは venue 検査を **skip** する
    /// compat 経路を pin する。skip 自体は意図的（engine 未接続 / 旧 server
    /// 経路で modal を出す）だが、UX risk として doc コメントで明示済。
    #[test]
    fn test_validate_skips_venue_check_when_available_venues_empty() {
        let (mut form, tmp_file) = make_valid_form();
        // venue は空文字でも `available_venues` が空 = compat 経路なら validate を通す。
        form.venue = "".to_string();
        form.available_venues = Vec::new();
        form.connected_venue = None;
        let result = form.validate();
        assert!(
            result.is_ok(),
            "available_venues 空のときは venue 検査を skip すべき (R3 M12 compat 経路); \
             got: {result:?}"
        );
        let _ = std::fs::remove_file(&tmp_file);
    }

    /// R3 M4 + R1 MEDIUM-1: `venue=="kabu_station"` で prod_mode=true のエラーメッセージは
    /// kabu 文脈の文言を返す (旧実装は固定で TACHIBANA_ALLOW_PROD を文中に含めていたため、
    /// kabu ユーザが tachibana env をいじろうとする誤誘導が起きた)。R1 MEDIUM-1 で
    /// kabu prod も `is_production_by_venue["kabu_station"] = false` のときは reject、
    /// 文言は KABU_ALLOW_PROD を示す。
    #[test]
    fn test_validate_prod_mode_error_message_is_venue_aware_for_kabu() {
        let (mut form, tmp_file) = make_valid_form();
        form.venue = "kabu_station".to_string();
        form.available_venues = vec!["tachibana".to_string(), "kabu_station".to_string()];
        form.connected_venue = Some("kabu_station".to_string());
        form.prod_mode = true;
        // kabu_station の is_production cap が false (env 未設定相当) → reject
        form.is_production_by_venue
            .insert("kabu_station".to_string(), false);
        let result = form.validate();
        assert!(
            result.is_err(),
            "prod_mode=true は kabu でも (kabu 用 prod cap が false の限り) reject"
        );
        let err = result.unwrap_err();
        assert!(
            err.contains("kabu_station") || err.contains("venue"),
            "kabu venue 文脈であることが文言に出るべき (M4); got: {err}"
        );
        let _ = std::fs::remove_file(&tmp_file);
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
            None,
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

    // R2-B M6: prefill_from_scenario の strategy_init_kwargs シリアライズ失敗ハンドリング
    // を source-pin する。実 runtime で serde_json::to_string_pretty が
    // Map<String, Value> に対して失敗するのは std fmt 内部限定のため挙動テストは困難。
    // 代わりにソース上に「log warn + 既存値保持」の経路が残っていることを pin する。
    #[test]
    fn test_prefill_from_scenario_logs_on_serialize_error() {
        let src = include_str!("../modal/live_strategy_form.rs");
        let pos = src
            .find("pub fn prefill_from_scenario")
            .expect("prefill_from_scenario not found");
        let mut end = (pos + 2500).min(src.len());
        while end > 0 && !src.is_char_boundary(end) {
            end -= 1;
        }
        let body = &src[pos..end];
        assert!(
            body.contains("log::warn!"),
            "prefill_from_scenario must log::warn! on serialization failure: {body}"
        );
        // 旧実装の `to_string_pretty(...).unwrap_or_default()` パターンが消えていること
        // (コメント内の言及は許容するため `).unwrap_or_default()` の閉じ括弧つきで pin する)。
        assert!(
            !body.contains(").unwrap_or_default()"),
            "prefill_from_scenario must NOT chain unwrap_or_default() on serialize result \
             (silent fallback): {body}"
        );
        // serialize 失敗時に既存値が保たれる (= self.strategy_init_kwargs = s は Ok 経路のみ)。
        assert!(
            body.contains("Ok(s) => self.strategy_init_kwargs = s"),
            "prefill_from_scenario must only assign strategy_init_kwargs on Ok: {body}"
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
        form.prefill_from_scenario(None, None, Some(123_456), None, None);
        assert_eq!(form.instrument_id, "1301.T");
        assert_eq!(form.max_qty, "50");
        assert_eq!(form.max_notional_jpy, "123456");
        assert!(form.strategy_init_kwargs.is_empty());
    }

    /// M-1: `prefill_from_scenario(.., Some(venue))` で `venue` フィールドが上書きされる。
    /// LiveStrategyScenarioLoaded.venue = "kabu_station" 経路で modal にセットされる契約。
    #[test]
    fn test_prefill_from_scenario_overwrites_venue() {
        let mut form = LiveStrategyFormModal {
            venue: "tachibana".to_string(),
            ..Default::default()
        };
        form.prefill_from_scenario(None, None, None, None, Some("kabu_station".to_string()));
        assert_eq!(form.venue, "kabu_station");
    }

    /// M-1: `prefill_from_scenario(.., None)` (scenario.venue が空) で既存値を保持。
    #[test]
    fn test_prefill_from_scenario_none_keeps_existing_venue() {
        let mut form = LiveStrategyFormModal {
            venue: "tachibana".to_string(),
            ..Default::default()
        };
        form.prefill_from_scenario(None, None, None, None, None);
        assert_eq!(form.venue, "tachibana", "venue=None must keep existing");
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

    // ── issue #42 R1 MEDIUM-1: GUI venue dropdown ──────────────────────────

    /// M-1: 初期化時 `venue` フィールドが空。
    /// modal を struct literal で構築する handlers/replay.rs::NativeOpenStrategyPicked
    /// が prefill 前に Default::default() で空文字を入れる経路を pin する。
    #[test]
    fn test_venue_field_default_empty() {
        let form = LiveStrategyFormModal::default();
        assert!(
            form.venue.is_empty(),
            "venue default must be empty string (handlers prefill the value)"
        );
        assert!(
            form.available_venues.is_empty(),
            "available_venues default must be empty Vec"
        );
        assert!(
            form.connected_venue.is_none(),
            "connected_venue default must be None"
        );
    }

    /// M-1: `Message::VenueChanged("kabu_station")` で `venue` フィールドが更新される。
    #[test]
    fn test_venue_changed_message_updates_state() {
        let mut form = LiveStrategyFormModal::default();
        let action = form.update(Message::VenueChanged("kabu_station".to_string()));
        assert!(action.is_none(), "VenueChanged must not emit Action");
        assert_eq!(form.venue, "kabu_station");
        // 連続更新も冪等に上書きできる。
        form.update(Message::VenueChanged("tachibana".to_string()));
        assert_eq!(form.venue, "tachibana");
    }

    /// M-1: `available_venues = ["tachibana"]` で `venue = "kabu_station"` → validate Err。
    /// dropdown 選択値が capability=true の venue リストに含まれることを必須化。
    #[test]
    fn test_validate_rejects_venue_not_in_available_list() {
        let (mut form, tmp_file) = make_valid_form();
        form.available_venues = vec!["tachibana".to_string()];
        form.venue = "kabu_station".to_string();
        let result = form.validate();
        assert!(
            result.is_err(),
            "venue 未対応 (available 外) は reject すべき"
        );
        let err = result.unwrap_err();
        assert!(
            err.contains("venue") || err.contains("対応"),
            "expected venue-mismatch error, got: {err}"
        );
        let _ = std::fs::remove_file(&tmp_file);
    }

    /// M-1: `connected_venue = Some("tachibana")` で `venue = "kabu_station"` → validate Err。
    /// 戦略ファイル指定 venue と engine 接続 venue の不整合検出。
    #[test]
    fn test_validate_rejects_venue_mismatch_with_connected() {
        let (mut form, tmp_file) = make_valid_form();
        form.available_venues = vec!["tachibana".to_string(), "kabu_station".to_string()];
        form.venue = "kabu_station".to_string();
        form.connected_venue = Some("tachibana".to_string());
        let result = form.validate();
        assert!(
            result.is_err(),
            "connected_venue と form.venue の不一致は reject すべき"
        );
        let err = result.unwrap_err();
        assert!(
            err.contains("接続") || err.contains("venue"),
            "expected connected-venue mismatch error, got: {err}"
        );
        let _ = std::fs::remove_file(&tmp_file);
    }

    /// M-1: `available_venues = ["tachibana", "kabu_station"]` で
    /// `venue = "kabu_station"` && `connected_venue = Some("kabu_station")` → validate Ok。
    #[test]
    fn test_validate_accepts_venue_in_available_list() {
        let (mut form, tmp_file) = make_valid_form();
        form.available_venues = vec!["tachibana".to_string(), "kabu_station".to_string()];
        form.venue = "kabu_station".to_string();
        form.connected_venue = Some("kabu_station".to_string());
        let result = form.validate();
        assert!(
            result.is_ok(),
            "available + connected 一致なら Ok: {:?}",
            result
        );
        assert_eq!(result.unwrap().venue, "kabu_station");
        let _ = std::fs::remove_file(&tmp_file);
    }

    /// M-1: `Submit` action の `venue` フィールドが form の `venue` を反映する。
    #[test]
    fn test_action_submit_includes_venue() {
        let (mut form, tmp_file) = make_valid_form();
        form.available_venues = vec!["tachibana".to_string()];
        form.venue = "tachibana".to_string();
        // connected_venue = None なら接続確認は skip（form 側のみで完結する経路）。
        let action = form.update(Message::Submit);
        match action {
            Some(Action::Submit { venue, .. }) => {
                assert_eq!(venue, "tachibana", "Submit Action must carry venue field");
            }
            other => panic!("expected Action::Submit with venue, got: {other:?}"),
        }
        let _ = std::fs::remove_file(&tmp_file);
    }

    /// M-1: `view()` が panic しない smoke test。available_venues=空 / 1件 / 2件 を網羅。
    /// iced widget の dropdown 構築自体に panic 経路 (例: Default::default() の失敗) が
    /// 入っていないことだけを確認する（GUI rendering は別途 e2e で検証）。
    #[test]
    fn test_view_renders_without_panic_for_various_venue_counts() {
        // 0 件
        let form_empty = LiveStrategyFormModal::default();
        let _ = form_empty.view();
        // 1 件
        let form_one = LiveStrategyFormModal {
            available_venues: vec!["tachibana".to_string()],
            venue: "tachibana".to_string(),
            ..Default::default()
        };
        let _ = form_one.view();
        // 2 件
        let form_two = LiveStrategyFormModal {
            available_venues: vec!["tachibana".to_string(), "kabu_station".to_string()],
            venue: "kabu_station".to_string(),
            ..Default::default()
        };
        let _ = form_two.view();
    }

    /// M-1: validated form が venue を確実に保持していること（field 存在 pin）。
    /// `ValidatedForm` に venue が追加されたかを type レベルで確認。
    #[test]
    fn test_validated_form_carries_venue_field() {
        let (mut form, tmp_file) = make_valid_form();
        form.available_venues = vec!["tachibana".to_string()];
        form.venue = "tachibana".to_string();
        let vf = form.validate().expect("validate must succeed");
        assert_eq!(vf.venue, "tachibana");
        let _ = std::fs::remove_file(&tmp_file);
    }

    // ── issue #42 R1 MEDIUM-1: venue-aware is_production ────────────────────

    /// R1 MEDIUM-1: kabu_station venue で `is_production_by_venue["kabu_station"] = true`
    /// なら prod_mode=true を Submit 可能。旧実装は hardcode で「Phase 5 へ繰越」と
    /// していたため kabu_station prod が恒久 reject されていた。server.py は既に
    /// `kabu_station.is_production = (_kabu_env == "prod")` を expose 済なので、
    /// form が cap を venue-aware に読めば user は KABU_ALLOW_PROD=1 + KABU_ENV=prod
    /// で kabu prod を起動できる。
    #[test]
    fn test_validate_allows_prod_mode_for_kabu_when_is_production_true() {
        let (mut form, tmp_file) = make_valid_form();
        form.available_venues = vec!["tachibana".to_string(), "kabu_station".to_string()];
        form.venue = "kabu_station".to_string();
        form.connected_venue = Some("kabu_station".to_string());
        form.prod_mode = true;
        form.is_production_by_venue
            .insert("kabu_station".to_string(), true);
        let result = form.validate();
        assert!(
            result.is_ok(),
            "kabu_station + prod_mode=true + is_production cap=true must validate Ok \
             (旧実装は hardcode reject していた): {result:?}"
        );
        assert!(result.unwrap().prod_mode);
        let _ = std::fs::remove_file(&tmp_file);
    }

    /// R1 MEDIUM-1: kabu_station で `is_production_by_venue["kabu_station"] = false`
    /// なら reject。エラー文言は kabu 用 env (`KABU_ALLOW_PROD`) を示す。
    #[test]
    fn test_validate_rejects_kabu_prod_when_cap_false_and_mentions_kabu_env() {
        let (mut form, tmp_file) = make_valid_form();
        form.available_venues = vec!["tachibana".to_string(), "kabu_station".to_string()];
        form.venue = "kabu_station".to_string();
        form.connected_venue = Some("kabu_station".to_string());
        form.prod_mode = true;
        form.is_production_by_venue
            .insert("kabu_station".to_string(), false);
        let result = form.validate();
        assert!(result.is_err(), "kabu prod + cap=false は reject");
        let err = result.unwrap_err();
        assert!(
            err.contains("kabu_station"),
            "error must mention kabu_station venue: {err}"
        );
        assert!(
            err.contains("KABU_ALLOW_PROD"),
            "error must reference KABU_ALLOW_PROD env (kabu prod gate): {err}"
        );
        assert!(
            err.contains("再起動"),
            "error must mention engine restart: {err}"
        );
        let _ = std::fs::remove_file(&tmp_file);
    }

    /// R1 MEDIUM-1: tachibana 経路は HashMap (`is_production_by_venue`) 経由でも
    /// 既存挙動を維持する（regression pin）。`is_production_by_venue["tachibana"] = true`
    /// なら Submit 可能。
    #[test]
    fn test_validate_allows_prod_mode_for_tachibana_via_hashmap() {
        let (mut form, tmp_file) = make_valid_form();
        form.available_venues = vec!["tachibana".to_string()];
        form.venue = "tachibana".to_string();
        form.prod_mode = true;
        form.is_production_by_venue
            .insert("tachibana".to_string(), true);
        let result = form.validate();
        assert!(
            result.is_ok(),
            "tachibana + prod_mode=true + is_production cap=true must validate Ok: {result:?}"
        );
        let _ = std::fs::remove_file(&tmp_file);
    }

    /// R1 MEDIUM-1: tachibana 経路で cap=false（unset）なら従来通り reject。
    /// 文言は TACHIBANA_ALLOW_PROD を示す。
    #[test]
    fn test_validate_rejects_tachibana_prod_when_cap_false_via_hashmap() {
        let (mut form, tmp_file) = make_valid_form();
        form.available_venues = vec!["tachibana".to_string()];
        form.venue = "tachibana".to_string();
        form.prod_mode = true;
        form.is_production_by_venue
            .insert("tachibana".to_string(), false);
        let result = form.validate();
        assert!(result.is_err());
        let err = result.unwrap_err();
        assert!(
            err.contains("TACHIBANA_ALLOW_PROD"),
            "tachibana 用 env が文言に出るべき: {err}"
        );
        let _ = std::fs::remove_file(&tmp_file);
    }

    /// R1 MEDIUM-1: HashMap に対応エントリが無い venue (= cap 未受信 / 旧 server) は
    /// 安全側 false にフォールバックして reject する。
    #[test]
    fn test_validate_rejects_prod_when_venue_missing_from_hashmap() {
        let (mut form, tmp_file) = make_valid_form();
        form.available_venues = vec!["kabu_station".to_string()];
        form.venue = "kabu_station".to_string();
        form.connected_venue = Some("kabu_station".to_string());
        form.prod_mode = true;
        // is_production_by_venue は空 → 未含有 = 安全側 false
        let result = form.validate();
        assert!(
            result.is_err(),
            "cap 未受信 venue は安全側 false にフォールバックして reject すべき"
        );
        let _ = std::fs::remove_file(&tmp_file);
    }
}
