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
    assert!(
        arm.contains("LiveStrategyState::Running"),
        "LiveStrategyReady arm must transition LiveStrategyState to Running: {arm}"
    );
    assert!(
        arm.contains("auto_generate_live_panes"),
        "LiveStrategyReady arm must call auto_generate_live_panes: {arm}"
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
