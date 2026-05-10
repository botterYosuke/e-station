//! 計画書テスト方針の 8 テスト（kabu venue login state machine + UI 配線）。
//!
//! `Flowsurface` はバイナリクレートでフル構造体インスタンス化が困難なため、
//! 本ファイルは update()/restart()/EngineConnected ハンドラを
//! ソースコードパターン検査で確認する。
//!
//! FSM 遷移（LoginStarted → LoginInFlight、EngineRehello → Idle）は
//! `src/venue_state.rs::tests` で同等のものが検証済み。

// ── Source helpers ────────────────────────────────────────────────────────────

fn read_main() -> String {
    let base = env!("CARGO_MANIFEST_DIR");
    let main = std::fs::read_to_string(format!("{base}/src/main.rs")).expect("read src/main.rs");
    let venue =
        std::fs::read_to_string(format!("{base}/src/handlers/venue.rs")).unwrap_or_default();
    let engine =
        std::fs::read_to_string(format!("{base}/src/handlers/engine.rs")).unwrap_or_default();
    let dashboard =
        std::fs::read_to_string(format!("{base}/src/handlers/dashboard.rs")).unwrap_or_default();
    format!("{main}\n{venue}\n{engine}\n{dashboard}")
}

fn scan_brace_body(src: &str, needle: &str, fallback_bytes: Option<usize>) -> String {
    let start = src
        .find(needle)
        .unwrap_or_else(|| panic!("needle not found: {needle}"));
    let rest = &src[start..];
    if let Some(open) = rest.find('{') {
        let bytes = rest.as_bytes();
        let mut depth: i32 = 0;
        let mut i = open;
        while i < bytes.len() {
            match bytes[i] {
                b'{' => depth += 1,
                b'}' => {
                    depth -= 1;
                    if depth == 0 {
                        return rest[..=i].to_string();
                    }
                }
                _ => {}
            }
            i += 1;
        }
    }
    if let Some(max) = fallback_bytes {
        let end = max.min(rest.len());
        let safe = (0..=end)
            .rev()
            .find(|&i| rest.is_char_boundary(i))
            .unwrap_or(0);
        rest[..safe].to_string()
    } else {
        rest.to_string()
    }
}

fn fn_body_brace(src: &str, needle: &str) -> String {
    scan_brace_body(src, needle, None)
}

fn handler_body_str(src: &str, needle: &str) -> String {
    scan_brace_body(src, needle, None)
}

// ── 3. Idle 時にボタンが on_press を持つ ────────────────────────────────────────

#[test]
fn kabu_footer_badge_idle_shows_login_button() {
    let src = read_main();
    let body = fn_body_brace(&src, "fn venue_login_chip(");

    assert!(
        body.contains("ログイン"),
        "venue_login_chip must render a 'ログイン' label for Idle state"
    );
    assert!(
        body.contains("on_press(on_press)"),
        "venue_login_chip must call on_press(on_press) when not in flight so \
         Idle state has a clickable login button"
    );
}

// ── 4. LoginInFlight 時にボタンがない ───────────────────────────────────────────

#[test]
fn kabu_footer_badge_in_flight_no_button() {
    let src = read_main();
    let body = fn_body_brace(&src, "fn venue_login_chip(");

    assert!(
        body.contains("LoginInFlight"),
        "venue_login_chip must handle LoginInFlight state"
    );
    assert!(
        body.contains("in_flight"),
        "venue_login_chip must use an in_flight flag to suppress the on_press \
         button when LoginInFlight so the user cannot double-click"
    );
}

// ── 5. RequestKabuLogin が IPC コマンドを送信する ────────────────────────────────

#[test]
fn request_kabu_login_sends_ipc() {
    let src = read_main();
    let body = handler_body_str(&src, "VenueMsg::RequestKabuLogin(trigger) =>");

    assert!(
        body.contains("try_claim_login_in_flight()"),
        "RequestKabuLogin handler must call try_claim_login_in_flight() \
         to suppress duplicate IPC sends"
    );
    assert!(
        body.contains("Command::RequestVenueLogin"),
        "RequestKabuLogin handler must send Command::RequestVenueLogin"
    );
    assert!(
        body.contains("KABU_STATION_VENUE_NAME"),
        "RequestKabuLogin handler must use KABU_STATION_VENUE_NAME as the venue string"
    );
    assert!(
        body.contains("Message::Venue(VenueMsg::KabuLoginIpcResult"),
        "RequestKabuLogin handler must use Message::Venue(VenueMsg::KabuLoginIpcResult as Task::perform callback"
    );
}

