use iced::Task;

use crate::LiveStrategyState;
use crate::Message;
use crate::messages::{DashboardMsg, EngineMsg, ReplayMsg, VenueMsg, WindowMsg};
use crate::modal;
use crate::screen;
use crate::widget::toast::Toast;

/// Outcome of the two-step replay submit IPC chain.
///
/// `LoadReplayData` is sent first, then `StartEngine`. The backend treats them
/// independently, so partial failures must be distinguishable to keep UI and
/// backend in sync (review M1):
/// - `BothOk` — backend has new replay loaded AND engine running.
/// - `StartFailed` — backend has new replay loaded; engine NOT running.
///   UI must still adopt the new params (the replay IS loaded) but surface
///   the start error so the user knows the run did not begin.
/// - `LoadFailed` — backend unchanged; UI must NOT adopt new params.
#[derive(Debug, PartialEq, Eq)]
pub(crate) enum ReplaySubmitResult {
    BothOk,
    StartFailed(String),
    LoadFailed(String),
}

/// Form data captured at Submit time, threaded through the async Task.
/// Used by `submit_result_to_message` to build the follow-up Message.
#[derive(Debug, Clone)]
pub(crate) struct ReplayCommitData {
    pub instrument_id: String,
    pub start_date: String,
    pub end_date: String,
    pub granularity: crate::modal::replay_form::Granularity,
    pub strategy_file: std::path::PathBuf,
    pub initial_cash: String,
}

