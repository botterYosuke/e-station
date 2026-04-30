//! §4c source-pin tests: auto_generate_replay_panes auto-bind.
//!
//! These structural pins verify that:
//!
//! 1. `ControlApiCommand::AutoGenerateReplayPanes` carries a `granularity` field
//!    so that the pane builder can choose D1 vs M1 vs skip-CandlestickChart.
//!
//! 2. `dashboard::auto_generate_replay_panes` calls `set_content_and_streams`
//!    (not just `State::with_kind`) so the replay kline/trade streams are bound
//!    into the pane state immediately, triggering the iced subscription loop.
//!
//! 3. The function uses `Exchange::ReplayStock` (or `Venue::Replay`) when
//!    constructing the TickerInfo stub for the auto-generated pane.
//!
//! Because `Dashboard` requires an iced runtime to instantiate, we inspect
//! source text instead of running the function directly (same technique as
//! `test_replay_order_list_view.rs`).

fn read_dashboard_src() -> String {
    std::fs::read_to_string(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/src/screen/dashboard.rs"
    ))
    .expect("read src/screen/dashboard.rs")
}

fn read_replay_api_src() -> String {
    std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/src/replay_api.rs"))
        .expect("read src/replay_api.rs")
}

// ── 4c-1: ControlApiCommand carries granularity ───────────────────────────────

#[test]
fn control_api_command_auto_generate_has_granularity_field() {
    let src = read_replay_api_src();

    // Find the AutoGenerateReplayPanes variant body
    let start = src
        .find("AutoGenerateReplayPanes")
        .expect("AutoGenerateReplayPanes variant not found in replay_api.rs");
    let after = &src[start..];
    let end = after.find('}').expect("closing brace not found");
    let body = &after[..end];

    assert!(
        body.contains("granularity"),
        "ControlApiCommand::AutoGenerateReplayPanes must have a `granularity` field \
         so the pane builder can select D1 vs M1 vs no-CandlestickChart. \
         §4c acceptance criterion."
    );
}

// ── 4c-2: auto_generate_replay_panes calls set_content_and_streams ────────────

#[test]
fn auto_generate_replay_panes_calls_set_content_and_streams() {
    let src = read_dashboard_src();

    // Locate the function body
    let start = src
        .find("fn auto_generate_replay_panes")
        .expect("auto_generate_replay_panes not found in dashboard.rs");
    let after = &src[start..];
    // The function ends at the outer `}` — find the next `pub fn` or `}` at top level.
    // As a heuristic, grab a generous window covering the function (~185 lines ≈ 11 kB).
    let window = &after[..after.len().min(15_000)];

    assert!(
        window.contains("set_content_and_streams"),
        "auto_generate_replay_panes must call set_content_and_streams to bind \
         the kline/trade stream into the pane state. Using only State::with_kind \
         leaves the pane unbound (Content::Kline {{ chart: None }}) and no \
         IPC subscription is sent. §4c acceptance criterion."
    );
}

// ── 4c-3: auto_generate_replay_panes uses Exchange::ReplayStock ───────────────

#[test]
fn auto_generate_replay_panes_uses_replay_stock_exchange() {
    let src = read_dashboard_src();

    // Either the function itself or a helper it calls must reference ReplayStock
    // (or Venue::Replay). Accept either form.
    assert!(
        src.contains("Exchange::ReplayStock") || src.contains("Venue::Replay"),
        "dashboard.rs must reference Exchange::ReplayStock (or Venue::Replay) \
         when constructing TickerInfo for the auto-generated replay pane so that \
         the IPC venue filter in backend.rs matches 'replay'. §4c acceptance criterion."
    );

    // Also verify that auto_generate_replay_panes calls the ticker-info helper.
    let start = src
        .find("fn auto_generate_replay_panes")
        .expect("auto_generate_replay_panes not found in dashboard.rs");
    let after = &src[start..];
    let window = &after[..after.len().min(6000)];
    assert!(
        window.contains("replay_ticker_info") || window.contains("ReplayStock"),
        "auto_generate_replay_panes must call replay_ticker_info (or directly use \
         Exchange::ReplayStock) to create the TickerInfo stub. §4c acceptance criterion."
    );
}

// ── 4c-4: function signature accepts granularity ─────────────────────────────

#[test]
fn auto_generate_replay_panes_signature_has_granularity() {
    let src = read_dashboard_src();

    let start = src
        .find("fn auto_generate_replay_panes")
        .expect("auto_generate_replay_panes not found in dashboard.rs");
    // Grab just the function signature (up to the opening `{`)
    let after = &src[start..];
    let sig_end = after.find('{').expect("opening brace not found");
    let sig = &after[..sig_end];

    assert!(
        sig.contains("granularity") || sig.contains("Timeframe"),
        "auto_generate_replay_panes must accept a granularity or Timeframe \
         parameter to choose D1 vs M1 and to skip CandlestickChart when \
         granularity is Trade. §4c acceptance criterion."
    );
}

// ── 4c-5: CandlestickChart is skipped when granularity = Trade ───────────────

#[test]
fn auto_generate_replay_panes_skips_candlestick_for_trade_granularity() {
    let src = read_dashboard_src();

    let start = src
        .find("fn auto_generate_replay_panes")
        .expect("auto_generate_replay_panes not found in dashboard.rs");
    let after = &src[start..];
    // Function is ~185 lines ≈ 11 kB; use 15 kB to cover the whole body.
    let window = &after[..after.len().min(15_000)];

    // The guard can be expressed as `if let Some(tf) = timeframe` or
    // `if granularity != Trade` etc. — we pin the existence of a conditional.
    assert!(
        window.contains("if let Some")
            || window.contains("timeframe.is_some")
            || window.contains("if timeframe"),
        "auto_generate_replay_panes must conditionally skip CandlestickChart \
         generation when granularity is Trade (no bars to render). \
         §4c acceptance criterion."
    );
}

// ── 4c-6: reload path rebinds stream/basis for changed granularity ────────────

#[test]
fn auto_generate_replay_panes_reload_rebinds_candlestick_stream() {
    let src = read_dashboard_src();

    let start = src
        .find("fn auto_generate_replay_panes")
        .expect("auto_generate_replay_panes not found in dashboard.rs");
    let after = &src[start..];
    let window = &after[..after.len().min(15_000)];

    // The reload path (is_first = false) must call set_content_and_streams for
    // CandlestickChart when timeframe is Some — not just clear_replay_chart_data.
    assert!(
        window.contains("set_content_and_streams"),
        "auto_generate_replay_panes reload path must call set_content_and_streams \
         to rebind the kline stream when granularity changes (D1↔M1). \
         §4c-6 acceptance criterion."
    );

    // The reload path must also close the CandlestickChart pane when switching
    // to Trade granularity (timeframe = None). Pin that panes.close is present.
    assert!(
        window.contains("panes.close") || window.contains("self.panes.close"),
        "auto_generate_replay_panes reload path must close the CandlestickChart \
         pane when granularity is Trade (no bars to render). \
         §4c-6 acceptance criterion."
    );

    // remove_registered_pane must be called to keep registry in sync after close.
    assert!(
        window.contains("remove_registered_pane"),
        "auto_generate_replay_panes must call remove_registered_pane after closing \
         CandlestickChart so the registry stays consistent. §4c-6 acceptance criterion."
    );
}
