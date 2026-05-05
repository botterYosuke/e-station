//! Regression guard for the schema 3.12 fix
//! ([docs/✅python-data-engine/replay-pane-auto-generate-fix.md]).
//!
//! Before the fix, `map_engine_event_to_tachibana` (now
//! `map_engine_event_to_message`) had **no arm** for
//! `EngineEvent::ReplayDataLoaded`, so the event fell into the trailing
//! `_ => None` and `Dashboard::auto_generate_replay_panes` was never
//! invoked. The replay GUI silently stayed on the empty starter pane.
//!
//! `flowsurface` is a bin-only crate, so the dispatcher cannot be invoked
//! directly from an integration-test binary. Instead, we string-scan
//! `src/main.rs` for the dispatcher and confirm the arm shape. Any future
//! refactor that drops the arm — e.g. by reverting to a `_ => None`
//! catch-all — will fail this test.

const SOURCE: &str = include_str!("../src/main.rs");

fn extract_function_body<'a>(source: &'a str, sig_marker: &str) -> Option<&'a str> {
    let start = source.find(sig_marker)?;
    let rest = &source[start..];
    let open = rest.find('{')?;
    let bytes = rest.as_bytes();
    let mut depth: i32 = 0;
    let mut i = open;
    while i < bytes.len() {
        match bytes[i] {
            b'{' => depth += 1,
            b'}' => {
                depth -= 1;
                if depth == 0 {
                    return Some(&rest[open..=i]);
                }
            }
            _ => {}
        }
        i += 1;
    }
    None
}

#[test]
fn dispatcher_renamed_to_map_engine_event_to_message() {
    assert!(
        SOURCE.contains("fn map_engine_event_to_message"),
        "map_engine_event_to_message not found in src/main.rs — was it renamed back to \
         map_engine_event_to_tachibana? See \
         docs/✅python-data-engine/replay-pane-auto-generate-fix.md (schema 3.12)."
    );
    assert!(
        !SOURCE.contains("fn map_engine_event_to_tachibana"),
        "Old name `map_engine_event_to_tachibana` is still present — the rename was \
         incomplete. The historical name caused the schema 3.12 ReplayDataLoaded miss \
         because future authors mistook the dispatcher for a Tachibana-specific helper."
    );
}

#[test]
fn dispatcher_routes_replay_data_loaded_to_message() {
    let body = extract_function_body(SOURCE, "fn map_engine_event_to_message")
        .expect("could not extract dispatcher body — signature changed?");

    assert!(
        body.contains("EngineEvent::ReplayDataLoaded"),
        "dispatcher has no `EngineEvent::ReplayDataLoaded` arm — the regression the \
         schema 3.12 fix prevents has been reintroduced (auto_generate_replay_panes \
         will never be called and the replay GUI will stay empty)."
    );
    assert!(
        body.contains("Message::ReplayDataLoaded"),
        "ReplayDataLoaded arm does not yield `Message::ReplayDataLoaded` — \
         Flowsurface::update() will not be able to invoke \
         Dashboard::auto_generate_replay_panes."
    );
    assert!(
        body.contains("instrument_id"),
        "ReplayDataLoaded arm does not destructure `instrument_id` — without it, \
         helper attach mode cannot tell the dashboard which instrument to generate \
         panes for."
    );
    assert!(
        body.contains("granularity"),
        "ReplayDataLoaded arm does not destructure `granularity` — the candle pane \
         will be created with the wrong (or missing) timeframe."
    );
}
