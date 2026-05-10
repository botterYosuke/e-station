//! Issue #39 回帰ピンテスト — 未ログイン・未選択時の空状態メッセージ
//!
//! 実装変更のサマリー:
//! - `pane.rs` に `stream_venue()` ヘルパーと `stream_empty_reason` / `order_venue_ready` 計算を追加
//! - `panel::view` / `chart::view` / `HeatmapShader::view` に `empty_reason: &str` を追加
//! - `orders::view` / `positions::view` / `buying_power::view` / `order_entry::view` に
//!   `venue_ready: bool` を追加
//!
//! ピン一覧:
//! | テスト名 | ピンする不変条件 |
//! |---|---|
//! | `stream_venue_helper_exists` | `Pane::stream_venue()` が定義されている |
//! | `stream_empty_reason_no_stream_case` | stream なしで「銘柄を選択してください」 |
//! | `stream_empty_reason_tachibana_not_ready` | 立花未ログインで「立花へのログインが必要です」 |
//! | `stream_empty_reason_kabu_not_ready` | kabu未ログインで「kabuステーションへのログインが必要です」 |
//! | `stream_empty_reason_fallback` | それ以外で「データ受信待ち...」 |
//! | `order_venue_ready_uses_tachibana_and_kabu_ready` | `order_venue_ready` が両 venue の ready 状態を参照 |
//! | `stream_empty_reason_propagated_to_panel_view` | `stream_empty_reason` が `panel::view` に渡される |
//! | `stream_empty_reason_propagated_to_chart_view` | `stream_empty_reason` が `chart::view` に渡される |
//! | `stream_empty_reason_propagated_to_heatmap_view` | `stream_empty_reason` が `HeatmapShader::view` に渡される |
//! | `order_venue_ready_propagated_to_orders_view` | `order_venue_ready` が `orders::view` に渡される |
//! | `order_venue_ready_propagated_to_buying_power_view` | `order_venue_ready` が `buying_power::view` に渡される |
//! | `order_venue_ready_propagated_to_positions_view` | `order_venue_ready` が `positions::view` に渡される |
//! | `order_venue_ready_propagated_to_order_entry_view` | `order_venue_ready` が `order_entry.view()` に渡される |
//! | `orders_view_shows_login_required_when_not_ready` | `orders::view` が `venue_ready=false` 時に「ログインが必要です」 |
//! | `positions_view_shows_login_required_when_not_ready` | `positions::view` が `venue_ready=false` 時に「ログインが必要です」 |
//! | `buying_power_view_guards_on_venue_ready` | `buying_power::view` が `!venue_ready` を先頭でガード |
//! | `order_entry_view_gates_submit_on_venue_ready` | `order_entry::view` が `on_press_maybe` で `venue_ready` をゲート |
//! | `panel_view_accepts_empty_reason_param` | `panel::view` の汎用ラッパーが `empty_reason: &str` を受け取る |

// ── ソース読み込みヘルパー ────────────────────────────────────────────────────────

fn read_src(rel: &str) -> String {
    let base = env!("CARGO_MANIFEST_DIR");
    std::fs::read_to_string(format!("{base}/{rel}"))
        .unwrap_or_else(|_| panic!("{rel} が見つかりません"))
}

fn scan_brace_body(src: &str, needle: &str) -> String {
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
    rest.to_string()
}

// ── pane.rs: stream_venue ───────────────────────────────────────────────────────

#[test]
fn stream_venue_helper_exists() {
    // `stream_venue()` が削除されると stream_empty_reason 計算とuninitialized_base が
    // 壊れる。Pane 内の fn 定義を pin する。
    let src = read_src("src/screen/dashboard/pane.rs");
    assert!(
        src.contains("fn stream_venue(&self)"),
        "Pane::stream_venue() が見つかりません。\n\
         stream_empty_reason の計算に必要なヘルパーです。"
    );
}

