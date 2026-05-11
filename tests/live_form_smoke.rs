//! issue #42 Phase 3: live_form smoke / contract tests.
//!
//! `flowsurface` is a binary crate so we cannot instantiate `Flowsurface`
//! directly. Instead we use **source-scan** tests (same technique as
//! `replay_form_submit_state_consistency.rs` and
//! `engine_event_routing_exhaustive.rs`) to pin the structural invariants
//! required by the receive criteria #2, #11, #13, #15, #17, #19.
//!
//! These complement the behavioural unit tests in
//! `src/modal/live_strategy_form.rs::tests` (`validate` / `prefill_from_scenario`
//! / `release_scenario_pending` / disabled_reason / Submit-action wiring).

const HANDLER_REPLAY: &str = include_str!("../src/handlers/replay.rs");
const HANDLER_VENUE: &str = include_str!("../src/handlers/venue.rs");
const HANDLER_ENGINE: &str = include_str!("../src/handlers/engine.rs");
const MAIN_RS: &str = include_str!("../src/main.rs");
const MESSAGES: &str = include_str!("../src/messages.rs");
const DASHBOARD: &str = include_str!("../src/screen/dashboard.rs");

fn handler_arm(needle: &str) -> &'static str {
    let start = HANDLER_REPLAY
        .find(needle)
        .unwrap_or_else(|| panic!("handler arm not found: {needle}"));
    let rest = &HANDLER_REPLAY[start..];
    // Slice up to the next ReplayMsg arm (or end), with a safe char-boundary
    // floor in case the slice end falls inside a multibyte UTF-8 character
    // (Japanese comments are common in this codebase).
    let mut end = rest[1..]
        .find("\n            ReplayMsg::")
        .map(|i| i + 1)
        .unwrap_or(rest.len());
    while end > 0 && !rest.is_char_boundary(end) {
        end -= 1;
    }
    &rest[..end]
}

/// Locate a top-level handler arm distinguished from inner construction calls.
///
/// `match_arms` lists alternative source patterns; the first one that exists in
/// the file is used as the slice start. Each pattern should uniquely identify
/// a handler arm (e.g. include the destructuring `{ strategy_id, token } =>`).
fn handler_arm_match(match_arms: &[&str]) -> &'static str {
    for pat in match_arms {
        if let Some(start) = HANDLER_REPLAY.find(pat) {
            let rest = &HANDLER_REPLAY[start..];
            let mut end = rest[1..]
                .find("\n            ReplayMsg::")
                .map(|i| i + 1)
                .unwrap_or(rest.len());
            while end > 0 && !rest.is_char_boundary(end) {
                end -= 1;
            }
            return &rest[..end];
        }
    }
    panic!("no handler arm found for any of: {match_arms:?}");
}

// ── 受入基準 #2 + #11: LiveStrategyReady → 4 ペイン自動生成 + 冪等 ─────────────

#[test]
fn test_live_strategy_ready_auto_generates_four_panes() {
    let arm = handler_arm("ReplayMsg::LiveStrategyReady {");
    // R2-B H7: factory `LiveStrategyState::try_running` 経由で空文字列 sentinel を
    // 防ぐ。caller は struct literal の代わりに factory を呼ぶ。
    assert!(
        arm.contains("LiveStrategyState::try_running"),
        "LiveStrategyReady arm must transition LiveStrategyState via try_running factory: {arm}"
    );
    assert!(
        arm.contains("auto_generate_live_panes"),
        "LiveStrategyReady arm must call auto_generate_live_panes: {arm}"
    );
}

/// R2-B H1: LiveStrategyReady arm は live_strategy_pending_strategy_id を None に戻す。
/// これがないと reconnect 後に旧 pending_strategy_id が残り、後続の LiveWarmupTimeoutFired
/// と誤照合してタイムアウトバナーを誤発火させる。
#[test]
fn test_live_strategy_ready_clears_pending_strategy_id() {
    let arm = handler_arm("ReplayMsg::LiveStrategyReady {");
    assert!(
        arm.contains("live_strategy_pending_strategy_id = None"),
        "LiveStrategyReady arm must clear live_strategy_pending_strategy_id: {arm}"
    );
}

/// R2-B H2: view() 内に live_warmup_timeout_banner を表示するパスがあることを source-pin する。
/// strategy_load_error と同パターン (banner_msg + 「再試行」ボタン + DismissLiveWarmupTimeoutBanner)
/// を要求する。banner 文字列の中身は handler 側で生成するため pin しない。
#[test]
fn test_view_renders_live_warmup_timeout_banner() {
    assert!(
        MAIN_RS.contains("self.live_warmup_timeout_banner"),
        "main.rs must reference self.live_warmup_timeout_banner in view()"
    );
    // Banner rendering signature: live_warmup_timeout_banner + 再試行 button + Dismiss msg.
    let pos = MAIN_RS
        .find("if let Some(banner_msg) = &self.live_warmup_timeout_banner")
        .expect("view() must render live_warmup_timeout_banner with `if let Some(banner_msg)`");
    let mut end = (pos + 1500).min(MAIN_RS.len());
    while end > 0 && !MAIN_RS.is_char_boundary(end) {
        end -= 1;
    }
    let window = &MAIN_RS[pos..end];
    assert!(
        window.contains("再試行"),
        "warmup banner must include 「再試行」 button label: {window}"
    );
    assert!(
        window.contains("DismissLiveWarmupTimeoutBanner"),
        "warmup banner button must dispatch DismissLiveWarmupTimeoutBanner: {window}"
    );
}

