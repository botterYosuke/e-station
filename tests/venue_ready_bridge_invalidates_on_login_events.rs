//! Structural pin for invariant **T35-CacheInvalidation**.
//!
//! The mode-agnostic `VENUE_READY_CACHE` bridge in `src/main.rs` must
//! invalidate the cached `VenueReady` not only on `VenueError` but
//! also on `VenueLoginStarted` and `VenueLoginCancelled`. Without
//! these arms, a stale `Ready` from a previous session can survive a
//! cancelled re-login, and the next engine reconnect resurrects it
//! via `Message::EngineConnected`'s synthesized `VenueEvent::Ready`
//! — the FSM bootstraps to Ready when the user actually has no live
//! session. Reviewer 2026-04-26 R4 (MEDIUM-3).
//!
//! This is a source-level structural pin (not a live broadcast unit
//! test) because the bridge runs inside a `tokio::spawn` future on a
//! dedicated runtime; the load-bearing property is "all four
//! lifecycle event arms are present in every bridge body".

const REQUIRED_ARMS: &[&str] = &[
    "EngineEvent::VenueReady",
    "EngineEvent::VenueError",
    "EngineEvent::VenueLoginStarted",
    "EngineEvent::VenueLoginCancelled",
];

#[test]
fn every_venue_ready_bridge_handles_all_four_lifecycle_events() {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/main.rs");
    let src = std::fs::read_to_string(path).expect("read src/main.rs");

    // `event_rx.recv().await` is the load-bearing call inside every
    // bridge body. We slice each occurrence's surrounding window and
    // assert every required arm is present in that window. Adding a
    // 5th bridge in the future is benign; the assertion runs on each.
    let needle = "event_rx.recv().await";
    let mut bodies = Vec::new();
    let mut search_from = 0;
    while let Some(pos) = src[search_from..].find(needle) {
        let absolute = search_from + pos;
        // 1500 bytes is enough to cover the longest existing bridge
        // body (about 900 bytes) with margin.
        let end = (absolute + 1500).min(src.len());
        bodies.push(&src[absolute..end]);
        search_from = absolute + needle.len();
    }
    assert!(
        bodies.len() >= 3,
        "expected at least 3 venue-ready bridge bodies in main.rs \
         (helper + external-mode reconnect inline + managed-mode inline), \
         found {}",
        bodies.len()
    );
    for (i, body) in bodies.iter().enumerate() {
        for arm in REQUIRED_ARMS {
            assert!(
                body.contains(arm),
                "venue-ready bridge body #{i} (event_rx.recv() occurrence #{i}) \
                 missing required arm `{arm}`. All bridges must invalidate the \
                 cache on every lifecycle edge or stale Ready resurrects on \
                 reconnect (T35-CacheInvalidation / R4 MEDIUM-3)."
            );
        }
    }
}
