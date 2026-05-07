use iced::{Task, widget::pane_grid};

use crate::Message;
use crate::messages::{DashboardMsg, ReplayMsg, VenueMsg, WindowMsg};
use crate::screen::{self, dashboard};
use crate::venue_state::{VenueEvent, VenueState};
use crate::widget::toast::Toast;
use crate::window;

impl crate::Flowsurface {
    pub(crate) fn handle_dashboard(&mut self, msg: DashboardMsg) -> Task<Message> {
        match msg {
            DashboardMsg::MarketWs(event) => {
                // M2: when the Tachibana depth stream reconnects (market
                // reopened after off-hours) while the FSM is stuck in an Error
                // state (e.g. market_closed banner), synthesize VenueReady to
                // clear the banner and re-arm the subscription bump path.
                if let exchange::Event::Connected(exchange::adapter::Exchange::TachibanaStock) =
                    &event
                    && matches!(self.tachibana_state, VenueState::Error { .. })
                {
                    log::info!(
                        "tachibana: depth stream reconnected while in Error state \
                         — synthesizing VenueReady to clear banner"
                    );
                    return Task::done(Message::Venue(VenueMsg::TachibanaEvent(VenueEvent::Ready)));
                }

                let main_window_id = self.main_window.id;
                let dashboard = self.active_dashboard_mut();

                match event {
                    exchange::Event::Connected(exchange) => {
                        log::info!("a stream connected to {exchange} WS");
                    }
                    exchange::Event::Disconnected(exchange, reason) => {
                        log::info!("a stream disconnected from {exchange} WS: {reason:?}");
                    }
                    exchange::Event::DepthReceived(stream, depth_update_t, depth) => {
                        let task = dashboard
                            .ingest_depth(&stream, depth_update_t, &depth, main_window_id)
                            .map(move |msg| {
                                Message::Dashboard(DashboardMsg::Layout {
                                    layout_id: None,
                                    event: msg,
                                })
                            });

                        return task;
                    }
                    exchange::Event::TradesReceived(stream, update_t, buffer) => {
                        let task = dashboard
                            .ingest_trades(&stream, &buffer, update_t, main_window_id)
                            .map(move |msg| {
                                Message::Dashboard(DashboardMsg::Layout {
                                    layout_id: None,
                                    event: msg,
                                })
                            });

                        if let Some(msg) = self.audio_stream.try_play_sound(&stream, &buffer) {
                            self.notifications.push(Toast::error(msg));
                        }

                        return task;
                    }
                    exchange::Event::KlineReceived(stream, kline) => {
                        return dashboard
                            .update_latest_klines(&stream, &kline, main_window_id)
                            .map(move |msg| {
                                Message::Dashboard(DashboardMsg::Layout {
                                    layout_id: None,
                                    event: msg,
                                })
                            });
                    }
                }
            }
            DashboardMsg::Layout {
                layout_id: id,
                event: msg,
            } => {
                let Some(active_layout) = self.layout_manager.active_layout_id() else {
                    log::error!("No active layout to handle dashboard message");
                    return Task::none();
                };

                let main_window = self.main_window;
                let layout_id = id.unwrap_or(active_layout.unique);
                let handles = self.handles.clone();

                if let Some(dashboard) = self.layout_manager.mut_dashboard(layout_id) {
                    let (main_task, event) =
                        dashboard.update(&handles, msg, &main_window, &layout_id);

                    let additional_task = match event {
                        Some(dashboard::Event::DistributeFetchedData {
                            layout_id,
                            pane_id,
                            data,
                            stream,
                        }) => dashboard
                            .distribute_fetched_data(main_window.id, pane_id, data, stream)
                            .map(move |msg| {
                                Message::Dashboard(DashboardMsg::Layout {
                                    layout_id: Some(layout_id),
                                    event: msg,
                                })
                            }),
                        Some(dashboard::Event::Notification(toast)) => {
                            self.notifications.push(toast);
                            Task::none()
                        }
                        Some(dashboard::Event::ResolveStreams { pane_id, streams }) => {
                            let tickers_info = self.sidebar.tickers_info();

                            let has_any_ticker_info =
                                tickers_info.values().any(|opt| opt.is_some());
                            if !has_any_ticker_info {
                                log::debug!(
                                    "Deferring persisted stream resolution for pane {pane_id}: ticker metadata not loaded yet"
                                );
                                return Task::none();
                            }

                            let resolved_streams =
                                streams.into_iter().try_fold(vec![], |mut acc, persist| {
                                    let resolver = |t: &exchange::Ticker| {
                                        tickers_info.get(t).and_then(|opt| *opt)
                                    };

                                    match persist.into_stream_kinds(resolver) {
                                        Ok(mut resolved) => {
                                            acc.append(&mut resolved);
                                            Ok(acc)
                                        }
                                        Err(err) => Err(format!(
                                            "Persisted stream still not resolvable: {err}"
                                        )),
                                    }
                                });

                            match resolved_streams {
                                Ok(resolved) => {
                                    if resolved.is_empty() {
                                        Task::none()
                                    } else {
                                        dashboard
                                            .resolve_streams(main_window.id, pane_id, resolved)
                                            .map(move |msg| {
                                                Message::Dashboard(DashboardMsg::Layout {
                                                    layout_id: None,
                                                    event: msg,
                                                })
                                            })
                                    }
                                }
                                Err(err) => {
                                    // This is typically a transient state (e.g. partial metadata, stale symbol)
                                    log::debug!("{err}");
                                    Task::none()
                                }
                            }
                        }
                        Some(dashboard::Event::RequestPalette) => {
                            let theme = self.theme.0.clone();

                            let main_window = self.main_window.id;
                            self.active_dashboard_mut()
                                .theme_updated(main_window, &theme);

                            Task::none()
                        }
                        Some(dashboard::Event::OrderEntryAction(action)) => {
                            use crate::screen::dashboard::panel::order_entry::{
                                Action, CashMarginKind,
                            };

                            fn cash_margin_tag(kind: CashMarginKind) -> String {
                                match kind {
                                    CashMarginKind::Cash => "cash_margin=cash".to_string(),
                                    CashMarginKind::MarginCreditNew => {
                                        "cash_margin=margin_credit_new".to_string()
                                    }
                                    CashMarginKind::MarginCreditRepay => {
                                        "cash_margin=margin_credit_repay".to_string()
                                    }
                                    CashMarginKind::MarginGeneralNew => {
                                        "cash_margin=margin_general_new".to_string()
                                    }
                                    CashMarginKind::MarginGeneralRepay => {
                                        "cash_margin=margin_general_repay".to_string()
                                    }
                                }
                            }

                            match action {
                                Action::OpenInstrumentPicker => Task::none(),
                                Action::RequestConfirm {
                                    instrument_id,
                                    order_side,
                                    order_type,
                                    quantity,
                                    price,
                                } => {
                                    let price_str = price
                                        .as_deref()
                                        .map(|p| format!(" @ {p}"))
                                        .unwrap_or_default();
                                    let side_str = match order_side {
                                        engine_client::dto::OrderSide::Buy => "買い",
                                        engine_client::dto::OrderSide::Sell => "売り",
                                    };
                                    let type_str = match order_type {
                                        engine_client::dto::OrderType::Market => "成行",
                                        engine_client::dto::OrderType::Limit => "指値",
                                        engine_client::dto::OrderType::StopMarket => "逆指値成行",
                                        engine_client::dto::OrderType::StopLimit => "逆指値指値",
                                        engine_client::dto::OrderType::MarketIfTouched => {
                                            "マーケットイフタッチ"
                                        }
                                        engine_client::dto::OrderType::LimitIfTouched => {
                                            "リミットイフタッチ"
                                        }
                                    };
                                    let body = format!(
                                        "{instrument_id} {side_str} {quantity}株 {type_str}{price_str}"
                                    );
                                    let dialog = screen::ConfirmDialog::new(
                                        body,
                                        Box::new(Message::Venue(VenueMsg::ConfirmOrderEntrySubmit)),
                                    )
                                    .with_confirm_btn_text("注文を発注する".to_string());
                                    self.confirm_dialog = Some(dialog);
                                    Task::none()
                                }
                                Action::SubmitOrder {
                                    request_id,
                                    venue,
                                    instrument_id,
                                    order_side,
                                    order_type,
                                    quantity,
                                    price,
                                    trigger_price,
                                    cash_margin,
                                } => {
                                    if let Some(conn) = self.engine_connection.as_ref().cloned() {
                                        let request_key =
                                            xxhash_rust::xxh3::xxh3_64(request_id.as_bytes());
                                        let order = engine_client::dto::SubmitOrderRequest {
                                            client_order_id: request_id.clone(),
                                            instrument_id,
                                            order_side,
                                            order_type,
                                            quantity,
                                            price,
                                            trigger_price,
                                            trigger_type: None,
                                            time_in_force: engine_client::dto::TimeInForce::Day,
                                            expire_time_ns: None,
                                            post_only: false,
                                            reduce_only: false,
                                            tags: vec![cash_margin_tag(cash_margin)],
                                            request_key,
                                        };
                                        let request_id_err = request_id.clone();
                                        return Task::perform(
                                            async move {
                                                conn.send(
                                                    engine_client::dto::Command::SubmitOrder {
                                                        request_id,
                                                        venue,
                                                        order,
                                                    },
                                                )
                                                .await
                                                .map_err(|e| e.to_string())
                                            },
                                            move |res| match res {
                                                Ok(()) => Message::Venue(VenueMsg::OrderToast(
                                                    Toast::info("注文送信完了".to_string()),
                                                )),
                                                Err(err) => {
                                                    Message::Venue(VenueMsg::OrderRejected {
                                                        client_order_id: request_id_err,
                                                        reason: format!("IPC 送信失敗: {err}"),
                                                    })
                                                }
                                            },
                                        );
                                    }
                                    // engine_connection が None — submitting をリセットして toast を出す
                                    Task::done(Message::Venue(VenueMsg::OrderRejected {
                                        client_order_id: request_id,
                                        reason: "エンジン未接続".to_string(),
                                    }))
                                }
                            }
                        }
                        Some(dashboard::Event::BuyingPowerAction(_action)) => {
                            // Guard: skip if a request is already in-flight to avoid
                            // overwriting the pending req_id and breaking IpcError routing.
                            if self.buying_power_request_id.is_some() {
                                return Task::none();
                            }
                            if let Some(conn) = self.engine_connection.as_ref().cloned() {
                                let req_id = uuid::Uuid::new_v4().to_string();
                                self.buying_power_request_id = Some(req_id.clone());
                                let main_window = self.main_window.id;
                                self.active_dashboard_mut()
                                    .distribute_buying_power_loading(main_window, true);
                                let req_id_for_err = req_id.clone();
                                return Task::perform(
                                    async move {
                                        conn.send(engine_client::dto::Command::GetBuyingPower {
                                            request_id: req_id,
                                            venue: crate::TACHIBANA_VENUE_NAME.to_string(),
                                        })
                                        .await
                                        .map_err(|e| e.to_string())
                                    },
                                    move |res| match res {
                                        Ok(()) => Message::Venue(
                                            VenueMsg::BuyingPowerSendCompleted(Ok(())),
                                        ),
                                        Err(err) => Message::Venue(VenueMsg::IpcError {
                                            request_id: Some(req_id_for_err),
                                            code: "send_failed".to_string(),
                                            message: err,
                                        }),
                                    },
                                );
                            }
                            // J-4: エンジン未接続時はユーザーに通知する（loading は立てない）
                            Task::done(Message::Venue(VenueMsg::OrderToast(Toast::error(
                                "エンジン未接続: 余力情報を取得できません".to_string(),
                            ))))
                        }
                        Some(dashboard::Event::OrderListAction(action)) => {
                            use crate::screen::dashboard::panel::orders::Action;
                            match action {
                                Action::RequestOrderList => {
                                    // Guard: skip if a request is already in-flight.
                                    if self.order_list_request_id.is_some() {
                                        return Task::none();
                                    }
                                    if let Some(conn) = self.engine_connection.as_ref().cloned() {
                                        let is_replay = crate::app_mode()
                                            == engine_client::dto::AppMode::Replay;
                                        let venue = if is_replay {
                                            "replay".to_string()
                                        } else {
                                            crate::TACHIBANA_VENUE_NAME.to_string()
                                        };
                                        let req_id = uuid::Uuid::new_v4().to_string();
                                        self.order_list_request_id = Some(req_id.clone());
                                        let main_window = self.main_window.id;
                                        self.active_dashboard_mut()
                                            .distribute_order_list_loading(main_window, true);
                                        return Task::perform(
                                            async move {
                                                conn.send(
                                                    engine_client::dto::Command::GetOrderList {
                                                        request_id: req_id,
                                                        venue,
                                                        filter:
                                                            engine_client::dto::OrderListFilter {
                                                                status: None,
                                                                instrument_id: None,
                                                                date: None,
                                                            },
                                                    },
                                                )
                                                .await
                                                .map_err(|e| e.to_string())
                                            },
                                            |r| Message::Venue(VenueMsg::OrderListSendCompleted(r)),
                                        );
                                    }
                                    // エンジン未接続時はユーザーに通知する（loading は立てない）
                                    Task::done(Message::Venue(VenueMsg::OrderToast(Toast::error(
                                        "エンジン未接続: 注文一覧を取得できません".to_string(),
                                    ))))
                                }
                                Action::CancelOrder {
                                    client_order_id,
                                    venue_order_id,
                                } => {
                                    let body =
                                        format!("注文 {} を取り消しますか？", client_order_id);
                                    let dialog = screen::ConfirmDialog::new(
                                        body,
                                        Box::new(Message::Venue(VenueMsg::ConfirmCancelOrder {
                                            client_order_id,
                                            venue_order_id,
                                        })),
                                    )
                                    .with_confirm_btn_text("取消実行".to_string());
                                    self.confirm_dialog = Some(dialog);
                                    Task::none()
                                }
                            }
                        }
                        Some(dashboard::Event::PositionsAction(
                            crate::screen::dashboard::panel::positions::Action::RequestPositions,
                        )) => {
                            if let Some(conn) = self.engine_connection.as_ref().cloned() {
                                if self.positions_request_id.is_none() {
                                    let req_id = uuid::Uuid::new_v4().to_string();
                                    self.positions_request_id = Some(req_id.clone());
                                    let main_window = self.main_window.id;
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
                                            Ok(()) => Message::Venue(
                                                VenueMsg::PositionsSendCompleted(Ok(())),
                                            ),
                                            Err(err) => Message::Venue(VenueMsg::IpcError {
                                                request_id: Some(req_id_for_err),
                                                code: "send_failed".to_string(),
                                                message: err,
                                            }),
                                        },
                                    );
                                }
                            } else {
                                return Task::done(Message::Venue(VenueMsg::OrderToast(
                                    Toast::error(
                                        "エンジン未接続: 保有銘柄を取得できません".to_string(),
                                    ),
                                )));
                            }
                            Task::none()
                        }
                        // N1.11-ui: relay speed button press to IPC
                        Some(dashboard::Event::ReplaySpeedAction(multiplier)) => {
                            if let Some(conn) = self.engine_connection.as_ref().cloned() {
                                let request_id = uuid::Uuid::new_v4().to_string();
                                return Task::perform(
                                    async move {
                                        conn.send(engine_client::dto::Command::SetReplaySpeed {
                                            request_id,
                                            multiplier,
                                        })
                                        .await
                                        .map_err(|e| e.to_string())
                                    },
                                    |res| match res {
                                        Ok(()) => Message::Venue(VenueMsg::OrderToast(
                                            Toast::info("再生速度を変更しました".to_string()),
                                        )),
                                        Err(err) => Message::Venue(VenueMsg::OrderToast(
                                            Toast::error(format!("再生速度変更失敗: {err}")),
                                        )),
                                    },
                                );
                            }
                            Task::none()
                        }
                        // N4.3: open OS file dialog for strategy .py file
                        Some(dashboard::Event::PickStrategyFile) => {
                            return Task::perform(
                                async {
                                    rfd::AsyncFileDialog::new()
                                        .add_filter("Python", &["py"])
                                        .pick_file()
                                        .await
                                        .map(|h| h.path().to_owned())
                                },
                                |p| Message::Replay(ReplayMsg::StrategyFilePicked(p)),
                            );
                        }
                        None => Task::none(),
                    };

                    return main_task
                        .map(move |msg| {
                            Message::Dashboard(DashboardMsg::Layout {
                                layout_id: Some(layout_id),
                                event: msg,
                            })
                        })
                        .chain(additional_task);
                }
            }
            DashboardMsg::RemoveNotification(index) => {
                self.notifications.remove(index);
            }
            // EC 約定通知 toast (Phase O2 T2.4)
            DashboardMsg::ToggleTradeFetch(checked) => {
                self.layout_manager
                    .iter_dashboards_mut()
                    .for_each(|dashboard| {
                        dashboard.toggle_trade_fetch(checked, &self.main_window);
                    });

                if checked {
                    self.confirm_dialog = None;
                }
            }
            DashboardMsg::Sidebar(message) => {
                let (task, action) = self.sidebar.update(message);

                match action {
                    Some(dashboard::sidebar::Action::TickerSelected(ticker_info, content)) => {
                        let main_window_id = self.main_window.id;
                        let handles = self.handles.clone();

                        let task = {
                            if let Some(kind) = content {
                                self.active_dashboard_mut().init_focused_pane(
                                    &handles,
                                    main_window_id,
                                    ticker_info,
                                    kind,
                                )
                            } else {
                                self.active_dashboard_mut().switch_tickers_in_group(
                                    &handles,
                                    main_window_id,
                                    ticker_info,
                                )
                            }
                        };

                        return task.map(move |msg| {
                            Message::Dashboard(DashboardMsg::Layout {
                                layout_id: None,
                                event: msg,
                            })
                        });
                    }
                    Some(dashboard::sidebar::Action::ErrorOccurred(err)) => {
                        self.notifications.push(Toast::error(err.to_string()));
                    }
                    Some(dashboard::sidebar::Action::OpenOrderPanel(kind)) => {
                        use data::layout::pane::ContentKind;
                        let main_window = self.main_window;
                        let dashboard = self.active_dashboard_mut();
                        let mut pane_added = false;
                        if let Some((window_id, focused_pane)) = dashboard.focus
                            && window_id == main_window.id
                        {
                            let new_state = dashboard::pane::State::with_kind(kind);
                            if let Some((new_pane, _)) = dashboard.panes.split(
                                pane_grid::Axis::Horizontal,
                                focused_pane,
                                new_state,
                            ) {
                                dashboard.focus = Some((window_id, new_pane));
                                pane_added = true;
                            }
                        } else {
                            self.notifications.push(Toast::error(
                                "注文パネルを開くにはまずペインを選択してください".to_string(),
                            ));
                        }

                        // VenueReady 後にペインを追加した場合の自動フェッチキャッチアップ。
                        // VenueReady 時の自動フェッチは既存ペインだけを対象とするため、
                        // 後から追加したペインはここでフェッチする。
                        // reconnect による VenueReady 再発火も同じ経路をカバーする。
                        if pane_added
                            && kind == ContentKind::BuyingPower
                            && self.tachibana_state.is_ready()
                            && self.buying_power_request_id.is_none()
                        {
                            if let Some(conn) = self.engine_connection.as_ref().cloned() {
                                let req_id = uuid::Uuid::new_v4().to_string();
                                self.buying_power_request_id = Some(req_id.clone());
                                let main_window = self.main_window.id;
                                self.active_dashboard_mut()
                                    .distribute_buying_power_loading(main_window, true);
                                let req_id_for_err = req_id.clone();
                                return Task::batch(vec![
                                    task.map(|m| Message::Dashboard(DashboardMsg::Sidebar(m))),
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
                                            Ok(()) => Message::Venue(
                                                VenueMsg::BuyingPowerSendCompleted(Ok(())),
                                            ),
                                            Err(err) => Message::Venue(VenueMsg::IpcError {
                                                request_id: Some(req_id_for_err),
                                                code: "send_failed".to_string(),
                                                message: err,
                                            }),
                                        },
                                    ),
                                ]);
                            } else {
                                log::warn!(
                                    "[BuyingPower auto-fetch] tachibana is ready but \
                                     engine_connection is None"
                                );
                            }
                        }

                        if pane_added
                            && kind == ContentKind::Positions
                            && self.tachibana_state.is_ready()
                            && self.positions_request_id.is_none()
                        {
                            if let Some(conn) = self.engine_connection.as_ref().cloned() {
                                let req_id = uuid::Uuid::new_v4().to_string();
                                self.positions_request_id = Some(req_id.clone());
                                let main_window = self.main_window.id;
                                self.active_dashboard_mut()
                                    .distribute_positions_loading(main_window, true);
                                let req_id_for_err = req_id.clone();
                                return Task::batch(vec![
                                    task.map(|m| Message::Dashboard(DashboardMsg::Sidebar(m))),
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
                                            Ok(()) => Message::Venue(
                                                VenueMsg::PositionsSendCompleted(Ok(())),
                                            ),
                                            Err(err) => Message::Venue(VenueMsg::IpcError {
                                                request_id: Some(req_id_for_err),
                                                code: "send_failed".to_string(),
                                                message: err,
                                            }),
                                        },
                                    ),
                                ]);
                            } else {
                                log::warn!(
                                    "[Positions auto-fetch] tachibana is ready but \
                                     engine_connection is None"
                                );
                            }
                        }

                        return task.map(|m| Message::Dashboard(DashboardMsg::Sidebar(m)));
                    }
                    Some(dashboard::sidebar::Action::RequestTachibanaLogin(trigger)) => {
                        let task = task.map(|m| Message::Dashboard(DashboardMsg::Sidebar(m)));
                        return Task::batch(vec![
                            task,
                            iced::Task::done(Message::Venue(VenueMsg::RequestTachibanaLogin(
                                trigger,
                            ))),
                        ]);
                    }
                    None => {}
                }

                return task.map(|m| Message::Dashboard(DashboardMsg::Sidebar(m)));
            }
            DashboardMsg::ApplyVolumeSizeUnit(pref) => {
                self.volume_size_unit = pref;
                self.confirm_dialog = None;

                let mut active_windows: Vec<window::Id> =
                    self.active_dashboard().popout.keys().copied().collect();
                active_windows.push(self.main_window.id);

                return window::collect_window_specs(active_windows, |windows| {
                    Message::Window(WindowMsg::RestartRequested(Some(windows)))
                });
            }
        }
        Task::none()
    }
}
