//! Structural pin for invariant **T35-VenueNamesIncludesTachibana**.
//!
//! `VENUE_NAMES` in `src/main.rs` drives initial backend registration
//! (`Flowsurface::new` and `Message::EngineConnected`'s rebuild loop).
//! Without `Venue::Tachibana` listed there, no `EngineClientBackend`
//! gets registered for the venue, and every
//! `handles.fetch_ticker_metadata(Venue::Tachibana, …)` call —
//! including the U4 gate's pending-replay path — errors out with
//! `No adapter handle configured for venue Tachibana`. The UI gate
//! still appears to work, but the metadata / stats / klines pipelines
//! all silently fail.
//!
//! Reviewer 2026-04-26 R4 (HIGH-1).

#[test]
fn venue_names_includes_tachibana_backend() {
    let path = concat!(env!("CARGO_MANIFEST_DIR"), "/src/main.rs");
    let src = std::fs::read_to_string(path).expect("read src/main.rs");

    // Locate `const VENUE_NAMES` and slice through to its closing `];`.
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
        body.contains("Venue::Tachibana"),
        "VENUE_NAMES must include Venue::Tachibana so an \
         EngineClientBackend is registered. Without the entry, every \
         fetch_ticker_metadata(Venue::Tachibana, …) call — including \
         the U4 gate's pending replay — errors with `No adapter handle \
         configured`. T35-VenueNamesIncludesTachibana"
    );

    // Also pin the wire identifier so a future rename keeps the
    // mapping aligned with `TACHIBANA_VENUE_NAME` / Python emitter.
    assert!(
        body.contains("\"tachibana\"") || body.contains("TACHIBANA_VENUE_NAME"),
        "VENUE_NAMES Tachibana entry must use the wire identifier \"tachibana\" \
         (or the TACHIBANA_VENUE_NAME constant) — the IPC venue tag is the \
         single source of truth shared with python/engine"
    );
}
