//! Structural guard for invariant **T35-RehelloOrder**.
//!
//! `engine_status_stream` must `yield Message::TachibanaVenueEvent(
//! VenueEvent::EngineRehello)` **before**
//! `yield Message::EngineConnected(conn)` in both the initial and the
//! `conn_rx.changed()` branches. The reverse order causes
//! `Flowsurface::update(Message::EngineConnected)` to call
//! `sidebar.update_handles()` while `tachibana_ready` still mirrors the
//! previous (now-stale) connection's `Ready` state — Tachibana
//! metadata refetch then bypasses the U4 gate. See
//! `docs/✅tachibana/implementation-plan-T3.5.md` §レビュー修正 R3.
//!
//! This is a source-level text scan rather than an AST/syn visitor
//! because the relevant `yield` expressions live inside an
//! `async_stream::stream!` macro body — syn cannot recognise the macro
//! contents as parseable Rust without expanding the macro. A literal
//! text search is sufficient: both yield lines are unambiguous and
//! their relative ordering is the load-bearing property.

const ENGINE_STATUS_STREAM_START: &str = "fn engine_status_stream()";
const REHELLO_TOKEN: &str = "Message::TachibanaVenueEvent(VenueEvent::EngineRehello)";
const CONNECTED_TOKEN: &str = "Message::EngineConnected(conn)";

#[test]
fn engine_rehello_yields_before_engine_connected_in_both_branches() {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/main.rs");
    let src = std::fs::read_to_string(path).expect("read src/main.rs");

    let body_start = src
        .find(ENGINE_STATUS_STREAM_START)
        .expect("engine_status_stream() definition not found in src/main.rs");
    // Slice from the function header to the next top-level `\nfn ` so we
    // do not pick up unrelated yields elsewhere in the file (the Stream
    // helper functions are the only callers of these tokens, but the
    // bound is cheap defensive scoping).
    let after = &src[body_start..];
    let end = after[1..]
        .find("\nfn ")
        .map(|n| n + 1)
        .unwrap_or(after.len());
    let body = &after[..end];

    let rehello_positions: Vec<usize> = body
        .match_indices(REHELLO_TOKEN)
        .map(|(idx, _)| idx)
        .collect();
    let connected_positions: Vec<usize> = body
        .match_indices(CONNECTED_TOKEN)
        .map(|(idx, _)| idx)
        .collect();

    // Each branch (initial-conn and conn_rx.changed) yields the pair
    // exactly once. If either count drifts, refactor below to update
    // the expectation alongside the source change.
    assert_eq!(
        rehello_positions.len(),
        2,
        "expected 2 EngineRehello yields in engine_status_stream body \
         (initial + changed branches), found {}",
        rehello_positions.len()
    );
    assert_eq!(
        connected_positions.len(),
        2,
        "expected 2 EngineConnected yields in engine_status_stream body \
         (initial + changed branches), found {}",
        connected_positions.len()
    );

    for (i, (rehello, connected)) in rehello_positions
        .iter()
        .zip(connected_positions.iter())
        .enumerate()
    {
        assert!(
            rehello < connected,
            "T35-RehelloOrder violated in engine_status_stream pair {}: \
             EngineRehello must yield before EngineConnected so the FSM \
             reset (set_tachibana_ready(false)) lands BEFORE the \
             update_handles refetch in the EngineConnected handler. \
             rehello_offset={rehello}, connected_offset={connected}",
            i + 1
        );
    }
}