#[test]
fn test_live_strategy_ready_idempotent_on_double_emit() {
    // Idempotency: auto_generate_live_panes uses live_pane_keys HashSet to
    // skip when the same triple has already been generated.
    assert!(
        DASHBOARD.contains("live_pane_keys.contains"),
        "auto_generate_live_panes must check live_pane_keys for idempotent skip"
    );
    assert!(
        DASHBOARD.contains("live_pane_keys.insert"),
        "auto_generate_live_panes must insert into live_pane_keys after generation"
    );
    // Triple-key constraint: (strategy_id, instrument_id, venue).
    assert!(
        DASHBOARD.contains("(String, String, String)"),
        "live_pane_keys must use a (strategy_id, instrument_id, venue) triple"
    );
}

#[test]
fn test_live_strategy_state_holds_triple() {
    assert!(
        MAIN_RS.contains("Running {")
            && MAIN_RS.contains("strategy_id: String")
            && MAIN_RS.contains("instrument_id: String")
            && MAIN_RS.contains("venue: String"),
        "LiveStrategyState::Running must carry (strategy_id, instrument_id, venue)"
    );
}

// ── 受入基準 #13: LIVE_SCENARIO 戦略 → modal prefill ──────────────────────────

#[test]
fn test_live_strategy_scenario_loaded_prefills_form() {
    let arm = handler_arm("ReplayMsg::LiveStrategyScenarioLoaded {");
    assert!(
        arm.contains("prefill_from_scenario"),
        "LiveStrategyScenarioLoaded arm must call prefill_from_scenario: {arm}"
    );
    assert!(
        arm.contains("pending_scenario_request_id"),
        "must check pending_scenario_request_id to discard stale responses: {arm}"
    );
}

// ── 受入基準 #15: warm_up timeout banner + リセット ────────────────────────────

#[test]
fn test_engine_started_without_live_strategy_ready_shows_timeout_banner() {
    let arm = handler_arm("ReplayMsg::LiveStarted {");
    assert!(
        arm.contains("LIVE_WARMUP_TIMEOUT_SECS"),
        "LiveStarted arm must arm a 60s timer using LIVE_WARMUP_TIMEOUT_SECS: {arm}"
    );
    assert!(
        arm.contains("LiveWarmupTimeoutFired"),
        "LiveStarted arm must emit LiveWarmupTimeoutFired after the timeout: {arm}"
    );
    let timeout_arm =
        handler_arm_match(&["ReplayMsg::LiveWarmupTimeoutFired { strategy_id, token } =>"]);
    assert!(
        timeout_arm.contains("live_warmup_timeout_banner"),
        "LiveWarmupTimeoutFired arm must set live_warmup_timeout_banner: {timeout_arm}"
    );
    assert!(
        timeout_arm.contains("ライブ戦略起動失敗") || timeout_arm.contains("warm_up timeout"),
        "timeout banner must contain the fixed wording: {timeout_arm}"
    );
}

#[test]
fn test_warming_up_resets_timeout_counter() {
    let arm = handler_arm("ReplayMsg::LiveWarmingUp {");
    assert!(
        arm.contains("live_warmup_timeout_token"),
        "LiveWarmingUp arm must bump live_warmup_timeout_token to reset the timer: {arm}"
    );
    assert!(
        arm.contains("wrapping_add(1)"),
        "token bump must be wrapping_add(1) to avoid overflow panic: {arm}"
    );
    assert!(
        arm.contains("Task::perform"),
        "LiveWarmingUp must restart the 60s tokio sleep timer: {arm}"
    );
}

/// R2-B H4: EngineError{code:"node_build_failed", strategy_id} を
/// LiveStrategyBuildFailed ReplayMsg に変換する map_engine_event_to_message ルート、
/// ハンドラが LiveStrategyState を Idle に戻し teardown_live_panes を呼ぶこと、
/// dashboard.rs に teardown_live_panes API があることを 3 点 pin する。
#[test]
fn test_node_build_failed_resets_state_and_teardowns_panes() {
    // (1) map_engine_event_to_message が code=="node_build_failed" を
    //     LiveStrategyBuildFailed に変換するパスがあること。
    assert!(
        MAIN_RS.contains("\"node_build_failed\""),
        "map_engine_event_to_message must branch on EngineError code == \"node_build_failed\""
    );
    assert!(
        MAIN_RS.contains("LiveStrategyBuildFailed"),
        "main.rs must emit ReplayMsg::LiveStrategyBuildFailed for node_build_failed"
    );

    // (2) Handler arm が LiveStrategyState::Idle に戻し teardown_live_panes を呼ぶ。
    let arm = handler_arm("ReplayMsg::LiveStrategyBuildFailed {");
    assert!(
        arm.contains("LiveStrategyState::Idle"),
        "LiveStrategyBuildFailed arm must reset to LiveStrategyState::Idle: {arm}"
    );
    assert!(
        arm.contains("teardown_live_panes"),
        "LiveStrategyBuildFailed arm must call teardown_live_panes: {arm}"
    );

    // (3) dashboard.rs に teardown_live_panes 関数があり、live_pane_keys を掃除する。
    assert!(
        DASHBOARD.contains("pub fn teardown_live_panes"),
        "dashboard.rs must declare pub fn teardown_live_panes"
    );
    let pos = DASHBOARD
        .find("pub fn teardown_live_panes")
        .expect("teardown_live_panes function not found");
    let mut end = (pos + 3000).min(DASHBOARD.len());
    while end > 0 && !DASHBOARD.is_char_boundary(end) {
        end -= 1;
    }
    let body = &DASHBOARD[pos..end];
    assert!(
        body.contains("live_pane_keys"),
        "teardown_live_panes must touch live_pane_keys: {body}"
    );
    assert!(
        body.contains("panes.close"),
        "teardown_live_panes must close panes: {body}"
    );
}