#[test]
fn stream_venue_checks_waiting_streams() {
    // `stream_venue()` は Ready 状態だけでなく Waiting 状態のストリームも参照する。
    // Waiting のみ参照すると、銘柄設定済み・未接続のケースで venue を特定できない。
    let src = read_src("src/screen/dashboard/pane.rs");
    let body = scan_brace_body(&src, "fn stream_venue(&self)");
    assert!(
        body.contains("ResolvedStream::Waiting"),
        "stream_venue() は ResolvedStream::Waiting を参照しなければなりません。\n\
         Waiting ストリームを無視すると未接続時の venue を取得できず、\n\
         「立花へのログインが必要です」等が表示されなくなります。"
    );
}

// ── pane.rs: stream_empty_reason の 4 ケース ─────────────────────────────────────

#[test]
fn stream_empty_reason_no_stream_case() {
    // ストリームなし（銘柄未選択）→ 「銘柄を選択してください」
    let src = read_src("src/screen/dashboard/pane.rs");
    let start = src
        .find("stream_empty_reason: &'static str")
        .or_else(|| src.find("let stream_empty_reason"))
        .expect("stream_empty_reason の定義が見つかりません");
    let context = &src[start..(start + 600).min(src.len())];
    assert!(
        context.contains("銘柄を選択してください"),
        "stream_empty_reason の None アーム（銘柄未選択）は\n\
         「銘柄を選択してください」を返さなければなりません。"
    );
}

#[test]
fn stream_empty_reason_tachibana_not_ready() {
    // 立花未ログイン → 「立花へのログインが必要です」
    let src = read_src("src/screen/dashboard/pane.rs");
    let start = src
        .find("stream_empty_reason: &'static str")
        .or_else(|| src.find("let stream_empty_reason"))
        .expect("stream_empty_reason の定義が見つかりません");
    let context = &src[start..(start + 600).min(src.len())];
    assert!(
        context.contains("立花へのログインが必要です"),
        "stream_empty_reason の Tachibana 未ログインアームは\n\
         「立花へのログインが必要です」を返さなければなりません。"
    );
    assert!(
        context.contains("tachibana_ready"),
        "stream_empty_reason の Tachibana アームは tachibana_ready フラグを参照しなければなりません。"
    );
}

#[test]
fn stream_empty_reason_kabu_not_ready() {
    // kabu未ログイン → 「kabuステーションへのログインが必要です」
    let src = read_src("src/screen/dashboard/pane.rs");
    let start = src
        .find("stream_empty_reason: &'static str")
        .or_else(|| src.find("let stream_empty_reason"))
        .expect("stream_empty_reason の定義が見つかりません");
    let context = &src[start..(start + 600).min(src.len())];
    assert!(
        context.contains("kabuステーションへのログインが必要です"),
        "stream_empty_reason の KabuStation 未ログインアームは\n\
         「kabuステーションへのログインが必要です」を返さなければなりません。"
    );
    assert!(
        context.contains("kabu_ready"),
        "stream_empty_reason の KabuStation アームは kabu_ready フラグを参照しなければなりません。"
    );
}

#[test]
fn stream_empty_reason_fallback() {
    // ログイン済み・ストリーム接続済みで受信待ち → 「データ受信待ち...」
    let src = read_src("src/screen/dashboard/pane.rs");
    let start = src
        .find("stream_empty_reason: &'static str")
        .or_else(|| src.find("let stream_empty_reason"))
        .expect("stream_empty_reason の定義が見つかりません");
    let context = &src[start..(start + 600).min(src.len())];
    assert!(
        context.contains("データ受信待ち"),
        "stream_empty_reason のフォールバックアームは\n\
         「データ受信待ち...」を返さなければなりません。\n\
         ログイン済み・ストリーム接続済みの通常待機状態に表示されます。"
    );
}

// ── pane.rs: order_venue_ready ────────────────────────────────────────────────

