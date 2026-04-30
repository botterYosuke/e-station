//! Structural pin for invariant **T36-VenueNamesIncludesReplay**.
//!
//! `VENUE_NAMES` in `src/main.rs` drives initial backend registration
//! (`Flowsurface::new` and `Message::EngineConnected`'s rebuild loop).
//! Without `Venue::Replay` listed there, no `EngineClientBackend`
//! gets registered for the replay venue, and every
//! `KlineUpdate { venue: "replay", ... }` IPC event would fall through
//! to the Binance fallback warn in `backend.rs:exchange_for`.
//!
//! §4b acceptance criterion.

#[test]
fn venue_names_includes_replay_backend() {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/main.rs");
    let src = std::fs::read_to_string(path).expect("read src/main.rs");

    let start = src
        .find("const VENUE_NAMES")
        .expect("VENUE_NAMES const not found in src/main.rs");
    let after = &src[start..];
    let end = after
        .find("];")
        .map(|n| n + 2)
        .expect("VENUE_NAMES const missing closing `];`");
    let body = &after[..end];

    assert!(
        body.contains("Venue::Replay"),
        "VENUE_NAMES must include Venue::Replay so an EngineClientBackend \
         is registered for the replay venue. Without the entry, KlineUpdate \
         events with venue=\"replay\" would fall back to the Binance backend. \
         T36-VenueNamesIncludesReplay"
    );

    assert!(
        body.contains("\"replay\""),
        "VENUE_NAMES Replay entry must use the wire identifier \"replay\" — \
         this must match the venue string emitted by Python engine_runner.py \
         (_IPC_VENUE_TAG = \"replay\"). T36-VenueNamesIncludesReplay"
    );
}
