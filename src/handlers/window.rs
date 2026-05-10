use iced::Task;

use crate::Message;
use crate::messages::{DashboardMsg, WindowMsg};
use crate::screen;
use crate::widget::toast::Toast;
use crate::window;
use crate::{CURRENT_PATH, SaveError};

impl crate::Flowsurface {
    pub(crate) fn handle_window(&mut self, msg: WindowMsg) -> Task<Message> {
        match msg {
            WindowMsg::Tick(now) => {
                let main_window_id = self.main_window.id;
                let handles = self.handles.clone();

                return self
                    .active_dashboard_mut()
                    .tick(&handles, now, main_window_id)
                    .map(move |msg| {
                        Message::Dashboard(DashboardMsg::Layout {
                            layout_id: None,
                            event: msg,
                        })
                    });
            }
            WindowMsg::WindowEvent(event) => match event {
                crate::window::Event::CloseRequested(window) => {
                    let main_window = self.main_window.id;
                    let dashboard = self.active_dashboard_mut();

                    if window != main_window {
                        dashboard.popout.remove(&window);
                        return window::close(window);
                    }

                    let mut active_windows = dashboard
                        .popout
                        .keys()
                        .copied()
                        .collect::<Vec<window::Id>>();
                    active_windows.push(main_window);

                    return window::collect_window_specs(active_windows, |w| {
                        Message::Window(WindowMsg::ExitRequested(w))
                    });
                }
            },
            // F4 BC-9 fix: set dirty baseline after startup so edits before the first
            // explicit Save are detected as dirty (ケース 3/4).
            WindowMsg::SetDirtyBaseline(windows) => {
                if let Some(json) = self.build_state_json(&windows) {
                    self.last_saved_bytes = Some(json.into_bytes());
                }
                return Task::none();
            }
            WindowMsg::ExitRequested(windows) => {
                // HIGH fix: another dialog is already visible — ignore this close request
                // until the user resolves it. Prevents F4 bypass via overlapping dialogs.
                if self.confirm_dialog.is_some() {
                    return Task::none();
                }
                // F4: check dirty before exiting (live mode only).
                // Replay mode never writes state, so skip the check there.
                let is_live = crate::app_mode() == engine_client::dto::AppMode::Live;
                if is_live && self.is_dirty(&windows) {
                    // Store window specs so Discard/SaveAndExit can proceed later.
                    self.pending_exit_windows = Some(windows);
                    let dialog = screen::ConfirmDialog::new(
                        "未保存の変更があります。".to_string(),
                        Box::new(Message::Window(WindowMsg::DiscardAndExit)),
                    )
                    .with_confirm_btn_text("破棄して終了".to_string())
                    .with_save_action(
                        Message::Window(WindowMsg::SaveAndExit),
                        "保存して終了".to_string(),
                    );
                    self.confirm_dialog = Some(dialog);
                    return Task::none();
                }
                // Clean exit: auto-save. Failure is shown as a toast but does NOT
                // abort exit — the user explicitly asked to quit.
                if !self.save_state_to_disk(&windows) {
                    self.notifications
                        .push(Toast::error("自動保存に失敗しました".to_string()));
                }
                return iced::exit();
            }
            WindowMsg::DiscardAndExit => {
                // User chose "破棄して終了" — do NOT save. saved-state.json stays as-is
                // so the next launch restores the state from before the discarded edits.
                self.confirm_dialog = None;
                self.pending_exit_windows = None;
                return iced::exit();
            }
            WindowMsg::SaveAndExit => {
                // User chose "保存して終了" — save then exit.
                // BC-5: if save fails, abort the exit and show an error (do not discard data).
                self.confirm_dialog = None;
                // F-M1 (R6): require a prior ExitRequested dirty check. `unwrap_or_default()`
                // here would silently turn `None` into an empty HashMap and corrupt the saved
                // layout. Log a warning and abort instead.
                let Some(windows) = self.pending_exit_windows.take() else {
                    log::warn!(
                        "[SaveAndExit] pending_exit_windows is None — SaveAndExit dispatched without prior ExitRequested dirty check"
                    );
                    return Task::none();
                };

                let Some(json) = self.build_state_json(&windows) else {
                    // replay mode has no state to save
                    return iced::exit();
                };

                // If a named document is open, write to it first (primary save target).
                let current_path = match CURRENT_PATH.lock() {
                    Ok(g) => g.clone(),
                    Err(poisoned) => poisoned.into_inner().clone(),
                };
                if let Some(p) = &current_path {
                    if let Err(e) = std::fs::write(p, json.as_bytes()) {
                        crate::log_save_error(&SaveError::IoError(e.kind()), p);
                        self.notifications.push(Toast::error(
                            "保存に失敗しました。再試行してください。".to_string(),
                        ));
                        self.pending_exit_windows = Some(windows);
                        let dialog = screen::ConfirmDialog::new(
                            "未保存の変更があります。".to_string(),
                            Box::new(Message::Window(WindowMsg::DiscardAndExit)),
                        )
                        .with_confirm_btn_text("破棄して終了".to_string())
                        .with_save_action(
                            Message::Window(WindowMsg::SaveAndExit),
                            "保存して終了".to_string(),
                        );
                        self.confirm_dialog = Some(dialog);
                        return Task::none();
                    }
                    // F-H1 (R6 / A-7): the named-doc write succeeded — update the dirty
                    // baseline immediately. Without this, a subsequent saved-state.json
                    // failure still triggers `iced::exit()` (current_path.is_some() branch)
                    // but leaves `last_saved_bytes` stale, violating the
                    // "明示 Save 直後に last_saved_bytes 更新" contract.
                    self.last_saved_bytes = Some(json.as_bytes().to_vec());
                }

                // Also write to saved-state.json (auto-restore slot) and update last_saved_bytes.
                // If CURRENT_PATH was successfully written, a saved-state.json failure is non-fatal
                // (data is safe in the named doc). If there is no CURRENT_PATH, saved-state.json
                // is the only copy — abort on failure.
                let saved_ok = self.write_json_to_saved_state_disk(&json);
                if current_path.is_some() || saved_ok {
                    return iced::exit();
                }

                self.notifications.push(Toast::error(
                    "保存に失敗しました。再試行してください。".to_string(),
                ));
                self.pending_exit_windows = Some(windows);
                let dialog = screen::ConfirmDialog::new(
                    "未保存の変更があります。".to_string(),
                    Box::new(Message::Window(WindowMsg::DiscardAndExit)),
                )
                .with_confirm_btn_text("破棄して終了".to_string())
                .with_save_action(
                    Message::Window(WindowMsg::SaveAndExit),
                    "保存して終了".to_string(),
                );
                self.confirm_dialog = Some(dialog);
                return Task::none();
            }
            WindowMsg::RestartRequested(Some(windows)) => {
                self.save_state_to_disk(&windows);
                return self.restart();
            }
            WindowMsg::RestartRequested(None) => {
                self.confirm_dialog = None;

                let mut active_windows = self
                    .active_dashboard()
                    .popout
                    .keys()
                    .copied()
                    .collect::<Vec<window::Id>>();
                active_windows.push(self.main_window.id);

                return window::collect_window_specs(active_windows, |windows| {
                    Message::Window(WindowMsg::RestartRequested(Some(windows)))
                });
            }
            WindowMsg::GoBack => {
                let main_window = self.main_window.id;

                #[cfg(target_os = "linux")]
                if self.menu_bar.open.is_some() {
                    log::debug!(
                        "widget_menu_bar: dismiss reason=esc open={:?}",
                        self.menu_bar.open
                    );
                    self.menu_bar = crate::menu_bar_state::update(
                        self.menu_bar.clone(),
                        crate::menu_bar_state::BarMessage::Dismiss,
                    );
                    return Task::none();
                }

                if self.confirm_dialog.is_some() {
                    // F5/M: if we're dismissing the overwrite confirm during a save-then-open
                    // flow, restore the dirty-confirm so the user can retry or discard instead
                    // of silently losing the pending open target.
                    if self
                        .confirm_dialog
                        .as_ref()
                        .is_some_and(|d| d.on_save.is_none())
                        && self.pending_open_file.is_some()
                    {
                        self.confirm_dialog = Some(
                            screen::ConfirmDialog::new(
                                "未保存の変更があります。".to_string(),
                                Box::new(Message::Window(WindowMsg::DiscardAndOpenFile)),
                            )
                            .with_confirm_btn_text("破棄して開く".to_string())
                            .with_save_action(
                                Message::Window(WindowMsg::SaveAndOpenFile),
                                "保存して開く".to_string(),
                            ),
                        );
                        return Task::none();
                    }
                    self.confirm_dialog = None;
                    self.pending_open_file = None;
                    self.pending_exit_windows = None;
                    // F7: release mode-switch guard so the next SwitchMode attempt is not
                    // permanently blocked after the user dismisses the dirty-confirm dialog.
                    self.mode_switch_state = None;
                } else if self.sidebar.active_menu().is_some() {
                    self.sidebar.set_menu(None);
                } else {
                    let dashboard = self.active_dashboard_mut();

                    if dashboard.go_back(main_window) {
                        return Task::none();
                    } else if dashboard.focus.is_some() {
                        dashboard.focus = None;
                    } else {
                        self.sidebar.hide_tickers_table();
                    }
                }
            }
            WindowMsg::NativeSaveAsPath(Some(path)) => {
                if self.confirm_dialog.is_some() {
                    return Task::none();
                }
                // F5: if the target file already exists, ask the user to confirm overwrite.
                if path.exists() {
                    let file_name = path
                        .file_name()
                        .map(|n| n.to_string_lossy().into_owned())
                        .unwrap_or_else(|| path.display().to_string());
                    let dialog = screen::ConfirmDialog::new(
                        format!("「{file_name}」はすでに存在します。上書きしますか？"),
                        // Carry path in the message so no shared slot is needed.
                        Box::new(Message::Window(WindowMsg::ConfirmSaveAsOverwrite { path })),
                    )
                    .with_confirm_btn_text("上書き保存".to_string());
                    self.confirm_dialog = Some(dialog);
                    return Task::none();
                }
                let mut active_windows: Vec<window::Id> =
                    self.active_dashboard().popout.keys().copied().collect();
                active_windows.push(self.main_window.id);
                return window::collect_window_specs(active_windows, move |windows| {
                    Message::Window(WindowMsg::NativeSaveAsWithSpecs {
                        path: path.clone(),
                        windows,
                    })
                });
            }
            WindowMsg::ConfirmSaveAsOverwrite { path } => {
                // M-3: guard against arriving without an open dialog (race condition).
                // Mirrors the confirm_dialog.is_none() pattern used in NativeOpenFilePendingCheck
                // and ExitRequested to prevent double-processing.
                if self.confirm_dialog.is_none() {
                    return Task::none();
                }
                self.confirm_dialog = None;
                // Proceed to collect specs with the path carried in the message.
                let mut active_windows: Vec<window::Id> =
                    self.active_dashboard().popout.keys().copied().collect();
                active_windows.push(self.main_window.id);
                return window::collect_window_specs(active_windows, move |windows| {
                    Message::Window(WindowMsg::NativeSaveAsWithSpecs {
                        path: path.clone(),
                        windows,
                    })
                });
            }
            WindowMsg::NativeSaveAsPath(None) => {
                // Save As dialog cancelled. If a "save-then-open" flow was in progress
                // (SaveAndOpenFile triggered this when CURRENT_PATH was None), restore the
                // confirm dialog so the user can choose Discard or retry Save.
                if self.pending_open_file.is_some() {
                    let dialog = screen::ConfirmDialog::new(
                        "未保存の変更があります。".to_string(),
                        Box::new(Message::Window(WindowMsg::DiscardAndOpenFile)),
                    )
                    .with_confirm_btn_text("破棄して開く".to_string())
                    .with_save_action(
                        Message::Window(WindowMsg::SaveAndOpenFile),
                        "保存して開く".to_string(),
                    );
                    self.confirm_dialog = Some(dialog);
                }
                return Task::none();
            }
            WindowMsg::NativeSaveAsWithSpecs { path, windows } => {
                // H-5: build JSON once and reuse for both the user path and saved-state.json,
                // avoiding a second build_state_json call inside save_state_to_disk.
                let Some(json) = self.build_state_json(&windows) else {
                    log::warn!(
                        "[NativeSaveAsWithSpecs] build_state_json returned None \
                         (replay mode or APP_MODE uninitialised) — skipping write to {}",
                        path.display()
                    );
                    return Task::none();
                };
                // H-3: offload both blocking writes to a tokio async task so the
                // iced update loop is never blocked by disk I/O.
                let json_bytes = json.into_bytes();
                let user_path = path.clone();
                let saved_state_path = data::SAVED_STATE_PATH;
                return Task::perform(
                    async move {
                        // A-3: write to user-specified path first; only on success
                        // also write saved-state.json (keep auto-restore slot in sync).
                        match tokio::fs::write(&user_path, &json_bytes).await {
                            Ok(()) => {
                                let saved_state_ok =
                                    tokio::fs::write(saved_state_path, &json_bytes)
                                        .await
                                        .map_err(|e| {
                                            log::warn!(
                                                "[NativeSaveComplete] failed to write \
                                                 saved-state.json: {e}"
                                            );
                                        })
                                        .is_ok();
                                (user_path, json_bytes, None, saved_state_ok)
                            }
                            Err(e) => (user_path, json_bytes, Some(e.kind()), false),
                        }
                    },
                    |(user_path, json_bytes, error_kind, saved_state_ok)| {
                        Message::Window(WindowMsg::NativeSaveComplete {
                            user_path,
                            json_bytes,
                            error_kind,
                            saved_state_ok,
                        })
                    },
                );
            }
            // H-3: completion handler for the async NativeSaveAsWithSpecs Task::perform.
            WindowMsg::NativeSaveComplete {
                user_path,
                json_bytes,
                error_kind,
                saved_state_ok,
            } => {
                match error_kind {
                    None => {
                        // A-7: update dirty baseline so is_dirty() returns false.
                        self.last_saved_bytes = Some(json_bytes);
                        // Record as current document.
                        match CURRENT_PATH.lock() {
                            Ok(mut guard) => *guard = Some(user_path.clone()),
                            Err(poisoned) => *poisoned.into_inner() = Some(user_path.clone()),
                        }
                        log::info!("Saved state to {}", user_path.display());
                        self.notifications.push(Toast::info(format!(
                            "保存しました: {}",
                            user_path.display()
                        )));
                        // Notify if auto-restore slot (saved-state.json) failed — the named doc
                        // is safe, but next startup will restore from an older state.
                        if !saved_state_ok {
                            self.notifications.push(Toast::error(
                                "自動復元スロット (saved-state.json) の更新に失敗しました。\
                                 次回起動時の復元が古い状態になる可能性があります。"
                                    .to_string(),
                            ));
                        }
                        // If SaveAndOpenFile triggered this save (CURRENT_PATH=None path),
                        // complete the open now that the current state is safely written.
                        if let Some((pending_json, pending_path, _windows)) =
                            self.pending_open_file.take()
                        {
                            if let Err(e) =
                                data::write_json_to_file(&pending_json, data::SAVED_STATE_PATH)
                            {
                                log::warn!("Failed to write imported state: {e}");
                                self.notifications.push(Toast::error(format!(
                                    "ファイルの適用に失敗しました: {e}"
                                )));
                                return Task::none();
                            }
                            match CURRENT_PATH.lock() {
                                Ok(mut guard) => *guard = Some(pending_path),
                                Err(poisoned) => *poisoned.into_inner() = Some(pending_path),
                            }
                            return self.restart();
                        }
                    }
                    Some(kind) => {
                        // BC-5: classify as IoError → WARN level.
                        crate::log_save_error(&SaveError::IoError(kind), &user_path);
                        self.notifications
                            .push(Toast::error("保存に失敗しました".to_string()));
                        // If SaveAndOpenFile triggered this save (CURRENT_PATH=None path),
                        // pending_open_file is still set but confirm_dialog is None.
                        // Restore the dialog so the user can retry or discard; without this
                        // the next Open skips F4 because pending_open_file.is_some() blocks
                        // the dirty-check condition at NativeOpenFilePendingCheck.
                        if self.pending_open_file.is_some() {
                            let dialog = screen::ConfirmDialog::new(
                                "未保存の変更があります。".to_string(),
                                Box::new(Message::Window(WindowMsg::DiscardAndOpenFile)),
                            )
                            .with_confirm_btn_text("破棄して開く".to_string())
                            .with_save_action(
                                Message::Window(WindowMsg::SaveAndOpenFile),
                                "保存して開く".to_string(),
                            );
                            self.confirm_dialog = Some(dialog);
                        }
                    }
                }
                return Task::none();
            }
            WindowMsg::NativeOpenFileCancelled => {
                return Task::none();
            }
            WindowMsg::NativeOpenFileApply { json, path } => {
                // Validate the JSON first; reject bad files early before any state change.
                match serde_json::from_str::<data::State>(&json) {
                    Ok(_) => {
                        // F4 fix: collect real window specs before the dirty check so that
                        // last_saved_bytes (which includes window positions) compares correctly.
                        // An empty HashMap would produce main_window_spec=None, always appearing
                        // dirty even immediately after a save.
                        let mut active_windows: Vec<window::Id> =
                            self.active_dashboard().popout.keys().copied().collect();
                        active_windows.push(self.main_window.id);
                        return window::collect_window_specs(active_windows, move |windows| {
                            Message::Window(WindowMsg::NativeOpenFilePendingCheck {
                                json: json.clone(),
                                path: path.clone(),
                                windows,
                            })
                        });
                    }
                    Err(e) => {
                        self.notifications
                            .push(Toast::error(format!("無効な設定ファイルです: {e}")));
                        return Task::none();
                    }
                }
            }
            WindowMsg::NativeOpenFilePendingCheck {
                json,
                path,
                windows,
            } => {
                // HIGH fix: another dialog is already visible — silently drop this request
                // to prevent F4 bypass via overlapping dialogs.
                if self.confirm_dialog.is_some() {
                    return Task::none();
                }
                // F4: dirty check with real window specs (avoids false positives from empty HashMap).
                if self.is_dirty(&windows) && self.pending_open_file.is_none() {
                    // Store windows so SaveAndOpenFile can build state JSON for the named doc.
                    self.pending_open_file = Some((json, path, windows));
                    let dialog = screen::ConfirmDialog::new(
                        "未保存の変更があります。".to_string(),
                        Box::new(Message::Window(WindowMsg::DiscardAndOpenFile)),
                    )
                    .with_confirm_btn_text("破棄して開く".to_string())
                    .with_save_action(
                        Message::Window(WindowMsg::SaveAndOpenFile),
                        "保存して開く".to_string(),
                    );
                    self.confirm_dialog = Some(dialog);
                    return Task::none();
                }
                if let Err(e) = data::write_json_to_file(&json, data::SAVED_STATE_PATH) {
                    // BC-5: write failure is an OS-level I/O error → WARN level.
                    log::warn!("Failed to write imported state: {e}");
                    self.notifications
                        .push(Toast::error(format!("ファイルの適用に失敗しました: {e}")));
                    return Task::none();
                }
                match CURRENT_PATH.lock() {
                    Ok(mut guard) => *guard = Some(path),
                    Err(poisoned) => *poisoned.into_inner() = Some(path),
                }
                return self.restart();
            }
            WindowMsg::DiscardAndOpenFile => {
                self.confirm_dialog = None;
                let Some((json, path, _windows)) = self.pending_open_file.take() else {
                    return Task::none();
                };
                if let Err(e) = data::write_json_to_file(&json, data::SAVED_STATE_PATH) {
                    // BC-5: write failure is an OS-level I/O error → WARN level.
                    log::warn!("Failed to write imported state: {e}");
                    self.notifications
                        .push(Toast::error(format!("ファイルの適用に失敗しました: {e}")));
                    return Task::none();
                }
                match CURRENT_PATH.lock() {
                    Ok(mut guard) => *guard = Some(path),
                    Err(poisoned) => *poisoned.into_inner() = Some(path),
                }
                return self.restart();
            }
            WindowMsg::SaveAndOpenFile => {
                // User chose "保存して開く" — save the current document first, then load the new
                // file. BC-5: if any save step fails, abort and restore the dialog.
                self.confirm_dialog = None;
                let Some((json, new_path, windows)) = self.pending_open_file.take() else {
                    return Task::none();
                };
                let current_path = match CURRENT_PATH.lock() {
                    Ok(g) => g.clone(),
                    Err(poisoned) => poisoned.into_inner().clone(),
                };
                if let Some(p) = &current_path {
                    match self.build_state_json(&windows) {
                        Some(current_json) => {
                            if let Err(e) = std::fs::write(p, current_json.as_bytes()) {
                                // BC-5: abort open; named doc save failed.
                                crate::log_save_error(&SaveError::IoError(e.kind()), p);
                                self.notifications.push(Toast::error(
                                    "保存に失敗しました。再試行してください。".to_string(),
                                ));
                                self.pending_open_file = Some((json, new_path, windows));
                                let dialog = screen::ConfirmDialog::new(
                                    "未保存の変更があります。".to_string(),
                                    Box::new(Message::Window(WindowMsg::DiscardAndOpenFile)),
                                )
                                .with_confirm_btn_text("破棄して開く".to_string())
                                .with_save_action(
                                    Message::Window(WindowMsg::SaveAndOpenFile),
                                    "保存して開く".to_string(),
                                );
                                self.confirm_dialog = Some(dialog);
                                return Task::none();
                            }
                            // MEDIUM fix: update dirty baseline so a subsequent Quit/Open after a
                            // failed saved-state.json write does not re-trigger a spurious dialog.
                            self.last_saved_bytes = Some(current_json.into_bytes());
                        }
                        None => { /* replay mode — no JSON to save, proceed with open */ }
                    }
                } else {
                    // No named document: open Save As dialog so the user can name the current
                    // state. Keep pending_open_file; NativeSaveComplete will pick it up and
                    // complete the open after the user-chosen path is written.
                    self.pending_open_file = Some((json, new_path, windows));
                    return Task::perform(
                        async {
                            rfd::AsyncFileDialog::new()
                                .add_filter("JSON", &["json"])
                                .set_file_name("saved-state.json")
                                .set_title("現在の設定を保存")
                                .save_file()
                                .await
                                .map(|handle| handle.path().to_owned())
                        },
                        |p| Message::Window(WindowMsg::NativeSaveAsPath(p)),
                    );
                }
                // R2-M1 (revert of R6 F-M4): saved-state.json is the file that
                // `Flowsurface::new()` reads on restart. If this mirror write fails
                // we MUST NOT update CURRENT_PATH and MUST NOT restart() — doing so
                // would reload the OLD saved-state.json while CURRENT_PATH points to
                // the NEW file, so the next Ctrl+S would silently overwrite the new
                // file with the old layout. Abort cleanly with warn + toast and let
                // the user retry from the menu.
                return match data::write_json_to_file(&json, data::SAVED_STATE_PATH) {
                    Ok(()) => {
                        match CURRENT_PATH.lock() {
                            Ok(mut guard) => *guard = Some(new_path),
                            Err(poisoned) => *poisoned.into_inner() = Some(new_path),
                        }
                        self.restart()
                    }
                    Err(e) => {
                        log::warn!(
                            "[SaveAndOpenFile] failed to write saved-state.json after named-doc save: {e} — aborting open to keep CURRENT_PATH consistent"
                        );
                        self.notifications.push(Toast::error(
                            "saved-state.json への書き込みに失敗しました。開く処理を中止します。"
                                .to_string(),
                        ));
                        Task::none()
                    }
                };
            }
            // F7: SwitchModeWithSpecs — window specs collected, perform dirty check for live→replay
            WindowMsg::SwitchModeWithSpecs { target, windows } => {
                use engine_client::dto::AppMode;
                // If mode switch guard was released (e.g. stale message), ignore.
                // L2: log at debug level so stale window-spec callbacks are
                // observable without spamming the user log.
                if self.mode_switch_state.is_none() {
                    log::debug!(
                        "[F7] SwitchModeWithSpecs ignored: mode_switch_state is None \
                         (likely stale window-spec callback after dialog dismiss)"
                    );
                    return Task::none();
                }
                if crate::app_mode() == AppMode::Live && self.is_dirty(&windows) {
                    // Update pending target (mode_switch_state target half is rewritten;
                    // guard half is preserved by reusing the existing guard).
                    if let Some((t, _g)) = self.mode_switch_state.as_mut() {
                        *t = target;
                    }
                    let dialog = screen::ConfirmDialog::new(
                        "未保存の変更があります。".to_string(),
                        Box::new(Message::Window(WindowMsg::DiscardAndSwitchMode)),
                    )
                    .with_confirm_btn_text("破棄してモード切替".to_string())
                    .with_save_action(
                        Message::Window(WindowMsg::SaveAndSwitchMode),
                        "保存してモード切替".to_string(),
                    );
                    self.confirm_dialog = Some(dialog);
                    return Task::none();
                }
                // Not dirty: save then restart. Abort if save fails (plan: 保存失敗 → 切替中止).
                if !self.save_state_to_disk(&windows) {
                    self.mode_switch_state = None;
                    // M-rust2 / M-4: surface a modal alert (parity with
                    // `SwitchModeSaveComplete`). Modal is the *only* notification
                    // surface — no Toast — to avoid the M-4 "2 surfaces saying
                    // overlapping things" problem.
                    let dialog = screen::ConfirmDialog::new(
                        "保存に失敗したためモード切替を中止しました。\nディスクの空き容量・権限を確認してください。"
                            .to_string(),
                        Box::new(Message::Window(WindowMsg::ToggleDialogModal(None))),
                    )
                    .with_confirm_btn_text("閉じる".to_string());
                    self.confirm_dialog = Some(dialog);
                    return Task::none();
                }
                return self.restart_with_mode(target);
            }
            // F7: user chose "破棄してモード切替"
            WindowMsg::DiscardAndSwitchMode => {
                self.confirm_dialog = None;
                // H1: stale early-return must release the guard so the next
                // SwitchMode is not permanently blocked.
                let Some((target, _guard)) = self.mode_switch_state.take() else {
                    return Task::none();
                };
                return self.restart_with_mode(target);
            }
            // F7: user chose "保存してモード切替" — collect window specs for save, then restart
            WindowMsg::SaveAndSwitchMode => {
                self.confirm_dialog = None;
                // H1: stale early-return — mode_switch_state must be Some here,
                // otherwise the dialog firing was a stale message; release nothing
                // (there is nothing to release) and return.
                // Note: do NOT take() the guard yet; it must outlive
                // collect_window_specs and the SwitchModeSaveComplete dispatch.
                let target = match self.mode_switch_state.as_ref() {
                    Some((t, _)) => *t,
                    None => return Task::none(),
                };
                // Collect current window specs for a proper save.
                // IMPORTANT: must NOT re-route through SwitchModeWithSpecs here because that
                // path re-checks is_dirty() — since we haven't saved yet it would still be true,
                // causing an infinite dialog loop. Route through SwitchModeSaveComplete instead
                // to unconditionally save and restart (F7 review fix 2026-05-04).
                let mut active_windows: Vec<window::Id> =
                    self.active_dashboard().popout.keys().copied().collect();
                active_windows.push(self.main_window.id);
                return window::collect_window_specs(active_windows, move |windows| {
                    Message::Window(WindowMsg::SwitchModeSaveComplete { target, windows })
                });
            }
            // F7: window specs collected for the "保存してモード切替" path — save then restart.
            WindowMsg::SwitchModeSaveComplete { target, windows } => {
                // Abort if save fails (plan: 保存失敗 → 切替中止).
                if !self.save_state_to_disk(&windows) {
                    self.mode_switch_state = None;
                    // M-4: modal を唯一の通知面にする。以前は Toast + Modal の二重表示
                    // にしていたが、Toast が自動消滅したあとも Modal が残るため、
                    // ユーザーが原因を読む面が一つに絞られた方が明確であり、
                    // Toast 側の残留テキストとも齟齬がない。
                    let dialog = screen::ConfirmDialog::new(
                        "保存に失敗したためモード切替を中止しました。\nディスクの空き容量・権限を確認してください。"
                            .to_string(),
                        Box::new(Message::Window(WindowMsg::ToggleDialogModal(None))),
                    )
                    .with_confirm_btn_text("閉じる".to_string());
                    self.confirm_dialog = Some(dialog);
                    return Task::none();
                }
                return self.restart_with_mode(target);
            }
            // User clicked "リプレイ停止" — stop replay without changing app mode.
            // Sends StopReplay (with the same 5s timeout as the F7 mode-switch flow);
            // the ack / timeout / EngineBusy events are routed through the shared
            // `ModeSwitch*` handlers, distinguished by `replay_stop_only_pending`.
            WindowMsg::ModeSwitchStopAcked => {
                self.replay_running = false;
                self.replay_paused = false;
                self.menu_bar.replay_bar.replay_has_history = false;
                if let Some((target, _guard)) = self.mode_switch_state.take() {
                    self.replay_stop_only_pending = false;
                    return self.restart_with_mode(target);
                }
                if std::mem::take(&mut self.replay_stop_only_pending) {
                    self.notifications
                        .push(Toast::info("リプレイを停止しました".to_string()));
                }
                return Task::none();
            }
            // F7: 5-second StopReplay timeout — send ForceStopReplay fallback
            // Shared between mode-switch and stop-only flows.
            WindowMsg::ModeSwitchStopTimeout => {
                log::warn!("[F7] StopReplay timed out — sending ForceStopReplay fallback");
                if self.mode_switch_state.is_none() && !self.replay_stop_only_pending {
                    // Already handled (ReplayStopped arrived before timeout) — ignore
                    return Task::none();
                }
                if let Some(conn) = self.engine_connection.clone() {
                    let request_id = uuid::Uuid::new_v4().to_string();
                    let force_task = Task::perform(
                        async move {
                            conn.send(engine_client::dto::Command::ForceStopReplay { request_id })
                                .await
                        },
                        |result| match result {
                            Ok(()) => Message::Engine(crate::messages::EngineMsg::Noop),
                            Err(_) => Message::Window(WindowMsg::ModeSwitchSendFailed),
                        },
                    );
                    let timeout_task = Task::perform(
                        async {
                            tokio::time::sleep(std::time::Duration::from_secs(2)).await;
                        },
                        |_| Message::Window(WindowMsg::ModeSwitchForceStopTimeout),
                    );
                    return Task::batch([force_task, timeout_task]);
                } else {
                    // No connection — release guard / pending flag and show error dialog
                    let was_mode_switch = self.mode_switch_state.take().is_some();
                    let was_stop_only = std::mem::take(&mut self.replay_stop_only_pending);
                    let body = if was_mode_switch {
                        "モード切替に失敗しました。\nエンジンとの接続が切れています。"
                    } else if was_stop_only {
                        "リプレイ停止に失敗しました。\nエンジンとの接続が切れています。"
                    } else {
                        return Task::none();
                    };
                    let dialog = screen::ConfirmDialog::new(
                        body.to_string(),
                        Box::new(Message::Window(WindowMsg::ToggleDialogModal(None))),
                    )
                    .with_confirm_btn_text("閉じる".to_string());
                    self.confirm_dialog = Some(dialog);
                    return Task::none();
                }
            }
            // F7: ForceStopReplay also timed out — release guard and show error
            // Shared between mode-switch and stop-only flows.
            WindowMsg::ModeSwitchForceStopTimeout => {
                log::warn!("[F7] ForceStopReplay also timed out — aborting");
                let stale = self.mode_switch_state.take().is_none();
                let was_stop_only = std::mem::take(&mut self.replay_stop_only_pending);
                if stale && !was_stop_only {
                    return Task::none();
                }
                let body = if !stale {
                    "モード切替に失敗しました。\nエンジンが応答しません。"
                } else {
                    "リプレイ停止に失敗しました。\nエンジンが応答しません。"
                };
                let dialog = screen::ConfirmDialog::new(
                    body.to_string(),
                    Box::new(Message::Window(WindowMsg::ToggleDialogModal(None))),
                )
                .with_confirm_btn_text("閉じる".to_string());
                self.confirm_dialog = Some(dialog);
                return Task::none();
            }
            // F7: send() returned Err — socket is broken; abort mode switch immediately without
            // waiting for the 5-second (StopReplay) or 2-second (ForceStopReplay) timeout.
            // Stale timeout messages that fire later are ignored because the pending
            // state will be cleared by then.
            WindowMsg::ModeSwitchSendFailed => {
                let was_mode_switch = self.mode_switch_state.take().is_some();
                let was_stop_only = std::mem::take(&mut self.replay_stop_only_pending);
                if was_mode_switch || was_stop_only {
                    let body = if was_mode_switch {
                        "モード切替に失敗しました。\nエンジンとの接続が切れています。"
                    } else {
                        "リプレイ停止に失敗しました。\nエンジンとの接続が切れています。"
                    };
                    let dialog = screen::ConfirmDialog::new(
                        body.to_string(),
                        Box::new(Message::Window(WindowMsg::ToggleDialogModal(None))),
                    )
                    .with_confirm_btn_text("閉じる".to_string());
                    self.confirm_dialog = Some(dialog);
                }
                return Task::none();
            }
            // F7: StopReplay EngineBusy — engine is IDLE (no replay loaded).
            // Skip the remaining 5s wait and send ForceStopReplay immediately.
            // ForceStopReplay has no state guard on the Python side and always responds
            // with ReplayStopped, so the flow can complete normally. Shared between
            // mode-switch and stop-only paths.
            WindowMsg::ModeSwitchStopBusy => {
                log::warn!(
                    "[F7] StopReplay rejected (engine IDLE) — sending ForceStopReplay immediately"
                );
                if self.mode_switch_state.is_none() && !self.replay_stop_only_pending {
                    return Task::none();
                }
                if let Some(conn) = self.engine_connection.clone() {
                    let request_id = uuid::Uuid::new_v4().to_string();
                    let force_task = Task::perform(
                        async move {
                            conn.send(engine_client::dto::Command::ForceStopReplay { request_id })
                                .await
                        },
                        |result| match result {
                            Ok(()) => Message::Engine(crate::messages::EngineMsg::Noop),
                            Err(_) => Message::Window(WindowMsg::ModeSwitchSendFailed),
                        },
                    );
                    let timeout_task = Task::perform(
                        async {
                            tokio::time::sleep(std::time::Duration::from_secs(2)).await;
                        },
                        |_| Message::Window(WindowMsg::ModeSwitchForceStopTimeout),
                    );
                    return Task::batch([force_task, timeout_task]);
                } else {
                    let was_mode_switch = self.mode_switch_state.take().is_some();
                    let was_stop_only = std::mem::take(&mut self.replay_stop_only_pending);
                    let body = if was_mode_switch {
                        "モード切替に失敗しました。\nエンジンとの接続が切れています。"
                    } else if was_stop_only {
                        "リプレイ停止に失敗しました。\nエンジンとの接続が切れています。"
                    } else {
                        return Task::none();
                    };
                    let dialog = screen::ConfirmDialog::new(
                        body.to_string(),
                        Box::new(Message::Window(WindowMsg::ToggleDialogModal(None))),
                    )
                    .with_confirm_btn_text("閉じる".to_string());
                    self.confirm_dialog = Some(dialog);
                    return Task::none();
                }
            }
            // F7: ForceStopReplay EngineBusy — genuine failure; abort the flow.
            // Shared between mode-switch and stop-only paths.
            WindowMsg::ModeSwitchEngineBusy(reason) => {
                self.engine_busy = true;
                let was_mode_switch = self.mode_switch_state.take().is_some();
                let was_stop_only = std::mem::take(&mut self.replay_stop_only_pending);
                if was_mode_switch || was_stop_only {
                    let body = if was_mode_switch {
                        format!("モード切替を中止しました。\nエンジンがビジー状態です: {reason}")
                    } else {
                        format!("リプレイ停止を中止しました。\nエンジンがビジー状態です: {reason}")
                    };
                    let dialog = screen::ConfirmDialog::new(
                        body,
                        Box::new(Message::Window(WindowMsg::ToggleDialogModal(None))),
                    )
                    .with_confirm_btn_text("閉じる".to_string());
                    self.confirm_dialog = Some(dialog);
                } else {
                    // No pending switch — fall back to warn toast
                    self.notifications.push(Toast::warn(format!(
                        "操作を受け付けられませんでした: {reason}"
                    )));
                }
                return Task::none();
            }
            // F7: true no-op — used to discard fire-and-forget Task completions.
            WindowMsg::ToggleDialogModal(dialog) => {
                // Fix: when dismissing (None), clear any parked dirty-check state so
                // a subsequent Open does not skip the confirm dialog (Issue 2 fix).
                if dialog.is_none() {
                    // F5/M: if we're dismissing the overwrite confirm during a save-then-open
                    // flow, restore the dirty-confirm so the user can retry or discard instead
                    // of silently losing the pending open target.
                    if self
                        .confirm_dialog
                        .as_ref()
                        .is_some_and(|d| d.on_save.is_none())
                        && self.pending_open_file.is_some()
                    {
                        self.confirm_dialog = Some(
                            screen::ConfirmDialog::new(
                                "未保存の変更があります。".to_string(),
                                Box::new(Message::Window(WindowMsg::DiscardAndOpenFile)),
                            )
                            .with_confirm_btn_text("破棄して開く".to_string())
                            .with_save_action(
                                Message::Window(WindowMsg::SaveAndOpenFile),
                                "保存して開く".to_string(),
                            ),
                        );
                        return Task::none();
                    }
                    self.pending_open_file = None;
                    self.pending_exit_windows = None;
                    // F7: release mode-switch guard when the dirty-confirm dialog is
                    // dismissed via backdrop click (ToggleDialogModal(None)), so the
                    // next SwitchMode attempt is not permanently blocked.
                    //
                    // M-rust3: this reset is unconditional — `ToggleDialogModal(None)`
                    // also fires for non-mode-switch dialogs (e.g. save-as overwrite,
                    // logout confirm). When `mode_switch_state` is already `None` the
                    // assignment is idempotent. The `ModeSwitchGuard::drop` Drop impl
                    // is what actually releases the cross-thread `MODE_SWITCHING`
                    // atomic and runs `lock_order_reset()` (H1), so dismissing an
                    // unrelated dialog cannot accidentally release the guard of an
                    // active mode-switch — it would only do so if the active
                    // mode-switch's own dialog is being closed.
                    self.mode_switch_state = None;
                    self.engine_busy = false;
                }
                self.confirm_dialog = dialog;
            }
            WindowMsg::DataFolderRequested => {
                if let Err(err) = data::open_data_folder() {
                    self.notifications
                        .push(Toast::error(format!("Failed to open data folder: {err}")));
                }
            }
            WindowMsg::OpenUrlRequested(url) => {
                if let Err(err) = data::open_url(url.as_ref()) {
                    self.notifications
                        .push(Toast::error(format!("Failed to open link: {err}")));
                }
            }
            WindowMsg::CaptureScreenshot => {
                let id = self.main_window.id;
                return iced::window::screenshot(id)
                    .map(|s| Message::Window(WindowMsg::ScreenshotReady(s)));
            }
            WindowMsg::ScreenshotReady(screenshot) => {
                let bytes = screenshot.rgba.to_vec();
                let width = screenshot.size.width;
                let height = screenshot.size.height;
                let ts = chrono::Local::now().format("%Y%m%d_%H%M%S").to_string();
                let path = data::data_path(Some(&format!("screenshots/screenshot_{ts}.png")));
                return Task::perform(
                    async move {
                        if let Some(parent) = path.parent() {
                            tokio::fs::create_dir_all(parent)
                                .await
                                .map_err(|e| e.to_string())?;
                        }
                        let path2 = path.clone();
                        tokio::task::spawn_blocking(move || {
                            image::save_buffer(
                                &path2,
                                &bytes,
                                width,
                                height,
                                image::ColorType::Rgba8,
                            )
                            .map_err(|e| e.to_string())
                        })
                        .await
                        .map_err(|e| e.to_string())??;
                        Ok::<_, String>(path)
                    },
                    |result| match result {
                        Ok(path) => Message::Window(WindowMsg::ScreenshotSaved(path)),
                        Err(e) => Message::Window(WindowMsg::ScreenshotFailed(e)),
                    },
                );
            }
            WindowMsg::ScreenshotSaved(path) => {
                self.notifications.push(Toast::info(format!(
                    "スクリーンショット保存: {}",
                    path.display()
                )));
            }
            WindowMsg::ScreenshotFailed(reason) => {
                self.notifications
                    .push(Toast::error(format!("スクリーンショット失敗: {reason}")));
            }
        }
        Task::none()
    }
}