#[test]
fn order_venue_ready_uses_tachibana_and_kabu_ready() {
    // `order_venue_ready` は Tachibana と KabuStation の両方の ready 状態を参照する。
    // 片方のみ参照すると、もう一方の venue でログイン未完了時に誤ったメッセージが表示される。
    let src = read_src("src/screen/dashboard/pane.rs");
    let start = src
        .find("let order_venue_ready")
        .expect("order_venue_ready の定義が見つかりません");
    let context = &src[start..(start + 400).min(src.len())];
    assert!(
        context.contains("tachibana_ready"),
        "order_venue_ready は tachibana_ready を参照しなければなりません。"
    );
    assert!(
        context.contains("kabu_ready"),
        "order_venue_ready は kabu_ready を参照しなければなりません。"
    );
    assert!(
        context.contains("Venue::Tachibana"),
        "order_venue_ready は Venue::Tachibana アームを持たなければなりません。"
    );
    assert!(
        context.contains("Venue::KabuStation"),
        "order_venue_ready は Venue::KabuStation アームを持たなければなりません。"
    );
}

// ── pane.rs: stream_empty_reason の伝播 ─────────────────────────────────────────

#[test]
fn stream_empty_reason_propagated_to_panel_view() {
    let src = read_src("src/screen/dashboard/pane.rs");
    assert!(
        src.contains("panel::view(panel, timezone, stream_empty_reason)"),
        "panel::view() には stream_empty_reason を渡さなければなりません。\n\
         渡さないと TimeAndSales / Ladder 等が「Waiting for data...」固定になります。"
    );
}

#[test]
fn stream_empty_reason_propagated_to_chart_view() {
    let src = read_src("src/screen/dashboard/pane.rs");
    assert!(
        src.contains("chart::view(chart, indicators, timezone, stream_empty_reason)"),
        "chart::view() には stream_empty_reason を渡さなければなりません。\n\
         渡さないとチャートの空状態が「Waiting for data...」固定になります。"
    );
}

#[test]
fn stream_empty_reason_propagated_to_heatmap_view() {
    let src = read_src("src/screen/dashboard/pane.rs");
    assert!(
        src.contains("HeatmapShader::view(chart, timezone, stream_empty_reason)"),
        "HeatmapShader::view() には stream_empty_reason を渡さなければなりません。\n\
         渡さないと Heatmap の空状態が「Waiting for data...」固定になります。"
    );
}

// ── pane.rs: order_venue_ready の伝播 ────────────────────────────────────────────

#[test]
fn order_venue_ready_propagated_to_orders_view() {
    let src = read_src("src/screen/dashboard/pane.rs");
    assert!(
        src.contains("panel::orders::view(panel, order_venue_ready)"),
        "panel::orders::view() には order_venue_ready を渡さなければなりません。\n\
         渡さないと未ログイン時に「注文なし」が表示されます。"
    );
}

#[test]
fn order_venue_ready_propagated_to_buying_power_view() {
    let src = read_src("src/screen/dashboard/pane.rs");
    assert!(
        src.contains("panel::buying_power::view(panel, order_venue_ready)"),
        "panel::buying_power::view() には order_venue_ready を渡さなければなりません。\n\
         渡さないと未ログイン時に「---」だらけの余力画面が表示されます。"
    );
}

#[test]
fn order_venue_ready_propagated_to_positions_view() {
    let src = read_src("src/screen/dashboard/pane.rs");
    assert!(
        src.contains("panel::positions::view(panel, order_venue_ready)"),
        "panel::positions::view() には order_venue_ready を渡さなければなりません。\n\
         渡さないと未ログイン時に「保有なし」が表示されます。"
    );
}

#[test]
fn order_venue_ready_propagated_to_order_entry_view() {
    let src = read_src("src/screen/dashboard/pane.rs");
    assert!(
        src.contains(".view(order_venue_ready)"),
        "order_entry.view() には order_venue_ready を渡さなければなりません。\n\
         渡さないと未ログイン時に送注ボタンが有効になります。"
    );
}