/// R2-B M8: EngineMsg::Connected (= reconnect) で live form の
/// pending_scenario_request_id を解除する経路が engine handler にあること。
/// reconnect で in-flight な LoadLiveStrategyScenario の応答は失われるため、
/// pending を残したまま放置すると手入力で続行できなくなる。
#[test]
fn test_engine_connected_releases_scenario_pending() {
    let pos = HANDLER_ENGINE
        .find("EngineMsg::Connected(conn) =>")
        .expect("EngineMsg::Connected arm not found");
    let mut end = (pos + 3000).min(HANDLER_ENGINE.len());
    while end > 0 && !HANDLER_ENGINE.is_char_boundary(end) {
        end -= 1;
    }
    let arm = &HANDLER_ENGINE[pos..end];
    assert!(
        arm.contains("live_strategy_form_modal"),
        "EngineMsg::Connected arm must reach live_strategy_form_modal: {arm}"
    );
    assert!(
        arm.contains("release_scenario_pending"),
        "EngineMsg::Connected arm must call release_scenario_pending on the form: {arm}"
    );
}

/// R2-B M7: VenueReady で live form の disabled_reason を None に解除する
/// 経路が tachibana / kabu 両方の handler arm に存在することを source-pin する。
/// 同じく LoginError{market_closed:true} で「市場が閉場中です」を set する経路も pin。
#[test]
fn test_disabled_reason_cleared_on_venue_ready() {
    // (1) live_strategy_form.rs: set_disabled_reason setter が存在する。
    let modal_src = include_str!("../src/modal/live_strategy_form.rs");
    assert!(
        modal_src.contains("pub fn set_disabled_reason"),
        "live_strategy_form.rs must declare pub fn set_disabled_reason"
    );

    // (2) venue handler: VenueEvent::Ready arm で set_disabled_reason(None) を呼ぶ。
    //     tachibana / kabu の両 venue 経路で対称に必要。
    let ready_calls = HANDLER_VENUE.matches("set_disabled_reason(None)").count();
    assert!(
        ready_calls >= 2,
        "VenueEvent::Ready arm must call set_disabled_reason(None) for both tachibana and kabu \
         (got {ready_calls} occurrences)"
    );

    // (3) venue handler: LoginError{market_closed:true} で
    //     set_disabled_reason(Some("市場が閉場中です"...)) を呼ぶ経路がある。
    assert!(
        HANDLER_VENUE.contains("市場が閉場中です"),
        "venue handler must use fixed wording 「市場が閉場中です」 for market_closed"
    );
    assert!(
        HANDLER_VENUE.contains("market_closed") && HANDLER_VENUE.contains("set_disabled_reason"),
        "venue handler must wire market_closed → set_disabled_reason"
    );
}

/// R2-B H3: LiveWarmingUp arm は strategy_id を pending / running と照合し、
/// mismatch の場合は早期 return する（旧 strategy の遅延 ticker で banner / timer が
/// 誤動作するのを防ぐ）。
#[test]
fn test_live_warming_up_ignores_mismatched_strategy_id() {
    let arm = handler_arm("ReplayMsg::LiveWarmingUp {");
    assert!(
        arm.contains("live_strategy_pending_strategy_id"),
        "LiveWarmingUp arm must check live_strategy_pending_strategy_id: {arm}"
    );
    assert!(
        arm.contains("LiveStrategyState::Running"),
        "LiveWarmingUp arm must also check the running-state strategy_id: {arm}"
    );
    // mismatch 経路は早期 return (Task::none()) を返す。
    assert!(
        arm.contains("return Task::none()"),
        "LiveWarmingUp arm must early-return when strategy_id mismatches: {arm}"
    );
}

#[test]
fn test_live_warmup_timeout_constant_is_60s() {
    // Pin the canonical value (統一決定 #17). Any change must be deliberate.
    assert!(
        MAIN_RS.contains("pub(crate) const LIVE_WARMUP_TIMEOUT_SECS: u64 = 60"),
        "LIVE_WARMUP_TIMEOUT_SECS must be exactly 60s (統一決定 #17 / 受入基準 #15)"
    );
}

// ── 受入基準 #17: EngineRehello → 4 ペイン冪等再生 ─────────────────────────────

#[test]
fn test_engine_rehello_replays_live_strategy_ready_via_pending_config() {
    // EngineRehello in venue handlers must dispatch LiveStrategyRehelloReplay.
    let count = HANDLER_VENUE.matches("LiveStrategyRehelloReplay").count();
    assert!(
        count >= 2,
        "Both tachibana and kabu EngineRehello arms must dispatch \
         LiveStrategyRehelloReplay (got {count} occurrences)"
    );
    // The handler must use LiveStrategyState::Running fields to re-run pane gen.
    let arm = handler_arm("ReplayMsg::LiveStrategyRehelloReplay");
    assert!(
        arm.contains("LiveStrategyState::Running"),
        "LiveStrategyRehelloReplay arm must pattern-match Running state: {arm}"
    );
    assert!(
        arm.contains("auto_generate_live_panes"),
        "LiveStrategyRehelloReplay arm must re-run auto_generate_live_panes: {arm}"
    );
}

// ── 受入基準 #19: LoadLiveStrategyScenario fallback ────────────────────────────

#[test]
fn test_load_live_strategy_scenario_timeout_falls_back_to_manual_input() {
    // NativeOpenStrategyPicked has both replay and live branches in a single
    // arm. We pin the live-branch invariants by checking the handler text
    // contains the live-mode IPC + 5s fallback wiring (entire file scope).
    assert!(
        HANDLER_REPLAY.contains("Command::LoadLiveStrategyScenario"),
        "live branch must send Command::LoadLiveStrategyScenario IPC"
    );
    assert!(
        HANDLER_REPLAY.contains("LIVE_SCENARIO_FALLBACK_TIMEOUT_SECS"),
        "live branch must arm a fallback timer with LIVE_SCENARIO_FALLBACK_TIMEOUT_SECS"
    );
    // The fallback timer must emit LiveStrategyScenarioFallback.
    assert!(
        HANDLER_REPLAY.contains("LiveStrategyScenarioFallback {"),
        "fallback timer must emit LiveStrategyScenarioFallback"
    );
    // The fallback arm must release the pending scenario.
    let fallback =
        handler_arm_match(&["ReplayMsg::LiveStrategyScenarioFallback { request_id } =>"]);
    assert!(
        fallback.contains("release_scenario_pending"),
        "LiveStrategyScenarioFallback arm must call release_scenario_pending: {fallback}"
    );
    // Pin the 5s constant.
    assert!(
        MAIN_RS.contains("pub(crate) const LIVE_SCENARIO_FALLBACK_TIMEOUT_SECS: u64 = 5"),
        "LIVE_SCENARIO_FALLBACK_TIMEOUT_SECS must be 5s (統一決定 #18)"
    );
}

