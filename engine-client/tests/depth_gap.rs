/// Tests for `DepthTracker` gap-detection logic.
use flowsurface_engine_client::DepthTracker;

#[test]
fn snapshot_then_sequential_diffs_ok() {
    let mut tracker = DepthTracker::new();
    assert!(tracker.on_snapshot("BTCUSDT", "s1", 100));
    assert!(tracker.on_diff("BTCUSDT", "s1", 101, 100));
    assert!(tracker.on_diff("BTCUSDT", "s1", 102, 101));
    assert!(tracker.on_diff("BTCUSDT", "s1", 103, 102));
}

#[test]
fn gap_detected_when_prev_seq_skips() {
    let mut tracker = DepthTracker::new();
    tracker.on_snapshot("ETHUSDT", "s1", 200);
    // seq=202 arrives but prev_seq=201 ≠ last(200) → gap
    let ok = tracker.on_diff("ETHUSDT", "s1", 202, 201);
    assert!(!ok, "should detect gap");
}

#[test]
fn gap_detected_without_snapshot() {
    let mut tracker = DepthTracker::new();
    let ok = tracker.on_diff("SOLUSDT", "s1", 1, 0);
    assert!(!ok, "diff before snapshot is a gap");
}

#[test]
fn new_session_id_is_independent() {
    let mut tracker = DepthTracker::new();
    tracker.on_snapshot("BTCUSDT", "s1", 50);
    tracker.on_diff("BTCUSDT", "s1", 51, 50);

    // A new session_id starts fresh
    tracker.on_snapshot("BTCUSDT", "s2", 1);
    assert!(tracker.on_diff("BTCUSDT", "s2", 2, 1));

    // Original session is still valid
    assert!(tracker.on_diff("BTCUSDT", "s1", 52, 51));
}

#[test]
fn reset_ticker_clears_all_sessions() {
    let mut tracker = DepthTracker::new();
    tracker.on_snapshot("BTCUSDT", "s1", 100);
    tracker.on_snapshot("BTCUSDT", "s2", 200);
    tracker.on_snapshot("ETHUSDT", "s1", 300);

    tracker.reset_ticker("BTCUSDT");

    // BTCUSDT sessions are gone
    assert!(!tracker.on_diff("BTCUSDT", "s1", 101, 100));
    assert!(!tracker.on_diff("BTCUSDT", "s2", 201, 200));

    // ETHUSDT is unaffected
    assert!(tracker.on_diff("ETHUSDT", "s1", 301, 300));
}

#[test]
fn reset_all_clears_everything() {
    let mut tracker = DepthTracker::new();
    tracker.on_snapshot("BTCUSDT", "s1", 100);
    tracker.on_snapshot("ETHUSDT", "s1", 200);
    tracker.reset_all();

    assert!(!tracker.on_diff("BTCUSDT", "s1", 101, 100));
    assert!(!tracker.on_diff("ETHUSDT", "s1", 201, 200));
}

#[test]
fn multiple_tickers_tracked_independently() {
    let mut tracker = DepthTracker::new();
    tracker.on_snapshot("BTCUSDT", "s1", 1000);
    tracker.on_snapshot("ETHUSDT", "s1", 500);

    assert!(tracker.on_diff("BTCUSDT", "s1", 1001, 1000));
    assert!(tracker.on_diff("ETHUSDT", "s1", 501, 500));

    // BTCUSDT gap does not affect ETHUSDT
    assert!(!tracker.on_diff("BTCUSDT", "s1", 1010, 1009)); // gap
    assert!(tracker.on_diff("ETHUSDT", "s1", 502, 501)); // still valid
}