// ── orders.rs: venue_ready によるメッセージ分岐 ──────────────────────────────────

#[test]
fn orders_view_signature_has_venue_ready() {
    let src = read_src("src/screen/dashboard/panel/orders.rs");
    assert!(
        src.contains("pub fn view(panel: &OrdersPanel, venue_ready: bool)"),
        "orders::view() のシグネチャに venue_ready: bool がなければなりません。\n\
         削除されると未ログイン時に「注文なし」が表示されます。"
    );
}

#[test]
fn orders_view_shows_login_required_when_not_ready() {
    let src = read_src("src/screen/dashboard/panel/orders.rs");
    assert!(
        src.contains("ログインが必要です"),
        "orders::view() は venue_ready=false かつ is_empty() のとき\n\
         「ログインが必要です」を表示しなければなりません。"
    );
    // loading 優先 → venue_ready チェック → デフォルト の順序を確認する
    // is_empty() ブロック内で loading チェックが venue_ready より先に来ることを確認する。
    // scan_brace_body は関数シグネチャ（venue_ready: bool）も含むため、
    // is_empty() ブロック開始位置から相対的に検索する。
    let is_empty_start = src
        .find("if panel.is_empty()")
        .expect("is_empty() ブロックが見つかりません");
    let is_empty_block = &src[is_empty_start..(is_empty_start + 500).min(src.len())];
    let loading_pos = is_empty_block.find("panel.loading").unwrap_or(usize::MAX);
    let venue_pos = is_empty_block.find("venue_ready").unwrap_or(usize::MAX);
    assert!(
        loading_pos < venue_pos,
        "orders::view() の is_empty() ブロックは venue_ready チェックより\n\
         loading チェックを先に行わなければなりません。\n\
         loading=true && venue_ready=false のとき「↻ 更新中…」が優先されるべきです。"
    );
}

// ── positions.rs: venue_ready によるメッセージ分岐 ──────────────────────────────

#[test]
fn positions_view_signature_has_venue_ready() {
    let src = read_src("src/screen/dashboard/panel/positions.rs");
    assert!(
        src.contains("pub fn view(panel: &PositionsPanel, venue_ready: bool)"),
        "positions::view() のシグネチャに venue_ready: bool がなければなりません。\n\
         削除されると未ログイン時に「保有なし」が表示されます。"
    );
}

#[test]
fn positions_view_shows_login_required_when_not_ready() {
    let src = read_src("src/screen/dashboard/panel/positions.rs");
    assert!(
        src.contains("ログインが必要です"),
        "positions::view() は venue_ready=false かつ is_empty() のとき\n\
         「ログインが必要です」を表示しなければなりません。"
    );
    // positions は panel.positions.is_empty() を使う。同じ ordering 確認。
    let is_empty_start = src
        .find("if panel.positions.is_empty()")
        .expect("positions is_empty() ブロックが見つかりません");
    // REPLAY バナーの後の通常 is_empty() ブロックを対象とする
    let after_replay = {
        let replay_end = src[is_empty_start..]
            .find("if let Some(ref err)")
            .map(|o| is_empty_start + o)
            .unwrap_or(is_empty_start);
        src[replay_end..]
            .find("if panel.positions.is_empty()")
            .map(|o| replay_end + o)
    };
    let block_start = after_replay.unwrap_or(is_empty_start);
    let is_empty_block = &src[block_start..(block_start + 500).min(src.len())];
    let loading_pos = is_empty_block.find("panel.loading").unwrap_or(usize::MAX);
    let venue_pos = is_empty_block.find("venue_ready").unwrap_or(usize::MAX);
    assert!(
        loading_pos < venue_pos,
        "positions::view() の is_empty() ブロックは venue_ready チェックより\n\
         loading チェックを先に行わなければなりません。"
    );
}

// ── buying_power.rs: !venue_ready ガード ─────────────────────────────────────────