#[test]
fn test_strategy_parse_failed_releases_form() {
    // venue handler IpcError arm must release the live form when code matches.
    assert!(
        HANDLER_VENUE.contains("strategy_parse_failed"),
        "venue IpcError handler must branch on code == \"strategy_parse_failed\""
    );
    let pos = HANDLER_VENUE
        .find("strategy_parse_failed")
        .expect("strategy_parse_failed branch not found");
    let mut end = (pos + 1500).min(HANDLER_VENUE.len());
    while end > 0 && !HANDLER_VENUE.is_char_boundary(end) {
        end -= 1;
    }
    let window = &HANDLER_VENUE[pos..end];
    assert!(
        window.contains("release_scenario_pending"),
        "strategy_parse_failed branch must call release_scenario_pending on the live form: \
         window=\n{window}"
    );
    assert!(
        window.contains("live_strategy_form_modal"),
        "strategy_parse_failed branch must access live_strategy_form_modal: {window}"
    );
}

// ── 受入基準 #8 (GUI part): SecondPasswordRequired → 赤帯 ────────────────────

#[test]
fn test_second_password_required_shows_status_banner() {
    let pos = HANDLER_VENUE
        .find("VenueMsg::SecondPasswordRequired(")
        .expect("SecondPasswordRequired arm not found");
    let mut end = (pos + 800).min(HANDLER_VENUE.len());
    while end > 0 && !HANDLER_VENUE.is_char_boundary(end) {
        end -= 1;
    }
    let window = &HANDLER_VENUE[pos..end];
    // 統一決定 #8 + #12 : fixed wording is 「第二暗証番号を設定してください」.
    assert!(
        window.contains("第二暗証番号を設定してください"),
        "SecondPasswordRequired arm must surface the fixed wording: {window}"
    );
    assert!(
        window.contains("Toast::error") || window.contains("notifications"),
        "SecondPasswordRequired arm must push a status notification: {window}"
    );
}

// ── disabled_reason → Submit disable ──────────────────────────────────────────

#[test]
fn test_disabled_reason_disables_submit() {
    // The view() function must omit on_press when disabled_reason is Some.
    let modal = include_str!("../src/modal/live_strategy_form.rs");
    let pos = modal
        .find("pub fn view(&self)")
        .expect("view() not found in live_strategy_form.rs");
    let mut end = (pos + 4000).min(modal.len());
    while end > 0 && !modal.is_char_boundary(end) {
        end -= 1;
    }
    let view = &modal[pos..end];
    assert!(
        view.contains("disabled_reason.is_none()"),
        "view() must omit on_press when disabled_reason is Some: {view}"
    );
    // Update arm must double-guard against Submit firing when disabled.
    let pos2 = modal
        .find("pub fn update(&mut self")
        .expect("update() not found");
    let mut end2 = (pos2 + 3000).min(modal.len());
    while end2 > 0 && !modal.is_char_boundary(end2) {
        end2 -= 1;
    }
    let update = &modal[pos2..end2];
    assert!(
        update.contains("self.disabled_reason.is_some()"),
        "update() must double-guard Submit when disabled_reason is Some: {update}"
    );
}

// ── R4 R3-RUST-1 + R3-RUST-2: warm_up progress UI 完成 ───────────────────────

/// 旧実装は `live_warmup_warming_message: Option<String>` を保持するだけで
/// view() に描画パスが無く、`LiveWarmingUp.progress` は `progress: _` で捨てて
/// いた。「進捗 banner + 数値表示」を実装するために
/// `live_warmup_warming_progress: Option<f32>` を追加し、view() でメッセージと
/// 進捗の両方をレンダリングする。
#[test]
fn test_main_rs_declares_live_warmup_warming_progress_field() {
    assert!(
        MAIN_RS.contains("live_warmup_warming_progress: Option<f32>"),
        "main.rs must declare live_warmup_warming_progress: Option<f32> field"
    );
    // 初期化箇所も pin
    assert!(
        MAIN_RS.contains("live_warmup_warming_progress: None"),
        "main.rs must initialize live_warmup_warming_progress to None"
    );
}

#[test]
fn test_live_warming_up_arm_saves_progress() {
    let arm = handler_arm("ReplayMsg::LiveWarmingUp {");
    // progress を `_` で捨てない。Some(progress) に保存する。
    assert!(
        arm.contains("live_warmup_warming_progress"),
        "LiveWarmingUp arm must save progress to live_warmup_warming_progress: {arm}"
    );
    // `Some(progress)` の代入式 (任意の prefix を許す)
    assert!(
        arm.contains("Some(progress)"),
        "LiveWarmingUp arm must wrap progress in Some(...) for the banner: {arm}"
    );
}

#[test]
fn test_view_renders_warming_up_progress_banner() {
    // view() が live_warmup_warming_message を描画するブロックを持つこと
    let pos = MAIN_RS
        .find("if let Some(msg) = &self.live_warmup_warming_message")
        .expect("view() must render live_warmup_warming_message via `if let Some(msg)`");
    let mut end = (pos + 1500).min(MAIN_RS.len());
    while end > 0 && !MAIN_RS.is_char_boundary(end) {
        end -= 1;
    }
    let window = &MAIN_RS[pos..end];
    // progress の数値表示 (% フォーマット)
    assert!(
        window.contains("live_warmup_warming_progress"),
        "warming-up banner must reference live_warmup_warming_progress: {window}"
    );
    // パーセンテージとして 0-100 にクランプして表示する
    assert!(
        window.contains("100.0") && window.contains("clamp"),
        "warming-up banner must format progress as 0-100% using clamp: {window}"
    );
}

