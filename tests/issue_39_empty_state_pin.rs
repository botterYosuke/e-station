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

/// `start + max_len` で得られる byte index を `src` の有効な char boundary に丸める。
///
/// PR #45 R1 review HIGH-3 (Phase R8 外部レビュー) の defensive hardening。
/// ピンテストは `&src[start..(start + N).min(src.len())]` のような byte slicing
/// で固定窓を取るが、対象ソース (`pane.rs` 等) に日本語コメントが含まれるため、
/// 窓の終端がたまたま multibyte char の途中に落ちると `byte index N is not a
/// char boundary` で panic する。`is_char_boundary` で end を後退させて
/// 安全な境界に丸める。
///
/// R4 Group D: `start > src.len()` の defensive clamp を追加。caller が誤って
/// `find()` 失敗時の `usize::MAX` 等を渡したケースでも、戻り値が
/// `[start_clamped, src.len()]` の正規範囲に収まることを保証する。
fn safe_slice_end(src: &str, start: usize, max_len: usize) -> usize {
    // R4 Group D defensive clamp: start も src.len() に丸める。これにより
    // caller の `&src[start..safe_slice_end(...)]` が start>len で panic することを
    // 完全に防ぐ (caller 側が start を別途 clamp する責任から解放)。
    let start = start.min(src.len());
    let mut end = start.saturating_add(max_len).min(src.len());
    while end > start && !src.is_char_boundary(end) {
        end -= 1;
    }
    end
}

#[test]
fn safe_slice_end_rounds_down_to_char_boundary() {
    // "abc日本語def" の "日" は 3 byte (UTF-8)。start=0, max_len=4 だと
    // 素朴な実装は byte index 4 (= 'c' の次の '日' の途中) を返し、
    // `&src[0..4]` で panic する。helper は 3 (= '日' の手前) に丸める。
    let s = "abc日本語def";
    assert_eq!(safe_slice_end(s, 0, 3), 3, "ASCII 境界はそのまま返す");
    assert_eq!(
        safe_slice_end(s, 0, 4),
        3,
        "multibyte 途中は前の境界に丸めるべき"
    );
    assert_eq!(
        safe_slice_end(s, 0, 5),
        3,
        "multibyte 途中 (2 byte 進入) も前の境界へ"
    );
    assert_eq!(
        safe_slice_end(s, 0, 6),
        6,
        "1 char 完全包含 (= '日' の直後) は許容"
    );
    assert_eq!(
        safe_slice_end(s, 0, 1000),
        s.len(),
        "max_len が src を超える場合は src.len() に clamp"
    );
    // 安全性: 返り値で実際に slice しても panic しない
    let end = safe_slice_end(s, 0, 4);
    let _ = &s[0..end];
}

// ── R4 Group D: safe_slice_end の境界値テスト追加 ────────────────────────────
//
// 既存テスト (`safe_slice_end_rounds_down_to_char_boundary`) は `start=0` 系統のみ
// 検証していたため、helper を実装変更したときに以下のエッジケースで silent panic
// する余地が残っていた。R4 review-fix-loop の rust-reviewer 推奨に従い 3 件追加:

#[test]
fn safe_slice_end_handles_max_len_zero() {
    // 空スライス要求 → start を返す (常に start == end の空 slice が安全)。
    let s = "abc日本語def";
    assert_eq!(safe_slice_end(s, 3, 0), 3, "max_len=0 は start を返す");
    // 安全性: 返り値で実際に slice しても panic しない
    let end = safe_slice_end(s, 3, 0);
    let _ = &s[3..end];
}

#[test]
fn safe_slice_end_handles_start_at_end() {
    // start = src.len() → end も src.len() (空 slice)。
    let s = "abc日本語def";
    let n = s.len();
    assert_eq!(
        safe_slice_end(s, n, 0),
        n,
        "start=src.len() は end=src.len() を返す"
    );
    assert_eq!(
        safe_slice_end(s, n, 100),
        n,
        "start=src.len() で max_len>0 でも end=src.len() を返す"
    );
    let end = safe_slice_end(s, n, 100);
    let _ = &s[n..end];
}

#[test]
fn safe_slice_end_clamps_start_beyond_len() {
    // start > src.len() でも panic せず src.len() に clamp して返す
    // (defensive hardening — caller が start>len の狂った値を渡しても、helper の
    // 戻り値は **常に start_clamped..end の安全な範囲** を表現する必要がある)。
    //
    // 旧実装は `start.saturating_add(max_len).min(src.len())` で end を src.len()
    // に押さえるが、start 自体をクランプしないため caller が `&s[start..end]` で
    // 即 panic していた (start=9999 > end=src.len())。defensive clamp `start.min(src.len())`
    // で end >= start_clamped (= src.len()) の post-condition を成立させる。
    let s = "abc日本語def";
    let n = s.len();
    assert_eq!(
        safe_slice_end(s, 9999, 100),
        n,
        "start>src.len() は src.len() を end として返す"
    );
    // post-condition: end >= start_clamped。helper 戻り値 end が src.len() で、
    // start を src.len() に clamp すれば `&s[start_clamped..end]` は空 slice として
    // 安全になる。defensive clamp が無いと、caller がナイーブに `&s[start..end]`
    // すると start > end で panic する。
    let end = safe_slice_end(s, 9999, 100);
    assert!(
        end >= n.min(9999),
        "post-condition: end ({end}) must be >= min(start, src.len()) ({}); \
         this guards against caller-side `&s[start..end]` panics when start > src.len()",
        n.min(9999)
    );
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
    let context = &src[start..safe_slice_end(src.as_str(), start, 600)];
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
    let context = &src[start..safe_slice_end(src.as_str(), start, 600)];
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
    let context = &src[start..safe_slice_end(src.as_str(), start, 600)];
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
    let context = &src[start..safe_slice_end(src.as_str(), start, 600)];
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
    let context = &src[start..safe_slice_end(src.as_str(), start, 400)];
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
    let is_empty_block = &src[is_empty_start..safe_slice_end(src.as_str(), is_empty_start, 500)];
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
    let is_empty_block = &src[block_start..safe_slice_end(src.as_str(), block_start, 500)];
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
        &src[start..safe_slice_end(src.as_str(), start, 600)]
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