// ── 6. restart() が kabu キャッシュを復元できる ───────────────────────────────────

#[test]
fn restart_restores_kabu_state_when_cached() {
    let src = read_main();
    let start = src.find("fn restart(").expect("restart() not found");
    let rest = &src[start..];
    let end = rest[1..]
        .find("\n    fn ")
        .map(|i| i + 1)
        .unwrap_or(rest.len());
    let body = &rest[..end];

    assert!(
        body.contains("KABU_STATION_VENUE_NAME"),
        "restart() must call cached_venue_is_ready(KABU_STATION_VENUE_NAME) \
         so kabu login state survives 'File > 開く...'"
    );
    assert!(
        body.contains("kabu_bootstrap"),
        "restart() must chain kabu_bootstrap so the kabu FSM is restored \
         when the venue cache is hot"
    );
    assert!(
        body.contains("KabuVenueEvent"),
        "restart() must synthesize KabuVenueEvent(Ready) when kabu cache is hot"
    );
}

// ── 7. KabuLoginIpcResult(Err) が LoginInFlight → Idle に戻す ──────────────────

#[test]
fn kabu_login_ipc_send_failure_returns_idle() {
    let src = read_main();
    let body = handler_body_str(&src, "VenueMsg::KabuLoginIpcResult(result) =>");

    assert!(
        body.contains("is_login_in_flight()"),
        "KabuLoginIpcResult(Err) handler must check is_login_in_flight() \
         before rolling back to Idle"
    );
    assert!(
        body.contains("VenueState::Idle"),
        "KabuLoginIpcResult(Err) handler must set kabu_state = VenueState::Idle \
         on IPC send failure so the user can retry"
    );
    assert!(
        body.contains("Toast::error"),
        "KabuLoginIpcResult(Err) handler must push a Toast::error so the user \
         sees an error message — kabu has no banner, so Toast is the only visible feedback"
    );
}

// ── 8. EngineConnected が kabu ready キャッシュから Ready を合成できる ─────────────

#[test]
fn engine_connected_restores_kabu_state_when_cached() {
    let src = read_main();
    let start = src
        .find("EngineMsg::Connected(conn) =>")
        .expect("EngineMsg::Connected arm not found");
    let rest = &src[start..];
    let raw_end = rest.find("\n            EngineMsg::").unwrap_or(rest.len());
    let safe_end = (0..=raw_end.min(rest.len()))
        .rev()
        .find(|&i| rest.is_char_boundary(i))
        .unwrap_or(0);
    let body = &rest[..safe_end];

    assert!(
        body.contains("KABU_STATION_VENUE_NAME"),
        "EngineConnected handler must check cached_venue_is_ready(KABU_STATION_VENUE_NAME)"
    );
    assert!(
        body.contains("kabu_synthetic"),
        "EngineConnected handler must build kabu_synthetic task"
    );
    assert!(
        body.contains("KabuVenueEvent"),
        "EngineConnected handler must synthesize Message::KabuVenueEvent(VenueEvent::Ready)"
    );
}

// ── 買余力バグ回帰テスト (Issue: kabu VenueReady 時に GetBuyingPower が未送信) ─────