#[test]
fn buying_power_view_signature_has_venue_ready() {
    let src = read_src("src/screen/dashboard/panel/buying_power.rs");
    assert!(
        src.contains("pub fn view(panel: &BuyingPowerPanel, venue_ready: bool)"),
        "buying_power::view() のシグネチャに venue_ready: bool がなければなりません。\n\
         削除されると未ログイン時に「---」だらけの余力画面が表示されます。"
    );
}

#[test]
fn buying_power_view_guards_on_venue_ready() {
    let src = read_src("src/screen/dashboard/panel/buying_power.rs");
    assert!(
        src.contains("if !venue_ready && !panel.is_replay && !panel.loading"),
        "buying_power::view() は `!venue_ready && !panel.is_replay && !panel.loading` で\n\
         ガードしなければなりません。\n\
         REPLAY モードや loading 中は venue_ready に関わらず通常表示を継続するためです。"
    );
    assert!(
        src.contains("ログインが必要です"),
        "buying_power::view() は venue_ready=false のとき「ログインが必要です」を\n\
         表示しなければなりません。"
    );
}

// ── order_entry.rs: on_press_maybe で venue_ready をゲート ─────────────────────────

#[test]
fn order_entry_view_signature_has_venue_ready() {
    let src = read_src("src/screen/dashboard/panel/order_entry.rs");
    assert!(
        src.contains("pub fn view(&self, venue_ready: bool)"),
        "order_entry::view() のシグネチャに venue_ready: bool がなければなりません。\n\
         削除されると未ログイン時に送注ボタンが有効になります。"
    );
}

#[test]
fn order_entry_view_gates_submit_on_venue_ready() {
    // 送注ボタンは venue_ready かつ submitting=false かつ入力有効時のみ活性化する。
    // `on_press_maybe` でゲートしないと、未ログインのまま「注文」ボタンが押せてしまう。
    let src = read_src("src/screen/dashboard/panel/order_entry.rs");
    assert!(
        src.contains("on_press_maybe"),
        "order_entry::view() の送注ボタンは on_press_maybe を使わなければなりません。\n\
         venue_ready=false のとき on_press を None にしてボタンを無効化します。"
    );
    let submit_btn_block = {
        let start = src
            .find("let submit_btn")
            .expect("submit_btn の定義が見つかりません");
        &src[start..(start + 600).min(src.len())]
    };
    assert!(
        submit_btn_block.contains("venue_ready"),
        "submit_btn の on_press_maybe 条件は venue_ready を含まなければなりません。"
    );
    assert!(
        submit_btn_block.contains("SubmitClicked"),
        "submit_btn の on_press_maybe が Some のとき Message::SubmitClicked を返さなければなりません。"
    );
}

// ── panel.rs: 汎用 view() の empty_reason パラメータ ────────────────────────────

#[test]
fn panel_view_accepts_empty_reason_param() {
    // 汎用 `panel::view()` ラッパーが `empty_reason: &str` を受け取り、
    // `is_empty()` のときそれを表示する。
    let src = read_src("src/screen/dashboard/panel.rs");
    assert!(
        src.contains("empty_reason"),
        "panel::view() は empty_reason パラメータを受け取らなければなりません。\n\
         受け取らないと「Waiting for data...」が固定表示されます。"
    );
    assert!(
        src.contains("text(empty_reason)"),
        "panel::view() は is_empty() のとき text(empty_reason) を表示しなければなりません。"
    );
}

// ── chart.rs / heatmap.rs: empty_reason パラメータ ──────────────────────────────

#[test]
fn chart_view_accepts_empty_reason_param() {
    let src = read_src("src/chart.rs");
    assert!(
        src.contains("empty_reason"),
        "chart::view() は empty_reason パラメータを受け取らなければなりません。"
    );
}

#[test]
fn heatmap_view_accepts_empty_reason_param() {
    let src = read_src("src/widget/chart/heatmap.rs");
    assert!(
        src.contains("empty_reason"),
        "HeatmapShader::view() は empty_reason パラメータを受け取らなければなりません。"
    );
}