#[test]
fn test_warming_up_progress_clears_on_ready() {
    let arm = handler_arm("ReplayMsg::LiveStrategyReady {");
    assert!(
        arm.contains("live_warmup_warming_progress = None"),
        "LiveStrategyReady arm must reset live_warmup_warming_progress to None: {arm}"
    );
}

#[test]
fn test_warming_up_progress_clears_on_live_stopped() {
    let arm = handler_arm("ReplayMsg::LiveStopped {");
    assert!(
        arm.contains("live_warmup_warming_progress = None"),
        "LiveStopped arm must reset live_warmup_warming_progress to None: {arm}"
    );
}

#[test]
fn test_warming_up_progress_clears_on_engine_connected() {
    // EngineConnected arm (handlers/engine.rs) 内でも progress を reset する
    let pos = HANDLER_ENGINE
        .find("EngineMsg::Connected(conn)")
        .expect("EngineMsg::Connected arm not found");
    let mut end = (pos + 6000).min(HANDLER_ENGINE.len());
    while end > 0 && !HANDLER_ENGINE.is_char_boundary(end) {
        end -= 1;
    }
    let arm = &HANDLER_ENGINE[pos..end];
    assert!(
        arm.contains("live_warmup_warming_progress = None"),
        "EngineConnected arm must reset live_warmup_warming_progress when not Running: {arm}"
    );
}

// ── R4 R3-SILENT-4: EngineConnected で pending_strategy_id を reset ──────────

/// engine reconnect 時、live session が Running でなければ pending_strategy_id /
/// warmup timeout token / warming message を reset すること。reconnect 直前まで
/// 「pending だが reply が来ない」状態だった場合、reconnect 後も古い
/// pending_strategy_id が残ると後続の LiveWarmupTimeoutFired を誤照合して
/// 既に消えた session のバナーを出してしまう silent UX failure になる。
#[test]
fn test_engine_connected_resets_pending_strategy_id() {
    let pos = HANDLER_ENGINE
        .find("EngineMsg::Connected(conn)")
        .expect("EngineMsg::Connected arm not found");
    let mut end = (pos + 6000).min(HANDLER_ENGINE.len());
    while end > 0 && !HANDLER_ENGINE.is_char_boundary(end) {
        end -= 1;
    }
    let arm = &HANDLER_ENGINE[pos..end];

    // 「live が Running でなければ pending 状態をクリアする」ガード + リセット文を pin。
    assert!(
        arm.contains("LiveStrategyState::Running"),
        "EngineConnected arm must check LiveStrategyState::Running before reset: {arm}"
    );
    assert!(
        arm.contains("live_strategy_pending_strategy_id = None"),
        "EngineConnected arm must reset live_strategy_pending_strategy_id when not Running: {arm}"
    );
    assert!(
        arm.contains("live_warmup_timeout_token"),
        "EngineConnected arm must bump live_warmup_timeout_token to invalidate stale timers: {arm}"
    );
    assert!(
        arm.contains("live_warmup_warming_message = None"),
        "EngineConnected arm must clear live_warmup_warming_message: {arm}"
    );
}

// ── R4 R3-SILENT-3: LiveStopped (正常停止) 経路でも teardown_live_panes ──────

/// 旧実装は LiveStopped arm で `clear_live_pane_keys()` だけ呼び、実際の 4 ペイン
/// は閉じられず残っていた。再起動時に `auto_generate_live_panes` が key 不一致で
/// 重複生成し、UX 上「同じパネルが 2 重に出る」 silent failure を抱えていた。
/// 正常停止経路も `node_build_failed` arm と同じく `teardown_live_panes(&strategy_id)`
/// で対称に閉じることを source-pin する。
#[test]
fn test_live_stopped_teardowns_panes() {
    let arm = handler_arm("ReplayMsg::LiveStopped {");
    assert!(
        arm.contains("teardown_live_panes"),
        "LiveStopped arm must call teardown_live_panes(&strategy_id) for symmetry \
         with node_build_failed teardown: {arm}"
    );
}

// ── R4 R3-RUST-3: EngineBusy{AnotherStrategyOnVenue} は venue 名を toast に含む ───

/// 旧実装は EngineBusy の fallback アーム (`_ =>`) で venue / busy_kind を
/// destructure せず汎用文言だけを出していたため、live 重複起動拒否時にどの
/// venue で reject されたかが GUI に表示されない silent UX failure を抱えていた。
/// busy_kind == AnotherStrategyOnVenue のとき venue を含む文言を出すこと、
/// および BusyKind enum を import していることを source-pin する。
#[test]
fn test_engine_busy_another_strategy_on_venue_includes_venue_in_toast() {
    // EngineBusy arm 全体を含む slice を切り出す。
    let pos = MAIN_RS
        .find("EngineEvent::EngineBusy {")
        .expect("EngineBusy arm not found in main.rs");
    let mut end = (pos + 2500).min(MAIN_RS.len());
    while end > 0 && !MAIN_RS.is_char_boundary(end) {
        end -= 1;
    }
    let arm = &MAIN_RS[pos..end];

    assert!(
        arm.contains("busy_kind"),
        "EngineBusy arm must destructure busy_kind: {arm}"
    );
    assert!(
        arm.contains("venue"),
        "EngineBusy arm must destructure venue: {arm}"
    );
    assert!(
        arm.contains("BusyKind::AnotherStrategyOnVenue"),
        "EngineBusy arm must match BusyKind::AnotherStrategyOnVenue: {arm}"
    );
    assert!(
        arm.contains("別の戦略"),
        "AnotherStrategyOnVenue toast must include 「別の戦略」 wording: {arm}"
    );
    // venue 値を文言にフォーマットすること (リテラル変数名は `v` を期待)。
    assert!(
        arm.contains("{v}") || arm.contains("venue.as_deref"),
        "AnotherStrategyOnVenue toast must format venue into the message: {arm}"
    );
}