#[test]
fn kabu_venue_ready_sends_get_buying_power() {
    // 修正前: KabuEvent::Ready ハンドラに GetBuyingPower が存在しなかった。
    // 結果: 立花セッションの旧値(¥20000000 等)がパネルに残り続けた。
    // 修正後: 立花 VenueReady と同様に KABU_STATION_VENUE_NAME で GetBuyingPower を送信する。
    let src = read_main();
    let body = scan_brace_body(&src, "VenueMsg::KabuEvent(event) =>", None);

    assert!(
        body.contains("Command::GetBuyingPower"),
        "KabuEvent::Ready ハンドラは GetBuyingPower を送信しなければならない。\n\
         存在しない場合、立花ログアウト後に kabu でログインしても買余力パネルは\n\
         立花の旧値を表示し続ける。"
    );
    assert!(
        body.contains("KABU_STATION_VENUE_NAME"),
        "KabuEvent::Ready ハンドラの GetBuyingPower は venue に KABU_STATION_VENUE_NAME を\n\
         使わなければならない。TACHIBANA_VENUE_NAME を渡すと kabu セッションがない状態で\n\
         立花 API を叩こうとしてエラーになる。"
    );
}

#[test]
fn buying_power_refresh_button_uses_active_venue() {
    // 修正前: BuyingPowerAction ハンドラは常に TACHIBANA_VENUE_NAME を使っていた。
    // 結果: kabu のみログイン中に「更新」ボタンを押すと立花 API にリクエストが飛び、
    //       SESSION_NOT_ESTABLISHED エラーになる（または古い立花値が保持される）。
    // M1 リファクタ後: active_venue_name() ヘルパーで venue を解決するよう変更された。
    //   ヘルパー自体は active_venue_name_helper_exists テストで kabu_state.is_ready() /
    //   KABU_STATION_VENUE_NAME を検証しているため、ここではハンドラが
    //   active_venue_name() を呼んでいることを確認するだけでよい。
    let src = read_main();
    let body = scan_brace_body(&src, "dashboard::Event::BuyingPowerAction(_action)", None);

    assert!(
        body.contains("active_venue_name()"),
        "BuyingPowerAction ハンドラは active_venue_name() で venue を解決しなければならない。\n\
         そうしないと kabu ログイン中の「更新」ボタンが立花 API を叩く。"
    );
}

#[test]
fn order_list_refresh_button_uses_active_venue() {
    // 修正前: OrderListAction ハンドラは非 replay 時に常に TACHIBANA_VENUE_NAME を使っていた。
    // 結果: kabu のみログイン中に注文一覧「更新」を押すと立花 API にリクエストが飛ぶ。
    // M1 リファクタ後: replay パスは "replay" 固定、live パスは active_venue_name() で解決する。
    let src = read_main();
    let body = scan_brace_body(&src, "Action::RequestOrderList =>", None);

    assert!(
        body.contains("active_venue_name()"),
        "OrderListAction ハンドラは live パスで active_venue_name() を使わなければならない。\n\
         そうしないと kabu ログイン中の注文一覧「更新」が立花 API を叩く。"
    );
    // replay パスは "replay" 固定を維持していること
    assert!(
        body.contains("\"replay\""),
        "OrderListAction ハンドラは replay モードのとき \"replay\" venue を使わなければならない。"
    );
}

#[test]
fn positions_refresh_button_uses_active_venue() {
    // 修正前: PositionsAction ハンドラは常に TACHIBANA_VENUE_NAME を使っていた。
    // 結果: kabu のみログイン中に保有銘柄「更新」を押すと立花 API にリクエストが飛ぶ。
    // M1 リファクタ後: active_venue_name() ヘルパーで venue を解決するよう変更された。
    let src = read_main();
    let body = scan_brace_body(&src, "Action::RequestPositions", None);

    assert!(
        body.contains("active_venue_name()"),
        "PositionsAction ハンドラは active_venue_name() で venue を解決しなければならない。\n\
         そうしないと kabu ログイン中の保有銘柄「更新」が立花 API を叩く。"
    );
}

// ── M4. status_bar が kabu チップを RequestKabuLogin に配線している ─────────────────

#[test]
fn kabu_chip_wired_to_request_kabu_login() {
    let src = read_main();
    let sb_body = fn_body_brace(&src, "fn status_bar(");
    assert!(
        sb_body.contains("RequestKabuLogin"),
        "status_bar must wire kabu chip to Message::RequestKabuLogin, not RequestTachibanaLogin"
    );
}

// ── Issue #37: pane_added 自動フェッチが kabu をチェックしていない ────────────────────

