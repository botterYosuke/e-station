use iced::Task;

use crate::Message;
use crate::messages::{DashboardMsg, VenueMsg};
use crate::modal;
use crate::screen::dashboard;
use crate::venue_state::{Trigger, VenueEvent, VenueState};
use crate::widget::toast::Toast;

impl crate::Flowsurface {
    pub(crate) fn handle_venue(&mut self, msg: VenueMsg) -> Task<Message> {
        match msg {
            VenueMsg::DismissTachibanaBanner => {
                // Route the dismiss through the FSM `next()` table so
                // the transition is unit-testable from `venue_state.rs`
                // and `main.rs::update()` does not become a second
                // source of truth for FSM mutations.
                //
                // Send RequestVenueLogout only for market_closed errors: in
                // that case the Python session is still valid and would be
                // silently reused on the next stock selection (auto-login).
                // For non-market-closed errors (ticker_not_found, login
                // failure, …) the session is already invalid — wiping it is
                // harmless but the condition stays narrow to avoid
                // unintended side-effects on recoverable warnings.
                let should_logout = matches!(
                    self.tachibana_state,
                    VenueState::Error {
                        market_closed: true,
                        ..
                    }
                );
                let next = std::mem::replace(&mut self.tachibana_state, VenueState::Idle)
                    .next(VenueEvent::Dismissed);
                self.tachibana_state = next;
                if should_logout && let Some(conn) = self.engine_connection.as_ref().cloned() {
                    return Task::perform(
                        async move {
                            conn.send(engine_client::dto::Command::RequestVenueLogout {
                                venue: crate::TACHIBANA_VENUE_NAME.to_string(),
                            })
                            .await
                            .map_err(|e| e.to_string())
                        },
                        |r| {
                            if let Err(e) = r {
                                log::warn!("Tachibana logout IPC (on banner dismiss) failed: {e}");
                            }
                            Message::Engine(crate::messages::EngineMsg::Noop)
                        },
                    );
                }
            }
            VenueMsg::RequestTachibanaLogin(trigger) => {
                // Duplicate-press suppression: claim the LoginInFlight
                // slot atomically BEFORE dispatching the IPC. Without
                // this, two rapid presses (Auto + Manual or two manual
                // double-clicks) both observe the FSM in `Idle` /
                // `Ready` / `Error` and dispatch duplicate
                // `RequestVenueLogin` IPC sends — a tkinter helper
                // spawns twice. Reviewer 2026-04-26 R4 (MEDIUM-2).
                // T35-U1-LoginButton / T35-U3-AutoRequestLogin.
                log::info!("RequestTachibanaLogin trigger={trigger:?}");
                let Some(conn) = self.engine_connection.as_ref().cloned() else {
                    log::warn!(
                        "RequestTachibanaLogin({trigger:?}) ignored — engine connection unavailable"
                    );
                    if matches!(trigger, Trigger::Manual) {
                        // Auto-fire is silent (the user just selected
                        // the venue and may not yet expect feedback);
                        // a manual button press deserves a visible
                        // notice that the click did register.
                        self.notifications.push(Toast::error(
                            "立花ログイン要求を送信できません — エンジン未接続".to_string(),
                        ));
                    }
                    return Task::none();
                };
                if !self.tachibana_state.try_claim_login_in_flight() {
                    log::debug!(
                        "RequestTachibanaLogin({trigger:?}) ignored — login already in flight"
                    );
                    return Task::none();
                }
                return Task::perform(
                    async move {
                        // request_id は Python エンジン側のログ相関 ID として使われる。
                        // Rust 側では TachibanaLoginIpcResult のコールバックに乗らないため、
                        // IPC 送信成功/失敗の照合には使用しない。
                        let request_id = uuid::Uuid::new_v4().to_string();
                        conn.send(engine_client::dto::Command::RequestVenueLogin {
                            request_id,
                            venue: crate::TACHIBANA_VENUE_NAME.to_string(),
                        })
                        .await
                        .map_err(|e| e.to_string())
                    },
                    |r| Message::Venue(VenueMsg::TachibanaLoginIpcResult(r)),
                );
            }
            VenueMsg::RequestTachibanaLogout => {
                log::info!("RequestTachibanaLogout");
                self.tachibana_state = VenueState::Idle;
                if let Some(conn) = self.engine_connection.as_ref().cloned() {
                    return Task::perform(
                        async move {
                            conn.send(engine_client::dto::Command::RequestVenueLogout {
                                venue: crate::TACHIBANA_VENUE_NAME.to_string(),
                            })
                            .await
                            .map_err(|e| e.to_string())
                        },
                        |r| {
                            if let Err(e) = r {
                                log::warn!("Tachibana logout IPC failed: {e}");
                            }
                            Message::Engine(crate::messages::EngineMsg::Noop)
                        },
                    );
                }
            }
            VenueMsg::TachibanaLoginIpcResult(result) => {
                // The optimistic `try_claim_login_in_flight` already
                // moved the FSM into `LoginInFlight`. Engine's
                // `VenueLoginStarted` is idempotent under that, but
                // an IPC send failure means the engine never received
                // the request and will not emit `VenueLoginStarted`
                // — roll the FSM back to `Idle` so the user can
                // retry. Reviewer 2026-04-26 R4 (MEDIUM-2).
                match result {
                    Ok(()) => {
                        log::debug!("RequestVenueLogin IPC sent");
                    }
                    Err(err) => {
                        log::warn!("RequestVenueLogin IPC failed: {err}");
                        self.notifications.push(Toast::error(format!(
                            "立花ログイン要求の送信に失敗しました: {err}"
                        )));
                        // FSM の next() を意図的に迂回して直接 Idle に戻す。
                        // IPC 送信が失敗した時点でエンジンには RequestVenueLogin が届いておらず、
                        // VenueLoginStarted も来ない。LoginCancelled は「ユーザー操作でキャンセル」の
                        // セマンティクスなので流用せず、ここで直接代入する。
                        if self.tachibana_state.is_login_in_flight() {
                            self.tachibana_state = VenueState::Idle;
                        }
                    }
                }
            }
            VenueMsg::TachibanaEvent(event) => {
                // Toast notifications for the in-flight / cancelled
                // states. The banner only renders `Error`
                // (F-Banner1: no Rust string literals in the banner),
                // so the user-facing "ログイン中" / "キャンセル" feedback
                // path goes through the existing toast channel where
                // Rust strings are conventional. Reviewer 2026-04-26
                // R2 (MED-3).
                match &event {
                    VenueEvent::LoginStarted => {
                        self.notifications.push(Toast::info(
                            "立花ログインダイアログを起動しました".to_string(),
                        ));
                    }
                    VenueEvent::LoginCancelled => {
                        self.notifications.push(Toast::warn(
                            "立花ログインがキャンセルされました".to_string(),
                        ));
                    }
                    VenueEvent::Ready => {
                        log::info!("tachibana: VenueReady — venue is now authenticated");
                    }
                    VenueEvent::EngineRehello => {
                        log::info!("tachibana: EngineRehello — state reset to Idle");
                    }
                    _ => {}
                }

                let old_state = std::mem::replace(&mut self.tachibana_state, VenueState::Idle);
                // Capture before `next()` consumes old_state.
                let needs_bump =
                    old_state.is_login_in_flight() || matches!(old_state, VenueState::Error { .. });
                let next = old_state.next(event);
                let is_ready = next.is_ready();
                self.tachibana_state = next;

                // Bump only when the session *newly* becomes available from a
                // state that required a login round-trip (LoginInFlight) or a
                // re-authentication after an error. Transitions from Idle or
                // Ready → Ready must NOT bump — those paths mean EngineConnected
                // already bumped (Idle) or the event is idempotent (Ready→Ready).
                if needs_bump && is_ready {
                    self.handles.bump_generation();
                    log::info!(
                        "tachibana: session established — restarting subscriptions (gen bumped)"
                    );
                }

                let replay = self
                    .sidebar
                    .tickers_table
                    .set_tachibana_ready(is_ready)
                    .map(|m| {
                        Message::Dashboard(DashboardMsg::Sidebar(
                            dashboard::sidebar::Message::TickersTable(m),
                        ))
                    });

                // Issue #25: propagate venue readiness to all dashboards.
                let main_window_id = self.main_window.id;
                self.layout_manager
                    .iter_dashboards_mut()
                    .for_each(|d| d.set_tachibana_ready(is_ready));

                // Auto-fetch buying power on venue ready if a pane is visible.
                let main_window = main_window_id;
                let auto_fetch_buying_power = if is_ready
                    && self.buying_power_request_id.is_none()
                    && self.active_dashboard().has_buying_power_pane(main_window)
                {
                    if let Some(conn) = self.engine_connection.as_ref().cloned() {
                        let req_id = uuid::Uuid::new_v4().to_string();
                        self.buying_power_request_id = Some(req_id.clone());
                        self.active_dashboard_mut()
                            .distribute_buying_power_loading(main_window, true);
                        let req_id_for_err = req_id.clone();
                        Task::perform(
                            async move {
                                conn.send(engine_client::dto::Command::GetBuyingPower {
                                    request_id: req_id,
                                    venue: crate::TACHIBANA_VENUE_NAME.to_string(),
                                })
                                .await
                                .map_err(|e| e.to_string())
                            },
                            move |res| match res {
                                Ok(()) => {
                                    Message::Venue(VenueMsg::BuyingPowerSendCompleted(Ok(())))
                                }
                                Err(err) => Message::Venue(VenueMsg::IpcError {
                                    request_id: Some(req_id_for_err),
                                    code: "send_failed".to_string(),
                                    message: err,
                                }),
                            },
                        )
                    } else {
                        Task::none()
                    }
                } else {
                    Task::none()
                };

                // Auto-fetch order list on venue ready if a pane is visible.
                let auto_fetch_orders = if is_ready
                    && self.order_list_request_id.is_none()
                    && self.active_dashboard().has_order_list_pane(main_window)
                {
                    if let Some(conn) = self.engine_connection.as_ref().cloned() {
                        let req_id = uuid::Uuid::new_v4().to_string();
                        self.order_list_request_id = Some(req_id.clone());
                        self.active_dashboard_mut()
                            .distribute_order_list_loading(main_window, true);
                        Task::perform(
                            async move {
                                conn.send(engine_client::dto::Command::GetOrderList {
                                    request_id: req_id,
                                    venue: crate::TACHIBANA_VENUE_NAME.to_string(),
                                    filter: engine_client::dto::OrderListFilter {
                                        status: None,
                                        instrument_id: None,
                                        date: None,
                                    },
                                })
                                .await
                                .map_err(|e| e.to_string())
                            },
                            |r| Message::Venue(VenueMsg::OrderListSendCompleted(r)),
                        )
                    } else {
                        Task::none()
                    }
                } else {
                    Task::none()
                };

                // Auto-fetch positions on venue ready if a pane is visible.
                let auto_fetch_positions = if is_ready
                    && self.positions_request_id.is_none()
                    && self.active_dashboard().has_positions_pane(main_window)
                {
                    if let Some(conn) = self.engine_connection.as_ref().cloned() {
                        let req_id = uuid::Uuid::new_v4().to_string();
                        self.positions_request_id = Some(req_id.clone());
                        self.active_dashboard_mut()
                            .distribute_positions_loading(main_window, true);
                        let req_id_for_err = req_id.clone();
                        Task::perform(
                            async move {
                                conn.send(engine_client::dto::Command::GetPositions {
                                    request_id: req_id,
                                    venue: crate::TACHIBANA_VENUE_NAME.to_string(),
                                })
                                .await
                                .map_err(|e| e.to_string())
                            },
                            move |res| match res {
                                Ok(()) => Message::Venue(VenueMsg::PositionsSendCompleted(Ok(()))),
                                Err(err) => Message::Venue(VenueMsg::IpcError {
                                    request_id: Some(req_id_for_err),
                                    code: "send_failed".to_string(),
                                    message: err,
                                }),
                            },
                        )
                    } else {
                        Task::none()
                    }
                } else {
                    Task::none()
                };

                return replay
                    .chain(auto_fetch_buying_power)
                    .chain(auto_fetch_orders)
                    .chain(auto_fetch_positions);
            }
            // Message::RequestKabuLogin(trigger) => (post-refactor name below)
            VenueMsg::RequestKabuLogin(trigger) => {
                log::info!("RequestKabuLogin trigger={trigger:?}");
                let Some(conn) = self.engine_connection.as_ref().cloned() else {
                    log::warn!(
                        "RequestKabuLogin({trigger:?}) ignored — engine connection unavailable"
                    );
                    if matches!(trigger, Trigger::Manual) {
                        self.notifications.push(Toast::error(
                            "kabuログイン要求を送信できません — エンジン未接続".to_string(),
                        ));
                    }
                    return Task::none();
                };
                if !self.kabu_state.try_claim_login_in_flight() {
                    log::debug!("RequestKabuLogin({trigger:?}) ignored — login already in flight");
                    return Task::none();
                }
                return Task::perform(
                    async move {
                        let request_id = uuid::Uuid::new_v4().to_string();
                        conn.send(engine_client::dto::Command::RequestVenueLogin {
                            request_id,
                            venue: crate::KABU_STATION_VENUE_NAME.to_string(),
                        })
                        .await
                        .map_err(|e| e.to_string())
                    },
                    |r| Message::Venue(VenueMsg::KabuLoginIpcResult(r)),
                );
            }
            VenueMsg::RequestKabuLogout => {
                log::info!("RequestKabuLogout");
                self.kabu_state = VenueState::Idle;
            }
            // Message::KabuLoginIpcResult(result) => (post-refactor name below)
            VenueMsg::KabuLoginIpcResult(result) => match result {
                Ok(()) => {
                    log::debug!("kabu RequestVenueLogin IPC sent");
                }
                Err(err) => {
                    log::warn!("kabu RequestVenueLogin IPC failed: {err}");
                    self.notifications.push(Toast::error(format!(
                        "kabuログイン要求の送信に失敗しました: {err}"
                    )));
                    // FSM の next() を意図的に迂回して直接 Idle に戻す。
                    // IPC 送信が失敗した時点でエンジンには RequestVenueLogin が届いておらず、
                    // VenueLoginStarted も来ない。LoginCancelled は「ユーザー操作でキャンセル」の
                    // セマンティクスなので流用せず、ここで直接代入する。
                    if self.kabu_state.is_login_in_flight() {
                        self.kabu_state = VenueState::Idle;
                    }
                }
            },
            VenueMsg::KabuEvent(event) => {
                match &event {
                    VenueEvent::LoginStarted => {
                        self.notifications.push(Toast::info(
                            "kabuログインダイアログを起動しました".to_string(),
                        ));
                    }
                    VenueEvent::LoginCancelled => {
                        self.notifications.push(Toast::warn(
                            "kabuログインがキャンセルされました".to_string(),
                        ));
                    }
                    VenueEvent::Ready => {
                        log::info!("kabu: VenueReady — venue is now authenticated");
                    }
                    VenueEvent::LoginError { message, .. } => {
                        log::warn!("kabu: VenueLoginError — {message}");
                        self.notifications
                            .push(Toast::error(format!("kabuログインエラー: {message}")));
                    }
                    VenueEvent::EngineRehello => {
                        log::info!("kabu: EngineRehello — state reset to Idle");
                    }
                    VenueEvent::Dismissed => {}
                }

                let old_state = std::mem::replace(&mut self.kabu_state, VenueState::Idle);
                let needs_bump =
                    old_state.is_login_in_flight() || matches!(old_state, VenueState::Error { .. });
                let next = old_state.next(event);
                let is_ready = next.is_ready();
                self.kabu_state = next;

                if needs_bump && is_ready {
                    self.handles.bump_generation();
                    log::info!("kabu: session established — restarting subscriptions (gen bumped)");
                }

                let main_window = self.main_window.id;

                // Auto-fetch order list on venue ready if a pane is visible.
                let auto_fetch_orders = if is_ready
                    && self.order_list_request_id.is_none()
                    && self.active_dashboard().has_order_list_pane(main_window)
                {
                    if let Some(conn) = self.engine_connection.as_ref().cloned() {
                        let req_id = uuid::Uuid::new_v4().to_string();
                        self.order_list_request_id = Some(req_id.clone());
                        self.active_dashboard_mut()
                            .distribute_order_list_loading(main_window, true);
                        Task::perform(
                            async move {
                                conn.send(engine_client::dto::Command::GetOrderList {
                                    request_id: req_id,
                                    venue: crate::KABU_STATION_VENUE_NAME.to_string(),
                                    filter: engine_client::dto::OrderListFilter {
                                        status: None,
                                        instrument_id: None,
                                        date: None,
                                    },
                                })
                                .await
                                .map_err(|e| e.to_string())
                            },
                            |r| Message::Venue(VenueMsg::OrderListSendCompleted(r)),
                        )
                    } else {
                        Task::none()
                    }
                } else {
                    Task::none()
                };

                // Auto-fetch positions on venue ready if a pane is visible.
                let auto_fetch_positions = if is_ready
                    && self.positions_request_id.is_none()
                    && self.active_dashboard().has_positions_pane(main_window)
                {
                    if let Some(conn) = self.engine_connection.as_ref().cloned() {
                        let req_id = uuid::Uuid::new_v4().to_string();
                        self.positions_request_id = Some(req_id.clone());
                        self.active_dashboard_mut()
                            .distribute_positions_loading(main_window, true);
                        let req_id_for_err = req_id.clone();
                        Task::perform(
                            async move {
                                conn.send(engine_client::dto::Command::GetPositions {
                                    request_id: req_id,
                                    venue: crate::KABU_STATION_VENUE_NAME.to_string(),
                                })
                                .await
                                .map_err(|e| e.to_string())
                            },
                            move |res| match res {
                                Ok(()) => Message::Venue(VenueMsg::PositionsSendCompleted(Ok(()))),
                                Err(err) => Message::Venue(VenueMsg::IpcError {
                                    request_id: Some(req_id_for_err),
                                    code: "send_failed".to_string(),
                                    message: err,
                                }),
                            },
                        )
                    } else {
                        Task::none()
                    }
                } else {
                    Task::none()
                };

                // Tachibana と同様に metadata fetch ゲートを解除
                let ticker_fetch = self
                    .sidebar
                    .tickers_table
                    .set_kabu_station_ready(is_ready)
                    .map(|m| {
                        Message::Dashboard(DashboardMsg::Sidebar(
                            dashboard::sidebar::Message::TickersTable(m),
                        ))
                    });

                // Issue #25: propagate kabu readiness to all dashboards.
                self.layout_manager
                    .iter_dashboards_mut()
                    .for_each(|d| d.set_kabu_ready(is_ready));

                // Auto-fetch buying power on venue ready if a pane is visible.
                let auto_fetch_buying_power = if is_ready
                    && self.buying_power_request_id.is_none()
                    && self.active_dashboard().has_buying_power_pane(main_window)
                {
                    if let Some(conn) = self.engine_connection.as_ref().cloned() {
                        let req_id = uuid::Uuid::new_v4().to_string();
                        self.buying_power_request_id = Some(req_id.clone());
                        self.active_dashboard_mut()
                            .distribute_buying_power_loading(main_window, true);
                        let req_id_for_err = req_id.clone();
                        Task::perform(
                            async move {
                                conn.send(engine_client::dto::Command::GetBuyingPower {
                                    request_id: req_id,
                                    venue: crate::KABU_STATION_VENUE_NAME.to_string(),
                                })
                                .await
                                .map_err(|e| e.to_string())
                            },
                            move |res| match res {
                                Ok(()) => {
                                    Message::Venue(VenueMsg::BuyingPowerSendCompleted(Ok(())))
                                }
                                Err(err) => Message::Venue(VenueMsg::IpcError {
                                    request_id: Some(req_id_for_err),
                                    code: "send_failed".to_string(),
                                    message: err,
                                }),
                            },
                        )
                    } else {
                        Task::none()
                    }
                } else {
                    Task::none()
                };

                return ticker_fetch
                    .chain(auto_fetch_buying_power)
                    .chain(auto_fetch_orders)
                    .chain(auto_fetch_positions);
            }
            // Message::EngineConnected(conn) => (post-refactor name below)
            VenueMsg::OrderToast(toast) => {
                self.notifications.push(toast);
            }
            // OrderFilled — toast + positions auto-refresh
            VenueMsg::OrderFilled {
                client_order_id,
                last_qty,
                last_price,
                leaves_qty,
            } => {
                let body = if leaves_qty == "0" {
                    format!("約定 {client_order_id}: {last_qty} 株 @ {last_price} 円（全約定）")
                } else {
                    format!(
                        "約定 {client_order_id}: {last_qty} 株 @ {last_price} 円（残 {leaves_qty} 株）"
                    )
                };
                self.notifications.push(Toast::info(body));

                // live モード（tachibana ログイン済み）のときのみ positions 自動更新。
                if !self.tachibana_state.is_ready() {
                    return Task::none();
                }
                let Some(conn) = self.engine_connection.as_ref().cloned() else {
                    return Task::none();
                };
                let main_window = self.main_window.id;
                if self.positions_request_id.is_none()
                    && self.active_dashboard().has_positions_pane(main_window)
                {
                    let req_id = uuid::Uuid::new_v4().to_string();
                    self.positions_request_id = Some(req_id.clone());
                    self.active_dashboard_mut()
                        .distribute_positions_loading(main_window, true);
                    let req_id_for_err = req_id.clone();
                    return Task::perform(
                        async move {
                            conn.send(engine_client::dto::Command::GetPositions {
                                request_id: req_id,
                                venue: crate::TACHIBANA_VENUE_NAME.to_string(),
                            })
                            .await
                            .map_err(|e| e.to_string())
                        },
                        move |res| match res {
                            Ok(()) => Message::Venue(VenueMsg::PositionsSendCompleted(Ok(()))),
                            Err(err) => Message::Venue(VenueMsg::IpcError {
                                request_id: Some(req_id_for_err),
                                code: "send_failed".to_string(),
                                message: err,
                            }),
                        },
                    );
                }
            }
            // Phase U1: distribute fresh order list to all OrderList panes
            VenueMsg::OrderListUpdated(orders) => {
                self.order_list_request_id = None;
                let main_window = self.main_window.id;
                self.active_dashboard_mut()
                    .distribute_order_list(main_window, orders);
            }
            VenueMsg::OrderListSendCompleted(Ok(())) => {
                // 送信成功: OrderListUpdated 受信を待つだけ
            }
            VenueMsg::OrderListSendCompleted(Err(err)) => {
                self.order_list_request_id = None;
                let main_window = self.main_window.id;
                self.active_dashboard_mut()
                    .distribute_order_list_error(main_window, err.clone());
                self.notifications
                    .push(Toast::error(format!("注文一覧取得失敗: {err}")));
            }
            VenueMsg::BuyingPowerSendCompleted(Ok(())) => {
                // 送信成功: BuyingPowerUpdated 受信を待つだけ
            }
            VenueMsg::BuyingPowerSendCompleted(Err(err)) => {
                self.buying_power_request_id = None;
                let main_window = self.main_window.id;
                self.active_dashboard_mut()
                    .distribute_buying_power_error(main_window, err.clone());
                self.notifications
                    .push(Toast::error(format!("余力情報取得失敗: {err}")));
            }
            VenueMsg::PositionsSendCompleted(Ok(())) => {
                // 送信成功: PositionsUpdated 受信を待つだけ
            }
            VenueMsg::PositionsSendCompleted(Err(err)) => {
                self.positions_request_id = None;
                let main_window = self.main_window.id;
                self.active_dashboard_mut()
                    .distribute_positions_error(main_window, err.clone());
            }
            // Positions: broadcast to all Positions panes
            VenueMsg::PositionsUpdated {
                request_id,
                venue: _,
                positions,
                ts_ms,
            } => {
                // Issue 5: push 型（request_id == ""）は replay 専用 pane へ配布。
                // engine_runner の fill / server.py の StepBackward が起点。
                if request_id.is_empty() {
                    let main_window = self.main_window.id;
                    self.active_dashboard_mut().distribute_replay_positions(
                        main_window,
                        positions,
                        ts_ms,
                    );
                    return Task::none();
                }

                let matches = self.positions_request_id.as_deref() == Some(request_id.as_str());
                if !matches {
                    log::debug!(
                        "[PositionsUpdated] stale/unrouted: request_id={request_id:?}, \
                         in-flight={:?}",
                        self.positions_request_id
                    );
                    return Task::none();
                }
                self.positions_request_id = None;
                let main_window = self.main_window.id;
                self.active_dashboard_mut()
                    .distribute_positions(main_window, positions, ts_ms);
            }
            // Phase U3: broadcast to all BuyingPower panes; silently no-ops if no pane exists
            VenueMsg::BuyingPowerUpdated {
                cash_available,
                cash_shortfall,
                credit_available,
                ts_ms,
            } => {
                self.buying_power_request_id = None;
                let main_window = self.main_window.id;
                self.active_dashboard_mut().distribute_buying_power(
                    main_window,
                    cash_available,
                    cash_shortfall,
                    credit_available,
                    ts_ms,
                );
            }
            // N1.16: REPLAY 仮想ポートフォリオ更新 — dashboard に配布
            VenueMsg::IpcError {
                request_id,
                code,
                message,
            } => {
                let matches_buying_power = self
                    .buying_power_request_id
                    .as_deref()
                    .zip(request_id.as_deref())
                    .is_some_and(|(bp, err)| bp == err);
                let matches_order_list = self
                    .order_list_request_id
                    .as_deref()
                    .zip(request_id.as_deref())
                    .is_some_and(|(ol, err)| ol == err);
                let matches_positions = self
                    .positions_request_id
                    .as_deref()
                    .zip(request_id.as_deref())
                    .is_some_and(|(p, err)| p == err);
                if matches_buying_power {
                    self.buying_power_request_id = None;
                    let main_window = self.main_window.id;
                    self.active_dashboard_mut()
                        .distribute_buying_power_error(main_window, format!("[{code}] {message}"));
                } else if matches_order_list {
                    self.order_list_request_id = None;
                    let main_window = self.main_window.id;
                    self.active_dashboard_mut()
                        .distribute_order_list_error(main_window, format!("[{code}] {message}"));
                } else if matches_positions {
                    self.positions_request_id = None;
                    let main_window = self.main_window.id;
                    self.active_dashboard_mut()
                        .distribute_positions_error(main_window, format!("[{code}] {message}"));
                } else if code == "strategy_load_failed" {
                    // N4.4: surface the error as a dismissable banner.
                    self.strategy_load_error = Some(message);
                } else {
                    log::debug!(
                        "[IpcError] unrouted: request_id={request_id:?}, code={code}, \
                         message={message}"
                    );
                }
            }
            // N4.3: user picked (or cancelled) the strategy file dialog.
            VenueMsg::OrderAccepted {
                client_order_id,
                venue_order_id,
            } => {
                let main_window = self.main_window.id;
                self.active_dashboard_mut()
                    .notify_order_accepted(main_window, &client_order_id);
                let vid = venue_order_id.unwrap_or_default();
                self.notifications.push(Toast::info(format!(
                    "注文受付: {client_order_id} (venue: {vid})"
                )));

                // live モード（tachibana ログイン済み）のときのみ自動更新。
                // replay バックテストも OrderAccepted を emit するため、このガードは必須。
                if !self.tachibana_state.is_ready() {
                    return Task::none();
                }

                let Some(conn) = self.engine_connection.as_ref().cloned() else {
                    return Task::none();
                };

                let refresh_orders = if self.order_list_request_id.is_none() {
                    let req_id = uuid::Uuid::new_v4().to_string();
                    self.order_list_request_id = Some(req_id.clone());
                    let main_window = self.main_window.id;
                    self.active_dashboard_mut()
                        .distribute_order_list_loading(main_window, true);
                    let conn_for_orders = conn.clone();
                    Task::perform(
                        async move {
                            conn_for_orders
                                .send(engine_client::dto::Command::GetOrderList {
                                    request_id: req_id,
                                    venue: crate::TACHIBANA_VENUE_NAME.to_string(),
                                    filter: engine_client::dto::OrderListFilter {
                                        status: None,
                                        instrument_id: None,
                                        date: None,
                                    },
                                })
                                .await
                                .map_err(|e| e.to_string())
                        },
                        |r| Message::Venue(VenueMsg::OrderListSendCompleted(r)),
                    )
                } else {
                    Task::none()
                };

                let refresh_buying_power = if self.buying_power_request_id.is_none() {
                    let req_id = uuid::Uuid::new_v4().to_string();
                    self.buying_power_request_id = Some(req_id.clone());
                    let main_window = self.main_window.id;
                    self.active_dashboard_mut()
                        .distribute_buying_power_loading(main_window, true);
                    let req_id_for_err = req_id.clone();
                    Task::perform(
                        async move {
                            conn.send(engine_client::dto::Command::GetBuyingPower {
                                request_id: req_id,
                                venue: crate::TACHIBANA_VENUE_NAME.to_string(),
                            })
                            .await
                            .map_err(|e| e.to_string())
                        },
                        move |res| match res {
                            Ok(()) => Message::Venue(VenueMsg::BuyingPowerSendCompleted(Ok(()))),
                            Err(err) => Message::Venue(VenueMsg::IpcError {
                                request_id: Some(req_id_for_err),
                                code: "send_failed".to_string(),
                                message: err,
                            }),
                        },
                    )
                } else {
                    Task::none()
                };

                return Task::batch([refresh_orders, refresh_buying_power]);
            }
            // Phase U0: OrderRejected — reset submitting flag with reason + toast
            VenueMsg::OrderRejected {
                client_order_id,
                reason,
            } => {
                let main_window = self.main_window.id;
                self.active_dashboard_mut().notify_order_rejected(
                    main_window,
                    &client_order_id,
                    reason.clone(),
                );
                self.notifications.push(Toast::error(format!(
                    "注文拒否: {client_order_id} {reason}"
                )));
            }
            // ── Phase U0: 注文確認ダイアログ → ConfirmSubmit ──────────────────
            VenueMsg::ConfirmOrderEntrySubmit => {
                self.confirm_dialog = None;
                let main_window_id = self.main_window.id;
                let dashboard = self.active_dashboard_mut();
                if let Some((window_id, focused_pane)) = dashboard.focus
                    && window_id == main_window_id
                {
                    // Dispatch ConfirmSubmit to the focused pane through the
                    // standard Pane → PaneEvent → OrderEntryMsg path so that
                    // the `OrderEntryAction` handler picks up the resulting
                    // SubmitOrder and fires the IPC call.
                    return iced::Task::done(Message::Dashboard(DashboardMsg::Layout {
                        layout_id: None,
                        event: dashboard::Message::Pane(
                            main_window_id,
                            dashboard::pane::Message::PaneEvent(
                                focused_pane,
                                dashboard::pane::Event::OrderEntryMsg(
                                    crate::screen::dashboard::panel::order_entry::Message::ConfirmSubmit,
                                ),
                            ),
                        ),
                    }));
                }
                self.notifications.push(crate::widget::toast::Toast::error(
                    "注文を確定するには発注ペインをクリックしてください".to_string(),
                ));
                return Task::none();
            }
            // ── Phase U1: 注文取消確認ダイアログ → CancelOrder IPC ─────────────
            VenueMsg::ConfirmCancelOrder {
                client_order_id,
                venue_order_id,
            } => {
                self.confirm_dialog = None;
                if let Some(conn) = self.engine_connection.as_ref().cloned() {
                    return Task::perform(
                        async move {
                            conn.send(engine_client::dto::Command::CancelOrder {
                                request_id: uuid::Uuid::new_v4().to_string(),
                                venue: crate::TACHIBANA_VENUE_NAME.to_string(),
                                client_order_id,
                                venue_order_id,
                            })
                            .await
                            .map_err(|e| e.to_string())
                        },
                        |res| match res {
                            Ok(()) => Message::Venue(VenueMsg::OrderToast(Toast::info(
                                "注文取消送信".to_string(),
                            ))),
                            Err(err) => Message::Venue(VenueMsg::OrderToast(Toast::error(
                                format!("注文取消失敗: {err}"),
                            ))),
                        },
                    );
                }
                self.notifications
                    .push(Toast::error("注文取消失敗: エンジン未接続".to_string()));
                return Task::none();
            }
            // ── Phase U0: 第二暗証番号 modal ──────────────────────────────────
            VenueMsg::SecondPasswordRequired(request_id) => {
                self.second_password_modal =
                    Some(modal::second_password::SecondPasswordModal::new(request_id));
            }
            VenueMsg::DismissSecondPasswordModal => {
                self.second_password_modal = None;
                if let Some(conn) = self.engine_connection.as_ref().cloned() {
                    return Task::perform(
                        async move {
                            conn.send(engine_client::dto::Command::ForgetSecondPassword)
                                .await
                                .map_err(|e| e.to_string())
                        },
                        |res| match res {
                            Ok(()) => Message::Venue(VenueMsg::OrderToast(Toast::info(
                                "第二暗証番号を解除しました".to_string(),
                            ))),
                            Err(err) => Message::Venue(VenueMsg::OrderToast(Toast::error(
                                format!("ForgetSecondPassword 送信失敗: {err}"),
                            ))),
                        },
                    );
                }
            }
            VenueMsg::SecondPasswordModal(msg) => {
                if let Some(modal) = &mut self.second_password_modal {
                    match modal.update(msg) {
                        Some(modal::second_password::Action::Submit { value }) => {
                            let request_id = modal.request_id.clone();
                            self.second_password_modal = None;
                            if let Some(conn) = self.engine_connection.as_ref().cloned() {
                                return Task::perform(
                                    async move {
                                        conn.send(engine_client::dto::Command::SetSecondPassword {
                                            request_id,
                                            value,
                                        })
                                        .await
                                        .map_err(|e| e.to_string())
                                    },
                                    |res| match res {
                                        Ok(()) => Message::Venue(VenueMsg::OrderToast(
                                            Toast::info("第二暗証番号を送信しました".to_string()),
                                        )),
                                        Err(err) => Message::Venue(VenueMsg::OrderToast(
                                            Toast::error(format!("第二暗証番号送信失敗: {err}")),
                                        )),
                                    },
                                );
                            }
                        }
                        Some(modal::second_password::Action::Cancel) => {
                            self.second_password_modal = None;
                            if let Some(conn) = self.engine_connection.as_ref().cloned() {
                                return Task::perform(
                                    async move {
                                        conn.send(engine_client::dto::Command::ForgetSecondPassword)
                                            .await
                                            .map_err(|e| e.to_string())
                                    },
                                    |res| match res {
                                        Ok(()) => Message::Venue(VenueMsg::OrderToast(
                                            Toast::info("第二暗証番号を解除しました".to_string()),
                                        )),
                                        Err(err) => {
                                            Message::Venue(VenueMsg::OrderToast(Toast::error(
                                                format!("ForgetSecondPassword 送信失敗: {err}"),
                                            )))
                                        }
                                    },
                                );
                            }
                        }
                        None => {}
                    }
                }
            }
        }
        Task::none()
    }
}
