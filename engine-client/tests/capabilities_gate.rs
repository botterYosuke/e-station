//! B3 (plan §T4 L600): UI-state-model contract for timeframe gating.
//!
//! When the engine advertises `supported_timeframes=["1d"]` for a venue,
//! the timeframe selector must:
//!   * mark `"1d"` as enabled,
//!   * mark every other timeframe (`"5m"`, `"1h"`, ...) as disabled.
//!
//! When no advertisement is made (capabilities not yet received, or the
//! venue did not declare a constraint), every timeframe must default to
//! enabled — fail-open avoids a hard UI lockout from a schema bug or a
//! mid-handshake state.

use flowsurface_engine_client::capabilities::is_timeframe_enabled;
use serde_json::json;

#[test]
fn test_unsupported_timeframes_are_disabled_when_capabilities_received() {
    let caps = json!({
        "supported_venues": ["tachibana"],
        "venue_capabilities": {
            "tachibana": {"supported_timeframes": ["1d"]},
        },
    });
    assert!(is_timeframe_enabled(&caps, "tachibana", "1d").unwrap());
    assert!(!is_timeframe_enabled(&caps, "tachibana", "1m").unwrap());
    assert!(!is_timeframe_enabled(&caps, "tachibana", "5m").unwrap());
    assert!(!is_timeframe_enabled(&caps, "tachibana", "1h").unwrap());
}

#[test]
fn test_all_timeframes_enabled_when_capabilities_missing() {
    let caps = json!({});
    assert!(is_timeframe_enabled(&caps, "tachibana", "1d").unwrap());
    assert!(is_timeframe_enabled(&caps, "tachibana", "5m").unwrap());
    assert!(is_timeframe_enabled(&caps, "binance", "1m").unwrap());
}

#[test]
fn test_venue_without_constraint_is_fail_open() {
    let caps = json!({
        "venue_capabilities": {
            "tachibana": {"supported_timeframes": ["1d"]},
        },
    });
    // binance did not advertise — fail-open.
    assert!(is_timeframe_enabled(&caps, "binance", "5m").unwrap());
}

#[test]
fn test_malformed_venue_capabilities_returns_err() {
    let caps = serde_json::json!({ "venue_capabilities": "not_an_object" });
    assert!(is_timeframe_enabled(&caps, "tachibana", "1d").is_err());
}