fn read_dashboard() -> String {
    let base = env!("CARGO_MANIFEST_DIR");
    std::fs::read_to_string(format!("{base}/src/handlers/dashboard.rs"))
        .expect("read src/handlers/dashboard.rs")
}

#[test]
fn pane_added_buying_power_triggers_for_kabu() {
    // 修正前: pane_added auto-fetch は tachibana_state.is_ready() だけを見ていた。
    // 結果: kabu のみログイン中に BuyingPower パネルを後から追加しても
    //       自動フェッチが発火せず、パネルがローディング状態で停止する。
    // M1 リファクタ後: active_venue_name() で venue を解決する。
    //   kabu_state.is_ready() はガード条件の or 節として残る（後から追加したペインのトリガー）。
    let src = read_dashboard();
    // コードシンボルベースの needle を使い、日本語コメント依存を排除する。
    // ContentKind::BuyingPower が登場する位置から、そのブロック前後の文脈（600 bytes）を抽出する。
    // ガード条件（kabu_state.is_ready()）はブロック本体の直前にあり、
    // フェッチコマンド（active_venue_name()）は本体内にある。
    let bp_start = src
        .find("ContentKind::BuyingPower")
        .expect("ContentKind::BuyingPower not found in dashboard.rs");
    // pane_added ガード条件から本体末尾までを含む十分な範囲（~600 bytes）を取る
    let context = &src[bp_start.saturating_sub(200)..];
    let context_end = 800.min(context.len());
    let context = &context[..context_end];

    assert!(
        context.contains("kabu_state.is_ready()"),
        "BuyingPower pane_added auto-fetch の条件式は kabu_state.is_ready() を含まなければならない。\n\
         そうしないと kabu のみログイン中に BuyingPower パネルを後から追加しても\n\
         自動フェッチが発火せず、パネルがローディング状態のまま停止する。"
    );
    assert!(
        context.contains("active_venue_name()"),
        "BuyingPower pane_added auto-fetch は active_venue_name() で venue を解決しなければならない。\n\
         kabu / tachibana の切り替えは active_venue_name() ヘルパーに委譲されている。"
    );
}

#[test]
fn pane_added_positions_triggers_for_kabu() {
    // 修正前: pane_added auto-fetch は tachibana_state.is_ready() だけを見ていた。
    // 結果: kabu のみログイン中に Positions パネルを後から追加しても
    //       自動フェッチが発火せず、パネルがローディング状態で停止する。
    // M1 リファクタ後: active_venue_name() で venue を解決する。
    let src = read_dashboard();
    // コードシンボルベースの needle を使い、日本語コメント依存を排除する。
    // BuyingPower ブロック以降から ContentKind::Positions を探す。
    let bp_pos = src
        .find("ContentKind::BuyingPower")
        .expect("ContentKind::BuyingPower not found in dashboard.rs");
    let after_bp = &src[bp_pos + "ContentKind::BuyingPower".len()..];
    let pos_start = after_bp
        .find("ContentKind::Positions")
        .expect("ContentKind::Positions not found after BuyingPower block");
    let context = &after_bp[pos_start.saturating_sub(200)..];
    let context_end = 800.min(context.len());
    let context = &context[..context_end];

    assert!(
        context.contains("kabu_state.is_ready()"),
        "Positions pane_added auto-fetch の条件式は kabu_state.is_ready() を含まなければならない。\n\
         そうしないと kabu のみログイン中に Positions パネルを後から追加しても\n\
         自動フェッチが発火せず、パネルがローディング状態のまま停止する。"
    );
    assert!(
        context.contains("active_venue_name()"),
        "Positions pane_added auto-fetch は active_venue_name() で venue を解決しなければならない。\n\
         kabu / tachibana の切り替えは active_venue_name() ヘルパーに委譲されている。"
    );
}

// ── M1: active_venue_name() ヘルパーの存在確認 ──────────────────────────────────────