// ── messages.rs invariants ────────────────────────────────────────────────────

#[test]
fn test_replay_msg_has_live_strategy_ready_variant() {
    assert!(
        MESSAGES.contains("LiveStrategyReady {"),
        "ReplayMsg must declare a LiveStrategyReady variant"
    );
    assert!(
        MESSAGES.contains("LiveStrategyRehelloReplay"),
        "ReplayMsg must declare LiveStrategyRehelloReplay for reconnect re-generation"
    );
    assert!(
        MESSAGES.contains("LiveStrategyScenarioLoaded {"),
        "ReplayMsg must declare LiveStrategyScenarioLoaded for prefill"
    );
    assert!(
        MESSAGES.contains("LiveStrategyScenarioFallback {"),
        "ReplayMsg must declare LiveStrategyScenarioFallback for the 5s timeout fallback"
    );
}

// ── R6 R5-SILENT-1: warm_up timeout 発火時に warming_message / progress を None に戻す ───

/// 旧実装は LiveWarmupTimeoutFired arm で banner だけ立て、
/// `live_warmup_warming_message` / `live_warmup_warming_progress` が残ったままに
/// なっていた。view() は warming banner と timeout banner の両方を描画するため、
/// 「ライブ戦略起動失敗」バナーの直下に古い「Warming up...」進捗バーが残る silent
/// UX failure になっていた。timeout 発火時に warming 系の表示状態を確実に消すこと
/// を source-pin する。
#[test]
fn test_live_warmup_timeout_clears_warming_message_and_progress() {
    let arm = handler_arm_match(&["ReplayMsg::LiveWarmupTimeoutFired { strategy_id, token } =>"]);
    assert!(
        arm.contains("live_warmup_warming_message = None"),
        "LiveWarmupTimeoutFired arm must clear live_warmup_warming_message to avoid \
         stale warming banner persisting after timeout: {arm}"
    );
    assert!(
        arm.contains("live_warmup_warming_progress = None"),
        "LiveWarmupTimeoutFired arm must clear live_warmup_warming_progress to avoid \
         stale progress bar persisting after timeout: {arm}"
    );
}

// ── R6 R5-SILENT-2: Error{code:"venue_not_connected"} を Rust 側で user 通知 ─────

/// 旧実装は IpcError ルーターの `code == "venue_not_connected"` を
/// buying_power / order_list / positions / strategy_load_failed / strategy_parse_failed
/// のどれにも match させず、最終 `else log::debug!(...)` で握りつぶしていた。
/// engine 側で venue 未接続のため live start を reject されているのに、GUI には
/// 何も通知が出ない silent failure。Toast::error で user 通知し、
/// `menu_bar.live_bar.strategy_file_stem` を clear して再試行可能な状態に戻す。
#[test]
fn test_venue_not_connected_user_notification() {
    // (1) IpcError 直下に `code == "venue_not_connected"` 分岐を持つこと。
    assert!(
        HANDLER_VENUE.contains("venue_not_connected"),
        "venue IpcError handler must branch on code == \"venue_not_connected\""
    );
    let pos = HANDLER_VENUE
        .find("\"venue_not_connected\"")
        .expect("venue_not_connected branch not found");
    let mut end = (pos + 1500).min(HANDLER_VENUE.len());
    while end > 0 && !HANDLER_VENUE.is_char_boundary(end) {
        end -= 1;
    }
    let window = &HANDLER_VENUE[pos..end];

    // (2) Toast::error で user 通知すること。
    assert!(
        window.contains("Toast::error"),
        "venue_not_connected branch must push a Toast::error to surface the failure \
         to the user (no silent debug log): window=\n{window}"
    );

    // (3) live_bar.strategy_file_stem を None に戻して再試行可能な状態にすること。
    assert!(
        window.contains("strategy_file_stem = None"),
        "venue_not_connected branch must clear menu_bar.live_bar.strategy_file_stem to \
         allow retry: window=\n{window}"
    );
}

// ── R4 Group C (silent-MEDIUM-1): LiveStopped no-op pin ─────────────────────────

/// `python/tests/test_engine_runner_live_warmup_failure.py:209-213` のコメントが
/// 唯一の根拠だった「Rust は EngineStarted 無しの EngineStopped を no-op として
/// 扱う」契約を Rust 側でも source-pin する。具体的には LiveStopped arm の `else`
/// 分岐 (running_match=false && pending_match=false) が:
///   - `log::warn!` を残し
///   - `live_strategy = LiveStrategyState::Idle` などの state mutation を含まず
///   - `teardown_live_panes` 等のペイン削除も呼ばない
///
/// ことを assert する。
///
/// この contract が壊れると、Python 側 `EngineStopped` emit が初期状態 (Idle) の
/// Rust に届いた瞬間に LiveBarState::default() が呼ばれて bar が消える等の silent
/// regression を生む。
#[test]
fn test_live_stopped_no_op_when_idle_pin() {
    let arm = handler_arm("ReplayMsg::LiveStopped {");
    let else_pos = arm
        .find("} else {")
        .expect("LiveStopped arm must have an `} else {` branch for no-op path");
    let else_body = &arm[else_pos..];

    assert!(
        else_body.contains("log::warn!"),
        "LiveStopped no-op arm must call log::warn! to keep the silent failure \
         observable in logs: else_body=\n{else_body}"
    );
    assert!(
        else_body.contains("strategy_id mismatch"),
        "LiveStopped no-op arm warning must explain the mismatch (helps diagnose \
         out-of-order EngineStopped events): else_body=\n{else_body}"
    );

    // state mutation が無いことを pin する (no-op contract)。
    assert!(
        !else_body.contains("LiveStrategyState::Idle"),
        "LiveStopped no-op arm must NOT mutate state to Idle (already Idle by \
         construction; mutation here would zero out an unrelated session): \
         else_body=\n{else_body}"
    );
    assert!(
        !else_body.contains("teardown_live_panes"),
        "LiveStopped no-op arm must NOT call teardown_live_panes (panes are owned \
         by the Running session whose strategy_id we don't match): \
         else_body=\n{else_body}"
    );
    assert!(
        !else_body.contains("LiveBarState::default()"),
        "LiveStopped no-op arm must NOT reset menu_bar.live_bar (would erase the \
         unrelated session's bar state): else_body=\n{else_body}"
    );
    assert!(
        !else_body.contains("clear_live_strategy_portfolio"),
        "LiveStopped no-op arm must NOT clear portfolio (would discard the \
         unrelated session's data): else_body=\n{else_body}"
    );
}

