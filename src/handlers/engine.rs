use std::sync::Arc;

use iced::Task;

use crate::messages::{DashboardMsg, EngineMsg, MenuMsg, VenueMsg};
use crate::venue_state::VenueEvent;
use crate::widget::toast::Toast;
use crate::{LiveStrategyState, Message};

impl crate::Flowsurface {
    pub(crate) fn handle_engine(&mut self, msg: EngineMsg) -> Task<Message> {
        match msg {
            EngineMsg::Restarting(restarting) => {
                self.engine_restarting = restarting;
                if restarting {
                    self.notifications.push(Toast::error(
                        "データエンジン再起動中 — チャートは復旧後に自動更新されます".to_string(),
                    ));
                    let main_window = self.main_window.id;
                    // [R03] Clear in-flight loading on disconnect so panes don't stay
                    // in "updating" state forever if the engine never comes back.
                    self.buying_power_request_id = None;
                    self.order_list_request_id = None;
                    self.positions_request_id = None;
                    // schema 3.14: engine プロセス再起動で session_epoch が 0 に
                    // 巻き戻る。前回値を `None` にリセットして `Some(N) → Some(0)`
                    // の `!=` 誤発火を防ぐ。registry の drain は不要 — 既存ロジックで
                    // pane 構造は保たれ、次回 ReplayDataLoaded で初期遷移として扱う。
                    self.last_replay_session_epoch = None;
                    self.layout_manager
                        .iter_dashboards_mut()
                        .for_each(|dashboard| {
                            dashboard.distribute_buying_power_loading(main_window, false);
                            dashboard.distribute_order_list_loading(main_window, false);
                            dashboard.distribute_positions_loading(main_window, false);
                            dashboard.notify_engine_disconnected(main_window);
                        });
                }
                // The actual backend rebuild + recovery toast are emitted
                // by `Message::EngineConnected` so a single source of
                // truth (the live connection) drives the swap. See
                // T35-H9-SingleRecoveryPath.
            }
            EngineMsg::Connected(conn) => {
                let was_restarting = self.engine_restarting;
                self.engine_connection = Some(Arc::clone(&conn));
                // In-flight requests are lost on reconnect; reset to avoid blocking
                // future auto-fetches via the is_none() guard. Also clear loading
                // so panes don't stay in "updating" state forever.
                let main_window = self.main_window.id;
                self.buying_power_request_id = None;
                self.order_list_request_id = None;
                self.positions_request_id = None;
                // R2-B M8: reconnect で in-flight な LoadLiveStrategyScenario の応答は
                // 失われるため、live form が開いていれば pending を解除して手入力モードに
                // 戻す。再起動後に form を再 open すれば再度 prefill 経路が走る (handler
                // 側で新 request_id を発行)。同じ pending_scenario_request_id 整合の
                // 流儀 (buying_power_request_id 等のリセットと対称) で扱う。
                if let Some(form) = self.live_strategy_form_modal.as_mut() {
                    form.release_scenario_pending();
                }
                // R4 R3-SILENT-4: live session が Running でなければ pending 状態を
                // reset する。reconnect 直前に「pending だが Ready が来ない」状態
                // だった場合、reconnect 後も古い pending_strategy_id が残ると
                // 後続の LiveWarmupTimeoutFired を誤照合して既に消えた session の
                // banner を出してしまう silent UX failure になる。
                // Running 状態 (LiveStrategyReady 受信済み) は保持する — reconnect
                // 後に EngineRehello → 4 ペイン再生成の冪等再生経路が走るため。
                if !matches!(self.live_strategy, LiveStrategyState::Running { .. }) {
                    self.live_strategy_pending_strategy_id = None;
                    self.live_warmup_timeout_token = self.live_warmup_timeout_token.wrapping_add(1);
                    self.live_warmup_warming_message = None;
                    // R4 R3-RUST-2: reconnect で pending を捨てるとき progress も None に戻す。
                    self.live_warmup_warming_progress = None;
                }
                self.active_dashboard_mut()
                    .distribute_buying_power_loading(main_window, false);
                self.active_dashboard_mut()
                    .distribute_order_list_loading(main_window, false);
                self.active_dashboard_mut()
                    .distribute_positions_loading(main_window, false);

                // Rebuild backends with the new connection and bump the generation
                // counter so iced assigns new subscription IDs and restarts streams.
                // D1: do NOT clear VENUE_CAPS_STORE here — old values remain as the
                // authoritative source during the reconnect window until
                // fetch_ticker_metadata upserts fresh entries. Clearing creates an
                // empty-store window where caps_client_aggr() falls back to the wrong
                // default (Hyperliquid would be misclassified as client-aggregated).
                let mut tachibana_meta_handle = None;
                for &(venue, name) in crate::VENUE_NAMES {
                    let backend = Arc::new(engine_client::EngineClientBackend::new(
                        Arc::clone(&conn),
                        name,
                        crate::VENUE_CAPS_STORE
                            .get()
                            .map(Arc::clone)
                            .unwrap_or_else(|| {
                                Arc::new(tokio::sync::RwLock::new(
                                    engine_client::VenueCapsStore::new(),
                                ))
                            }),
                    ));
                    // B5: capture the Tachibana meta handle before the backend
                    // is moved into the type-erased `AdapterHandles`. This is
                    // the only point where the typed `Arc<EngineClientBackend>`
                    // is available to call `ticker_meta_handle()`.
                    if venue == exchange::adapter::Venue::Tachibana {
                        tachibana_meta_handle = Some(backend.ticker_meta_handle());
                    }
                    self.handles.set_backend(venue, backend);
                }
                // Wire the handle into the sidebar's ticker filter so
                // Japanese-name incremental search works after each reconnect.
                self.sidebar
                    .set_tachibana_meta_handle(tachibana_meta_handle);

                // Re-apply current proxy state before bumping the generation so
                // that stream-subscribe commands are enqueued after SetProxy in
                // the engine's FIFO command channel.  Send unconditionally —
                // including `None` — so a user-cleared proxy cannot be revived
                // by a stale value held in the freshly spawned engine.
                let proxy_url = self.network.proxy_cfg().map(|p| p.to_url_string());
                if !conn.try_send_now(engine_client::dto::Command::SetProxy { url: proxy_url }) {
                    log::warn!("Failed to queue proxy for engine reconnect");
                }

                self.handles.bump_generation();

                // Also propagate to the sidebar's TickersTable so it uses
                // the new connection for metadata/stats fetches.
                let sidebar_refetch = self
                    .sidebar
                    .update_handles(self.handles.clone())
                    .map(|m| Message::Dashboard(DashboardMsg::Sidebar(m)));

                if was_restarting {
                    self.notifications
                        .push(Toast::info("データエンジン接続を復旧しました".to_string()));
                }

                // Clear the disconnection error from all OrderEntry panes so
                // they return to normal state after reconnect (M-1).
                {
                    let main_window = self.main_window.id;
                    self.layout_manager
                        .iter_dashboards_mut()
                        .for_each(|dashboard| {
                            dashboard.notify_engine_reconnected(main_window);
                        });
                }

                // Bridge the broadcast-replay gap from BOTH directions:
                //   - managed mode: `ProcessManager` caches post-
                //     `apply_after_handshake` readiness internally.
                //   - external mode (`--data-engine-url`): the
                //     mode-agnostic `VENUE_READY_CACHE` bridge task
                //     captured `VenueReady` between connect() and
                //     iced's late `subscribe_events()`.
                // Either source being `true` means the engine
                // currently considers Tachibana ready — synthesize
                // `VenueEvent::Ready` so the FSM bootstraps correctly.
                // Reviewers 2026-04-26 R2 (HIGH-1) / R3 (HIGH-2).
                let is_ready_from_manager = self
                    .engine_manager
                    .as_ref()
                    .is_some_and(|m| m.try_is_venue_ready(crate::TACHIBANA_VENUE_NAME));
                let is_ready_from_bridge =
                    crate::cached_venue_is_ready(crate::TACHIBANA_VENUE_NAME);
                let tachibana_synthetic = if (is_ready_from_manager || is_ready_from_bridge)
                    && !self.tachibana_state.is_ready()
                {
                    Some(Task::done(Message::Venue(VenueMsg::TachibanaEvent(
                        VenueEvent::Ready,
                    ))))
                } else {
                    None
                };

                let is_kabu_ready_from_manager = self
                    .engine_manager
                    .as_ref()
                    .is_some_and(|m| m.try_is_venue_ready(crate::KABU_STATION_VENUE_NAME));
                let is_kabu_ready_from_bridge =
                    crate::cached_venue_is_ready(crate::KABU_STATION_VENUE_NAME);
                // KabuVenueEvent(Ready) synthesized when kabu cache is hot
                let kabu_synthetic = if (is_kabu_ready_from_manager || is_kabu_ready_from_bridge)
                    && !self.kabu_state.is_ready()
                {
                    Some(Task::done(Message::Venue(VenueMsg::KabuEvent(
                        VenueEvent::Ready,
                    ))))
                } else {
                    None
                };

                let extras = [tachibana_synthetic, kabu_synthetic];
                let has_extras = extras.iter().any(Option::is_some);
                if has_extras {
                    return Task::batch(
                        std::iter::once(Some(sidebar_refetch))
                            .chain(extras)
                            .flatten()
                            .collect::<Vec<_>>(),
                    );
                }
                return sidebar_refetch;
            }
            // INVARIANT: Python は command を reject した場合のみ EngineBusy を送出する。
            // IPC Ok(()) の後に EngineBusy が来ることはない。
            // したがってロールバックと IPC 成功が競合するシナリオは発生しない。
            EngineMsg::PauseReplayBusy { reason } => {
                log::warn!("PauseReplay EngineBusy (rolling back): {reason}");
                // reason は log のみ。Toast は out of scope（R2-H2）。
                let has_history = self.menu_bar.replay_bar.replay_has_history;
                return Task::done(Message::Menu(MenuMsg::Bar(
                    crate::menu_bar_state::BarMessage::ReplayPauseStateChanged {
                        paused: false,
                        has_history,
                    },
                )));
            }
            EngineMsg::ResumeReplayBusy { reason } => {
                log::warn!("ResumeReplay EngineBusy (rolling back): {reason}");
                // reason は log のみ。Toast は out of scope（R2-H2）。
                let has_history = self.menu_bar.replay_bar.replay_has_history;
                return Task::done(Message::Menu(MenuMsg::Bar(
                    crate::menu_bar_state::BarMessage::ReplayPauseStateChanged {
                        paused: true,
                        has_history,
                    },
                )));
            }
            EngineMsg::Noop => return Task::none(),
            // N1.12: ExecutionMarker → broadcast overlay dot to all Kline charts
        }
        Task::none()
    }
}