#[test]
fn active_venue_name_helper_exists() {
    // M1: Flowsurface impl に active_venue_name() ヘルパーが存在し、
    //     kabu_state.is_ready() で KABU_STATION_VENUE_NAME を返す実装であることを確認する。
    let src = read_main();
    let body = fn_body_brace(&src, "fn active_venue_name(");

    assert!(
        body.contains("kabu_state.is_ready()"),
        "active_venue_name() は kabu_state.is_ready() で venue を切り替えなければならない。"
    );
    assert!(
        body.contains("KABU_STATION_VENUE_NAME"),
        "active_venue_name() は kabu がアクティブなとき KABU_STATION_VENUE_NAME を返さなければならない。"
    );
    assert!(
        body.contains("TACHIBANA_VENUE_NAME"),
        "active_venue_name() は kabu が非アクティブなとき TACHIBANA_VENUE_NAME を返さなければならない。"
    );
}

// ── H1: OrderFilled / OrderAccepted が kabu 対応しているか確認 ───────────────────────

fn read_venue() -> String {
    let base = env!("CARGO_MANIFEST_DIR");
    std::fs::read_to_string(format!("{base}/src/handlers/venue.rs"))
        .expect("read src/handlers/venue.rs")
}

#[test]
fn order_filled_guard_covers_kabu() {
    // H1: OrderFilled のガードが tachibana と kabu の両方に対応しているか確認する。
    // 修正前: !self.tachibana_state.is_ready() のみで kabu を無視していた。
    // 結果: kabu のみログイン中に約定しても positions 自動更新が発火しない。
    // 注: VenueMsg::OrderFilled は struct パターン展開なので handler_body_str ではなく
    //     raw テキスト検索でガード条件を確認する。
    let src = read_venue();
    let filled_start = src
        .find("VenueMsg::OrderFilled {")
        .expect("VenueMsg::OrderFilled not found in venue.rs");
    // ガード条件まで（最初の 1200 bytes）を取得する
    let context_end = 1200.min(src.len() - filled_start);
    let context = &src[filled_start..filled_start + context_end];

    assert!(
        context.contains("kabu_state.is_ready()"),
        "OrderFilled ハンドラは kabu_state.is_ready() をガード条件に含めなければならない。\n\
         修正: !tachibana_state.is_ready() && !kabu_state.is_ready() と AND でガードする。"
    );
}

#[test]
fn order_accepted_guard_covers_kabu() {
    // H1: OrderAccepted のガードが tachibana と kabu の両方に対応しているか確認する。
    // 修正前: !self.tachibana_state.is_ready() のみで kabu を無視していた。
    // 結果: kabu のみログイン中に注文受付されても order list / buying power 自動更新が発火しない。
    let src = read_venue();
    let accepted_start = src
        .find("VenueMsg::OrderAccepted {")
        .expect("VenueMsg::OrderAccepted not found in venue.rs");
    let context_end = 1200.min(src.len() - accepted_start);
    let context = &src[accepted_start..accepted_start + context_end];

    assert!(
        context.contains("kabu_state.is_ready()"),
        "OrderAccepted ハンドラは kabu_state.is_ready() をガード条件に含めなければならない。\n\
         修正: !tachibana_state.is_ready() && !kabu_state.is_ready() と AND でガードする。"
    );
}

// ── M2: Positions / BuyingPower pane_added で engine_connection is None 時に Toast を出す ──

#[test]
fn pane_added_buying_power_none_connection_shows_toast() {
    // M2: BuyingPower pane_added の engine_connection is None 分岐に Toast があることを確認。
    let src = read_dashboard();
    // buying_power_request_id.is_none() ブロックの else 節を抽出する
    let start = src
        .find("buying_power_request_id.is_none()")
        .expect("buying_power_request_id.is_none() not found in dashboard.rs");
    let block = &src[start..];
    assert!(
        block.contains("engine_connection is None") || block.contains("Toast"),
        "BuyingPower pane_added の engine_connection が None の場合に Toast を出力しなければならない。"
    );
}

#[test]
fn pane_added_positions_none_connection_shows_toast() {
    // M2: Positions pane_added の engine_connection is None 分岐に Toast があることを確認。
    let src = read_dashboard();
    let catch_up_pos = src
        .find("buying_power_request_id.is_none()")
        .expect("buying_power_request_id.is_none() not found");
    let tail = &src[catch_up_pos..];
    let start = tail
        .find("positions_request_id.is_none()")
        .expect("positions_request_id.is_none() not found in pane_added area");
    let block = &tail[start..];
    assert!(
        block.contains("Toast"),
        "Positions pane_added の engine_connection が None の場合に Toast を出力しなければならない。"
    );
}