// ── R4 Group A (silent-HIGH-1): LiveStrategyBuildFailed が code を持つ ──────────

/// `EngineError{code, strategy_id=Some(_)}` の以下 5 つを `LiveStrategyBuildFailed`
/// に統合した (R4 Group A 設計判断: 既存 variant を再利用)。`messages.rs` 側で
/// `code` field を持つこと、handler 側が code 別の toast 文言を出すことを pin する。
#[test]
fn test_live_strategy_build_failed_carries_code_field() {
    assert!(
        MESSAGES.contains("LiveStrategyBuildFailed {"),
        "ReplayMsg must declare a LiveStrategyBuildFailed variant"
    );
    let var_pos = MESSAGES
        .find("LiveStrategyBuildFailed {")
        .expect("LiveStrategyBuildFailed variant not found");
    let mut end = (var_pos + 600).min(MESSAGES.len());
    while end > 0 && !MESSAGES.is_char_boundary(end) {
        end -= 1;
    }
    let body = &MESSAGES[var_pos..end];
    assert!(
        body.contains("code:"),
        "LiveStrategyBuildFailed must carry a `code: String` field so the handler \
         can branch on EngineError.code for toast wording: body=\n{body}"
    );
}

#[test]
fn test_engine_error_routes_warm_up_codes_to_build_failed_arm() {
    // map_engine_event_to_message in main.rs branches on a code allow-list.
    //
    // R6: needle を `const STRATEGY_ABORT_CODES` (= 定義の位置) に強化。
    // 旧版 `find("STRATEGY_ABORT_CODES")` は doc コメント内の同名文字列に
    // 先に hit して body が誤位置になる脆さがあった (R6 で実際に踏んだ)。
    let abort_pos = MAIN_RS.find("const STRATEGY_ABORT_CODES").expect(
        "`const STRATEGY_ABORT_CODES` must exist in main.rs to route warm_up_failed et al.",
    );
    let mut end = (abort_pos + 800).min(MAIN_RS.len());
    while end > 0 && !MAIN_RS.is_char_boundary(end) {
        end -= 1;
    }
    let body = &MAIN_RS[abort_pos..end];

    for required_code in [
        "warm_up_failed",
        "kernel_unavailable",
        "venue_not_supported",
        "market_closed",
        "node_build_failed",
        // R6 silent-HIGH-1: server.py が必ず emit する 2 code (engine_run_failed
        // = runner 内部例外, timeout = 3600s wait_for) もこの allow-list に
        // 含まれていなければ silent regression を起こす (state machine 固着)。
        "engine_run_failed",
        "timeout",
    ] {
        assert!(
            body.contains(required_code),
            "abort-codes allow-list must include {required_code:?} so EngineError with \
             that code routes to LiveStrategyBuildFailed (otherwise silent-HIGH-1 \
             regression): body=\n{body}"
        );
    }
}

#[test]
fn test_live_strategy_build_failed_handler_branches_on_code() {
    // handler arm が code 別の toast prefix を出すこと
    let arm = handler_arm("ReplayMsg::LiveStrategyBuildFailed {");
    assert!(
        arm.contains("code"),
        "LiveStrategyBuildFailed handler must destructure code: {arm}"
    );
    for prefix in [
        "warm_up_failed",
        "kernel_unavailable",
        "venue_not_supported",
        "market_closed",
        "node_build_failed",
        // R7 サニティで発見: R6-A で abort-codes allow-list と handler の
        // toast prefix 表に "engine_run_failed" / "timeout" を追加したが、
        // 本テスト (handler arm の prefix pin) を更新し忘れていた。allow-list
        // を pin する `test_engine_error_routes_warm_up_codes_to_build_failed_arm`
        // と handler の toast 文言を pin する本テストの対称性を確保する。
        "engine_run_failed",
        "timeout",
    ] {
        assert!(
            arm.contains(prefix),
            "LiveStrategyBuildFailed handler must format toast prefix for code \
             {prefix:?}: {arm}"
        );
    }
}

// ── issue #42 R1 MEDIUM-1: GUI venue dropdown + prefill from scenario ───────

/// R1 MEDIUM-1: `NativeOpenStrategyPicked` の live 分岐は modal を構築する際に
/// `engine_client::capabilities::supports_live_strategy` で filter した
/// `available_venues` を渡す。これがないと kabu-only / tachibana-only 構成で
/// dropdown に non-supported venue が出る silent UX failure になる。
#[test]
fn test_native_open_strategy_picked_filters_available_venues_by_capability() {
    let arm = handler_arm("ReplayMsg::NativeOpenStrategyPicked(picked) =>");
    assert!(
        arm.contains("supports_live_strategy"),
        "NativeOpenStrategyPicked live arm must filter available_venues via \
         engine_client::capabilities::supports_live_strategy: {arm}"
    );
    assert!(
        arm.contains("available_venues"),
        "NativeOpenStrategyPicked live arm must populate available_venues on \
         LiveStrategyFormModal: {arm}"
    );
    // tachibana / kabu_station の両方が候補として走査される (cap filter で 0/1/2 件
    // に絞られる)。
    for venue in ["tachibana", "kabu_station"] {
        assert!(
            arm.contains(&format!("\"{venue}\"")),
            "live arm must iterate venue {venue:?} for capability filter: {arm}"
        );
    }
}

