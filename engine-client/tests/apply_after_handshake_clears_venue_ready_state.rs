//! Structural pin for invariant **T35-VenueReadyStateCycleClear**.
//!
//! `ProcessManager::apply_after_handshake_with_timeout` MUST clear
//! `self.venue_ready_state` at the top of the function so a stale
//! `VenueReady` snapshot from a previous recovery cycle does not
//! survive into a cycle that emits no fresh lifecycle events
//! (e.g. the user logged out between cycles, no credentials left,
//! no `SetVenueCredentials` sent, no `VenueReady` / `VenueError`
//! fired). Without the clear, `Flowsurface::Message::EngineConnected`'s
//! `try_is_venue_ready` query synthesizes a phantom
//! `VenueEvent::Ready` and the UI bootstraps to Ready when the
//! venue is actually un-authenticated.
//!
//! `main.rs`'s global `VENUE_READY_CACHE` is already cleared per
//! cycle (R3); this test mirrors that invariant for the
//! `ProcessManager` side cache so the OR query in `EngineConnected`
//! sees both caches in sync. Reviewer 2026-04-26 R6.
//!
//! Source-level scan because the relevant property is "the clear
//! call is the first statement of the function body" — exercising
//! it through the wait loop would require a live broadcast harness
//! plus a reused `ProcessManager` across two `start()` calls,
//! infrastructure that is not present in this test crate.

const FN_HEADER: &str = "pub async fn apply_after_handshake_with_timeout(";
const REQUIRED_CLEAR: &str = "self.venue_ready_state.lock().await.clear()";

#[test]
fn apply_after_handshake_clears_stale_venue_ready_state() {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/process.rs");
    let src = std::fs::read_to_string(path).expect("read engine-client/src/process.rs");

    let header_pos = src
        .find(FN_HEADER)
        .expect("apply_after_handshake_with_timeout signature not found");
    // Slice through the function body (next top-level `\n    pub async fn`
    // or end of file). Generous bound — apply_after_handshake_with_timeout
    // is ~250 lines and we only care about the prefix.
    let after = &src[header_pos..];
    let body_start = after.find('{').expect("function body open brace") + 1;
    let body = &after[body_start..(body_start + 4_000).min(after.len())];

    assert!(
        body.contains(REQUIRED_CLEAR),
        "apply_after_handshake_with_timeout body must call \
         `self.venue_ready_state.lock().await.clear()` near the top \
         to drop stale snapshots from previous recovery cycles. \
         T35-VenueReadyStateCycleClear / Reviewer 2026-04-26 R6."
    );

    // Defensive ordering check: the clear should appear BEFORE the
    // `subscribe_events()` call so even early VenueReady events from
    // the new cycle land in a freshly-cleared cache.
    let clear_pos = body.find(REQUIRED_CLEAR).unwrap();
    let subscribe_pos = body
        .find("connection.subscribe_events()")
        .expect("subscribe_events call not found in apply_after_handshake_with_timeout");
    assert!(
        clear_pos < subscribe_pos,
        "venue_ready_state.clear() must run BEFORE subscribe_events() so \
         the new cycle starts from an empty cache (clear at offset {clear_pos} \
         vs subscribe at offset {subscribe_pos})"
    );
}
