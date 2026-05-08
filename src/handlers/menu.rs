use iced::Task;

use crate::Message;
use crate::messages::{EngineMsg, MenuMsg, ReplayMsg, VenueMsg, WindowMsg};
use crate::widget::toast::Toast;
use crate::window;
use crate::{CURRENT_PATH, ModeSwitchGuard};

impl crate::Flowsurface {
    pub(crate) fn handle_menu(&mut self, msg: MenuMsg) -> Task<Message> {
        match msg {
            MenuMsg::Bar(bar_msg) => {
                use crate::menu_bar_state::{self, BarMessage};
                let native = if let BarMessage::Pick(ref action) = bar_msg {
                    let mapped = crate::widget_menu_bar::to_native_action(action);
                    if mapped.is_none() {
                        // H5 (F8 R1): a Pick whose `Action` does not map to a
                        // `native_menu::Action` is silently dropped. That can
                        // only happen if a new `menu::Action` variant is added
                        // without extending `to_native_action` — surface it as
                        // a warning so the missing wiring is obvious in logs.
                        log::warn!(
                            "widget_menu_bar: to_native_action returned None for {action:?} \
                             — Pick will be dropped (missing native_menu::Action mapping)"
                        );
                    }
                    mapped
                } else {
                    None
                };
                // schema 3.15: handle action variants before calling update().
                // Input-field variants are handled purely by update() below.
                let task = match &bar_msg {
                    BarMessage::Toggle(top) if self.menu_bar.open != Some(*top) => {
                        log::debug!("widget_menu_bar: open={top:?}");
                        Task::none()
                    }
                    BarMessage::Toggle(top) => {
                        log::debug!("widget_menu_bar: toggle_close reason=re_toggle top={top:?}");
                        Task::none()
                    }
                    BarMessage::Dismiss => {
                        log::debug!(
                            "widget_menu_bar: dismiss reason=outside_click open={:?}",
                            self.menu_bar.open
                        );
                        Task::none()
                    }
                    BarMessage::DismissFocusLost => {
                        log::debug!(
                            "widget_menu_bar: dismiss reason=focus_lost open={:?}",
                            self.menu_bar.open
                        );
                        Task::none()
                    }
                    BarMessage::Pick(_) => Task::none(),
                    // Input-field changes — pure state update, no side effects.
                    BarMessage::StartDateChanged(_)
                    | BarMessage::EndDateChanged(_)
                    | BarMessage::GranularityChanged(_)
                    | BarMessage::InitialCashChanged(_) => Task::none(),
                    BarMessage::PickStrategyFile => Task::perform(
                        async {
                            rfd::AsyncFileDialog::new()
                                .add_filter("Python", &["py"])
                                .set_title("戦略ファイルを選択")
                                .pick_file()
                                .await
                                .map(|h| h.path().to_owned())
                        },
                        |p| Message::Replay(ReplayMsg::NativeOpenStrategyPicked(p)),
                    ),
                    BarMessage::PressPlay => {
                        // If paused, this is a Resume action.
                        if self.replay_paused && self.replay_running {
                            if let Some(conn) = self.engine_connection.as_ref().cloned() {
                                self.replay_paused = false;
                                let has_history = self.menu_bar.replay_bar.replay_has_history;
                                let req_id = uuid::Uuid::new_v4().to_string();
                                Task::perform(
                                    async move {
                                        conn.send(engine_client::dto::Command::ResumeReplay {
                                            request_id: req_id,
                                        })
                                        .await
                                        .map_err(|e| e.to_string())
                                    },
                                    move |res| match res {
                                        Ok(()) => Message::Engine(EngineMsg::Noop),
                                        Err(e) => {
                                            log::error!(
                                                "ResumeReplay IPC failed (rolling back): {e}"
                                            );
                                            Message::Menu(MenuMsg::Bar(
                                                crate::menu_bar_state::BarMessage::ReplayPauseStateChanged {
                                                    paused: true,
                                                    has_history,
                                                },
                                            ))
                                        }
                                    },
                                )
                            } else {
                                Task::none()
                            }
                        } else {
                            // New replay session: open the form modal so the user
                            // can review / enter the instrument_id before submitting.
                            // Bar fields (prefilled by SCENARIO) are propagated into
                            // the modal so they are visible as defaults.
                            use crate::modal::replay_form::ReplayFormModal;
                            let bar = &self.menu_bar.replay_bar;
                            self.replay_form_modal = Some(ReplayFormModal {
                                instrument_id: bar.instrument_id.clone(),
                                start_date: bar.start_date.clone(),
                                end_date: bar.end_date.clone(),
                                granularity: bar.granularity.clone(),
                                strategy_file: bar.strategy_file.clone(),
                                initial_cash: bar.initial_cash.clone(),
                                validation_error: None,
                                submitting: false,
                            });
                            Task::none()
                        } // end else (new replay session)
                    }
                    BarMessage::PressPause => {
                        if let Some(conn) = self.engine_connection.as_ref().cloned() {
                            self.replay_paused = true;
                            let has_history = self.menu_bar.replay_bar.replay_has_history;
                            let req_id = uuid::Uuid::new_v4().to_string();
                            Task::perform(
                                async move {
                                    conn.send(engine_client::dto::Command::PauseReplay {
                                        request_id: req_id,
                                    })
                                    .await
                                    .map_err(|e| e.to_string())
                                },
                                move |res| match res {
                                    Ok(()) => Message::Engine(EngineMsg::Noop),
                                    // R2-H2: IPC 失敗時は楽観的更新をロールバックする。
                                    // ReplayPauseStateChanged { paused: false } で self.replay_paused
                                    // と menu_bar.replay_bar.replay_paused を両方 false に戻す。
                                    // エラーは log::error で記録する（Toast は R2-H2 要件外）。
                                    Err(e) => {
                                        log::error!("PressPause IPC failed (rolling back): {e}");
                                        Message::Menu(MenuMsg::Bar(
                                            crate::menu_bar_state::BarMessage::ReplayPauseStateChanged {
                                                paused: false,
                                                has_history,
                                            },
                                        ))
                                    }
                                },
                            )
                        } else {
                            Task::none()
                        }
                    }
                    BarMessage::PressStepForward => {
                        if let Some(conn) = self.engine_connection.as_ref().cloned() {
                            let req_id = uuid::Uuid::new_v4().to_string();
                            Task::perform(
                                async move {
                                    conn.send(engine_client::dto::Command::StepReplay {
                                        request_id: req_id,
                                    })
                                    .await
                                    .map_err(|e| e.to_string())
                                },
                                |res| match res {
                                    Ok(()) => Message::Engine(EngineMsg::Noop),
                                    Err(e) => Message::Venue(VenueMsg::OrderToast(Toast::error(
                                        format!("Step+ に失敗: {e}"),
                                    ))),
                                },
                            )
                        } else {
                            Task::none()
                        }
                    }
                    BarMessage::PressStepBackward => {
                        if let Some(conn) = self.engine_connection.as_ref().cloned() {
                            let req_id = uuid::Uuid::new_v4().to_string();
                            Task::perform(
                                async move {
                                    conn.send(engine_client::dto::Command::StepBackward {
                                        request_id: req_id,
                                    })
                                    .await
                                    .map_err(|e| e.to_string())
                                },
                                |res| match res {
                                    Ok(()) => Message::Engine(EngineMsg::Noop),
                                    Err(e) => Message::Venue(VenueMsg::OrderToast(Toast::error(
                                        format!("Step- に失敗: {e}"),
                                    ))),
                                },
                            )
                        } else {
                            Task::none()
                        }
                    }
                    // R2-M1: ReplayPauseStateChanged が来るたびに self.replay_paused も同期する。
                    // menu_bar_state::update() は menu_bar.replay_bar.replay_paused を更新するが、
                    // view() は self.replay_paused を参照するため両方を同期する必要がある。
                    BarMessage::ReplayPauseStateChanged { paused, .. } => {
                        self.replay_paused = *paused;
                        Task::none()
                    }
                    BarMessage::PressStop => Task::done(Message::Replay(ReplayMsg::StopReplayOnly)),
                    BarMessage::LivePressStop => {
                        return Task::done(Message::Replay(ReplayMsg::StopLiveStrategy));
                    }
                    BarMessage::LivePressPause | BarMessage::LivePressPlay => Task::none(),
                };
                self.menu_bar = menu_bar_state::update(self.menu_bar.clone(), bar_msg);
                if let Some(native_action) = native {
                    return Task::batch([
                        task,
                        Task::done(Message::Menu(MenuMsg::NativeAction(native_action))),
                    ]);
                }
                task
            }
            // ── Native OS menu bar ──────────────────────────────────────────
            MenuMsg::NativeSetup(raw_id) => {
                crate::native_menu::attach(raw_id, crate::app_mode());
                Task::none()
            }
            MenuMsg::NativeAction(action) => {
                use crate::native_menu::Action;
                match action {
                    Action::OpenFile => {
                        // N4-live: live モードでは `.py` 戦略ファイルを開いて
                        // LiveStrategyFormModal を起動する。
                        if crate::app_mode() == engine_client::dto::AppMode::Live {
                            return Task::perform(
                                async {
                                    rfd::AsyncFileDialog::new()
                                        .add_filter("Python", &["py"])
                                        .set_title("戦略ファイルを開く")
                                        .pick_file()
                                        .await
                                        .map(|h| h.path().to_owned())
                                },
                                |p| Message::Replay(ReplayMsg::NativeOpenStrategyPicked(p)),
                            );
                        }
                        // F6a: replay モードでは `.py` 戦略ファイルを開いて
                        // SCENARIO を Python 側で抽出する。
                        if crate::app_mode() == engine_client::dto::AppMode::Replay {
                            return Task::perform(
                                async {
                                    rfd::AsyncFileDialog::new()
                                        .add_filter("Python", &["py"])
                                        .set_title("戦略ファイルを開く")
                                        .pick_file()
                                        .await
                                        .map(|h| h.path().to_owned())
                                },
                                |p| Message::Replay(ReplayMsg::NativeOpenStrategyPicked(p)),
                            );
                        }
                        Task::perform(
                            async {
                                // Returns None if cancelled; Some((json_result, path)) otherwise.
                                let handle = rfd::AsyncFileDialog::new()
                                    .add_filter("JSON", &["json"])
                                    .set_title("設定ファイルを開く")
                                    .pick_file()
                                    .await?;
                                let path = handle.path().to_owned();
                                let bytes = handle.read().await;
                                let result = String::from_utf8(bytes).map_err(|e| e.to_string());
                                Some((result, path))
                            },
                            |result| match result {
                                None => Message::Window(WindowMsg::NativeOpenFileCancelled),
                                Some((Ok(json), path)) => {
                                    Message::Window(WindowMsg::NativeOpenFileApply { json, path })
                                }
                                Some((Err(e), _)) => Message::Venue(VenueMsg::OrderToast(
                                    Toast::error(format!("ファイルを読み込めませんでした: {e}")),
                                )),
                            },
                        )
                    }
                    Action::Save => {
                        // F-M2 (R6): suppress when a confirm_dialog is already on screen.
                        // Otherwise Ctrl+S during a dirty/overwrite confirm spawns a second
                        // rfd save_file() dialog, multi-launching the OS picker.
                        if self.confirm_dialog.is_some() {
                            return Task::none();
                        }
                        // If a current path is known, write to it directly.
                        // Otherwise fall back to the Save As dialog.
                        let path = match CURRENT_PATH.lock() {
                            Ok(guard) => guard.clone(),
                            Err(poisoned) => poisoned.into_inner().clone(),
                        };
                        if let Some(p) = path {
                            // Capture path in the closure — no shared slot needed.
                            let mut active_windows: Vec<window::Id> =
                                self.active_dashboard().popout.keys().copied().collect();
                            active_windows.push(self.main_window.id);
                            window::collect_window_specs(active_windows, move |windows| {
                                Message::Window(WindowMsg::NativeSaveAsWithSpecs {
                                    path: p.clone(),
                                    windows,
                                })
                            })
                        } else {
                            Task::perform(
                                async {
                                    rfd::AsyncFileDialog::new()
                                        .add_filter("JSON", &["json"])
                                        .set_file_name("saved-state.json")
                                        .set_title("保存先を選択")
                                        .save_file()
                                        .await
                                        .map(|h| h.path().to_owned())
                                },
                                |p| Message::Window(WindowMsg::NativeSaveAsPath(p)),
                            )
                        }
                    }
                    Action::SaveAs => {
                        // F-M2 (R6): see Action::Save — same dialog re-entrancy guard.
                        if self.confirm_dialog.is_some() {
                            return Task::none();
                        }
                        Task::perform(
                            async {
                                rfd::AsyncFileDialog::new()
                                    .add_filter("JSON", &["json"])
                                    .set_file_name("saved-state.json")
                                    .set_title("名前を付けて保存\u{2026}（Save As）")
                                    .save_file()
                                    .await
                                    .map(|h| h.path().to_owned())
                            },
                            |p| Message::Window(WindowMsg::NativeSaveAsPath(p)),
                        )
                    }
                    Action::Quit => {
                        let active_windows: Vec<window::Id> = self
                            .active_dashboard()
                            .popout
                            .keys()
                            .copied()
                            .chain(std::iter::once(self.main_window.id))
                            .collect();
                        window::collect_window_specs(active_windows, |w| {
                            Message::Window(WindowMsg::ExitRequested(w))
                        })
                    }
                    Action::SwitchMode(target) => {
                        use engine_client::dto::AppMode;
                        // Guard: don't start a mode switch if another dialog is already showing
                        if self.confirm_dialog.is_some() {
                            return Task::none();
                        }
                        let current = crate::app_mode();
                        // Same mode — no-op (menu item should be disabled, but be defensive)
                        if current == target {
                            return Task::none();
                        }
                        // Prevent re-entry: if already switching, no-op
                        let Some(guard) = ModeSwitchGuard::try_acquire() else {
                            return Task::none();
                        };
                        // M2 (lightweight): record MODE_SWITCHING acquisition so
                        // any subsequent APP_MODE / CURRENT_PATH acquisitions on
                        // this thread can be order-checked.
                        crate::lock_order_acquire("MODE_SWITCHING");
                        self.mode_switch_state = Some((target, guard));

                        match (current, target) {
                            (AppMode::Live, AppMode::Replay) => {
                                // WAL in-flight check: reject if there are unconfirmed orders
                                if crate::has_wal_in_flight_orders() {
                                    self.mode_switch_state = None;
                                    let dialog = crate::screen::ConfirmDialog::new(
                                        "未約定の注文があります。\nモードを切り替えることができません。"
                                            .to_string(),
                                        Box::new(Message::Window(WindowMsg::ToggleDialogModal(None))),
                                    )
                                    .with_confirm_btn_text("閉じる".to_string());
                                    self.confirm_dialog = Some(dialog);
                                    return Task::none();
                                }
                                // Collect window specs for dirty check
                                let mut active_windows: Vec<window::Id> =
                                    self.active_dashboard().popout.keys().copied().collect();
                                active_windows.push(self.main_window.id);
                                window::collect_window_specs(active_windows, move |windows| {
                                    Message::Window(WindowMsg::SwitchModeWithSpecs {
                                        target: AppMode::Replay,
                                        windows,
                                    })
                                })
                            }
                            (AppMode::Replay, AppMode::Live) => {
                                // Send StopReplay then wait for ReplayStopped (with timeout).
                                // M13: mode_switch_state was already populated above with
                                // target == AppMode::Live, so no separate pending field write.
                                if let Some(conn) = self.engine_connection.clone() {
                                    let request_id = uuid::Uuid::new_v4().to_string();
                                    // Send StopReplay. If send fails immediately (broken socket),
                                    // abort without waiting for the 5-second timeout.
                                    let send_task = Task::perform(
                                        async move {
                                            conn.send(engine_client::dto::Command::StopReplay {
                                                request_id,
                                            })
                                            .await
                                        },
                                        |result| match result {
                                            Ok(()) => Message::Engine(EngineMsg::Noop),
                                            Err(_) => {
                                                Message::Window(WindowMsg::ModeSwitchSendFailed)
                                            }
                                        },
                                    );
                                    // 5-second timeout
                                    let timeout_task = Task::perform(
                                        async {
                                            tokio::time::sleep(std::time::Duration::from_secs(5))
                                                .await;
                                        },
                                        |_| Message::Window(WindowMsg::ModeSwitchStopTimeout),
                                    );
                                    Task::batch([send_task, timeout_task])
                                } else {
                                    // No engine connection — just restart directly.
                                    // mode_switch_state stays Some until restart_with_mode runs;
                                    // restart_with_mode replaces *self, dropping the guard.
                                    self.restart_with_mode(AppMode::Live)
                                }
                            }
                            _ => {
                                self.mode_switch_state = None;
                                Task::none()
                            }
                        }
                    }
                }
            } // F6a: replay モードで `.py` を Open dialog で選択した結果を受け取り、
              // engine に `Command::LoadStrategyScenario` を発行する。
        }
    }
}