/// R1 MEDIUM-1: `NativeOpenStrategyPicked` の live 分岐は modal の `connected_venue`
/// を `tachibana_state.is_ready()` / `kabu_state.is_ready()` から導出する。
/// これがないと「kabu に login 中だが tachibana 用 scenario を開いた」状況を
/// `validate()` が検出できず、Submit 後の server.py `venue_not_supported` reject
/// まで気付けない silent UX failure になる。
#[test]
fn test_native_open_strategy_picked_sets_connected_venue_from_state() {
    let arm = handler_arm("ReplayMsg::NativeOpenStrategyPicked(picked) =>");
    assert!(
        arm.contains("connected_venue"),
        "NativeOpenStrategyPicked live arm must pass connected_venue to modal: {arm}"
    );
    assert!(
        arm.contains("tachibana_state.is_ready()"),
        "connected_venue must be derived from tachibana_state.is_ready(): {arm}"
    );
    assert!(
        arm.contains("kabu_state.is_ready()"),
        "connected_venue must be derived from kabu_state.is_ready(): {arm}"
    );
}

/// R1 MEDIUM-1: `LiveStrategyScenarioLoaded` arm は scenario.venue を form に
/// prefill する。旧実装は `..` で venue を破棄していたため、戦略ファイル指定の
/// venue が GUI 側に届かなかった (silent UX failure)。
#[test]
fn test_live_strategy_scenario_loaded_prefills_venue() {
    let arm = handler_arm("ReplayMsg::LiveStrategyScenarioLoaded {");
    assert!(
        arm.contains("venue,"),
        "LiveStrategyScenarioLoaded arm must destructure venue (not discard with `..`): {arm}"
    );
    // prefill_from_scenario(.., venue) で venue が確実に流れていること。
    let prefill_pos = arm
        .find("prefill_from_scenario(")
        .expect("LiveStrategyScenarioLoaded must call prefill_from_scenario");
    let mut prefill_end = (prefill_pos + 500).min(arm.len());
    while prefill_end > 0 && !arm.is_char_boundary(prefill_end) {
        prefill_end -= 1;
    }
    let prefill_window = &arm[prefill_pos..prefill_end];
    assert!(
        prefill_window.contains("venue"),
        "prefill_from_scenario invocation must pass venue argument: {prefill_window}"
    );
}

/// R3 M11: `LiveStrategyFormModal::validate()` の dynamic test が
/// `src/modal/live_strategy_form.rs::tests` モジュール内に存在することを pin。
/// flowsurface は binary crate のため tests/ 配下から直接 use できないが、
/// inline test モジュールが venue 関連 validation を実 struct でカバーしている
/// (M3/M4 の RED test を `test_validate_returns_venue_error_before_prod_mode_error`
/// などとして追加済み)。本 source-pin は test 削除 regression を防ぐ。
#[test]
fn test_live_form_module_has_dynamic_validate_tests_for_venue() {
    let form_src = include_str!("../src/modal/live_strategy_form.rs");
    for needle in [
        "fn test_validate_returns_venue_error_before_prod_mode_error",
        "fn test_validate_rejects_venue_not_in_available_list",
        "fn test_validate_rejects_venue_mismatch_with_connected",
        "fn test_validate_prod_mode_error_message_is_venue_aware_for_kabu",
    ] {
        assert!(
            form_src.contains(needle),
            "live_strategy_form.rs::tests must contain dynamic validate test {needle:?} \
             (R3 M11: do not delete venue / prod_mode validate coverage)"
        );
    }
}

/// R3 M2: `LiveStrategyScenarioLoaded` arm は scenario.venue が Some + その venue が
/// ready のとき、form の `connected_venue` も scenario 側の値で上書きする。
/// 両 venue Ready 時に initial が tachibana 固定だと、kabu scenario を開いたユーザに
/// 「engine 接続 venue 'tachibana' と一致しない」誤誘導が出てしまうため、scenario
/// を SoT として connected_venue を refine する。
#[test]
fn test_live_strategy_scenario_loaded_refines_connected_venue_from_scenario() {
    let arm = handler_arm("ReplayMsg::LiveStrategyScenarioLoaded {");
    assert!(
        arm.contains("set_connected_venue") || arm.contains("connected_venue ="),
        "LiveStrategyScenarioLoaded arm must refine form.connected_venue from \
         scenario.venue when both ready (R3 M2): {arm}"
    );
    // scenario.venue は両 venue ready のとき SoT として使う = `tachibana_state` と
    // `kabu_state` の両方を見て分岐していること。
    assert!(
        arm.contains("tachibana_state") && arm.contains("kabu_state"),
        "scenario-based connected_venue refinement must consider both \
         tachibana_state and kabu_state readiness (R3 M2): {arm}"
    );
}

/// R1 MEDIUM-1: Action::Submit に venue フィールドが載っていて、handler 側で
/// destructure している (Submit 経路が venue を unintentional に discard しない
/// ことを pin する)。`EngineStartConfig` に venue field を載せる将来の経路の足場。
#[test]
fn test_live_strategy_form_msg_submit_destructures_venue() {
    let arm = handler_arm("ReplayMsg::LiveStrategyFormMsg(msg) =>");
    assert!(
        arm.contains("Action::Submit {"),
        "LiveStrategyFormMsg arm must match Action::Submit: {arm}"
    );
    assert!(
        arm.contains("venue,"),
        "LiveStrategyFormMsg::Submit arm must destructure venue field: {arm}"
    );
}