// ── H-1: OrderList pane_added の engine_connection is None 分岐に Toast を追加 ──────────

#[test]
fn pane_added_order_list_none_connection_shows_toast() {
    // pane_added OrderList の engine_connection is None 分岐が
    // BuyingPower / Positions と同様に Toast を出すことをピン。
    let src = read_dashboard();
    // auto-fetch コメント以降に限定して OrderList ブロックを抽出する
    let catch_up_pos = src
        .find("VenueReady 後にペインを追加した場合の自動フェッチキャッチアップ")
        .expect("auto-fetch catch-up comment not found");
    let tail = &src[catch_up_pos..];
    let body = scan_brace_body(tail, "ContentKind::OrderList", None);
    assert!(
        body.contains("Toast::error") || body.contains("notifications.push"),
        "OrderList pane_added auto-fetch は engine_connection is None のとき\n\
         Toast を出さなければならない。BuyingPower / Positions との対称性を保つ。"
    );
}

// ── M3: PositionsSendCompleted(Err) に Toast を追加 ──────────────────────────────────

#[test]
fn positions_send_completed_err_shows_toast() {
    // M3: PositionsSendCompleted(Err) ハンドラに Toast が存在することを確認する。
    // BuyingPowerSendCompleted(Err) には既に Toast がある。対称性を保つため追加する。
    let src = read_main();
    let body = handler_body_str(&src, "VenueMsg::PositionsSendCompleted(Err(err)) =>");

    assert!(
        body.contains("Toast::error"),
        "PositionsSendCompleted(Err) ハンドラは Toast::error を push しなければならない。\n\
         BuyingPowerSendCompleted(Err) と対称にすること。"
    );
}

// ── C-1: ConfirmCancelOrder が active_venue_name() を使う ────────────────────────────────

#[test]
fn confirm_cancel_order_uses_active_venue() {
    // 修正前: ConfirmCancelOrder の venue が TACHIBANA_VENUE_NAME ハードコードで
    // kabu のみログイン中に注文を取り消すと無音で失敗していた。
    //
    // venue.rs のみ対象（read_main() は dashboard.rs の呼び出し側にも
    // VenueMsg::ConfirmCancelOrder が出現するため結合順序依存になる）。
    let src = read_venue();
    let start = src
        .find("VenueMsg::ConfirmCancelOrder")
        .expect("VenueMsg::ConfirmCancelOrder not found in venue.rs");
    let body = &src[start..];
    assert!(
        body.contains("active_venue_name()"),
        "ConfirmCancelOrder ハンドラは active_venue_name() で venue を選択しなければならない。\n\
         TACHIBANA_VENUE_NAME をハードコードすると kabu のみログイン中の注文取消が\n\
         SESSION_NOT_ESTABLISHED エラーになりユーザーに通知されない。"
    );
}

// ── M4: OrderList pane_added 自動フェッチが追加されているか確認 ──────────────────────────

#[test]
fn pane_added_order_list_triggers_for_any_venue() {
    // M4: OpenOrderPanel で OrderList ペインを追加したとき、
    //     tachibana または kabu がログイン済みなら GetOrderList が自動フェッチされること。
    let src = read_dashboard();
    // buying_power_request_id.is_none() ブロック以降を対象にする
    let catch_up_pos = src
        .find("buying_power_request_id.is_none()")
        .expect("buying_power_request_id.is_none() not found in dashboard.rs");
    let tail = &src[catch_up_pos..];

    assert!(
        tail.contains("ContentKind::OrderList"),
        "dashboard.rs の pane_added ブロックに ContentKind::OrderList の自動フェッチが\n\
         なければならない。kabu または tachibana ログイン済みで OrderList ペインを後から\n\
         追加したとき GetOrderList が発火する必要がある。"
    );
    assert!(
        tail.contains("order_list_request_id.is_none()"),
        "OrderList pane_added auto-fetch は order_list_request_id.is_none() で\n\
         二重送信をガードしなければならない。"
    );
}
