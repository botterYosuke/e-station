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
    scan_brace_body(src, needle, Some(3_000))
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
    let src = read_main();
    let body = scan_brace_body(&src, "dashboard::Event::BuyingPowerAction(_action)", None);

    assert!(
        body.contains("kabu_state.is_ready()"),
        "BuyingPowerAction ハンドラは kabu_state.is_ready() で venue を切り替えなければ\n\
         ならない。そうしないと kabu ログイン中の「更新」ボタンが立花 API を叩く。"
    );
    assert!(
        body.contains("KABU_STATION_VENUE_NAME"),
        "BuyingPowerAction ハンドラは kabu がアクティブなとき KABU_STATION_VENUE_NAME を\n\
         GetBuyingPower の venue に使わなければならない。"
    );
}

#[test]
fn order_list_refresh_button_uses_active_venue() {
    // 修正前: OrderListAction ハンドラは非 replay 時に常に TACHIBANA_VENUE_NAME を使っていた。
    // 結果: kabu のみログイン中に注文一覧「更新」を押すと立花 API にリクエストが飛ぶ。
    let src = read_main();
    let body = scan_brace_body(&src, "Action::RequestOrderList =>", None);

    assert!(
        body.contains("kabu_state.is_ready()"),
        "OrderListAction ハンドラは kabu_state.is_ready() で venue を切り替えなければ\n\
         ならない。そうしないと kabu ログイン中の注文一覧「更新」が立花 API を叩く。"
    );
    assert!(
        body.contains("KABU_STATION_VENUE_NAME"),
        "OrderListAction ハンドラは kabu がアクティブなとき KABU_STATION_VENUE_NAME を\n\
         GetOrderList の venue に使わなければならない。"
    );
}

#[test]
fn positions_refresh_button_uses_active_venue() {
    // 修正前: PositionsAction ハンドラは常に TACHIBANA_VENUE_NAME を使っていた。
    // 結果: kabu のみログイン中に保有銘柄「更新」を押すと立花 API にリクエストが飛ぶ。
    // BuyingPowerAction / OrderListAction は Issue #36 で修正済みだが PositionsAction が漏れていた。
    let src = read_main();
    let body = scan_brace_body(&src, "Action::RequestPositions", None);

    assert!(
        body.contains("kabu_state.is_ready()"),
        "PositionsAction ハンドラは kabu_state.is_ready() で venue を切り替えなければ\n\
         ならない。そうしないと kabu ログイン中の保有銘柄「更新」が立花 API を叩く。"
    );
    assert!(
        body.contains("KABU_STATION_VENUE_NAME"),
        "PositionsAction ハンドラは kabu がアクティブなとき KABU_STATION_VENUE_NAME を\n\
         GetPositions の venue に使わなければならない。"
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
