use iced::Task;

use crate::LiveStrategyState;
use crate::Message;
use crate::messages::{DashboardMsg, EngineMsg, ReplayMsg, VenueMsg, WindowMsg};
use crate::modal;
use crate::screen;
use crate::widget::toast::Toast;

impl crate::Flowsurface {
    pub(crate) fn handle_replay(&mut self, msg: ReplayMsg) -> Task<Message> {
        match msg {
            ReplayMsg::BuyingPower {
                cash,
                buying_power,
                equity,
                ts_event_ms,
            } => {
                let main_window = self.main_window.id;
                self.active_dashboard_mut().distribute_replay_buying_power(
                    main_window,
                    cash,
                    buying_power,
                    equity,
                    ts_event_ms,
                );
            }
            // N3: live strategy 買付余力スナップショット — dashboard に配布
            ReplayMsg::LiveBuyingPower {
                cash,
                equity,
                ts_event_ms,
            } => {
                self.menu_bar.live_bar.current_time = Some(crate::format_live_time(ts_event_ms));
                let main_window = self.main_window.id;
                self.active_dashboard_mut().distribute_live_buying_power(
                    main_window,
                    cash,
                    equity,
                    ts_event_ms,
                );
            }
            // Phase U3: IpcError → route to BuyingPower / OrderList panel if request_id matches
            ReplayMsg::StrategyFilePicked(path) => {
                self.replay_strategy_file = path;
                return Task::none();
            }
            // N4.4: user dismissed the strategy load error banner.
            ReplayMsg::DismissStrategyLoadError => {
                self.strategy_load_error = None;
                return Task::none();
            }
            // schema 3.12: replay 用ペイン自動生成。GUI 内フォーム経由でも
            // helper attach mode でも同じ経路を通す。
            // Message::ReplayDataLoaded { == post-refactor arm below
            ReplayMsg::DataLoaded {
                instrument_id,
                instrument_ids,
                granularity,
                session_epoch,
                ..
            } => {
                // schema 3.14: 新 epoch を観測したら旧ペインを全閉じして registry を
                // リセット（リプレイファイル切替時の stale pane 残存バグ対応）。
                // 比較は `!=` — engine 再起動で epoch が 0 に巻き戻ったときは切断
                // ハンドラで `last_replay_session_epoch = None` にリセットされる。
                //
                // 読み (has_registered_panes) と書き (drain + close) を 1 回の
                // mutable 借用にまとめ、active_dashboard() / active_dashboard_mut()
                // の二段呼び出しによる「読み先と書き先がずれるリスク」を回避する
                // （review-fix R1 MEDIUM iced-1）。
                let prev = self.last_replay_session_epoch;
                let dashboard = self.active_dashboard_mut();
                let session_changed = match (prev, session_epoch) {
                    (Some(prev), Some(curr)) => prev != curr,
                    // 初回 None → Some(N): registry が空でない場合のみ発動
                    // （helper attach 経路で先に何かが登録されている異常系のガード）。
                    (None, Some(_)) => dashboard.replay_pane_registry.has_registered_panes(),
                    // 旧 engine（minor<14）からの永続 None や None → None は無視。
                    _ => false,
                };
                if session_changed {
                    let stale = dashboard.replay_pane_registry.drain_all_registered();
                    let n = stale.len();
                    // pane_grid::State::close は未知の pane id に対して None を
                    // 返す (no-op)。drain で回収した id はこの dashboard に紐づく
                    // ものだけなので安全。
                    for pane in stale {
                        dashboard.panes.close(pane);
                    }
                    log::info!(
                        "ReplayDataLoaded: session_epoch={session_epoch:?} \
                         — closed {n} stale panes from previous session"
                    );
                }
                if session_epoch.is_some() {
                    self.last_replay_session_epoch = session_epoch;
                }

                // schema 3.13: instrument_ids（複数）を優先。なければ instrument_id 単体に後方互換。
                let ids: Vec<String> = instrument_ids
                    .filter(|v| !v.is_empty())
                    .unwrap_or_else(|| instrument_id.into_iter().collect());

                if ids.is_empty() {
                    log::error!(
                        "ReplayDataLoaded: instrument_id(s) missing — auto pane generation \
                         skipped. (Old engine schema_minor<13 or schema bug.)"
                    );
                    return Task::none();
                }
                let timeframe = match granularity {
                    Some(engine_client::dto::ReplayGranularity::Daily) => {
                        Some(exchange::Timeframe::D1)
                    }
                    Some(engine_client::dto::ReplayGranularity::Minute) => {
                        Some(exchange::Timeframe::M1)
                    }
                    // Trade tick：bar 無しなので CandlestickChart はスキップ。
                    Some(engine_client::dto::ReplayGranularity::Trade) | None => None,
                };
                // Sync bar to first instrument so the read-only label reflects what's loaded.
                self.menu_bar.replay_bar.instrument_id = ids[0].clone();
                // Clear stale time display from previous replay session (Issue 3).
                self.menu_bar.replay_bar.current_day = None;
                let main_window_id = self.main_window.id;
                self.replay_running = true;
                log::info!(
                    "ReplayDataLoaded: auto-generating replay panes for {} instrument(s) \
                     timeframe={timeframe:?}",
                    ids.len()
                );
                let mut tasks = Vec::with_capacity(ids.len());
                for id in &ids {
                    let task = self
                        .active_dashboard_mut()
                        .auto_generate_replay_panes(main_window_id, id, timeframe)
                        .map(move |msg| {
                            Message::Dashboard(DashboardMsg::Layout {
                                layout_id: None,
                                event: msg,
                            })
                        });
                    tasks.push(task);
                }
                return Task::batch(tasks);
            }
            // Replay engine finished → auto-refresh order list from Python's in-memory fills.
            ReplayMsg::Finished => {
                self.replay_running = false;
                self.replay_paused = false;
                self.menu_bar.replay_bar.current_day = None;
                self.menu_bar.replay_bar.replay_has_history = false;
                if let Some(conn) = self.engine_connection.as_ref().cloned() {
                    return Task::perform(
                        async move {
                            conn.send(engine_client::dto::Command::GetOrderList {
                                request_id: uuid::Uuid::new_v4().to_string(),
                                venue: "replay".to_string(),
                                filter: engine_client::dto::OrderListFilter {
                                    status: None,
                                    instrument_id: None,
                                    date: None,
                                },
                            })
                            .await
                            .map_err(|e| e.to_string())
                        },
                        |res| match res {
                            Ok(()) => Message::Venue(VenueMsg::OrderToast(Toast::info(
                                "注文一覧を更新しました".to_string(),
                            ))),
                            Err(e) => {
                                log::error!("[ReplayFinished] GetOrderList failed: {e}");
                                Message::Venue(VenueMsg::OrderToast(Toast::error(format!(
                                    "注文一覧の取得に失敗: {e}"
                                ))))
                            }
                        },
                    );
                }
                return Task::none();
            }
            // schema 3.15: replay bar current-day display update.
            ReplayMsg::DateChanged(date) => {
                self.menu_bar.replay_bar.current_day = Some(date);
                return Task::none();
            }
            // schema 3.22: per-tick replay time signal — update current_day with sub-day
            // precision. TimeUpdated arrives after DateChanged for the same tick, so it
            // overwrites the date-only string with a formatted timestamp.
            ReplayMsg::TimeUpdated { timestamp_ms } => {
                let formatted = crate::format_replay_time(
                    timestamp_ms,
                    self.menu_bar.replay_bar.granularity.as_ref(),
                );
                self.menu_bar.replay_bar.current_day = Some(formatted);
                return Task::none();
            }
            // schema 3.16: replay history changed — update ⏮ button enable state.
            ReplayMsg::HistoryChanged { has_history } => {
                self.menu_bar.replay_bar.replay_has_history = has_history;
                return Task::none();
            }
            // R2-H1: RestoreSnapshot received — flush chart overlay markers + reset day display.
            // ExecutionMarker / StrategySignal は戦略状態に依存するため、巻き戻し時に必ずクリア
            // する。OHLC kline 自体は実市場データなので保持する（次の StepReplay で再進入する
            // 際は同じ ts まで再生される）。
            ReplayMsg::RestoreSnapshotPending {
                step_index,
                ts_event_ms,
            } => {
                log::debug!(
                    "RestoreSnapshotPending: step_index={step_index} ts_event_ms={ts_event_ms}"
                );
                let main_window_id = self.main_window.id;
                self.active_dashboard_mut()
                    .clear_chart_overlays(main_window_id);
                self.menu_bar.replay_bar.current_day = None;
                return Task::none();
            }
            // ── Widget menu bar (all platforms) ───────────────────────────
            ReplayMsg::NativeOpenStrategyPicked(picked) => {
                let Some(path) = picked else {
                    return Task::none();
                };
                // N4-live: live モードでは live_strategy_form_modal を設定する。
                if crate::app_mode() == engine_client::dto::AppMode::Live {
                    if self.live_strategy.is_running() {
                        self.notifications
                            .push(Toast::warn("Live 戦略がすでに実行中です".to_string()));
                        return Task::none();
                    }
                    let form = modal::live_strategy_form::LiveStrategyFormModal {
                        strategy_file: path,
                        ..Default::default()
                    };
                    self.live_strategy_form_modal = Some(form);
                    return Task::none();
                }
                // replay mode: LoadStrategyScenario 経路へ続く。
                let Some(conn) = self.engine_connection.as_ref().cloned() else {
                    self.notifications.push(Toast::error(
                        "engine に接続されていないため SCENARIO を読み込めません".to_string(),
                    ));
                    return Task::none();
                };
                let path_str = path.to_string_lossy().into_owned();
                // request_id を同期的に生成してステートに記録することで、
                // 連続 open 時に古い応答を StrategyScenarioLoadedEvent で捨てられる。
                let request_id = uuid::Uuid::new_v4().to_string();
                self.pending_scenario_request_id = Some(request_id.clone());
                return Task::perform(
                    async move {
                        conn.send(engine_client::dto::Command::LoadStrategyScenario {
                            request_id,
                            path: path_str,
                        })
                        .await
                        .map_err(|e| e.to_string())
                    },
                    |res| match res {
                        Ok(()) => Message::Engine(EngineMsg::Noop),
                        Err(err) => Message::Venue(VenueMsg::OrderToast(Toast::error(format!(
                            "戦略ファイルの読み込み要求に失敗しました: {err}"
                        )))),
                    },
                );
            }
            // F6a: SCENARIO 抽出成功 → ReplayFormModal を prefill。modal が
            // 開いていなければ新規生成する（GUI で `Open` 直後はまだ modal が
            // 出ていない正規ルート）。
            // CURRENT_PATH はレイアウト JSON の保存先のみを示す。戦略 .py を
            // セットすると live 切替後の Ctrl+S が .py を JSON で上書きするため
            // ここでは更新しない。
            ReplayMsg::ScenarioLoaded {
                request_id,
                path,
                scenario,
                resolved_instruments,
            } => {
                // 連続して別ファイルを開いた場合、古い応答を無視する。
                if self.pending_scenario_request_id.as_deref() != Some(request_id.as_str()) {
                    return Task::none();
                }
                self.pending_scenario_request_id = None;
                let form = self
                    .replay_form_modal
                    .get_or_insert_with(modal::replay_form::ReplayFormModal::default);
                match scenario {
                    Some(value) => {
                        form.prefill_from_scenario(
                            path.clone(),
                            &value,
                            resolved_instruments.as_deref(),
                        );
                        self.menu_bar.replay_bar.prefill_from_scenario(
                            path,
                            &value,
                            resolved_instruments.as_deref(),
                        );
                    }
                    None => {
                        form.set_strategy_file_only(path.clone());
                        self.menu_bar.replay_bar.set_strategy_file_only(path);
                    }
                }
                return Task::none();
            }
            // F6a: SCENARIO 抽出失敗 → トースト表示。`current_path` は更新しない。
            // 成功パスと対称に request_id で突き合わせ、古い失敗を捨てる。
            ReplayMsg::ScenarioLoadFailed {
                request_id,
                path,
                reason,
            } => {
                if self.pending_scenario_request_id.as_deref() != Some(request_id.as_str()) {
                    return Task::none();
                }
                self.pending_scenario_request_id = None;
                let name = path
                    .file_name()
                    .map(|n| n.to_string_lossy().into_owned())
                    .unwrap_or_else(|| path.to_string_lossy().into_owned());
                self.notifications.push(Toast::error(format!(
                    "{name} を読み込めませんでした: {reason}"
                )));
                return Task::none();
            }
            // Phase 8.1c: Replay 起動フォームを開く
            ReplayMsg::ShowDialog => {
                self.replay_form_modal = Some(modal::replay_form::ReplayFormModal::default());
                return Task::none();
            }
            // Phase 8.1c: Replay フォーム内部メッセージの処理
            ReplayMsg::FormMsg(modal::replay_form::Message::PickStrategyFile) => {
                return Task::perform(
                    async {
                        rfd::AsyncFileDialog::new()
                            .add_filter("Python", &["py"])
                            .set_title("戦略ファイルを選択")
                            .pick_file()
                            .await
                            .map(|h| h.path().to_owned())
                    },
                    |p| Message::Replay(ReplayMsg::NativeOpenStrategyPicked(p)),
                );
            }
            ReplayMsg::FormMsg(msg) => {
                if let Some(form) = self.replay_form_modal.as_mut() {
                    match form.update(msg) {
                        Some(modal::replay_form::Action::Cancel) => {
                            self.replay_form_modal = None;
                        }
                        Some(modal::replay_form::Action::Submit {
                            instrument_ids,
                            start_date,
                            end_date,
                            granularity,
                            strategy_file,
                            initial_cash,
                        }) => {
                            self.replay_form_modal = None;
                            // Write back validated form values to ReplayBarState so the
                            // menu bar displays the active replay settings.
                            // Display only first instrument_id to avoid UI layout breaking with long lists.
                            self.menu_bar.replay_bar.instrument_id = instrument_ids[0].clone();
                            self.menu_bar.replay_bar.start_date = start_date.clone();
                            self.menu_bar.replay_bar.end_date = end_date.clone();
                            self.menu_bar.replay_bar.granularity = Some(granularity.clone());
                            self.menu_bar.replay_bar.strategy_file = Some(strategy_file.clone());
                            self.menu_bar.replay_bar.initial_cash = initial_cash.to_string();
                            if let Some(conn) = self.engine_connection.as_ref().cloned() {
                                let strategy_file_str =
                                    strategy_file.to_string_lossy().into_owned();
                                let gran_dto = granularity.to_dto();
                                let first_id = instrument_ids[0].clone();
                                return Task::perform(
                                    async move {
                                        let load_req_id = uuid::Uuid::new_v4().to_string();
                                        conn.send(engine_client::dto::Command::LoadReplayData {
                                            request_id: load_req_id,
                                            instrument_id: first_id.clone(),
                                            instrument_ids: Some(instrument_ids.clone()),
                                            start_date: start_date.clone(),
                                            end_date: end_date.clone(),
                                            granularity: gran_dto.clone(),
                                        })
                                        .await
                                        .map_err(|e| e.to_string())?;
                                        let start_req_id = uuid::Uuid::new_v4().to_string();
                                        conn.send(engine_client::dto::Command::StartEngine {
                                            request_id: start_req_id,
                                            engine: engine_client::dto::EngineKind::Backtest,
                                            strategy_id: "user-strategy".to_string(),
                                            config: engine_client::dto::EngineStartConfig {
                                                instrument_id: first_id,
                                                instrument_ids: Some(instrument_ids),
                                                start_date: Some(start_date),
                                                end_date: Some(end_date),
                                                initial_cash: Some(initial_cash.to_string()),
                                                granularity: Some(gran_dto),
                                                strategy_file: Some(strategy_file_str),
                                                strategy_init_kwargs: None,
                                                max_qty: None,
                                                max_notional_jpy: None,
                                            },
                                        })
                                        .await
                                        .map_err(|e| e.to_string())?;
                                        Ok::<(), String>(())
                                    },
                                    |res| match res {
                                        Ok(()) => Message::Venue(VenueMsg::OrderToast(
                                            Toast::info("Replay を開始しました".to_string()),
                                        )),
                                        Err(e) => Message::Venue(VenueMsg::OrderToast(
                                            Toast::error(format!("Replay 起動失敗: {e}")),
                                        )),
                                    },
                                );
                            }
                        }
                        Some(modal::replay_form::Action::PickStrategyFile) => {
                            // PickStrategyFile は上の専用アームで処理される
                        }
                        None => {}
                    }
                }
                return Task::none();
            }
            // N4-live: EngineStarted (live) → set running state and update bar
            ReplayMsg::LiveStarted {
                strategy_id,
                ts_event_ms,
            } => {
                self.live_strategy = LiveStrategyState::Running { strategy_id };
                self.menu_bar.live_bar.current_time = Some(crate::format_live_time(ts_event_ms));
                self.menu_bar.live_bar.live_paused = false;
                // strategy_file_stem は Submit 時に既設定済み（通常フロー）。
                // 再接続シナリオでは None のままバーに "--" が表示されるが、
                // 再接続サポートは Phase 1-11 の対象外。
                return Task::none();
            }
            // N4-live: EngineStopped (live) → clear running state
            ReplayMsg::LiveStopped { strategy_id } => {
                if let LiveStrategyState::Running {
                    strategy_id: running_id,
                } = &self.live_strategy
                {
                    if running_id == &strategy_id {
                        self.live_strategy = LiveStrategyState::Idle;
                        self.menu_bar.live_bar = crate::menu_bar_state::LiveBarState::default();
                        let main_window = self.main_window.id;
                        self.active_dashboard_mut()
                            .clear_live_strategy_portfolio(main_window);
                    } else {
                        log::warn!(
                            "LiveEngineStoppedEvent: strategy_id mismatch (got={strategy_id}, \
                             running={running_id}); ignoring"
                        );
                    }
                }
                return Task::none();
            }
            // N4-live: ■ ボタンから StopEngine を送信する
            ReplayMsg::StopLiveStrategy => {
                let LiveStrategyState::Running { strategy_id } = &self.live_strategy else {
                    return Task::none();
                };
                let strategy_id = strategy_id.clone();
                let Some(conn) = self.engine_connection.as_ref().cloned() else {
                    return Task::none();
                };
                return Task::perform(
                    async move {
                        conn.send(engine_client::dto::Command::StopEngine {
                            request_id: uuid::Uuid::new_v4().to_string(),
                            strategy_id,
                        })
                        .await
                        .map_err(|e| e.to_string())
                    },
                    |res| match res {
                        Ok(()) => Message::Engine(EngineMsg::Noop),
                        Err(e) => Message::Venue(VenueMsg::OrderToast(Toast::error(format!(
                            "Live 停止失敗: {e}"
                        )))),
                    },
                );
            }
            // N4-live: StartEngine IPC 送信失敗 → strategy_file_stem をクリアしてトースト
            ReplayMsg::LiveStartFailed(e) => {
                self.menu_bar.live_bar.strategy_file_stem = None;
                return Task::done(Message::Venue(VenueMsg::OrderToast(Toast::error(format!(
                    "Live 起動失敗: {e}"
                )))));
            }
            // N4-live: live strategy フォーム modal
            ReplayMsg::LiveStrategyFormMsg(msg) => {
                if let Some(form) = &mut self.live_strategy_form_modal {
                    match form.update(msg) {
                        Some(modal::live_strategy_form::Action::Submit {
                            instrument_id,
                            strategy_file,
                            max_qty,
                            max_notional_jpy,
                        }) => {
                            let Some(conn) = self.engine_connection.as_ref().cloned() else {
                                // フォームを閉じずにエラーを通知する
                                return Task::done(Message::Venue(VenueMsg::OrderToast(
                                    Toast::error("engine に接続されていません".to_string()),
                                )));
                            };
                            let session_id = uuid::Uuid::new_v4().to_string();
                            self.menu_bar.live_bar.strategy_file_stem = strategy_file
                                .file_stem()
                                .map(|s| s.to_string_lossy().into_owned());
                            self.live_strategy_form_modal = None;
                            let strategy_file_str = strategy_file.to_string_lossy().into_owned();
                            return Task::perform(
                                async move {
                                    conn.send(engine_client::dto::Command::StartEngine {
                                        request_id: uuid::Uuid::new_v4().to_string(),
                                        engine: engine_client::dto::EngineKind::Live,
                                        strategy_id: session_id,
                                        config: engine_client::dto::EngineStartConfig {
                                            instrument_id,
                                            instrument_ids: None,
                                            strategy_file: Some(strategy_file_str),
                                            strategy_init_kwargs: None,
                                            max_qty: Some(max_qty),
                                            max_notional_jpy: Some(max_notional_jpy),
                                            start_date: None,
                                            end_date: None,
                                            initial_cash: None,
                                            granularity: None,
                                        },
                                    })
                                    .await
                                    .map_err(|e| e.to_string())
                                },
                                |res| match res {
                                    Ok(()) => Message::Engine(EngineMsg::Noop),
                                    Err(e) => Message::Replay(ReplayMsg::LiveStartFailed(e)),
                                },
                            );
                        }
                        Some(modal::live_strategy_form::Action::Cancel) => {
                            self.live_strategy_form_modal = None;
                        }
                        None => {}
                    }
                }
                return Task::none();
            }
            ReplayMsg::StopReplayOnly => {
                if self.replay_stop_only_pending || self.mode_switch_state.is_some() {
                    return Task::none();
                }
                if !self.replay_running {
                    return Task::none();
                }
                let Some(conn) = self.engine_connection.clone() else {
                    let dialog = screen::ConfirmDialog::new(
                        "リプレイ停止に失敗しました。\nエンジンとの接続が切れています。"
                            .to_string(),
                        Box::new(Message::Window(WindowMsg::ToggleDialogModal(None))),
                    )
                    .with_confirm_btn_text("閉じる".to_string());
                    self.confirm_dialog = Some(dialog);
                    return Task::none();
                };
                self.replay_stop_only_pending = true;
                let request_id = uuid::Uuid::new_v4().to_string();
                let send_task = Task::perform(
                    async move {
                        conn.send(engine_client::dto::Command::StopReplay { request_id })
                            .await
                    },
                    |result| match result {
                        Ok(()) => Message::Engine(EngineMsg::Noop),
                        Err(_) => Message::Window(WindowMsg::ModeSwitchSendFailed),
                    },
                );
                let timeout_task = Task::perform(
                    async {
                        tokio::time::sleep(std::time::Duration::from_secs(5)).await;
                    },
                    |_| Message::Window(WindowMsg::ModeSwitchStopTimeout),
                );
                return Task::batch([send_task, timeout_task]);
            }
            // F7: ReplayStopped event received — proceed with restart_with_mode
            // (also handles the stop-only flow: drops the pending flag and emits
            // a confirmation toast without restarting the dashboard).
            ReplayMsg::ExecutionMarker {
                side,
                price,
                ts_event_ms,
            } => {
                let price_f32 = price.parse::<f32>().unwrap_or_else(|e| {
                    log::warn!("ExecutionMarker: price parse failed: {e}, raw={price:?}");
                    0.0
                });
                let data = crate::chart::kline::ExecutionMarkerData {
                    side,
                    price_f32,
                    ts_event_ms,
                };
                let main_window = self.main_window.id;
                self.active_dashboard_mut()
                    .distribute_execution_markers(main_window, data);
            }
            // N1.12: StrategySignal → broadcast overlay diamond to all Kline charts
            ReplayMsg::StrategySignal {
                signal_kind,
                price,
                ts_event_ms,
                tag,
            } => {
                let price_f32 = price.and_then(|p| p.parse::<f32>().ok());
                let data = crate::chart::kline::StrategySignalData {
                    signal_kind,
                    price_f32,
                    ts_event_ms,
                    tag,
                };
                let main_window = self.main_window.id;
                self.active_dashboard_mut()
                    .distribute_strategy_signals(main_window, data);
            } // Phase U0: OrderAccepted — reset submitting flag + toast
        }
        Task::none()
    }
}