/// Pure mapping from the IPC outcome to the follow-up Message.
/// Extracted so the partial-failure branches can be unit-tested without
/// constructing a full `Flowsurface`.
pub(crate) fn submit_result_to_message(
    result: ReplaySubmitResult,
    commit: ReplayCommitData,
) -> Message {
    match result {
        ReplaySubmitResult::BothOk => Message::Replay(ReplayMsg::CommitReplayBarState {
            instrument_id: commit.instrument_id,
            start_date: commit.start_date,
            end_date: commit.end_date,
            granularity: commit.granularity,
            strategy_file: commit.strategy_file,
            initial_cash: commit.initial_cash,
            start_error: None,
        }),
        ReplaySubmitResult::StartFailed(error) => {
            // M1: Load succeeded → backend has new replay loaded → commit bar
            // state. Carry the start-engine error so the handler shows a toast.
            Message::Replay(ReplayMsg::CommitReplayBarState {
                instrument_id: commit.instrument_id,
                start_date: commit.start_date,
                end_date: commit.end_date,
                granularity: commit.granularity,
                strategy_file: commit.strategy_file,
                initial_cash: commit.initial_cash,
                start_error: Some(error),
            })
        }
        ReplaySubmitResult::LoadFailed(error) => Message::Venue(VenueMsg::OrderToast(
            Toast::error(format!("Replay 起動失敗: {error}")),
        )),
    }
}

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
                    // issue #42 Phase 3 (統一決定 #18): LoadLiveStrategyScenario を送って
                    // LIVE_SCENARIO の prefill を要求する。同期的に request_id を保存し、
                    // 5s 経って応答が来なければ pending を解除して手入力 fallback に戻す。
                    let request_id = uuid::Uuid::new_v4().to_string();
                    // issue #42 R1 MEDIUM-1 (2026-05-11): Ready から各 venue の `is_production`
                    // cap を venue 毎に抜いて modal の prod_mode disable 判定に渡す。
                    // 旧版は tachibana 専用の単一フィールドだったため、kabu_station prod が
                    // form 側で hardcode reject されていた（server.py 側では既に
                    // `kabu_station.is_production` を expose 済だった silent UX failure）。
                    //
                    // engine 未接続のときは空 HashMap で構築 → validate() は安全側 false
                    // にフォールバック（= demo 扱い）。env 変更には engine 再起動が必要なため、
                    // modal 表示中の動的更新は不要（統一決定 #14）。
                    let is_production_by_venue: std::collections::HashMap<String, bool> = self
                        .engine_connection
                        .as_ref()
                        .map(|conn| {
                            let caps = conn.capabilities();
                            ["tachibana", "kabu_station"]
                                .iter()
                                .map(|v| {
                                    (
                                        (*v).to_string(),
                                        engine_client::capabilities::is_production(
                                            caps.as_ref(),
                                            v,
                                        ),
                                    )
                                })
                                .collect()
                        })
                        .unwrap_or_default();
                    // issue #42 R1 MEDIUM-1: dropdown 用の available_venues を
                    // `supports_live_strategy=true` で filter する。engine 未接続なら
                    // 空 Vec で構築 (form 側の validate() は空のとき venue 検査を skip し、
                    // server.py の `_handle_start_engine` の `_connected_venue` 判定に委ねる)。
                    let available_venues: Vec<String> = self
                        .engine_connection
                        .as_ref()
                        .map(|conn| {
                            let caps = conn.capabilities();
                            ["tachibana", "kabu_station"]
                                .iter()
                                .filter(|v| {
                                    engine_client::capabilities::supports_live_strategy(
                                        caps.as_ref(),
                                        v,
                                    )
                                })
                                .map(|v| v.to_string())
                                .collect()
                        })
                        .unwrap_or_default();
                    // issue #42 R1 MEDIUM-1: 現在 login 済 venue を form に渡して
                    // 戦略 venue との不一致を validate() で検出可能にする。両方 Ready
                    // のときは tachibana 優先（typical UX で最初に login する方）。
                    let connected_venue = if self.tachibana_state.is_ready() {
                        Some("tachibana".to_string())
                    } else if self.kabu_state.is_ready() {
                        Some("kabu_station".to_string())
                    } else {
                        None
                    };
                    // issue #42 R1 MEDIUM-1: dropdown の初期値を `connected_venue`
                    // にしておく (LiveStrategyScenarioLoaded.venue が来たら上書きする)。
                    // connected_venue が None なら空文字 (= default) のままにする。
                    let initial_venue = connected_venue.clone().unwrap_or_default();
                    let form = modal::live_strategy_form::LiveStrategyFormModal {
                        strategy_file: path.clone(),
                        pending_scenario_request_id: Some(request_id.clone()),
                        is_production_by_venue,
                        available_venues,
                        connected_venue,
                        venue: initial_venue,
                        ..Default::default()
                    };
                    self.live_strategy_form_modal = Some(form);
                    let Some(conn) = self.engine_connection.as_ref().cloned() else {
                        // engine 未接続でも form は表示する（手入力で続行可能）。
                        if let Some(f) = self.live_strategy_form_modal.as_mut() {
                            f.release_scenario_pending();
                        }
                        return Task::none();
                    };
                    let path_str = path.to_string_lossy().into_owned();
                    let request_id_for_send = request_id.clone();
                    let send_task = Task::perform(
                        async move {
                            conn.send(engine_client::dto::Command::LoadLiveStrategyScenario {
                                request_id: request_id_for_send,
                                strategy_path: path_str,
                            })
                            .await
                            .map_err(|e| e.to_string())
                        },
                        |res| match res {
                            Ok(()) => Message::Engine(EngineMsg::Noop),
                            Err(err) => Message::Venue(VenueMsg::OrderToast(Toast::error(
                                format!("LoadLiveStrategyScenario 送信失敗: {err}"),
                            ))),
                        },
                    );
                    let request_id_for_timeout = request_id;
                    let timeout_task = Task::perform(
                        async move {
                            tokio::time::sleep(std::time::Duration::from_secs(
                                crate::LIVE_SCENARIO_FALLBACK_TIMEOUT_SECS,
                            ))
                            .await;
                            request_id_for_timeout
                        },
                        |req_id| {
                            Message::Replay(ReplayMsg::LiveStrategyScenarioFallback {
                                request_id: req_id,
                            })
                        },
                    );
                    return Task::batch([send_task, timeout_task]);
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
                            // M2: Check connection BEFORE clearing the modal so a missing
                            // connection does not silently discard user input. Keep the
                            // modal open and surface a toast so the user can retry.
                            let Some(conn) = self.engine_connection.as_ref().cloned() else {
                                return Task::done(Message::Venue(VenueMsg::OrderToast(
                                    Toast::error(
                                        "エンジン未接続のため Replay を開始できません。再接続後にやり直してください。"
                                            .to_string(),
                                    ),
                                )));
                            };
                            self.replay_form_modal = None;
                            // H-2: DO NOT update menu_bar.replay_bar here. Only commit state
                            // after IPC succeeds, in the Task callback below.
                            let strategy_file_str = strategy_file.to_string_lossy().into_owned();
                            let gran_dto = granularity.to_dto();
                            let first_id = instrument_ids[0].clone();
                            // Capture form data for the follow-up Message (BothOk / StartFailed
                            // both commit; LoadFailed leaves the bar unchanged).
                            let commit = ReplayCommitData {
                                instrument_id: first_id.clone(),
                                start_date: start_date.clone(),
                                end_date: end_date.clone(),
                                granularity: granularity.clone(),
                                strategy_file: strategy_file.clone(),
                                initial_cash: initial_cash.to_string(),
                            };
                            return Task::perform(
                                async move {
                                    // M1: differentiate Load failure (backend unchanged) vs
                                    // Start failure (backend has new replay loaded). The two
                                    // cases require different UI follow-ups.
                                    let load_req_id = uuid::Uuid::new_v4().to_string();
                                    if let Err(e) = conn
                                        .send(engine_client::dto::Command::LoadReplayData {
                                            request_id: load_req_id,
                                            instrument_id: first_id.clone(),
                                            instrument_ids: Some(instrument_ids.clone()),
                                            start_date: start_date.clone(),
                                            end_date: end_date.clone(),
                                            granularity: gran_dto.clone(),
                                        })
                                        .await
                                    {
                                        return ReplaySubmitResult::LoadFailed(e.to_string());
                                    }
                                    let start_req_id = uuid::Uuid::new_v4().to_string();
                                    if let Err(e) = conn
                                        .send(engine_client::dto::Command::StartEngine {
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
                                    {
                                        return ReplaySubmitResult::StartFailed(e.to_string());
                                    }
                                    ReplaySubmitResult::BothOk
                                },
                                move |res| submit_result_to_message(res, commit.clone()),
                            );
                        }
                        Some(modal::replay_form::Action::PickStrategyFile) => {
                            // PickStrategyFile は上の専用アームで処理される
                        }
                        None => {}
                    }
                }
                return Task::none();
            }
            // N4-live: EngineStarted (live) → bar 表示更新と warm_up timeout タイマー起動。
            // issue #42 Phase 3 (統一決定 #4): Running 遷移は `LiveStrategyReady` 受信時に
            // 行う（`EngineStarted` には instrument_id / venue が無いため）。ここでは
            // pending_strategy_id 設定と 60s timeout タイマーの起動のみを担当する。
            ReplayMsg::LiveStarted {
                strategy_id,
                ts_event_ms,
            } => {
                self.live_strategy_pending_strategy_id = Some(strategy_id.clone());
                self.menu_bar.live_bar.current_time = Some(crate::format_live_time(ts_event_ms));
                self.menu_bar.live_bar.live_paused = false;
                self.live_warmup_timeout_banner = None;
                // 統一決定 #17: EngineStarted 受信後 60s 以内に LiveStrategyReady が
                // 来なければ "warm_up timeout" banner を出す。LiveStrategyWarmingUp 受信
                // ごとに `live_warmup_timeout_token` を bump してタイマーをリセットする。
                let token = self.live_warmup_timeout_token.wrapping_add(1);
                self.live_warmup_timeout_token = token;
                let sid = strategy_id;
                return Task::perform(
                    async move {
                        tokio::time::sleep(std::time::Duration::from_secs(
                            crate::LIVE_WARMUP_TIMEOUT_SECS,
                        ))
                        .await;
                        (sid, token)
                    },
                    |(sid, token)| {
                        Message::Replay(ReplayMsg::LiveWarmupTimeoutFired {
                            strategy_id: sid,
                            token,
                        })
                    },
                );
            }
            // issue #42 Phase 3: LiveStrategyReady 受信 → Running 遷移 + 4 ペイン自動生成。
            // 冪等: 同じ三つ組で 2 度受信しても auto_generate_live_panes が no-op で済む。
            ReplayMsg::LiveStrategyReady {
                strategy_id,
                instrument_id,
                venue,
                ..
            } => {
                // R2-B H7: 空文字列 sentinel を防ぐため `try_running` factory を経由する。
                // 失敗時は log warn を残して遷移しない（auto_generate_live_panes 呼出も skip）。
                let new_state = match LiveStrategyState::try_running(
                    strategy_id.clone(),
                    instrument_id.clone(),
                    venue.clone(),
                ) {
                    Ok(s) => s,
                    Err(reason) => {
                        log::warn!(
                            "LiveStrategyReady ignored — invalid triple ({reason}): \
                             strategy_id={strategy_id:?}, instrument_id={instrument_id:?}, venue={venue:?}"
                        );
                        return Task::none();
                    }
                };
                self.live_strategy = new_state;
                self.live_warmup_timeout_banner = None;
                // R2-B H1: pending_strategy_id をここでもクリアする。LiveStopped arm のみで
                // クリアする実装だと、reconnect で LiveStrategyReady 受信 → Running 遷移したあと
                // 別 strategy が再 Submit された際に 旧 pending_strategy_id が残ってしまい
                // LiveWarmupTimeoutFired の照合 (`pending_strategy_id == strategy_id` 比較) を
                // 誤発火させる可能性がある。
                self.live_strategy_pending_strategy_id = None;
                // タイマートークンを bump して未到達タイマーを無効化する。
                self.live_warmup_timeout_token = self.live_warmup_timeout_token.wrapping_add(1);
                self.live_warmup_warming_message = None;
                // R4 R3-RUST-2: Ready 受信で warming-up progress も None に戻す。
                self.live_warmup_warming_progress = None;
                let main_window_id = self.main_window.id;
                self.active_dashboard_mut().auto_generate_live_panes(
                    main_window_id,
                    &strategy_id,
                    &instrument_id,
                    &venue,
                );
                return Task::none();
            }
            // issue #42 Phase 3: warm_up 進捗 banner 更新 + timeout カウンタリセット。
            ReplayMsg::LiveWarmingUp {
                strategy_id,
                progress,
                message,
            } => {
                // R2-B H3: 別 strategy 用の warm_up 進捗を誤って banner / timer に
                // 反映しないよう strategy_id 照合を加える。pending（EngineStarted 後
                // Ready 前）または Running（Ready 後 / 後追い ticker）のいずれかに
                // 該当する場合のみ受け取る。それ以外は古い start のものとして無視。
                let matches_pending =
                    self.live_strategy_pending_strategy_id.as_deref() == Some(strategy_id.as_str());
                let matches_running = matches!(
                    &self.live_strategy,
                    LiveStrategyState::Running { strategy_id: s, .. } if s == &strategy_id
                );
                if !matches_pending && !matches_running {
                    log::debug!(
                        "LiveWarmingUp ignored — strategy_id mismatch \
                         (got={strategy_id}, pending={:?}, running={:?})",
                        self.live_strategy_pending_strategy_id,
                        match &self.live_strategy {
                            LiveStrategyState::Running { strategy_id: s, .. } => Some(s.clone()),
                            LiveStrategyState::Idle => None,
                        }
                    );
                    return Task::none();
                }
                self.live_warmup_warming_message = Some(message);
                // R4 R3-RUST-2: progress も保持してバナーに % 形式で表示する。
                self.live_warmup_warming_progress = Some(progress);
                // 既存タイマーを無効化して 60s タイマーを再起動する（カウンタリセット）。
                let token = self.live_warmup_timeout_token.wrapping_add(1);
                self.live_warmup_timeout_token = token;
                return Task::perform(
                    async move {
                        tokio::time::sleep(std::time::Duration::from_secs(
                            crate::LIVE_WARMUP_TIMEOUT_SECS,
                        ))
                        .await;
                        (strategy_id, token)
                    },
                    |(sid, token)| {
                        Message::Replay(ReplayMsg::LiveWarmupTimeoutFired {
                            strategy_id: sid,
                            token,
                        })
                    },
                );
            }
            // issue #42 Phase 3: warm_up 60s timeout 発火 — token が古ければ無視。
            ReplayMsg::LiveWarmupTimeoutFired { strategy_id, token } => {
                if token != self.live_warmup_timeout_token {
                    // LiveStrategyWarmingUp / LiveStrategyReady 受信で token が bump 済 → 無視。
                    return Task::none();
                }
                if matches!(self.live_strategy, LiveStrategyState::Running { .. }) {
                    // 既に Running に遷移済（Ready が来た）→ 表示しない。
                    return Task::none();
                }
                if self.live_strategy_pending_strategy_id.as_deref() != Some(strategy_id.as_str()) {
                    // 別 strategy_id 用のタイマー（古い start のもの）→ 無視。
                    return Task::none();
                }
                // R6 R5-SILENT-1: timeout 発火時は warming banner / progress を消してから
                // timeout banner を立てる。両方残ると view() で「Warming up...」と
                // 「ライブ戦略起動失敗」が同時に出る silent UX failure になる。
                self.live_warmup_warming_message = None;
                self.live_warmup_warming_progress = None;
                self.live_warmup_timeout_banner =
                    Some("ライブ戦略起動失敗（warm_up timeout）".to_string());
                return Task::none();
            }
            // issue #42 Phase 3: 「再試行」ボタン押下 — banner を消す（再 Submit は modal 経由）。
            ReplayMsg::DismissLiveWarmupTimeoutBanner => {
                self.live_warmup_timeout_banner = None;
                return Task::none();
            }
            // R2-B H4 / 統一決定 #21 副次 invariant:
            // `node.build()` 失敗 → live 4 ペインを teardown + LiveStrategyState を
            // Idle に戻す + ユーザー通知。strategy_id が pending / running と一致しない
            // 場合は古い start の遅延通知として log warn のみ（auto_generate_live_panes が
            // 走っていないペインを誤って teardown しないため）。
            ReplayMsg::LiveStrategyBuildFailed {
                strategy_id,
                code,
                message,
            } => {
                let matches_pending =
                    self.live_strategy_pending_strategy_id.as_deref() == Some(strategy_id.as_str());
                let matches_running = matches!(
                    &self.live_strategy,
                    LiveStrategyState::Running { strategy_id: s, .. } if s == &strategy_id
                );
                if !matches_pending && !matches_running {
                    log::warn!(
                        "LiveStrategyBuildFailed ignored — strategy_id mismatch \
                         (got={strategy_id}, code={code}, message={message})"
                    );
                    return Task::none();
                }

                // state machine reset
                self.live_strategy = LiveStrategyState::Idle;
                self.live_strategy_pending_strategy_id = None;
                self.live_warmup_timeout_banner = None;
                self.live_warmup_warming_message = None;
                // R4 R3-RUST-2: build_failed 経路でも warming-up progress を None に戻す。
                self.live_warmup_warming_progress = None;
                self.live_warmup_timeout_token = self.live_warmup_timeout_token.wrapping_add(1);
                self.menu_bar.live_bar = crate::menu_bar_state::LiveBarState::default();

                // ペイン teardown (warm_up 失敗で auto_generate_live_panes が
                // 呼ばれていないケースでも no-op で安全)
                let main_window = self.main_window.id;
                let dashboard = self.active_dashboard_mut();
                dashboard.clear_live_strategy_portfolio(main_window);
                dashboard.teardown_live_panes(&strategy_id);

                // R4 Group A: code に応じて toast の prefix を切り替える
                // ("ライブ戦略起動失敗" を共通文言とし、code 別の根拠を併記)。
                let reason = match code.as_str() {
                    "node_build_failed" => "node.build 失敗",
                    "warm_up_failed" => "warm_up 失敗",
                    "kernel_unavailable" => "kernel 利用不可",
                    "venue_not_supported" => "未対応 venue",
                    "market_closed" => "市場閉場中",
                    // R6 silent-HIGH-1 + LOW-1: server.py が必ず emit する 2 code の
                    // 日本語化 (英語コードを toast に出すと UI トーン不一致になる)。
                    "engine_run_failed" => "エンジン実行失敗",
                    "timeout" => "エンジンタイムアウト",
                    _ => code.as_str(),
                };
                self.notifications.push(Toast::error(format!(
                    "ライブ戦略起動失敗（{reason}）: {message}"
                )));
                return Task::none();
            }
            // issue #42 Phase 3: LIVE_SCENARIO 抽出応答 → modal prefill。
            // pending_scenario_request_id と突合して古い応答は捨てる（replay 対称）。
            ReplayMsg::LiveStrategyScenarioLoaded {
                request_id,
                instrument_id,
                max_qty,
                max_notional_jpy,
                strategy_init_kwargs,
                venue,
            } => {
                // R3 M2: scenario.venue を SoT として `connected_venue` を refine する。
                // 両 venue ready の場合、modal 構築時の `tachibana_state.is_ready()`
                // / `kabu_state.is_ready()` 由来の固定優先 (tachibana 優先) では
                // kabu scenario を開いたユーザに「engine 接続 venue 'tachibana' と
                // 一致しない」誤誘導が出る。scenario が venue を明示しているとき、
                // その venue が ready なら connected_venue を refine する。
                let refined_connected_venue: Option<Option<String>> =
                    if let Some(scen_venue) = venue.as_deref() {
                        let tachi_ready = self.tachibana_state.is_ready();
                        let kabu_ready = self.kabu_state.is_ready();
                        match scen_venue {
                            "tachibana" if tachi_ready => Some(Some("tachibana".to_string())),
                            "kabu_station" if kabu_ready => Some(Some("kabu_station".to_string())),
                            _ => None,
                        }
                    } else {
                        None
                    };
                if let Some(form) = self.live_strategy_form_modal.as_mut()
                    && form.pending_scenario_request_id.as_deref() == Some(request_id.as_str())
                {
                    // R3 M2: scenario が venue を明示し該当 venue が ready なら
                    // connected_venue を refine する (両 ready 時の固定 tachibana 優先
                    // による誤誘導を解消)。
                    if let Some(new_cv) = refined_connected_venue {
                        form.set_connected_venue(new_cv);
                    }
                    // issue #42 R1 MEDIUM-1: venue を form.venue に prefill する
                    // (旧実装は `..` で破棄していた)。
                    form.prefill_from_scenario(
                        instrument_id,
                        max_qty,
                        max_notional_jpy,
                        strategy_init_kwargs,
                        venue,
                    );
                }
                return Task::none();
            }
            // issue #42 Phase 3: LoadLiveStrategyScenario の 5s timeout / parse_failed →
            // pending を解除して手入力フォールバック。
            ReplayMsg::LiveStrategyScenarioFallback { request_id } => {
                if let Some(form) = self.live_strategy_form_modal.as_mut()
                    && form.pending_scenario_request_id.as_deref() == Some(request_id.as_str())
                {
                    form.release_scenario_pending();
                }
                return Task::none();
            }
            // issue #42 Phase 3: EngineRehello 受信時に Running 状態なら 4 ペイン再生成。
            // Python engine からの再 emit は不要（Rust 内部で完結する冪等再生）。
            ReplayMsg::LiveStrategyRehelloReplay => {
                if let LiveStrategyState::Running {
                    strategy_id,
                    instrument_id,
                    venue,
                } = &self.live_strategy
                {
                    let strategy_id = strategy_id.clone();
                    let instrument_id = instrument_id.clone();
                    let venue = venue.clone();
                    let main_window_id = self.main_window.id;
                    self.active_dashboard_mut().auto_generate_live_panes(
                        main_window_id,
                        &strategy_id,
                        &instrument_id,
                        &venue,
                    );
                }
                return Task::none();
            }
            // N4-live: EngineStopped (live) → clear running state
            ReplayMsg::LiveStopped { strategy_id } => {
                let pending_match =
                    self.live_strategy_pending_strategy_id.as_deref() == Some(strategy_id.as_str());
                let running_match = match &self.live_strategy {
                    LiveStrategyState::Running {
                        strategy_id: running_id,
                        ..
                    } => running_id == &strategy_id,
                    LiveStrategyState::Idle => false,
                };
                if running_match || pending_match {
                    self.live_strategy = LiveStrategyState::Idle;
                    self.live_strategy_pending_strategy_id = None;
                    self.live_warmup_timeout_banner = None;
                    self.live_warmup_warming_message = None;
                    // R4 R3-RUST-2: LiveStopped 経路でも warming-up progress を None に戻す。
                    self.live_warmup_warming_progress = None;
                    self.live_warmup_timeout_token = self.live_warmup_timeout_token.wrapping_add(1);
                    self.menu_bar.live_bar = crate::menu_bar_state::LiveBarState::default();
                    let main_window = self.main_window.id;
                    let dashboard = self.active_dashboard_mut();
                    dashboard.clear_live_strategy_portfolio(main_window);
                    // R4 R3-SILENT-3: 正常停止経路でも 4 ペインを teardown する。
                    // 旧実装は `clear_live_pane_keys()` で key だけ消し実ペインを
                    // 残していたため、再起動時に `auto_generate_live_panes` が key
                    // 不一致で重複生成する UX silent failure を抱えていた。
                    // `node_build_failed` arm と同じ teardown を呼んで対称化する。
                    dashboard.teardown_live_panes(&strategy_id);
                } else {
                    log::warn!(
                        "LiveEngineStoppedEvent: strategy_id mismatch (got={strategy_id}); ignoring"
                    );
                }
                return Task::none();
            }
            // N4-live: ■ ボタンから StopEngine を送信する
            ReplayMsg::StopLiveStrategy => {
                let LiveStrategyState::Running { strategy_id, .. } = &self.live_strategy else {
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
                            strategy_init_kwargs,
                            prod_mode,
                            venue,
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
                            // issue #42 Phase 3.5: prod_mode は engine config に流さない。
                            // engine プロセス起動時の env (`TACHIBANA_ALLOW_PROD=1` +
                            // `tachibana_is_demo=False`) が SoT で、GUI からは変更できない
                            // (統一決定 #14)。modal の `validate()` は cap=false で
                            // `prod_mode=true` を reject するため、ここに到達した時点で
                            //   - prod_mode=false なら engine は demo env、または
                            //   - prod_mode=true && cap=true（engine が prod env で起動済み）
                            // のいずれかが成立する。よって StartEngine だけ送れば、
                            // engine は自分の env に従って demo / prod を決定する。
                            let _ = prod_mode;
                            // issue #42 R1 MEDIUM-1: venue は現状 `EngineStartConfig` に
                            // 載らない (Phase 4 §263 の wire 設計判断)。server.py の
                            // `_handle_start_engine` が `_connected_venue` を SoT として
                            // dispatch するため、ここで venue を渡す必要はない。modal
                            // の `validate()` が `available_venues` 含有と `connected_venue`
                            // 一致を済ませているので、Submit 到達時点で
                            //   - venue == connected_venue (engine 側の dispatch と一致)、または
                            //   - connected_venue=None (GUI 側で判定 skip → server.py の
                            //     venue_not_supported reject に委ねる)
                            // のいずれかが成立する。将来 `EngineStartConfig.venue` を
                            // 追加するときは config struct literal に `venue: Some(venue)`
                            // を載せる経路となる (schema bump 1 件)。
                            let _ = venue;
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
                                            strategy_init_kwargs,
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
            // H-2: Commit replay_bar state after `LoadReplayData` succeeds.
            // Emitted from the Submit Task for both `BothOk` and `StartFailed`
            // — in either case the backend has loaded the new replay session,
            // so the bar must reflect the new params (M1).
            // `start_error.is_some()` ⇒ StartEngine failed; surface a toast
            // so the user knows the strategy did not start.
            ReplayMsg::CommitReplayBarState {
                instrument_id,
                start_date,
                end_date,
                granularity,
                strategy_file,
                initial_cash,
                start_error,
            } => {
                self.menu_bar.replay_bar.instrument_id = instrument_id;
                self.menu_bar.replay_bar.start_date = start_date;
                self.menu_bar.replay_bar.end_date = end_date;
                self.menu_bar.replay_bar.granularity = Some(granularity);
                self.menu_bar.replay_bar.strategy_file = Some(strategy_file);
                self.menu_bar.replay_bar.initial_cash = initial_cash;
                if let Some(err) = start_error {
                    return Task::done(Message::Venue(VenueMsg::OrderToast(Toast::error(
                        format!("Replay データは読み込まれましたが起動に失敗しました: {err}"),
                    ))));
                }
            }
        }
        Task::none()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::modal::replay_form::Granularity;
    use std::path::PathBuf;

    fn sample_commit() -> ReplayCommitData {
        ReplayCommitData {
            instrument_id: "1301.TSE".to_string(),
            start_date: "2025-01-06".to_string(),
            end_date: "2025-01-10".to_string(),
            granularity: Granularity::Minute,
            strategy_file: PathBuf::from("/tmp/strategy.py"),
            initial_cash: "1000000".to_string(),
        }
    }

    #[test]
    fn both_ok_emits_commit_with_no_start_error() {
        let msg = submit_result_to_message(ReplaySubmitResult::BothOk, sample_commit());
        match msg {
            Message::Replay(ReplayMsg::CommitReplayBarState {
                instrument_id,
                start_error,
                ..
            }) => {
                assert_eq!(instrument_id, "1301.TSE");
                assert!(start_error.is_none(), "BothOk must not carry a start_error");
            }
            other => panic!("expected CommitReplayBarState, got {other:?}"),
        }
    }

    #[test]
    fn start_failed_still_commits_bar_state() {
        // M1 regression: backend has new replay loaded after LoadReplayData
        // succeeded, so the bar MUST reflect the new params even if
        // StartEngine failed afterwards.
        let msg = submit_result_to_message(
            ReplaySubmitResult::StartFailed("schema mismatch".to_string()),
            sample_commit(),
        );
        match msg {
            Message::Replay(ReplayMsg::CommitReplayBarState {
                instrument_id,
                start_date,
                start_error,
                ..
            }) => {
                assert_eq!(instrument_id, "1301.TSE");
                assert_eq!(start_date, "2025-01-06");
                assert_eq!(
                    start_error.as_deref(),
                    Some("schema mismatch"),
                    "StartFailed must carry the engine-start error so the handler shows a toast",
                );
            }
            other => panic!("expected CommitReplayBarState, got {other:?}"),
        }
    }

    #[test]
    fn load_failed_does_not_commit_bar_state() {
        // M1 regression: backend is unchanged when LoadReplayData failed,
        // so the bar must NOT adopt the new params. Only a toast is emitted.
        let msg = submit_result_to_message(
            ReplaySubmitResult::LoadFailed("connection reset".to_string()),
            sample_commit(),
        );
        match msg {
            Message::Venue(VenueMsg::OrderToast(_)) => {}
            Message::Replay(ReplayMsg::CommitReplayBarState { .. }) => {
                panic!("LoadFailed must NOT commit bar state — backend unchanged");
            }
            other => panic!("expected OrderToast, got {other:?}"),
        }
    }
}
